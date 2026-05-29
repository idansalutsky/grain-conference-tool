"""Telegram bot — the field capture path.

Flow:
  1. Rep clicks "Connect Telegram" button on the web UI.
  2. UI calls POST /api/telegram/issue-token → returns deep link.
  3. Rep clicks the deep link on their phone — Telegram opens to /start <token>.
  4. /start binds telegram_user_id → rep row. From now on every voice memo
     or text the rep sends the bot creates an encounter attributed to them.

DRY_RUN_TELEGRAM is implicit: when TELEGRAM_BOT_TOKEN is not set, send_message
and download_voice are no-ops. The web UI's "type a quick note" form is the
fallback capture path that always works.
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx

from . import config, db, voice

log = logging.getLogger("grain.telegram")

TELEGRAM_API = "https://api.telegram.org"

# A message that is *just* a LinkedIn profile URL → capture as an identity.
_LINKEDIN_ONLY_RE = re.compile(
    r"^\s*(https?://)?([a-z0-9-]+\.)?linkedin\.com/in/[^\s]+\s*$", re.IGNORECASE
)

# The path the FastAPI webhook route is mounted at. Telegram POSTs updates here.
WEBHOOK_PATH = "/api/telegram/webhook"
_WEBHOOK_SECRET_KEY = "telegram.webhook_secret"


# ---------------------------------------------------------------------------
# Token / binding
# ---------------------------------------------------------------------------
def issue_link_token(rep_id: str, conference_id: Optional[str] = None) -> str:
    """Generate a one-time UUID token; persist on the rep row.

    If `conference_id` is provided, the rep's `active_conference_id` will be
    set to that value when /start is redeemed. This lets the "Connect Telegram
    for THIS event" button bind a rep to a specific event in one round-trip
    — no /event command, no dropdown.
    """
    token = uuid.uuid4().hex
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM reps WHERE id = ?", (rep_id,)).fetchone()
        if not row:
            raise ValueError(f"rep {rep_id} not found")
        conn.execute(
            "UPDATE reps SET telegram_link_token = ?, "
            "telegram_link_token_event_id = ? WHERE id = ?",
            (token, conference_id, rep_id),
        )
    finally:
        conn.close()
    return token


def deep_link(token: str) -> str:
    bot = (config.TELEGRAM_BOT_USERNAME or "your_bot").lstrip("@")
    return f"https://t.me/{bot}?start={token}"


def _bind_rep(rep_id: str, telegram_user_id: int) -> Optional[str]:
    """Bind rep to telegram_user_id. If the pending issue-token carried a
    conference_id, promote it to `active_conference_id` so subsequent
    captures auto-attribute to that event.

    Returns the conference_id the rep is now bound to (or None).
    """
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT telegram_link_token_event_id FROM reps WHERE id = ?",
            (rep_id,),
        ).fetchone()
        conf_id = row["telegram_link_token_event_id"] if row else None
        conn.execute(
            "UPDATE reps SET telegram_user_id = ?, telegram_link_token = NULL, "
            "telegram_link_token_event_id = NULL, "
            "active_conference_id = COALESCE(?, active_conference_id) "
            "WHERE id = ?",
            (telegram_user_id, conf_id, rep_id),
        )
        return conf_id
    finally:
        conn.close()


def set_active_conference(rep_id: str, conference_id: Optional[str]) -> None:
    """Manually set/clear a rep's active conference (used by web UI button or
    by an optional `/event` chat command)."""
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE reps SET active_conference_id = ? WHERE id = ?",
            (conference_id, rep_id),
        )
    finally:
        conn.close()


def _rep_by_token(token: str) -> Optional[dict]:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM reps WHERE telegram_link_token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _rep_by_telegram_id(telegram_user_id: int) -> Optional[dict]:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM reps WHERE telegram_user_id = ?", (telegram_user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------
def send_message(chat_id: int, text: str, *, parse_mode: Optional[str] = "Markdown") -> dict:
    if not config.TELEGRAM_BOT_TOKEN:
        log.info("[Telegram DRY] sendMessage chat=%s text=%s", chat_id, text[:200])
        return {"ok": True, "dry_run": True}
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(
                f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json=payload,
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    if r.status_code >= 400:
        return {"ok": False, "error": r.text[:300]}
    return r.json()


def download_file(file_id: str, *, fallback_ext: str = ".bin") -> Optional[Path]:
    """Download any Telegram file by id. Keeps the server-side extension when
    Telegram provides one (so audio/image format is honest downstream)."""
    if not config.TELEGRAM_BOT_TOKEN:
        return None
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(
                f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/getFile",
                params={"file_id": file_id},
            )
            if r.status_code >= 400:
                return None
            file_path = (r.json().get("result") or {}).get("file_path")
            if not file_path:
                return None
            ext = Path(file_path).suffix or fallback_ext
            r2 = client.get(
                f"{TELEGRAM_API}/file/bot{config.TELEGRAM_BOT_TOKEN}/{file_path}"
            )
            if r2.status_code >= 400:
                return None
            local = config.AUDIO_DIR / f"telegram_{file_id[:12]}{ext}"
            local.write_bytes(r2.content)
            return local
    except httpx.HTTPError as exc:
        log.warning("download_file failed: %s", exc)
        return None


def download_voice(file_id: str) -> Optional[Path]:
    return download_file(file_id, fallback_ext=".ogg")


# ---------------------------------------------------------------------------
# Webhook registration — one-call setup for deploy
# ---------------------------------------------------------------------------
def _rotate_webhook_secret() -> str:
    """Generate + persist a FRESH secret token Telegram echoes back on every
    update so we can reject spoofed POSTs. Rotated on each set_webhook call so
    any previously-leaked secret is immediately invalidated."""
    secret = uuid.uuid4().hex
    db.set_setting(_WEBHOOK_SECRET_KEY, secret)
    return secret


def webhook_secret() -> Optional[str]:
    """The stored webhook secret (None until set_webhook has run)."""
    return db.get_setting(_WEBHOOK_SECRET_KEY)


def _validate_base_url(base_url: str) -> tuple[bool, str]:
    """The webhook target must be a bare public https origin (scheme+host,
    no path/userinfo/query). If PUBLIC_BASE_URL is configured, it must match
    exactly — an allowlist so the bot can't be repointed at an arbitrary host.
    """
    from urllib.parse import urlparse
    if not base_url or not isinstance(base_url, str):
        return False, "base_url required"
    p = urlparse(base_url.rstrip("/"))
    if p.scheme != "https" or not p.netloc:
        return False, "base_url must be a public https:// origin"
    if p.username or p.password:
        return False, "base_url must not contain credentials"
    if p.path not in ("", "/") or p.query or p.fragment:
        return False, "base_url must be a bare origin (no path/query)"
    allow = config.PUBLIC_BASE_URL
    if allow and base_url.rstrip("/") != allow.rstrip("/"):
        return False, f"base_url not in allowlist (expected {allow})"
    return True, ""


def set_webhook(base_url: str) -> dict:
    """Register this server's public webhook with Telegram in one call.

    `base_url` is the public origin of the deployed API
    (e.g. https://grain-api.onrender.com). We append WEBHOOK_PATH and pass a
    persisted `secret_token`; Telegram returns it in the
    `X-Telegram-Bot-Api-Secret-Token` header on every update, which the
    webhook route verifies.
    """
    if not config.TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set — add it in "
                "Settings → API keys first."}
    ok, why = _validate_base_url(base_url)
    if not ok:
        return {"ok": False, "error": why}
    url = base_url.rstrip("/") + WEBHOOK_PATH
    secret = _rotate_webhook_secret()
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(
                f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/setWebhook",
                json={
                    "url": url,
                    "secret_token": secret,
                    "allowed_updates": ["message", "edited_message"],
                    "drop_pending_updates": True,
                },
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)[:200], "webhook_url": url}
    if r.status_code >= 400:
        return {"ok": False, "error": r.text[:300], "webhook_url": url}
    body = r.json()
    return {"ok": bool(body.get("ok")), "webhook_url": url,
            "description": body.get("description")}


def delete_webhook() -> dict:
    """Unregister the webhook (e.g. to switch back to local polling)."""
    if not config.TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set"}
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(
                f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/deleteWebhook",
                json={"drop_pending_updates": False},
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return r.json() if r.status_code < 400 else {"ok": False, "error": r.text[:300]}


def get_webhook_info() -> dict:
    """Ask Telegram what webhook (if any) is currently registered + its health
    (pending update count, last error). Useful for the deploy-time check."""
    if not config.TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set"}
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(
                f"{TELEGRAM_API}/bot{config.TELEGRAM_BOT_TOKEN}/getWebhookInfo"
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return r.json() if r.status_code < 400 else {"ok": False, "error": r.text[:300]}


# ---------------------------------------------------------------------------
# Webhook handler
# ---------------------------------------------------------------------------
def handle_update(update: dict) -> dict:
    """Process one Telegram webhook update. Returns a summary."""
    msg = update.get("message") or update.get("edited_message") or {}
    if not msg:
        return {"action": "ignored", "reason": "no message in update"}

    from_user = msg.get("from") or {}
    tg_user_id = from_user.get("id")
    chat_id = (msg.get("chat") or {}).get("id") or tg_user_id
    text = (msg.get("text") or "").strip()

    # /start <token> — bind (and optionally bind to an event)
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        token = parts[1].strip() if len(parts) > 1 else ""
        if not token:
            send_message(chat_id, "Please use the connect link from the dashboard.")
            return {"action": "start_no_token"}
        rep = _rep_by_token(token)
        if not rep:
            send_message(chat_id, "Invalid or expired link.")
            return {"action": "start_invalid_token"}
        bound_conf = _bind_rep(rep["id"], tg_user_id)
        if bound_conf:
            conn = db.get_conn()
            try:
                conf_row = conn.execute(
                    "SELECT name FROM conferences WHERE id = ?", (bound_conf,),
                ).fetchone()
                conf_name = conf_row["name"] if conf_row else bound_conf
            finally:
                conn.close()
            reply = (
                f"Linked! You're now bound to *{rep['full_name']}*.\n"
                f"Active event: *{conf_name}*\n\n"
                "Send a voice memo, a text, a *photo of their badge*, or just a "
                "*LinkedIn link* — every capture auto-tags to this event."
            )
        else:
            reply = (
                f"Linked! You're now bound to *{rep['full_name']}*.\n\n"
                "Send a voice memo, text, badge photo, or a LinkedIn link — I'll "
                "log it and reply with intel on who you just met."
            )
        send_message(chat_id, reply)
        return {"action": "rep_bound", "rep_id": rep["id"],
                "active_conference_id": bound_conf}

    # All other messages require a bound rep
    rep = _rep_by_telegram_id(tg_user_id)
    if not rep:
        send_message(chat_id,
                     "I don't recognise you yet. Open the dashboard and click "
                     "'Connect Telegram' to get a link.")
        return {"action": "unbound_user"}

    # Pick up the rep's active conference if any — this is the per-event
    # binding that means "every capture auto-tags to Money20/20".
    active_conf = rep.get("active_conference_id")

    # Voice memo
    voice_obj = msg.get("voice")
    if voice_obj:
        file_id = voice_obj.get("file_id")
        local = download_voice(file_id) if file_id else None
        if local is None:
            send_message(chat_id, "Couldn't download that voice memo — try again or send text.")
            return {"action": "voice_download_failed"}
        try:
            result = voice.capture_voice(
                audio_path=local, rep_id=rep["id"], capture_mode="telegram",
                conference_id=active_conf,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("voice capture failed: %s", exc)
            send_message(chat_id, "Something broke processing your voice memo.")
            return {"action": "voice_capture_failed", "error": str(exc)[:200]}
        send_message(chat_id, _intel_reply(result))
        return {"action": "voice_encounter", "encounter_id": result["encounter_id"]}

    # Badge / business-card photo — Telegram sends an array of sizes; the last
    # is the largest (best for OCR). A caption, if present, is kept as context.
    photos = msg.get("photo") or []
    if photos:
        file_id = (photos[-1] or {}).get("file_id")
        local = download_file(file_id, fallback_ext=".jpg") if file_id else None
        if local is None:
            send_message(chat_id, "Couldn't download that photo — try again or type the name.")
            return {"action": "photo_download_failed"}
        try:
            result = voice.capture_image(
                image_path=local, rep_id=rep["id"], capture_mode="telegram_badge",
                conference_id=active_conf,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("badge capture failed: %s", exc)
            send_message(chat_id, "Something broke reading that badge.")
            return {"action": "badge_capture_failed", "error": str(exc)[:200]}
        if not result.get("ok", True):
            send_message(chat_id, "📷 " + result.get("reason", "Couldn't read the badge — retry."))
            return {"action": "badge_unreadable"}
        send_message(chat_id, _intel_reply(result))
        return {"action": "badge_encounter", "encounter_id": result["encounter_id"]}

    # A bare LinkedIn URL → capture as an identity (strong match key).
    if text and _LINKEDIN_ONLY_RE.match(text):
        try:
            result = voice.capture_linkedin(
                url=text.strip(), rep_id=rep["id"], capture_mode="telegram_linkedin",
                conference_id=active_conf,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("linkedin capture failed: %s", exc)
            send_message(chat_id, "Something broke processing that LinkedIn link.")
            return {"action": "linkedin_capture_failed", "error": str(exc)[:200]}
        if not result.get("ok", True):
            send_message(chat_id, result.get("reason", "Couldn't use that link."))
            return {"action": "linkedin_unusable"}
        send_message(chat_id, _intel_reply(result))
        return {"action": "linkedin_encounter", "encounter_id": result["encounter_id"]}

    # Plain text
    if text:
        try:
            result = voice.capture_text(
                text=text, rep_id=rep["id"], capture_mode="telegram",
                conference_id=active_conf,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("text capture failed: %s", exc)
            send_message(chat_id, "Something broke processing that.")
            return {"action": "text_capture_failed", "error": str(exc)[:200]}
        send_message(chat_id, _intel_reply(result))
        return {"action": "text_encounter", "encounter_id": result["encounter_id"]}

    return {"action": "ignored", "reason": "no voice/text payload"}


def _intel_reply(result: dict) -> str:
    """Format the bot reply: what we logged + the arc verdict + nudge state."""
    struct = result.get("structured") or {}
    name = struct.get("name") or "?"
    company = struct.get("company") or "?"
    title = struct.get("title") or ""
    lines = [f"✅ Logged: *{name}* @ {company}"]
    if title:
        lines.append(f"_({title})_")
    arc = result.get("arc") or {}
    if arc.get("kind"):
        emoji = {"warming": "📈", "flat": "▫️", "cooling": "📉",
                 "tire_kicker": "⚠️"}.get(arc["kind"], "")
        lines.append(f"\n{emoji} Arc: *{arc['kind']}* — {arc.get('summary','')}")
    nudge = result.get("nudge") or {}
    if nudge.get("nudge_active"):
        lines.append(f"\n💡 Nudge: {nudge.get('nudge_text', '')}")
    elif nudge.get("why_suppressed"):
        # Don't spam the rep with suppression reasons — only show if this is
        # a long-running silent contact.
        pass
    return "\n".join(lines)
