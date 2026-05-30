"""/api/reps + /api/coverage — manage the GTM team and who covers which event.

This is the "who covers what" the brief names in the business problem, and the
no-code admin that satisfies "a non-developer should be able to update this":
add reps, assign them to events, and bind each rep's Telegram per event.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ... import db
from ... import telegram

router = APIRouter(prefix="/api", tags=["team"])

REGIONS = {"NA", "EU", "APAC", "MEA", "LATAM"}


class RepIn(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=120)
    email: Optional[str] = Field(None, max_length=160)
    region: Optional[str] = Field(None, max_length=8)


# ---------------------------------------------------------------------------
# Reps
# ---------------------------------------------------------------------------
@router.get("/reps")
def list_reps() -> dict:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT r.*, "
            "(SELECT COUNT(*) FROM coverage c WHERE c.rep_id = r.id) AS events_covered, "
            "(SELECT COUNT(*) FROM encounters e WHERE e.rep_id = r.id) AS captures "
            "FROM reps r ORDER BY r.region, r.full_name"
        ).fetchall()
    finally:
        conn.close()
    return {"count": len(rows), "items": [dict(r) for r in rows]}


@router.post("/reps", status_code=201)
def create_rep(body: RepIn) -> dict:
    region = (body.region or "").upper() or None
    if region and region not in REGIONS:
        raise HTTPException(400, f"region must be one of {sorted(REGIONS)}")
    rep_id = "rep_" + uuid.uuid4().hex[:10]
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO reps (id, full_name, email, region, created_at) VALUES (?,?,?,?,?)",
            (rep_id, body.full_name.strip(), body.email, region, db.now_iso()),
        )
    finally:
        conn.close()
    db.log_feedback(decision_kind="rep_added", target_kind="rep", target_id=rep_id,
                    after={"full_name": body.full_name, "region": region}, decided_by="ui")
    return {"id": rep_id, "full_name": body.full_name, "email": body.email, "region": region}


@router.patch("/reps/{rep_id}")
def update_rep(rep_id: str, body: RepIn) -> dict:
    region = (body.region or "").upper() or None
    if region and region not in REGIONS:
        raise HTTPException(400, f"region must be one of {sorted(REGIONS)}")
    conn = db.get_conn()
    try:
        cur = conn.execute(
            "UPDATE reps SET full_name = ?, email = ?, region = ? WHERE id = ?",
            (body.full_name.strip(), body.email, region, rep_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "rep not found")
    finally:
        conn.close()
    return {"id": rep_id, "full_name": body.full_name, "email": body.email, "region": region}


@router.delete("/reps/{rep_id}")
def delete_rep(rep_id: str) -> dict:
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM coverage WHERE rep_id = ?", (rep_id,))
        cur = conn.execute("DELETE FROM reps WHERE id = ?", (rep_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "rep not found")
    finally:
        conn.close()
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Coverage (rep ↔ event assignments)
# ---------------------------------------------------------------------------
class CoverageIn(BaseModel):
    conference_id: str
    rep_id: str


@router.get("/coverage")
def list_coverage(conference_id: Optional[str] = None, rep_id: Optional[str] = None) -> dict:
    where, params = [], []
    if conference_id:
        where.append("c.conference_id = ?"); params.append(conference_id)
    if rep_id:
        where.append("c.rep_id = ?"); params.append(rep_id)
    sql = (
        "SELECT c.id, c.conference_id, c.rep_id, c.created_at, "
        "r.full_name AS rep_name, r.region AS rep_region, "
        "conf.name AS conference_name, conf.start_date, conf.city, conf.country, conf.tier "
        "FROM coverage c "
        "JOIN reps r ON r.id = c.rep_id "
        "JOIN conferences conf ON conf.id = c.conference_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY conf.start_date"
    conn = db.get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return {"count": len(rows), "items": [dict(r) for r in rows]}


@router.post("/coverage", status_code=201)
def assign_coverage(body: CoverageIn) -> dict:
    conn = db.get_conn()
    try:
        if not conn.execute("SELECT 1 FROM conferences WHERE id = ?", (body.conference_id,)).fetchone():
            raise HTTPException(404, "conference not found")
        if not conn.execute("SELECT 1 FROM reps WHERE id = ?", (body.rep_id,)).fetchone():
            raise HTTPException(404, "rep not found")
        cid = "cov_" + uuid.uuid4().hex[:10]
        try:
            conn.execute(
                "INSERT INTO coverage (id, conference_id, rep_id, created_at) VALUES (?,?,?,?)",
                (cid, body.conference_id, body.rep_id, db.now_iso()),
            )
        except Exception:
            raise HTTPException(409, "already assigned")
    finally:
        conn.close()
    return {"id": cid, "conference_id": body.conference_id, "rep_id": body.rep_id}


# ---------------------------------------------------------------------------
# "Send a rep their trip" — one click → paste-ready handoff message + bind link
# ---------------------------------------------------------------------------
_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _fmt_when(start_date: Optional[str]) -> str:
    """'2026-08-10' → 'Aug 2026'. Best-effort; returns '' if unparseable."""
    if not start_date or len(start_date) < 7:
        return ""
    try:
        year = start_date[0:4]
        month = int(start_date[5:7])
        if 1 <= month <= 12:
            return f"{_MONTHS[month]} {year}"
    except (ValueError, IndexError):
        pass
    return ""


def _event_line(name: str, city: Optional[str], when: str) -> str:
    """One bullet: '• Money20/20 — Las Vegas, Oct 2026'."""
    place = (city or "").strip()
    bits = [b for b in (place, when) if b]
    suffix = (" — " + ", ".join(bits)) if bits else ""
    return f"• {name}{suffix}"


@router.get("/reps/{rep_id}/event-links")
def rep_event_links(rep_id: str) -> dict:
    """One click → a paste-ready handoff message for a rep: their assigned
    events + a SINGLE identity Telegram bind link they tap once to start
    capturing in the field. The link is identity-only (conference_id=None) — the
    rep redeems it once, then sets their active event from the dashboard.
    """
    conn = db.get_conn()
    try:
        rep = conn.execute(
            "SELECT id, full_name FROM reps WHERE id = ?", (rep_id,)
        ).fetchone()
        if not rep:
            raise HTTPException(404, "rep not found")
        # Reuse the same coverage join shape as list_coverage, rep-scoped.
        rows = conn.execute(
            "SELECT conf.id, conf.name, conf.start_date, conf.city, conf.tier "
            "FROM coverage c "
            "JOIN conferences conf ON conf.id = c.conference_id "
            "WHERE c.rep_id = ? "
            "ORDER BY conf.start_date",
            (rep_id,),
        ).fetchall()
    finally:
        conn.close()

    # One PER-EVENT bind link each: tapping it on the phone connects the rep to
    # the bot AND sets that event as active, so captures auto-tag correctly.
    events = []
    for r in rows:
        link = telegram.deep_link(telegram.issue_link_token(rep_id, r["id"]))
        events.append({
            "id": r["id"], "name": r["name"], "start_date": r["start_date"],
            "city": r["city"], "tier": r["tier"], "deep_link": link,
        })

    first_name = (rep["full_name"] or "").strip().split(" ")[0] or "there"
    if events:
        n = len(events)
        bullets = "\n".join(
            f"• {e['name']} ({e['city'] or '—'}, {_fmt_when(e['start_date']) or 'TBC'})\n"
            f"  Connect for this event: {e['deep_link']}"
            for e in events
        )
        message_text = (
            f"Hi {first_name} — you're covering {n} "
            f"{'event' if n == 1 else 'events'} this season. Tap the link under "
            "each one when you're there and you're set to capture (voice / photo / "
            f"text → auto-logged to that event):\n\n{bullets}\n\n"
            "Each link connects your Telegram and sets that event as active. "
            "Anything you send the bot is logged + you get instant intel back."
        )
    else:
        # No coverage yet — still give an identity link so they can get set up.
        deep_link = telegram.deep_link(telegram.issue_link_token(rep_id))
        message_text = (
            f"Hi {first_name} — no events are assigned to you yet, but you can "
            "still connect now and pick your event later:\n\n"
            f"Connect Telegram: {deep_link}"
        )

    return {
        "rep_id": rep["id"],
        "rep_name": rep["full_name"],
        "events": events,
        "message_text": message_text,
    }


@router.delete("/coverage")
def unassign_coverage(conference_id: str, rep_id: str) -> dict:
    conn = db.get_conn()
    try:
        conn.execute(
            "DELETE FROM coverage WHERE conference_id = ? AND rep_id = ?",
            (conference_id, rep_id),
        )
    finally:
        conn.close()
    return {"status": "unassigned"}
