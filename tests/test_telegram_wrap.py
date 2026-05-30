"""End-of-event wrap-up flow in the Telegram bot.

Covers: /wrap (and bare "done"/"wrap up" phrases) producing a digest of the
rep's captures + recommended follow-up drafts + event-scoped nudges, while a
normal capture message is NOT hijacked. send_message and the follow-up drafter
are mocked so the suite runs hermetically (no network / LLM)."""
from __future__ import annotations

from unittest.mock import patch

from grain import db
from grain import telegram as tg

# Unique IDs — the session DB is shared across test files, and
# test_telegram_matrix.py reuses 880022 with active_conference_id=NULL, which
# would otherwise collide here (two reps sharing one telegram_user_id).
TG_ID = 884417
REP_ID = "rep-tg-wrap"
CONF_ID = "conf-wrap"


def _seed_conf():
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO conferences (id, name, created_at, updated_at) "
            "VALUES (?,?,?,?)",
            (CONF_ID, "Money20/20", db.now_iso(), db.now_iso()),
        )
    finally:
        conn.close()


def _bound_rep(active_conf=None):
    _seed_conf()
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO reps (id, full_name, region, telegram_user_id, "
            "created_at) VALUES (?,?,?,?,?)",
            (REP_ID, "Wrap Rep", "EU", TG_ID, db.now_iso()),
        )
        conn.execute(
            "UPDATE reps SET telegram_user_id = ?, active_conference_id = ? WHERE id = ?",
            (TG_ID, active_conf, REP_ID),
        )
    finally:
        conn.close()


def _update(**message):
    return {"update_id": 1, "message": {"from": {"id": TG_ID},
                                        "chat": {"id": TG_ID}, **message}}


# The digest the follow-up drafter would return — one recommended contact and
# one tire-kicker (filtered out of the inlined drafts).
_DIGEST = {
    "ok": True, "conference_id": CONF_ID, "event_name": "Money20/20",
    "count": 2, "recommended_count": 1,
    "drafts": [
        {"ok": True, "recommended": True, "name": "Jane Rivera",
         "company": "Stripe", "subject": "Following up from Money20/20",
         "body": "Hi Jane, great connecting...", "contact_id": "c-jane"},
        {"ok": True, "recommended": False, "name": "Tom Kicker",
         "company": "Nowhere", "subject": "Hi", "body": "...",
         "contact_id": "c-tom"},
    ],
}


def test_wrap_command_returns_digest_with_event_and_recommended_contact():
    _bound_rep(active_conf=CONF_ID)
    sent = []
    with patch("grain.telegram.send_message", side_effect=lambda cid, txt, **k: sent.append(txt)), \
         patch("grain.telegram.voice.close_capture_session", return_value={"ok": True}) as close, \
         patch("grain.telegram.followup.draft_for_event", return_value=_DIGEST), \
         patch("grain.telegram._event_active_nudges", return_value=[]):
        out = tg.handle_update(_update(text="/wrap"))
    assert out["action"] == "wrap"
    assert out["conference_id"] == CONF_ID
    close.assert_called_once_with(REP_ID)
    body = "\n".join(sent)
    assert "Money20/20" in body
    assert "Jane Rivera" in body          # recommended contact is inlined
    assert "Tom Kicker" not in body       # tire-kicker is not


def test_bare_done_routes_to_wrap_not_capture():
    _bound_rep(active_conf=CONF_ID)
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.close_capture_session", return_value={"ok": True}), \
         patch("grain.telegram.followup.draft_for_event", return_value=_DIGEST), \
         patch("grain.telegram._event_active_nudges", return_value=[]), \
         patch("grain.telegram.voice.capture_text") as cap:
        out = tg.handle_update(_update(text="done"))
    assert out["action"] == "wrap"
    cap.assert_not_called()               # NOT logged as a captured lead


def test_wrap_up_phrase_routes_to_wrap():
    _bound_rep(active_conf=CONF_ID)
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.close_capture_session", return_value={"ok": True}), \
         patch("grain.telegram.followup.draft_for_event", return_value=_DIGEST), \
         patch("grain.telegram._event_active_nudges", return_value=[]), \
         patch("grain.telegram.voice.capture_text") as cap:
        out = tg.handle_update(_update(text="Wrap Up"))   # case-insensitive
    assert out["action"] == "wrap"
    cap.assert_not_called()


def test_normal_capture_is_not_hijacked():
    _bound_rep(active_conf=CONF_ID)
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.followup.draft_for_event") as draft, \
         patch("grain.telegram.voice.capture_text", return_value={
             "ok": True, "encounter_id": "e_text", "structured": {"name": "Jane"},
             "contact_id": "c", "arc": None, "nudge": None}) as cap:
        out = tg.handle_update(_update(text="Met Jane at Stripe"))
    assert out["action"] == "text_encounter"
    cap.assert_called_once()
    draft.assert_not_called()             # wrap path never engaged


def test_wrap_with_no_active_event_is_friendly():
    _bound_rep(active_conf=None)
    sent = []
    with patch("grain.telegram.send_message", side_effect=lambda cid, txt, **k: sent.append(txt)), \
         patch("grain.telegram.followup.draft_for_event") as draft:
        out = tg.handle_update(_update(text="/wrap"))
    assert out["action"] == "wrap_no_event"
    draft.assert_not_called()
    assert "No active event set" in "\n".join(sent)


def test_event_scoped_nudges_only_include_this_events_contacts():
    """The nudge query intersects nudge_active contacts with encounters at the
    active event — a nudge-active contact met at a DIFFERENT event is excluded."""
    _seed_conf()
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO conferences (id, name, created_at, updated_at) "
            "VALUES (?,?,?,?)",
            ("conf-other", "Other Event", db.now_iso(), db.now_iso()),
        )
        # Contact met AT our event, nudge active → should appear.
        conn.execute(
            "INSERT OR REPLACE INTO contacts (id, primary_name, primary_company, "
            "nudge_active, nudge_text, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("c-here", "Here Person", "AcmeCo", 1, "Ping them about FX",
             db.now_iso(), db.now_iso()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO encounters (id, contact_id, conference_id, "
            "rep_id, captured_at, capture_mode) VALUES (?,?,?,?,?,?)",
            ("e-here", "c-here", CONF_ID, REP_ID, db.now_iso(), "telegram"),
        )
        # Contact met at a DIFFERENT event, nudge active → should NOT appear.
        conn.execute(
            "INSERT OR REPLACE INTO contacts (id, primary_name, primary_company, "
            "nudge_active, nudge_text, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("c-there", "There Person", "OtherCo", 1, "Different nudge",
             db.now_iso(), db.now_iso()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO encounters (id, contact_id, conference_id, "
            "rep_id, captured_at, capture_mode) VALUES (?,?,?,?,?,?)",
            ("e-there", "c-there", "conf-other", REP_ID, db.now_iso(), "telegram"),
        )
    finally:
        conn.close()
    nudges = tg._event_active_nudges(CONF_ID)
    names = {n["primary_name"] for n in nudges}
    assert "Here Person" in names
    assert "There Person" not in names
