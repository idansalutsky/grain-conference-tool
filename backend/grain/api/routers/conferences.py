"""/api/conferences — list, filter, get, rescore, manual score adjust."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
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
    finally:
        conn.close()
    return {"total": total, "count": len(rows), "items": [_row(r) for r in rows]}


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
def adjust_score(conference_id: str, body: ScoreAdjust) -> dict:
    """Human-in-the-loop manual score adjustment. The rep can argue with the
    7-factor model when reality on the ground says otherwise."""
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
    db.log_feedback(
        decision_kind="conference_score_adjust",
        target_kind="conference", target_id=conference_id,
        before={"score": before, "tier": row["tier"]},
        after={"score": after, "tier": new_tier, "delta": body.delta},
        reason=body.reason, decided_by=body.decided_by,
    )
    return {"score": after, "tier": new_tier, "delta": body.delta, "before": before}
