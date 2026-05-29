"""/api/contacts — cross-conference canonical contacts (with arc verdict)."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import arc, db, nudge

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


@router.get("")
def list_contacts(
    arc_verdict: Optional[str] = None,
    nudge_active: Optional[bool] = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    where, params = [], []
    if arc_verdict:
        where.append("arc_verdict = ?"); params.append(arc_verdict)
    if nudge_active is not None:
        where.append("nudge_active = ?"); params.append(1 if nudge_active else 0)
    sql = "SELECT * FROM contacts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    conn = db.get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    finally:
        conn.close()
    return {"total": total, "count": len(rows), "items": [dict(r) for r in rows]}


@router.get("/{contact_id}")
def get_contact(contact_id: str) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        if not row:
            raise HTTPException(404, "contact not found")
        enc_rows = conn.execute(
            "SELECT id, conference_id, captured_at, capture_mode, sentiment, "
            "meeting_requested, structured_json, soft_signals_json "
            "FROM encounters WHERE contact_id = ? ORDER BY captured_at DESC",
            (contact_id,),
        ).fetchall()
        brief_rows = conn.execute(
            "SELECT id, conference_id, brief_text, brief_json, generated_at "
            "FROM briefs WHERE contact_id = ? ORDER BY generated_at DESC LIMIT 5",
            (contact_id,),
        ).fetchall()
    finally:
        conn.close()
    encounters = []
    for r in enc_rows:
        d = dict(r)
        d["structured"] = json.loads(d.pop("structured_json") or "{}")
        d["soft_signals"] = json.loads(d.pop("soft_signals_json") or "[]")
        encounters.append(d)
    briefs = []
    for r in brief_rows:
        d = dict(r)
        d["brief_json"] = json.loads(d["brief_json"] or "{}")
        briefs.append(d)
    return {**dict(row), "encounters": encounters, "briefs": briefs}


class ArcOverride(BaseModel):
    arc_verdict: str
    summary: Optional[str] = None
    decided_by: Optional[str] = "ui"


@router.post("/{contact_id}/arc/override")
def override_arc(contact_id: str, body: ArcOverride) -> dict:
    if body.arc_verdict not in {"warming", "flat", "cooling", "tire_kicker"}:
        raise HTTPException(400, "invalid arc verdict")
    conn = db.get_conn()
    try:
        before = conn.execute(
            "SELECT arc_verdict, arc_summary FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
        if not before:
            raise HTTPException(404, "contact not found")
        conn.execute(
            "UPDATE contacts SET arc_verdict = ?, arc_summary = ?, "
            "arc_confidence = 1.0, updated_at = ? WHERE id = ?",
            (body.arc_verdict, body.summary or "manually set", db.now_iso(), contact_id),
        )
    finally:
        conn.close()
    db.log_feedback(
        decision_kind="arc_override", target_kind="contact",
        target_id=contact_id, before=dict(before), after={
            "arc_verdict": body.arc_verdict, "summary": body.summary,
        }, decided_by=body.decided_by,
    )
    # Re-evaluate nudge after the override
    nudge.evaluate(contact_id)
    return {"status": "overridden"}


@router.post("/{contact_id}/arc/reclassify")
def reclassify_arc(contact_id: str) -> dict:
    """Re-run the arc classifier on demand (LLM + deterministic)."""
    verdict = arc.classify(contact_id, use_llm=True)
    nudge_state = nudge.evaluate(contact_id)
    return {
        "arc": {"kind": verdict.kind, "confidence": verdict.confidence,
                "summary": verdict.summary, "features": verdict.features},
        "nudge": nudge_state,
    }
