"""/api/conferences — list, filter, get, rescore, manual score adjust."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from ... import db, scoring

router = APIRouter(prefix="/api/conferences", tags=["conferences"])


def _row(r) -> dict:
    d = dict(r)
    if d.get("score_breakdown_json"):
        try:
            d["score_breakdown"] = json.loads(d["score_breakdown_json"])
        except (json.JSONDecodeError, TypeError):
            d["score_breakdown"] = None
    return d


@router.get("")
def list_conferences(
    tier: Optional[str] = None,
    region: Optional[str] = None,
    vertical: Optional[str] = None,
    min_score: Optional[float] = None,
    search: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    where, params = [], []
    if tier:
        where.append("tier = ?"); params.append(tier)
    if region:
        where.append("region = ?"); params.append(region)
    if vertical:
        where.append("vertical = ?"); params.append(vertical)
    if min_score is not None:
        where.append("score >= ?"); params.append(min_score)
    if search and search.strip():
        # DEFECT 1: case-insensitive match on name OR themes.
        like = f"%{search.strip().lower()}%"
        where.append("(LOWER(name) LIKE ? OR LOWER(IFNULL(themes,'')) LIKE ?)")
        params.append(like); params.append(like)
    sql = "SELECT * FROM conferences"
    count_sql = "SELECT COUNT(*) FROM conferences"
    if where:
        clause = " WHERE " + " AND ".join(where)
        sql += clause
        count_sql += clause
    sql += " ORDER BY score DESC NULLS LAST, start_date ASC LIMIT ? OFFSET ?"
    conn = db.get_conn()
    try:
        rows = conn.execute(sql, params + [limit, offset]).fetchall()
        total = conn.execute(count_sql, params).fetchone()[0]  # respects filters
        # How many reps cover each event — so the list rows can show the same
        # coverage signal as the dashboard ("3 reps" / "cover it") in one query.
        rep_counts = {
            r[0]: r[1] for r in conn.execute(
                "SELECT conference_id, COUNT(*) FROM coverage GROUP BY conference_id"
            ).fetchall()
        }
    finally:
        conn.close()
    items = []
    for r in rows:
        d = _row(r)
        d["reps_assigned"] = rep_counts.get(d["id"], 0)
        items.append(d)
    return {"total": total, "count": len(rows), "items": items}


class ConferenceIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    start_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    end_date: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    region: Optional[str] = Field(None, description="NA / EU / APAC / MEA / LATAM")
    vertical: Optional[str] = None
    format: Optional[str] = Field(None, description="expo / summit / conference / webinar")
    themes: Optional[str] = None
    estimated_attendance: Optional[int] = None
    cost_pass_usd: Optional[float] = None
    website: Optional[str] = None


@router.post("", status_code=201)
def create_conference(body: ConferenceIn) -> dict:
    """Manually add an event a non-developer wants to track. It is scored by the
    same 7-factor model immediately, so it slots into the tiering with no code."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:48] or "event"
    cid = f"{slug}-{db.now_iso()[:10]}"
    payload = {
        "id": cid, "name": body.name.strip(),
        "start_date": body.start_date, "end_date": body.end_date or body.start_date,
        "city": body.city, "country": body.country, "region": (body.region or "").upper() or None,
        "vertical": body.vertical, "format": body.format, "themes": body.themes,
        "estimated_attendance": body.estimated_attendance, "cost_pass_usd": body.cost_pass_usd,
        "website": body.website, "created_at": db.now_iso(), "updated_at": db.now_iso(),
    }
    conn = db.get_conn()
    try:
        if conn.execute("SELECT 1 FROM conferences WHERE id = ?", (cid,)).fetchone():
            raise HTTPException(409, "an event with this name+date already exists")
        cols = ",".join(payload.keys())
        ph = ",".join("?" * len(payload))
        conn.execute(f"INSERT INTO conferences ({cols}) VALUES ({ph})", tuple(payload.values()))
    finally:
        conn.close()
    # Score it right away so it appears tiered.
    s = scoring.score_conference({**payload, "id": cid})
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE conferences SET score = ?, tier = ?, score_breakdown_json = ?, updated_at = ? WHERE id = ?",
            (s.total, s.tier, json.dumps(s.to_breakdown_dict(), ensure_ascii=False), db.now_iso(), cid),
        )
    finally:
        conn.close()
    return {"id": cid, "score": round(s.total, 1), "tier": s.tier}


