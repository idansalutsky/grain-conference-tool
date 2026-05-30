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

from . import config, db, followup, voice

log = logging.getLogger("grain.telegram")

TELEGRAM_API = "https://api.telegram.org"

# A message that is *just* a LinkedIn profile URL → capture as an identity.
_LINKEDIN_ONLY_RE = re.compile(
    r"^\s*(https?://)?([a-z0-9-]+\.)?linkedin\.com/in/[^\s]+\s*$", re.IGNORECASE
)

# The path the FastAPI webhook route is mounted at. Telegram POSTs updates here.
WEBHOOK_PATH = "/api/telegram/webhook"
_WEBHOOK_SECRET_KEY = "telegram.webhook_secret"

# Slash commands that trigger the end-of-event wrap-up digest.
WRAP_COMMANDS = ("wrap", "summary", "recap")

# Bare-text phrases (no leading slash) that mean "I'm done at this event — wrap
# it up", routed to the wrap handler INSTEAD of being logged as a captured lead.
# Kept deliberately TIGHT (exact match on the whole trimmed message, case-
# insensitive) so it never hijacks a real capture like "done deal with Maria"
# or "we finished negotiating terms".
WRAP_PHRASES = frozenset({
    "done", "wrap", "wrap up", "wrap it up", "that's a wrap", "thats a wrap",
    "finished", "end of day", "end of event",
})


def _is_wrap_phrase(text: str) -> bool:
    """True only when the WHOLE trimmed message is one of the wrap phrases."""
    return text.strip().lower() in WRAP_PHRASES


# ---------------------------------------------------------------------------
# Token / binding
# ---------------------------------------------------------------------------
def issue_link_token(rep_id: str, conference_id: Optional[str] = None) -> str:
    """Issue a NEW one-time bind token (one row in telegram_link_tokens).

    A rep can hold many live tokens at once — one per event they cover — so
    per-event connect links coexist instead of clobbering a single slot. When a
    token carries a `conference_id`, redeeming it (/start) sets that event as the
    rep's active event, so every capture auto-tags to the right conference.
    """
    token = uuid.uuid4().hex
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM reps WHERE id = ?", (rep_id,)).fetchone()
        if not row:
            raise ValueError(f"rep {rep_id} not found")
        conn.execute(
            "INSERT INTO telegram_link_tokens (token, rep_id, conference_id, "
            "created_at) VALUES (?,?,?,?)",
            (token, rep_id, conference_id, db.now_iso()),
        )
    finally:
        conn.close()
    return token


def deep_link(token: str) -> str:
    bot = (config.TELEGRAM_BOT_USERNAME or "your_bot").lstrip("@")
    return f"https://t.me/{bot}?start={token}"


def _bind_rep(rep_id: str, telegram_user_id: int,
              conference_id: Optional[str] = None) -> Optional[str]:
    """Bind a rep's Telegram and (if the redeemed token carried an event) set it
    as the rep's active event so captures auto-attribute. A Telegram user maps to
    exactly one rep, so we also clear this telegram_user_id off any OTHER rep
    (a phone re-bound to a different rep detaches from the old one).

    Returns the conference_id the rep is now active on (or None).
    """
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE reps SET telegram_user_id = NULL WHERE telegram_user_id = ? "
            "AND id != ?",
            (telegram_user_id, rep_id),
        )
        conn.execute(
            "UPDATE reps SET telegram_user_id = ?, "
            "active_conference_id = COALESCE(?, active_conference_id) WHERE id = ?",
            (telegram_user_id, conference_id, rep_id),
        )
        return conference_id
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


def _redeem_token(token: str) -> Optional[dict]:
    """Resolve a (still-unredeemed) bind token to its rep + event, mark it
    redeemed, and return {rep: <rep row>, conference_id}. None if invalid/used."""
    conn = db.get_conn()
    try:
        tok = conn.execute(
            "SELECT rep_id, conference_id FROM telegram_link_tokens "
            "WHERE token = ? AND redeemed_at IS NULL",
            (token,),
        ).fetchone()
        if not tok:
            return None
        rep = conn.execute(
            "SELECT * FROM reps WHERE id = ?", (tok["rep_id"],)
        ).fetchone()
        if not rep:
            return None
        conn.execute(
            "UPDATE telegram_link_tokens SET redeemed_at = ? WHERE token = ?",
            (db.now_iso(), token),
        )
        return {"rep": dict(rep), "conference_id": tok["conference_id"]}
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
# End-of-event "wrap up"
# ---------------------------------------------------------------------------
# Telegram hard-caps a message at ~4096 chars; keep a safety margin and cap the
# number of follow-up drafts we inline so the digest never gets rejected.
_WRAP_CHAR_BUDGET = 3800
_WRAP_MAX_DRAFTS = 6


