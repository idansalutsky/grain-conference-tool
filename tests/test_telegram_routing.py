"""Telegram webhook routing — handle_update dispatches each input type and the
in-field commands correctly. send_message + the LLM captures are mocked (a real
bot token / LLM key may be present in .env; we must not hit the network)."""
from __future__ import annotations

from unittest.mock import patch

from grain import db
from grain import telegram as tg

TG_ID = 990011
REP_ID = "rep-tg-route"


def _bound_rep():
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO reps (id, full_name, region, telegram_user_id, "
            "created_at) VALUES (?,?,?,?,?)",
            (REP_ID, "TG Rep", "EU", TG_ID, db.now_iso()),
        )
        conn.execute("UPDATE reps SET telegram_user_id = ? WHERE id = ?", (TG_ID, REP_ID))
    finally:
        conn.close()


def _update(**message):
    return {"update_id": 1, "message": {"from": {"id": TG_ID},
                                        "chat": {"id": TG_ID}, **message}}


def test_photo_routes_to_badge_capture():
    _bound_rep()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.download_file", return_value="x.jpg"), \
         patch("grain.telegram.voice.capture_image", return_value={
             "ok": True, "encounter_id": "e_badge", "structured": {"name": "B"},
             "contact_id": "c1", "arc": None, "nudge": None}) as cap:
        out = tg.handle_update(_update(photo=[{"file_id": "small"}, {"file_id": "big"}]))
    assert out["action"] == "badge_encounter"
    assert cap.call_args.kwargs["capture_mode"] == "telegram_badge"


def test_shared_contact_routes_to_contact_capture():
    _bound_rep()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.capture_contact", return_value={
             "ok": True, "encounter_id": "e_ct", "structured": {"name": "C"},
             "contact_id": "c2", "arc": None, "nudge": None}) as cap:
        out = tg.handle_update(_update(contact={
            "phone_number": "+1 415 555 1212", "first_name": "Cara", "last_name": "Diaz"}))
    assert out["action"] == "contact_encounter"
    assert cap.call_args.kwargs["phone"] == "+1 415 555 1212"
    assert cap.call_args.kwargs["name"] == "Cara Diaz"


def test_bare_linkedin_url_routes_to_linkedin_capture():
    _bound_rep()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.capture_linkedin", return_value={
             "ok": True, "encounter_id": "e_li", "structured": {"name": "L"},
             "contact_id": "c3", "arc": None, "nudge": None}) as cap:
        out = tg.handle_update(_update(text="https://www.linkedin.com/in/jane-doe/"))
    assert out["action"] == "linkedin_encounter"
    assert "linkedin.com/in/jane-doe" in cap.call_args.kwargs["url"]


def test_next_command_breaks_session():
    _bound_rep()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.close_capture_session", return_value={"ok": True}) as br:
        out = tg.handle_update(_update(text="/next"))
    assert out["action"] == "session_break"
    br.assert_called_once_with(REP_ID)


def test_fix_command_edits_last_encounter():
    _bound_rep()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.last_encounter_for_rep", return_value={"id": "e_last"}), \
         patch("grain.telegram.voice.edit_encounter", return_value={
             "structured": {"name": "Corrected Co"}, "contact_id": "c", "arc": None,
             "nudge": None}) as ed:
        out = tg.handle_update(_update(text="/fix company Corrected Co"))
    assert out["action"] == "fix"
    assert ed.call_args[0][1] == {"company": "Corrected Co"}


def test_fix_bad_usage_is_rejected():
    _bound_rep()
    with patch("grain.telegram.send_message"):
        out = tg.handle_update(_update(text="/fix bogusfield value"))
    assert out["action"] == "fix_usage"


def test_undo_with_nothing_is_safe():
    _bound_rep()
    with patch("grain.telegram.send_message"), \
         patch("grain.telegram.voice.last_encounter_for_rep", return_value=None):
        out = tg.handle_update(_update(text="/undo"))
    assert out["action"] == "undo_empty"


def test_unbound_user_is_told_to_connect():
    with patch("grain.telegram.send_message"):
        out = tg.handle_update({"update_id": 2, "message": {
            "from": {"id": 12345678}, "chat": {"id": 12345678}, "text": "hi"}})
    assert out["action"] == "unbound_user"
