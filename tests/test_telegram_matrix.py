"""Telegram message-type matrix — drives ``handle_update`` through EVERY input
shape against the temp test DB and asserts the right capture is invoked (or the
right rejection is returned), so the field-capture pipeline is provable without
a phone.

Covers: /start <token> binding, plain text, voice, photo/badge, shared contact,
bare LinkedIn URL, edited messages, in-field commands (/next /undo /fix), the
unbound-user message, and every malformed/empty/unknown update — asserting none
of them 500 the webhook (handle_update never raises).

send_message and voice.capture_* are mocked: a real bot token / LLM key may be
present in .env and we must not hit the network.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from grain import db
from grain import telegram as tg

TG_ID = 880022
REP_ID = "rep-tg-matrix"


def _seed_rep_bound() -> None:
    """A rep already bound to TG_ID (telegram_user_id set, no active conf)."""
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO reps (id, full_name, region, created_at) "
            "VALUES (?,?,?,?)",
            (REP_ID, "Matrix Rep", "EU", db.now_iso()),
        )
        conn.execute(
            "UPDATE reps SET telegram_user_id = ?, active_conference_id = NULL, "
            "telegram_link_token = NULL, telegram_link_token_event_id = NULL "
            "WHERE id = ?",
            (TG_ID, REP_ID),
        )
    finally:
        conn.close()


def _seed_rep_unbound_with_token(token: str, conf_id: str | None = None) -> None:
    """A rep with a pending link token but NOT yet bound to a telegram id."""
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO reps (id, full_name, region, created_at) "
            "VALUES (?,?,?,?)",
            (REP_ID, "Matrix Rep", "EU", db.now_iso()),
        )
        conn.execute(
            "UPDATE reps SET telegram_user_id = NULL, active_conference_id = NULL "
            "WHERE id = ?",
            (REP_ID,),
        )
        # New model: bind tokens live in their own table (many per rep coexist).
        conn.execute(
            "INSERT OR REPLACE INTO telegram_link_tokens "
            "(token, rep_id, conference_id, created_at) VALUES (?,?,?,?)",
            (token, REP_ID, conf_id, db.now_iso()),
        )
    finally:
        conn.close()


def _msg(**message) -> dict:
    return {"update_id": 1, "message": {"from": {"id": TG_ID},
                                        "chat": {"id": TG_ID}, **message}}


def _edited(**message) -> dict:
    """An edited_message update (no top-level 'message' key)."""
    return {"update_id": 1, "edited_message": {"from": {"id": TG_ID},
                                               "chat": {"id": TG_ID}, **message}}


_FAKE_RESULT = {
    "ok": True, "encounter_id": "e_x", "structured": {"name": "X", "company": "Y"},
    "contact_id": "c_x", "arc": {"kind": "warming", "summary": "s"},
    "nudge": {"nudge_active": True, "nudge_text": "do the thing"},
}


# ---------------------------------------------------------------------------
# /start binding
# ---------------------------------------------------------------------------
def test_start_with_valid_token_binds_rep():
    token = "tok-valid-001"
    _seed_rep_unbound_with_token(token)
    with patch("grain.telegram.send_message") as sm:
        out = tg.handle_update(_msg(text=f"/start {token}"))
    assert out["action"] == "rep_bound"
    assert out["rep_id"] == REP_ID
    sm.assert_called_once()
    # rep is now bound
    assert tg._rep_by_telegram_id(TG_ID)["id"] == REP_ID


def test_start_binds_to_event_when_token_carried_conference():
    token = "tok-valid-evt"
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO conferences (id, name, created_at, updated_at) "
            "VALUES (?,?,?,?)",
            ("conf-evt", "Money20/20 Europe", db.now_iso(), db.now_iso()),
        )
    finally:
        conn.close()
    _seed_rep_unbound_with_token(token, conf_id="conf-evt")
    with patch("grain.telegram.send_message") as sm:
        out = tg.handle_update(_msg(text=f"/start {token}"))
    assert out["action"] == "rep_bound"
    assert out["active_conference_id"] == "conf-evt"
    # the reply names the active event
    assert "Money20/20 Europe" in sm.call_args[0][1]


def test_start_with_no_token_prompts_for_link():
    with patch("grain.telegram.send_message"):
        out = tg.handle_update(_msg(text="/start"))
    assert out["action"] == "start_no_token"


def test_start_with_invalid_token_is_rejected():
    with patch("grain.telegram.send_message"):
        out = tg.handle_update(_msg(text="/start totally-bogus-token"))
    assert out["action"] == "start_invalid_token"


# ---------------------------------------------------------------------------
# Capture types (bound rep)
# ---------------------------------------------------------------------------
def test_plain_text_routes_to_text_capture():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.capture_text", return_value=_FAKE_RESULT) as cap:
        out = tg.handle_update(_msg(text="Met the CFO of Wise, wants a follow-up"))
    assert out["action"] == "text_encounter"
    assert cap.call_args.kwargs["capture_mode"] == "telegram"
    assert cap.call_args.kwargs["rep_id"] == REP_ID


def test_voice_routes_to_voice_capture():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.download_voice", return_value="memo.ogg"), \
         patch("grain.telegram.voice.capture_voice", return_value=_FAKE_RESULT) as cap:
        out = tg.handle_update(_msg(voice={"file_id": "voice-file-1"}))
    assert out["action"] == "voice_encounter"
    assert cap.call_args.kwargs["capture_mode"] == "telegram"


def test_voice_download_failure_is_handled():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.download_voice", return_value=None):
        out = tg.handle_update(_msg(voice={"file_id": "voice-file-1"}))
    assert out["action"] == "voice_download_failed"


def test_photo_routes_to_badge_capture():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.download_file", return_value="badge.jpg"), \
         patch("grain.telegram.voice.capture_image", return_value=_FAKE_RESULT) as cap:
        out = tg.handle_update(_msg(photo=[{"file_id": "sm"}, {"file_id": "lg"}]))
    assert out["action"] == "badge_encounter"
    assert cap.call_args.kwargs["capture_mode"] == "telegram_badge"


def test_shared_contact_routes_to_contact_capture():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.capture_contact", return_value=_FAKE_RESULT) as cap:
        out = tg.handle_update(_msg(contact={
            "phone_number": "+44 20 7946 0000",
            "first_name": "Jo", "last_name": "Banks"}))
    assert out["action"] == "contact_encounter"
    assert cap.call_args.kwargs["phone"] == "+44 20 7946 0000"
    assert cap.call_args.kwargs["name"] == "Jo Banks"


def test_bare_linkedin_url_routes_to_linkedin_capture():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.capture_linkedin", return_value=_FAKE_RESULT) as cap:
        out = tg.handle_update(_msg(text="https://www.linkedin.com/in/jane-doe/"))
    assert out["action"] == "linkedin_encounter"
    assert "linkedin.com/in/jane-doe" in cap.call_args.kwargs["url"]


def test_linkedin_url_without_scheme_still_matches():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.capture_linkedin", return_value=_FAKE_RESULT) as cap:
        out = tg.handle_update(_msg(text="linkedin.com/in/sam-fox"))
    assert out["action"] == "linkedin_encounter"
    assert cap.called


def test_edited_message_is_processed():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.capture_text", return_value=_FAKE_RESULT) as cap:
        out = tg.handle_update(_edited(text="Correction: it was the VP not the CFO"))
    assert out["action"] == "text_encounter"
    assert cap.called


# ---------------------------------------------------------------------------
# In-field commands
# ---------------------------------------------------------------------------
def test_next_command_breaks_session():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.close_capture_session",
               return_value={"ok": True}) as br:
        out = tg.handle_update(_msg(text="/next"))
    assert out["action"] == "session_break"
    br.assert_called_once_with(REP_ID)


def test_undo_with_nothing_is_safe():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.last_encounter_for_rep", return_value=None):
        out = tg.handle_update(_msg(text="/undo"))
    assert out["action"] == "undo_empty"


def test_fix_command_edits_last_encounter():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.last_encounter_for_rep",
               return_value={"id": "e_last"}), \
         patch("grain.telegram.voice.edit_encounter", return_value={
             "structured": {"name": "Fixed Co"}, "contact_id": "c",
             "arc": None, "nudge": None}) as ed:
        out = tg.handle_update(_msg(text="/fix company Fixed Co"))
    assert out["action"] == "fix"
    assert ed.call_args[0][1] == {"company": "Fixed Co"}


def test_fix_bad_field_is_rejected():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"):
        out = tg.handle_update(_msg(text="/fix bogus value"))
    assert out["action"] == "fix_usage"


def test_unknown_command_lists_help():
    _seed_rep_bound()
    with patch("grain.telegram.send_message"):
        out = tg.handle_update(_msg(text="/wat"))
    assert out["action"] == "unknown_command"


# ---------------------------------------------------------------------------
# Unbound user / rejection paths
# ---------------------------------------------------------------------------
def test_unbound_user_is_told_to_tap_deep_link():
    with patch("grain.telegram.send_message") as sm:
        out = tg.handle_update({"update_id": 9, "message": {
            "from": {"id": 7777777}, "chat": {"id": 7777777}, "text": "hello?"}})
    assert out["action"] == "unbound_user"
    # the reply must point them at the connect link
    assert "Connect Telegram" in sm.call_args[0][1]


# ---------------------------------------------------------------------------
# Malformed / empty / unknown updates — must never raise (no webhook 500)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("update", [
    {},                                              # empty
    {"update_id": 1},                                # no message
    {"message": {}},                                 # empty message
    {"message": {"from": {}, "chat": {}}},           # no sender id
    {"message": {"from": {"id": TG_ID}}},            # no chat, no payload
    {"message": {"chat": {"id": TG_ID}}},            # no 'from' (no sender id)
    {"channel_post": {"text": "hi"}},                # unsupported update type
    None,                                            # not even a dict
    "garbage",                                       # wrong type entirely
    [1, 2, 3],                                       # list
    {"message": {"from": {"id": TG_ID}, "chat": {"id": TG_ID},
                 "sticker": {"file_id": "s"}}},      # unhandled message kind
    {"message": {"from": {"id": TG_ID}, "chat": {"id": TG_ID},
                 "location": {"latitude": 1.0}}},    # unhandled message kind
])
def test_malformed_updates_never_raise(update):
    with patch("grain.telegram.send_message"):
        out = tg.handle_update(update)
    assert isinstance(out, dict)
    assert "action" in out
    # none of these should be a successful encounter
    assert not out["action"].endswith("_encounter")


def test_internal_error_is_caught_not_raised():
    """If a downstream lookup throws, handle_update must swallow it and return a
    structured error rather than 500-ing the webhook."""
    _seed_rep_bound()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram._rep_by_telegram_id",
               side_effect=RuntimeError("db exploded")):
        out = tg.handle_update(_msg(text="anything"))
    assert out["action"] == "error"
    assert "db exploded" in out["error"]