def _event_active_nudges(conference_id: str, limit: int = 6) -> list[dict]:
    """Active warming nudges, SCOPED to contacts the rep actually has an
    encounter with at this event. Mirrors today.py `_active_nudges` but
    intersects on encounters.conference_id so the wrap only surfaces nudges
    for people captured here. Read-only.
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT c.id, c.primary_name, c.primary_company, "
            "c.nudge_text, c.updated_at "
            "FROM contacts c "
            "JOIN encounters e ON e.contact_id = c.id "
            "WHERE c.nudge_active = 1 AND e.conference_id = ? "
            "ORDER BY c.updated_at DESC LIMIT ?",
            (conference_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _event_missing_contact_info(conference_id: str, limit: int = 8) -> list[dict]:
    """Contacts captured at this event with NO way to reach them — no email, no
    phone, no LinkedIn. These are leads the rep should chase for details before
    leaving the floor, or they're effectively lost. Read-only."""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT c.id, c.primary_name, c.primary_company "
            "FROM contacts c JOIN encounters e ON e.contact_id = c.id "
            "WHERE e.conference_id = ? "
            "AND COALESCE(c.primary_email,'') = '' "
            "AND COALESCE(c.phone,'') = '' "
            "AND COALESCE(c.linkedin_handle,'') = '' "
            "ORDER BY c.updated_at DESC LIMIT ?",
            (conference_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _format_wrap(digest: dict, nudges: list[dict],
                 missing: list[dict] | None = None,
                 agent: dict | None = None) -> str:
    """Build the Telegram wrap message: an (optional) agent-reasoned recap, then
    recommended follow-up drafts + nudges + missing-info. Caps drafts to stay
    under Telegram's limit."""
    event_name = digest.get("event_name") or "this event"
    count = digest.get("count", 0)
    rec_count = digest.get("recommended_count", 0)
    lines = [
        f"📋 *Wrap-up — {event_name}*",
        f"{count} captured · {rec_count} worth a follow-up",
    ]

    # Agent-reasoned recap (the "thinking" layer) when available.
    if agent:
        if agent.get("summary"):
            lines.append(f"\n🧠 {agent['summary'].strip()}")
        if agent.get("urgent"):
            lines.append("⚡ *Urgent:* " + ", ".join(agent["urgent"][:6]))
        if agent.get("account_plays"):
            lines.append("🏢 *Account plays:* " + ", ".join(agent["account_plays"][:5]))

    recommended = [d for d in digest.get("drafts", []) if d.get("recommended")]
    shown = recommended[:_WRAP_MAX_DRAFTS]
    if shown:
        lines.append("\n*Ready-to-send drafts:*")
    for d in shown:
        name = d.get("name") or "?"
        company = d.get("company") or "?"
        subject = (d.get("subject") or "").strip()
        body = (d.get("body") or "").strip()
        block = f"\n*{name}* @ {company}"
        if subject:
            block += f"\n{subject}"
        if body:
            block += f"\n{body}"
        lines.append(block)

    text = "\n".join(lines)
    # Truncate by dropping trailing drafts if we blew the budget.
    while len(text) > _WRAP_CHAR_BUDGET and len(shown) > 1:
        shown = shown[:-1]
        kept = lines[: 3 + len(shown)] if any("Ready-to-send" in l for l in lines) else lines
        text = "\n".join(kept)
    truncated = len(shown) < len(recommended)
    if truncated:
        text += f"\n\n_+{len(recommended) - len(shown)} more drafts in the dashboard._"

    if nudges:
        nudge_lines = ["\n*Nudges:*"]
        for n in nudges:
            nm = n.get("primary_name") or "?"
            nt = (n.get("nudge_text") or "").strip()
            nudge_lines.append(f"💡 *{nm}* — {nt}" if nt else f"💡 *{nm}*")
        text += "\n" + "\n".join(nudge_lines)

    if missing:
        miss_lines = ["\n📇 *Grab contact details before you leave* "
                      "(no email / phone / LinkedIn yet):"]
        for m in missing:
            nm = m.get("primary_name") or "?"
            co = m.get("primary_company") or "?"
            miss_lines.append(f"• {nm} @ {co}")
        text += "\n" + "\n".join(miss_lines)

    # Hard safety cap — Telegram rejects messages over 4096 chars. The draft
    # budget above trims drafts, but the nudge + missing sections are appended
    # after, so guarantee the whole message fits regardless of section sizes.
    if len(text) > 4000:
        text = text[:3960].rstrip() + "\n\n…(full details in the dashboard)"
    return text


def _wrap_event(rep: dict, chat_id: int) -> dict:
    """Handle an end-of-event wrap request: close the open capture session,
    draft follow-ups for the rep's active event, gather event-scoped nudges,
    and reply with a single digest message."""
    active_conf = rep.get("active_conference_id")
    if not active_conf:
        send_message(
            chat_id,
            "No active event set — bind one from an event's Coverage panel first.",
        )
        return {"action": "wrap_no_event"}

    # Wrapping the event means this batch of captures is closed out — reset the
    # session so any future capture starts fresh.
    voice.close_capture_session(rep["id"])

    digest = followup.draft_for_event(active_conf)
    if not digest.get("ok"):
        send_message(chat_id, "Couldn't build the wrap-up for that event.")
        return {"action": "wrap_failed", "error": digest.get("error")}

    nudges = _event_active_nudges(active_conf)
    missing = _event_missing_contact_info(active_conf)
    # The post-event wrap-up AGENT reasons over the captures (urgent, account
    # plays, cross-conference repeats). Best-effort: if it's unavailable (no key)
    # or errors, the deterministic digest below still ships — /wrap never breaks.
    agent = None
    try:
        from . import agent_wrap
        agent = agent_wrap.run_wrap_agent(active_conf)
    except Exception as exc:  # noqa: BLE001
        log.info("wrap agent unavailable, using deterministic digest: %s", exc)
    send_message(chat_id, _format_wrap(digest, nudges, missing, agent))
    return {
        "action": "wrap",
        "conference_id": active_conf,
        "count": digest.get("count", 0),
        "recommended_count": digest.get("recommended_count", 0),
        "nudge_count": len(nudges),
        "missing_contact_info": len(missing),
        "agent_used": bool(agent),
    }


# ---------------------------------------------------------------------------
# Webhook handler
# ---------------------------------------------------------------------------
def handle_update(update: dict) -> dict:
    """Process one Telegram webhook update. Returns a summary.

    This is the public webhook entry point and MUST NOT raise: a thrown
    exception here would 500 the public ``/api/telegram/webhook`` route, and
    Telegram retries 5xx — turning one bad update into a retry storm. Every
    failure (malformed payload, DB hiccup, unexpected message shape) is caught
    and mapped to a structured ``{action: "error"|"ignored", ...}`` summary so
    the webhook always answers 200.
    """
    if not isinstance(update, dict):
        return {"action": "ignored", "reason": "update not a dict"}
    try:
        return _dispatch_update(update)
    except Exception as exc:  # noqa: BLE001 — webhook must never 500
        log.exception("handle_update crashed on update_id=%s: %s",
                      update.get("update_id"), exc)
        return {"action": "error", "error": str(exc)[:200]}


def _dispatch_update(update: dict) -> dict:
    """Route one update to the right capture. Wrapped by ``handle_update``."""
    msg = update.get("message") or update.get("edited_message") or {}
    if not isinstance(msg, dict) or not msg:
        return {"action": "ignored", "reason": "no message in update"}

    from_user = msg.get("from") or {}
    tg_user_id = from_user.get("id")
    chat_id = (msg.get("chat") or {}).get("id") or tg_user_id
    text = (msg.get("text") or "").strip()

    # No identifiable sender (channel post, anonymous admin, service message)
    # — nothing we can attribute a capture to. Ignore quietly.
    if tg_user_id is None:
        return {"action": "ignored", "reason": "no sender id"}

    # /start <token> — bind (and optionally bind to an event)
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        token = parts[1].strip() if len(parts) > 1 else ""
        if not token:
            send_message(chat_id, "Please use the connect link from the dashboard.")
            return {"action": "start_no_token"}
        redeemed = _redeem_token(token)
        if not redeemed:
            send_message(chat_id, "Invalid or already-used link.")
            return {"action": "start_invalid_token"}
        rep = redeemed["rep"]
        bound_conf = _bind_rep(rep["id"], tg_user_id, redeemed["conference_id"])
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

    # In-field commands — correct/clean a capture without leaving the chat.
    if text.startswith("/"):
        cmd, _, rest = text.partition(" ")
        cmd = cmd.lower().lstrip("/")
        rest = rest.strip()
        # End-of-event wrap — MUST come before the session-break branch so
        # /done isn't swallowed as a mere "next person" break. To a
        # salesperson "done" means "I'm done at this event", so /done now
        # routes to the wrap digest; /next remains the session-break command.
        if cmd in WRAP_COMMANDS or cmd == "done":
            return _wrap_event(rep, chat_id)
        if cmd in ("next",):
            voice.close_capture_session(rep["id"])
            send_message(chat_id, "👍 New person — the next capture starts fresh.")
            return {"action": "session_break"}
        if cmd == "undo":
            last = voice.last_encounter_for_rep(rep["id"])
            if not last:
                send_message(chat_id, "Nothing to undo.")
                return {"action": "undo_empty"}
            voice.delete_encounter(last["id"])
            send_message(chat_id, "🗑️ Removed your last capture.")
            return {"action": "undo", "encounter_id": last["id"]}
        if cmd == "fix":
            field, _, value = rest.partition(" ")
            field, value = field.strip().lower(), value.strip()
            if field not in {"name", "company", "title", "email", "phone"} or not value:
                send_message(chat_id, "Usage: /fix <name|company|title|email|phone> <value>")
                return {"action": "fix_usage"}
            last = voice.last_encounter_for_rep(rep["id"])
            if not last:
                send_message(chat_id, "Nothing to fix yet.")
                return {"action": "fix_empty"}
            result = voice.edit_encounter(last["id"], {field: value})
            send_message(chat_id, "✏️ Updated.\n\n" + _intel_reply(result))
            return {"action": "fix", "encounter_id": last["id"], "field": field}
        # Unknown command → fall through (could be /start handled above, or noise)
        send_message(chat_id, "Commands: /wrap (end-of-event recap), /next "
                     "(new person), /undo, /fix <field> <value>.")
        return {"action": "unknown_command", "command": cmd}

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

    # Shared contact card (phone number + name via vCard) — a strong identity
    # key. Stitches into an open session if the rep just snapped this person's
    # badge or sent a voice note.
    contact_obj = msg.get("contact")
    if contact_obj:
        full = " ".join(p for p in [contact_obj.get("first_name"),
                                    contact_obj.get("last_name")] if p).strip()
        try:
            result = voice.capture_contact(
                name=full or None, phone=contact_obj.get("phone_number"),
                rep_id=rep["id"], capture_mode="telegram_contact",
                conference_id=active_conf,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("contact capture failed: %s", exc)
            send_message(chat_id, "Something broke saving that contact.")
            return {"action": "contact_capture_failed", "error": str(exc)[:200]}
        if not result.get("ok", True):
            send_message(chat_id, result.get("reason", "Couldn't use that contact."))
            return {"action": "contact_unusable"}
        send_message(chat_id, _intel_reply(result))
        return {"action": "contact_encounter", "encounter_id": result["encounter_id"]}

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

    # Bare-text end-of-event phrase ("done", "wrap up", …) → wrap digest, NOT a
    # captured lead. Tight exact-match so real captures ("done deal with Maria")
    # still flow through to capture below.
    if text and _is_wrap_phrase(text):
        return _wrap_event(rep, chat_id)

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
    title = struct.get("title") or struct.get("role") or ""
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
    # Must-have nudge: if we have no way to reach this person, say so NOW while
    # they're still in front of the rep — an unreachable lead is a lost lead.
    if not (struct.get("email") or struct.get("phone") or struct.get("linkedin")):
        lines.append("\n📇 No email / phone / LinkedIn yet — grab one before "
                     "they move on.")
    return "\n".join(lines)
