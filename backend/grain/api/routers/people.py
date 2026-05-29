"""/api/people — speakers + sponsors + attendees per conference."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ... import db
from ...icp import IcpConfig

router = APIRouter(prefix="/api/people", tags=["people"])


class PersonIn(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=200)
    title: Optional[str] = Field(None, max_length=200)
    company_name: Optional[str] = Field(None, max_length=200)
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    vertical: Optional[str] = None
    conference_id: Optional[str] = None
    source_kind: str = "manual_add"


@router.get("")
def list_people(
    conference_id: Optional[str] = None,
    persona: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    where, params = [], []
    if conference_id:
        where.append("conference_id = ?"); params.append(conference_id)
    if persona:
        where.append("persona = ?"); params.append(persona)
    sql = "SELECT * FROM people"
    count_sql = "SELECT COUNT(*) FROM people"
    if where:
        clause = " WHERE " + " AND ".join(where)
        sql += clause
        count_sql += clause
    sql += " ORDER BY persona_weight DESC NULLS LAST, icp_score DESC NULLS LAST LIMIT ? OFFSET ?"
    conn = db.get_conn()
    try:
        rows = conn.execute(sql, params + [limit, offset]).fetchall()
        total = conn.execute(count_sql, params).fetchone()[0]  # respects filters
    finally:
        conn.close()
    return {"total": total, "count": len(rows), "items": [dict(r) for r in rows]}


@router.get("/{person_id}")
def get_person(person_id: str) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "person not found")
    return dict(row)


@router.post("", status_code=201)
def add_person(body: PersonIn) -> dict:
    icp = IcpConfig.default()
    persona, weight, matched = icp.classify_persona(body.title or "")
    person_id = f"p_man_{uuid.uuid4().hex[:12]}"
    payload = {
        "id": person_id,
        "full_name": body.full_name,
        "first_name": (body.full_name.split() or [""])[0],
        "last_name": " ".join(body.full_name.split()[1:]),
        "title": body.title, "company_name": body.company_name,
        "email": body.email, "linkedin_url": body.linkedin_url,
        "vertical": body.vertical, "source_kind": body.source_kind,
        "conference_id": body.conference_id,
        "persona": persona, "persona_weight": float(weight or 0.0),
        "created_at": db.now_iso(),
    }
    conn = db.get_conn()
    try:
        cols = ",".join(payload.keys())
        ph = ",".join("?" * len(payload))
        conn.execute(f"INSERT INTO people ({cols}) VALUES ({ph})", tuple(payload.values()))
    finally:
        conn.close()
    db.log_feedback(
        decision_kind="person_added",
        target_kind="person", target_id=person_id,
        after={"full_name": body.full_name, "persona": persona, "matched": matched},
        decided_by="api", reason="manual add",
    )
    return payload


@router.delete("/{person_id}")
def delete_person(person_id: str) -> dict:
    conn = db.get_conn()
    try:
        cur = conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "person not found")
    finally:
        conn.close()
    db.log_feedback(decision_kind="person_deleted", target_kind="person",
                    target_id=person_id, decided_by="api")
    return {"status": "deleted"}


class IcpOverride(BaseModel):
    persona: Optional[str] = Field(None, description="Override persona: BUYER/CHAMPION/PAIN_OWNER/GATEKEEPER/ENTRY_POINT/INFLUENCER")
    persona_weight: Optional[float] = Field(None, ge=0.0, le=1.0)
    icp_score: Optional[float] = Field(None, ge=0.0, le=100.0)
    reason: str
    decided_by: Optional[str] = "ui"


VALID_PERSONAS = {"BUYER", "CHAMPION", "PAIN_OWNER", "GATEKEEPER", "ENTRY_POINT", "INFLUENCER"}


@router.post("/{person_id}/icp/override")
def override_icp(person_id: str, body: IcpOverride) -> dict:
    """Human-in-the-loop persona / weight / ICP-score override on a discovered
    person. The rep knows reality on the ground better than the title-pattern
    classifier — let them argue."""
    if body.persona and body.persona not in VALID_PERSONAS:
        raise HTTPException(400, f"persona must be one of {sorted(VALID_PERSONAS)}")
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT persona, persona_weight, icp_score FROM people WHERE id = ?",
            (person_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "person not found")
        before = dict(row)
        updates: dict = {}
        if body.persona is not None:
            updates["persona"] = body.persona
        if body.persona_weight is not None:
            updates["persona_weight"] = float(body.persona_weight)
        if body.icp_score is not None:
            updates["icp_score"] = float(body.icp_score)
        if not updates:
            raise HTTPException(400, "no fields to update")
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE people SET {set_clause} WHERE id = ?",
            (*updates.values(), person_id),
        )
    finally:
        conn.close()
    db.log_feedback(
        decision_kind="people_score_override",
        target_kind="person", target_id=person_id,
        before=before, after=updates,
        reason=body.reason, decided_by=body.decided_by,
    )
    return {"status": "overridden", "before": before, "after": updates}
