"""/api/briefs — approach brief generation + lookup."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ... import brief as brief_mod
from ... import db

router = APIRouter(prefix="/api/briefs", tags=["briefs"])


class BriefRequest(BaseModel):
    name: str
    company: str
    title: Optional[str] = None
    vertical: Optional[str] = None
    conference_id: Optional[str] = None
    contact_id: Optional[str] = None
    person_id: Optional[str] = None


@router.post("/generate", status_code=201)
def generate_brief(body: BriefRequest) -> dict:
    out = brief_mod.generate(
        name=body.name, company=body.company, title=body.title,
        vertical=body.vertical, conference_id=body.conference_id,
        contact_id=body.contact_id, person_id=body.person_id,
        use_web_search=True, persist=True,
    )
    return out


class PrepRequest(BaseModel):
    conference_id: str
    top_n: int = 5


@router.post("/prep", status_code=201)
def prep_for_event(body: PrepRequest) -> dict:
    """Generate approach briefs for the top N targets at a conference.

    Pre-event prep workflow: rep clicks 'prep me for Money20/20' before flying,
    we generate briefs for the highest-weight buying-committee targets so the
    rep walks in already armed.
    """
    conn = db.get_conn()
    try:
        targets = conn.execute(
            "SELECT id, full_name, title, company_name, vertical, persona "
            "FROM people WHERE conference_id = ? "
            "AND persona IN ('BUYER','CHAMPION','PAIN_OWNER','ENTRY_POINT') "
            "ORDER BY persona_weight DESC LIMIT ?",
            (body.conference_id, body.top_n),
        ).fetchall()
    finally:
        conn.close()

    if not targets:
        return {"prepared": 0, "briefs": []}

    out: list[dict] = []
    for t in targets:
        if not t["company_name"]:
            continue
        try:
            res = brief_mod.generate(
                name=t["full_name"], company=t["company_name"],
                title=t["title"], vertical=t["vertical"],
                conference_id=body.conference_id, person_id=t["id"],
                use_web_search=True, persist=True,
            )
            out.append({
                "person_id": t["id"], "full_name": t["full_name"],
                "title": t["title"], "company": t["company_name"],
                "persona": t["persona"], "brief_id": res["brief_id"],
                "fx_angle": res["brief_json"].get("fx_angle"),
                "trigger_news_count": len(res["brief_json"].get("trigger_news") or []),
            })
        except Exception as exc:  # noqa: BLE001
            out.append({
                "person_id": t["id"], "full_name": t["full_name"],
                "error": str(exc)[:160],
            })

    return {"prepared": len(out), "briefs": out}


@router.get("/by-person/{person_id}")
def briefs_by_person(person_id: str) -> dict:
    """List cached briefs for a given person (used to show prep state)."""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, conference_id, brief_text, brief_json, generated_at "
            "FROM briefs WHERE person_id = ? ORDER BY generated_at DESC",
            (person_id,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["brief_json"] = json.loads(d.get("brief_json") or "{}")
        out.append(d)
    return {"items": out}


@router.get("/{brief_id}")
def get_brief(brief_id: str) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "brief not found")
    d = dict(row)
    d["brief_json"] = json.loads(d.get("brief_json") or "{}")
    return d


class BriefRating(BaseModel):
    rating: int
    reason: Optional[str] = None
    decided_by: Optional[str] = "ui"


@router.post("/{brief_id}/rate")
def rate_brief(brief_id: str, body: BriefRating) -> dict:
    if not (1 <= body.rating <= 5):
        raise HTTPException(400, "rating must be 1..5")
    db.log_feedback(
        decision_kind="brief_rate", target_kind="brief", target_id=brief_id,
        after={"rating": body.rating}, reason=body.reason,
        decided_by=body.decided_by,
    )
    return {"status": "rated"}
