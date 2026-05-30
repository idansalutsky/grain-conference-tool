"""Post-event wrap-up agent — tool-calling loop + graceful no-key fallback."""
from __future__ import annotations

import json

from grain import agent_wrap, db


def _seed_event_with_capture():
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO conferences (id, name, created_at, updated_at) "
            "VALUES ('conf-wrapagent','Wrap Agent Expo',?,?)",
            (db.now_iso(), db.now_iso()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO contacts (id, primary_name, primary_company, "
            "arc_verdict, primary_email, created_at, updated_at) "
            "VALUES ('c-wa','Wendy Arc','Stripe','warming','w@stripe.com',?,?)",
            (db.now_iso(), db.now_iso()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO encounters (id, contact_id, conference_id, "
            "captured_at, capture_mode) VALUES ('e-wa','c-wa','conf-wrapagent',?,?)",
            (db.now_iso(), "telegram"),
        )
    finally:
        conn.close()


def test_wrap_agent_returns_none_without_key(monkeypatch):
    """No LLM key → returns None so the caller falls back to the deterministic
    digest (the wrap must never break)."""
    monkeypatch.setattr(agent_wrap.llm.config, "OPENROUTER_API_KEY", None, raising=False)
    assert agent_wrap.run_wrap_agent("conf-wrapagent") is None


def test_wrap_agent_runs_tools_then_finalizes(monkeypatch):
    """With the LLM available, the agent calls a tool, gets a real result, then
    finalizes a structured wrap."""
    _seed_event_with_capture()
    monkeypatch.setattr(agent_wrap.llm.config, "OPENROUTER_API_KEY", "test-key", raising=False)

    calls = {"n": 0}

    def fake_chat_with_tools(messages, tools, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            # First turn: the model calls list_event_captures.
            return {"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "t1", "type": "function", "function": {
                    "name": "list_event_captures",
                    "arguments": json.dumps({"conference_id": "conf-wrapagent"})}}]}}]}
        # Second turn: the model finalizes.
        return {"choices": [{"message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": "t2", "type": "function", "function": {
                "name": "finalize_wrap",
                "arguments": json.dumps({
                    "summary": "One warming contact worth a fast follow-up.",
                    "urgent": ["Wendy Arc"], "missing_info": [],
                    "account_plays": []})}}]}}]}

    monkeypatch.setattr(agent_wrap.llm, "chat_with_tools", fake_chat_with_tools)
    res = agent_wrap.run_wrap_agent("conf-wrapagent")
    assert res is not None
    assert "warming" in res["summary"].lower()
    assert "Wendy Arc" in res["urgent"]
    assert "list_event_captures" in res.get("trace", [])
    assert calls["n"] == 2  # ran a tool, then finalized


def test_wrap_agent_unknown_conference_returns_none(monkeypatch):
    monkeypatch.setattr(agent_wrap.llm.config, "OPENROUTER_API_KEY", "test-key", raising=False)
    assert agent_wrap.run_wrap_agent("conf-does-not-exist") is None
