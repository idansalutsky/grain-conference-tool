"""/api/nudges — active nudges + dismiss."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import db, nudge

router = APIRouter(prefix="/api/nudges", tags=["nudges"])


@router.get("")
def list_active_nudges() -> dict:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, primary_name, primary_company, primary_title, "
            "arc_verdict, arc_confidence, arc_summary, nudge_text, updated_at "
            "FROM contacts WHERE nudge_active = 1 ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return {"count": len(rows), "items": [dict(r) for r in rows]}


class NudgeDismiss(BaseModel):
    reason: str
    decided_by: str = "ui"


@router.post("/{contact_id}/dismiss")
def dismiss_nudge(contact_id: str, body: NudgeDismiss) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT nudge_active, nudge_text FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "contact not found")
        before = {"nudge_active": row["nudge_active"], "nudge_text": row["nudge_text"]}
        conn.execute(
            "UPDATE contacts SET nudge_active = 0, nudge_text = NULL, "
            "updated_at = ? WHERE id = ?",
            (db.now_iso(), contact_id),
        )
    finally:
        conn.close()
    db.log_feedback(
        decision_kind="nudge_dismiss", target_kind="contact",
        target_id=contact_id, before=before,
        after={"dismissed": True}, reason=body.reason,
        decided_by=body.decided_by,
    )
    return {"status": "dismissed"}


@router.post("/{contact_id}/accept")
def accept_nudge(contact_id: str, body: NudgeDismiss) -> dict:
    """Mark a nudge as accepted (rep is going to act on it). Logs to feedback."""
    db.log_feedback(
        decision_kind="nudge_accept", target_kind="contact",
        target_id=contact_id, reason=body.reason,
        decided_by=body.decided_by,
    )
    return {"status": "accepted"}


@router.post("/recompute")
def recompute_all_nudges() -> dict:
    """Re-evaluate the nudge gate for every contact. Used after the settings
    sliders change."""
    conn = db.get_conn()
    try:
        ids = [r["id"] for r in conn.execute("SELECT id FROM contacts").fetchall()]
    finally:
        conn.close()
    n_active = 0
    for cid in ids:
        out = nudge.evaluate(cid)
        if out.get("nudge_active"):
            n_active += 1
    return {"recomputed": len(ids), "active_now": n_active}
