"""Real-world salesperson behaviour — messy, multi-part, out-of-order bursts.

These verify the agent assembles the connected pieces into the RIGHT place:
the right encounter, the right contact, the right event. Each test mimics a
concrete floor scenario. LLM extraction is patched; everything else is real.
Unique rep_ids isolate each scenario in the shared session DB.
"""
from __future__ import annotations

from unittest.mock import patch

from grain import db, voice


def _enc_count(contact_id):
    conn = db.get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM encounters WHERE contact_id=?",
                            (contact_id,)).fetchone()[0]
    finally:
        conn.close()


def _contact_exists(cid):
    conn = db.get_conn()
    try:
        return conn.execute("SELECT 1 FROM contacts WHERE id=?", (cid,)).fetchone() is not None
    finally:
        conn.close()


def _make_conf(cid, name):
    conn = db.get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO conferences (id,name,region,vertical,"
                     "created_at,updated_at) VALUES (?,?,?,?,?,?)",
                     (cid, name, "EU", "payments", db.now_iso(), db.now_iso()))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Source priority (unit) — printed badge corrects a mis-heard voice; a LinkedIn
# slug guess never overrides a printed name.
# ---------------------------------------------------------------------------
def test_badge_corrects_misheard_voice_name():
    out = voice._merge_structured(
        {"name": "Dana Lavi"}, {"name": "Dana Levi"},
        base_mode="telegram", new_mode="telegram_badge")
    assert out["name"] == "Dana Levi"          # printed beats heard


def test_heard_does_not_override_printed_name():
    out = voice._merge_structured(
        {"name": "Dana Levi"}, {"name": "Dana Lavi"},
        base_mode="telegram_badge", new_mode="telegram")
    assert out["name"] == "Dana Levi"          # badge stays authoritative


def test_linkedin_slug_does_not_override_printed_name():
    out = voice._merge_structured(
        {"name": "Dana Levi"}, {"name": "dana levy"},
        base_mode="badge_photo", new_mode="telegram_linkedin")
    assert out["name"] == "Dana Levi"


# ---------------------------------------------------------------------------
# Out-of-order: context (no name) FIRST, then the badge names them.
# The nameless placeholder contact must be promoted + the orphan cleaned.
# ---------------------------------------------------------------------------
def test_context_first_then_badge_promotes_and_cleans_orphan():
    rep = "rep-rw-ctxfirst"
    with patch("grain.voice.llm.text_to_lead", return_value={
        "name": None, "company": None, "title": None, "vertical": None,
        "sentiment": 5, "soft_signals": ["wants_meeting", "explicit_pain"],
        "meeting_requested": True,
        "what_discussed": "runs treasury; bad FX leakage on payouts", "transcript": "",
    }):
        ctx = voice.capture_text_fast(text="she wants a demo, big FX pain", rep_id=rep)
    placeholder = ctx["contact_id"]

    with patch("grain.voice.llm.image_to_lead", return_value={
        "name": "Dana Promote", "company": "PromoCo", "title": "CFO",
        "vertical": "payments", "sentiment": 3, "soft_signals": [],
        "meeting_requested": False, "what_discussed": None,
    }):
        badge = voice.capture_image_fast(image_path=__file__, rep_id=rep)

    assert badge["stitched"] is True
    assert badge["structured"]["name"] == "Dana Promote"    # named by the badge
    assert "FX" in (badge["structured"]["what_discussed"] or "")  # context retained
    assert badge["structured"]["meeting_requested"] is True
    assert _enc_count(badge["contact_id"]) == 1             # still ONE encounter
    assert not _contact_exists(placeholder)                 # nameless orphan cleaned


# ---------------------------------------------------------------------------
# Badge first, then a shared contact card adds the phone — one encounter.
# ---------------------------------------------------------------------------
def test_badge_then_contact_card_adds_phone():
    rep = "rep-rw-card"
    with patch("grain.voice.llm.image_to_lead", return_value={
        "name": "Omar Said", "company": "RailsCo", "title": "Treasurer",
        "vertical": "payments", "sentiment": 3, "soft_signals": [],
        "meeting_requested": False, "what_discussed": None,
    }):
        badge = voice.capture_image_fast(image_path=__file__, rep_id=rep)
    card = voice.capture_contact_fast(
        name="Omar Said", phone="+44 20 7946 0958", rep_id=rep)

    assert card["stitched"] is True
    assert card["contact_id"] == badge["contact_id"]
    assert card["structured"]["phone"] == "+44 20 7946 0958"
    assert card["structured"]["name"] == "Omar Said"
    assert _enc_count(badge["contact_id"]) == 1


# ---------------------------------------------------------------------------
# Same person, two DIFFERENT events → two encounters, one contact (the
# cross-conference arc). Stitching must NOT merge across events.
# ---------------------------------------------------------------------------
def test_same_person_two_events_two_touches_one_contact():
    _make_conf("rw-evt-A", "Event A 2026")
    _make_conf("rw-evt-B", "Event B 2026")
    rep = "rep-rw-2events"
    payload = {
        "name": "Lena Cross", "company": "CrossCo", "title": "VP Finance",
        "vertical": "payments", "sentiment": 4, "soft_signals": [],
        "meeting_requested": False, "what_discussed": "intro", "transcript": "",
    }
    with patch("grain.voice.llm.text_to_lead", return_value=payload):
        a = voice.capture_text_fast(text="met Lena at A", rep_id=rep, conference_id="rw-evt-A")
        b = voice.capture_text_fast(text="met Lena at B", rep_id=rep, conference_id="rw-evt-B")
    assert b.get("stitched") is False           # different event → not merged
    assert a["contact_id"] == b["contact_id"]   # but same person
    assert _enc_count(a["contact_id"]) == 2     # two genuine touches


