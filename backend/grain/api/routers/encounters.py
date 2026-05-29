"""/api/encounters — voice + text capture + list + cascade re-run."""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ... import config, db, voice

router = APIRouter(prefix="/api/encounters", tags=["encounters"])

# A message that is *just* a LinkedIn profile URL (optionally wrapped in
# whitespace) → route to the LinkedIn capture path rather than text extraction.
_LINKEDIN_ONLY_RE = re.compile(
    r"^\s*(https?://)?([a-z0-9-]+\.)?linkedin\.com/in/[^\s]+\s*$", re.IGNORECASE
)

# Image content-types we accept for badge capture.
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic"}


class TextCapture(BaseModel):
    text: str
    rep_id: Optional[str] = None
    conference_id: Optional[str] = None


@router.get("")
def list_encounters(
    contact_id: Optional[str] = None,
    conference_id: Optional[str] = None,
    limit: int = 100,
) -> dict:
    where, params = [], []
    if contact_id:
        where.append("contact_id = ?"); params.append(contact_id)
    if conference_id:
        where.append("conference_id = ?"); params.append(conference_id)
    sql = "SELECT * FROM encounters"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY captured_at DESC LIMIT ?"
    params.append(limit)
    conn = db.get_conn()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["structured"] = json.loads(d.pop("structured_json") or "{}")
        d["soft_signals"] = json.loads(d.pop("soft_signals_json") or "[]")
        out.append(d)
    return {"count": len(out), "items": out}


@router.post("/text", status_code=201)
def capture_text(body: TextCapture, background_tasks: BackgroundTasks) -> dict:
    """Fast path: LLM extract + persist + entity resolve. ~2s.

    Arc + nudge cascade kicks off as a background task and updates the
    contact row. The frontend can poll `GET /api/contacts/{id}` after a few
    seconds to see the arc verdict + nudge state.
    """
    if not body.text.strip():
        raise HTTPException(400, "text is required")
    # If the rep just pasted a LinkedIn URL, capture it as an identity (the URL
    # is a strong entity-resolution key) instead of trying to extract prose.
    if _LINKEDIN_ONLY_RE.match(body.text):
        result = voice.capture_linkedin_fast(
            url=body.text.strip(), rep_id=body.rep_id,
            conference_id=body.conference_id, capture_mode="web_linkedin",
        )
    else:
        result = voice.capture_text_fast(
            text=body.text, rep_id=body.rep_id,
            conference_id=body.conference_id, capture_mode="web_text",
        )
    if result.get("contact_id") and result.get("cascade_status") == "pending":
        background_tasks.add_task(voice.run_cascade_in_background, result["contact_id"])
    return result


@router.post("/voice", status_code=201)
async def capture_voice(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    rep_id: Optional[str] = Form(None),
    conference_id: Optional[str] = Form(None),
) -> dict:
    """Fast path for voice: upload + LLM extract + entity resolve. ~3-4s.

    Arc + nudge run in the background. The response carries the structured
    lead immediately so the rep on the floor can move on.
    """
    if not audio.filename:
        raise HTTPException(400, "audio file required")
    suffix = Path(audio.filename).suffix.lower() or ".webm"
    local = config.AUDIO_DIR / f"web_{uuid.uuid4().hex[:12]}{suffix}"
    with local.open("wb") as f:
        shutil.copyfileobj(audio.file, f)
    result = voice.capture_voice_fast(
        audio_path=local, rep_id=rep_id,
        conference_id=conference_id, capture_mode="web_voice",
    )
    if result.get("contact_id") and result.get("cascade_status") == "pending":
        background_tasks.add_task(voice.run_cascade_in_background, result["contact_id"])
    return result


@router.post("/image", status_code=201)
async def capture_image(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    rep_id: Optional[str] = Form(None),
    conference_id: Optional[str] = Form(None),
) -> dict:
    """Badge / business-card photo → OCR → structured lead → resolve. ~3-4s.

    Returns ok:false (no contact created) when OCR can't read a name, so the
    rep can retry instead of getting a junk contact. Arc + nudge run in the
    background as with the other capture paths.
    """
    if not image.filename:
        raise HTTPException(400, "image file required")
    suffix = Path(image.filename).suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        raise HTTPException(400, f"unsupported image type {suffix or '?'}")
    local = config.AUDIO_DIR / f"badge_{uuid.uuid4().hex[:12]}{suffix}"
    with local.open("wb") as f:
        shutil.copyfileobj(image.file, f)
    result = voice.capture_image_fast(
        image_path=local, rep_id=rep_id,
        conference_id=conference_id, capture_mode="badge_photo",
    )
    if result.get("contact_id") and result.get("cascade_status") == "pending":
        background_tasks.add_task(voice.run_cascade_in_background, result["contact_id"])
    return result


@router.post("/cascade/{contact_id}")
def trigger_cascade(contact_id: str) -> dict:
    """Manually re-run arc + nudge. Used when the rep wants the latest
    verdict on demand (e.g. after overriding the arc)."""
    return voice.run_cascade_in_background(contact_id)
