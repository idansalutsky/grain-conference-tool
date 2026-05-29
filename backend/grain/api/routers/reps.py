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