# ---------------------------------------------------------------------------
# Mid-burst switch: A, then B (split), then nameless context attaches to the
# LAST person (B), not A.
# ---------------------------------------------------------------------------
def test_context_attaches_to_most_recent_person():
    rep = "rep-rw-switch"
    with patch("grain.voice.llm.text_to_lead", return_value={
        "name": "Alpha One", "company": "AlphaCo", "title": "CFO",
        "vertical": "payments", "sentiment": 4, "soft_signals": [],
        "meeting_requested": False, "what_discussed": "a", "transcript": "",
    }):
        a = voice.capture_text_fast(text="met Alpha", rep_id=rep)
    with patch("grain.voice.llm.text_to_lead", return_value={
        "name": "Beta Two", "company": "BetaCo", "title": "Treasurer",
        "vertical": "payments", "sentiment": 4, "soft_signals": [],
        "meeting_requested": False, "what_discussed": "b", "transcript": "",
    }):
        b = voice.capture_text_fast(text="met Beta", rep_id=rep)
    with patch("grain.voice.llm.text_to_lead", return_value={
        "name": None, "company": None, "title": None, "vertical": None,
        "sentiment": 5, "soft_signals": ["wants_meeting"], "meeting_requested": True,
        "what_discussed": "wants pricing next week", "transcript": "",
    }):
        ctx = voice.capture_text_fast(text="wants pricing next week", rep_id=rep)

    assert ctx["stitched"] is True
    assert ctx["contact_id"] == b["contact_id"]     # attached to Beta (last)
    assert ctx["contact_id"] != a["contact_id"]
    assert _enc_count(a["contact_id"]) == 1         # Alpha untouched


# ---------------------------------------------------------------------------
# Long burst: badge → voice context → contact card, all one person → ONE
# encounter that accumulates every piece in its proper field.
# ---------------------------------------------------------------------------
def test_three_part_burst_assembles_one_complete_encounter():
    rep = "rep-rw-long"
    with patch("grain.voice.llm.image_to_lead", return_value={
        "name": "Tri Burst", "company": "TriCo", "title": "CFO",
        "vertical": "payments", "sentiment": 3, "soft_signals": [],
        "meeting_requested": False, "what_discussed": None,
    }):
        p1 = voice.capture_image_fast(image_path=__file__, rep_id=rep)
    with patch("grain.voice.llm.text_to_lead", return_value={
        "name": None, "company": None, "title": None, "vertical": None,
        "sentiment": 5, "soft_signals": ["wants_meeting", "explicit_pain"],
        "meeting_requested": True, "what_discussed": "wants a demo on payouts FX",
        "transcript": "",
    }):
        voice.capture_text_fast(text="wants a demo", rep_id=rep)
    p3 = voice.capture_contact_fast(name="Tri Burst", phone="+1 312 555 7000", rep_id=rep)

    assert _enc_count(p1["contact_id"]) == 1
    s = p3["structured"]
    assert s["name"] == "Tri Burst" and s["company"] == "TriCo"   # from badge
    assert "demo" in (s["what_discussed"] or "")                   # from voice
    assert s["phone"] == "+1 312 555 7000"                         # from card
    assert s["meeting_requested"] is True and s["sentiment"] == 5


# ---------------------------------------------------------------------------
# "Sarah" (heard, partial) then the badge "Sarah Cohen" (printed, full):
# merges, and the printed full name wins.
# ---------------------------------------------------------------------------
def test_first_name_then_badge_completes_name():
    rep = "rep-rw-partial"
    with patch("grain.voice.llm.text_to_lead", return_value={
        "name": "Quinn", "company": None, "title": None, "vertical": None,
        "sentiment": 4, "soft_signals": [], "meeting_requested": False,
        "what_discussed": "brief hello", "transcript": "",
    }):
        first = voice.capture_text_fast(text="met Quinn", rep_id=rep)
    with patch("grain.voice.llm.image_to_lead", return_value={
        "name": "Quinn Harper", "company": "HarperPay", "title": "Treasurer",
        "vertical": "payments", "sentiment": 3, "soft_signals": [],
        "meeting_requested": False, "what_discussed": None,
    }):
        full = voice.capture_image_fast(image_path=__file__, rep_id=rep)
    assert full["stitched"] is True                       # still ONE encounter
    assert full["structured"]["name"] == "Quinn Harper"   # badge completed it
    # The merge stayed one encounter; it now lives on the fully-named contact.
    assert _enc_count(full["contact_id"]) == 1
    # The bare-"Quinn" placeholder was a weak identity match → re-resolved to a
    # proper contact and the placeholder cleaned up (no stranded duplicate).
    if first["contact_id"] != full["contact_id"]:
        assert not _contact_exists(first["contact_id"])


# ---------------------------------------------------------------------------
# Hard boundary: two people named in one message. We deliberately capture the
# PRIMARY and keep the rest as context — we do NOT fabricate a second contact
# from a name merely *mentioned* (could be someone they didn't actually meet).
# ---------------------------------------------------------------------------
def test_two_people_in_one_message_captures_primary_only():
    rep = "rep-rw-twople"
    with patch("grain.voice.llm.text_to_lead", return_value={
        "name": "Dana Duo", "company": "DuoCo", "title": "CFO",
        "vertical": "payments", "sentiment": 4, "soft_signals": [],
        "meeting_requested": False,
        "what_discussed": "also introduced her treasurer Raj", "transcript": "",
    }):
        cap = voice.capture_text_fast(text="met Dana and Raj", rep_id=rep)
    assert cap["structured"]["name"] == "Dana Duo"      # one clean primary
    assert "Raj" in (cap["structured"]["what_discussed"] or "")  # the rest kept as context
    assert _enc_count(cap["contact_id"]) == 1