@router.get("/{conference_id}")
def get_conference(conference_id: str) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM conferences WHERE id = ?", (conference_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "conference not found")
    return _row(row)


@router.get("/{conference_id}/outcomes")
def conference_outcomes(conference_id: str) -> dict:
    """What actually happened at this event: the connections made, briefs
    prepped, meetings booked, and follow-ups drafted — the per-event results the
    manager wants in one place."""
    conn = db.get_conn()
    try:
        def one(sql, *a):
            return conn.execute(sql, a).fetchone()[0]
        encounters = one("SELECT COUNT(*) FROM encounters WHERE conference_id = ?", conference_id)
        contacts = one("SELECT COUNT(DISTINCT contact_id) FROM encounters "
                       "WHERE conference_id = ? AND contact_id IS NOT NULL", conference_id)
        meetings = one("SELECT COUNT(*) FROM encounters WHERE conference_id = ? "
                       "AND meeting_requested = 1", conference_id)
        drafts = one("SELECT COUNT(*) FROM encounters WHERE conference_id = ? "
                     "AND followup_draft IS NOT NULL AND followup_draft != ''", conference_id)
        briefs = one("SELECT COUNT(*) FROM briefs WHERE conference_id = ?", conference_id)
        rows = conn.execute(
            "SELECT e.id, e.contact_id, e.captured_at, e.meeting_requested, "
            "c.primary_name, c.primary_company, c.primary_title, c.arc_verdict "
            "FROM encounters e LEFT JOIN contacts c ON c.id = e.contact_id "
            "WHERE e.conference_id = ? ORDER BY e.captured_at DESC LIMIT 15",
            (conference_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "encounters": encounters, "contacts": contacts, "meetings": meetings,
        "drafts": drafts, "briefs": briefs,
        "connections": [dict(r) for r in rows],
    }


@router.post("/rescore", status_code=202)
def rescore_all() -> dict:
    n = scoring.rescore_all()
    return {"rescored": n}


class ScoreAdjust(BaseModel):
    delta: float = Field(..., ge=-50.0, le=50.0,
                         description="Add/subtract from the current score (0..100 scale)")
    reason: str
    decided_by: Optional[str] = "ui"


@router.post("/{conference_id}/score/adjust")
def adjust_score(
    conference_id: str,
    body: ScoreAdjust,
    background_tasks: BackgroundTasks,
) -> dict:
    """Human-in-the-loop manual score adjustment. The rep can argue with the
    7-factor model when reality on the ground says otherwise.

    DEFECT 6: the adjusted score is persisted as a sticky OVERRIDE (via
    scoring.set_score_override), so the next /rescore respects it instead of
    silently wiping the human's call back to the model number.

    SCALE FIX: this endpoint MUST NOT block on a full rescore_all() (re-scoring
    all ~195 events). We update the ONE adjusted conference synchronously and
    return immediately; the global rescore (which only refreshes other events'
    model breakdowns, never touching this sticky override) is scheduled in the
    BACKGROUND so rapid sequential adjusts each return fast.
    """
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT score, tier FROM conferences WHERE id = ?", (conference_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "conference not found")
        before = float(row["score"] or 0)
        after = max(0.0, min(100.0, before + body.delta))
        new_tier = "A" if after >= 70 else "B" if after >= 50 else "C"
        conn.execute(
            "UPDATE conferences SET score = ?, tier = ?, updated_at = ? "
            "WHERE id = ?",
            (after, new_tier, db.now_iso(), conference_id),
        )
    finally:
        conn.close()
    # Pin the override so rescore_all() won't overwrite it. (Synchronous: the
    # sticky override must be durable BEFORE the background rescore can run.)
    scoring.set_score_override(conference_id, after)
    db.log_feedback(
        decision_kind="conference_score_adjust",
        target_kind="conference", target_id=conference_id,
        before={"score": before, "tier": row["tier"]},
        after={"score": after, "tier": new_tier, "delta": body.delta},
        reason=body.reason, decided_by=body.decided_by,
    )
    # Refresh every OTHER event's model breakdown shortly after, off the request
    # path. rescore_all() honours this conference's sticky override, so the rep's
    # call is never clobbered; other tiers just catch up moments later.
    background_tasks.add_task(scoring.rescore_all)
    return {"score": after, "tier": new_tier, "delta": body.delta, "before": before}
