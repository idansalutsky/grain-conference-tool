"""Post-event wrap-up agent — a real tool-calling LLM that closes out an event.

Unlike the per-capture pipeline (fast, deterministic, one tap on the floor), the
end-of-event wrap is a moment where *reasoning* adds value: review everyone the
rep captured, check what's missing, spot cross-conference / multi-contact account
patterns a static digest can't, draft the follow-ups worth sending, and flag the
urgent ones. So this is an agent: it picks tools, iterates, and reasons a summary.

It is bounded (max_tools) and ALWAYS degrades to the deterministic digest
(`telegram._format_wrap` via `followup.draft_for_event`) when there is no LLM key
or the agent errors — so /wrap never breaks.
"""
from __future__ import annotations

import json
from typing import Any

from . import db, llm

MAX_ITERATIONS = 8


# ---------------------------------------------------------------------------
# Tools the agent can call
# ---------------------------------------------------------------------------
def _tool_list_event_captures(conference_id: str) -> dict:
    """Everyone captured at this event, with arc + reachability + touch count."""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT c.id, c.primary_name, c.primary_company, "
            "c.primary_title, c.arc_verdict, c.primary_email, c.phone, "
            "c.linkedin_handle, "
            "(SELECT COUNT(*) FROM encounters e2 WHERE e2.contact_id = c.id) AS touches "
            "FROM contacts c JOIN encounters e ON e.contact_id = c.id "
            "WHERE e.conference_id = ? ORDER BY c.updated_at DESC",
            (conference_id,),
        ).fetchall()
    finally:
        conn.close()
    people = []
    for r in rows:
        reachable = bool(r["primary_email"] or r["phone"] or r["linkedin_handle"])
        people.append({
            "contact_id": r["id"], "name": r["primary_name"],
            "company": r["primary_company"], "title": r["primary_title"],
            "arc": r["arc_verdict"], "reachable": reachable,
            "touches": r["touches"],
        })
    return {"n": len(people), "captures": people}


def _tool_check_cross_conference(contact_id: str) -> dict:
    """Has this person been encountered at OTHER events? (the repeat-ICP signal)."""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT e.conference_id, conf.name "
            "FROM encounters e LEFT JOIN conferences conf ON conf.id = e.conference_id "
            "WHERE e.contact_id = ?",
            (contact_id,),
        ).fetchall()
        c = conn.execute(
            "SELECT primary_name, arc_verdict FROM contacts WHERE id = ?",
            (contact_id,),
        ).fetchone()
    finally:
        conn.close()
    events = [r["name"] or r["conference_id"] for r in rows if r["conference_id"]]
    return {
        "name": c["primary_name"] if c else None,
        "arc": c["arc_verdict"] if c else None,
        "n_events": len(events), "events": events,
        "is_repeat": len(events) > 1,
    }


def _tool_account_clusters(conference_id: str) -> dict:
    """Companies with MORE THAN ONE contact captured here — an account-play signal
    a per-contact view misses."""
    data = _tool_list_event_captures(conference_id)
    by_company: dict[str, list[str]] = {}
    for p in data["captures"]:
        co = (p.get("company") or "").strip()
        if co:
            by_company.setdefault(co, []).append(p.get("name") or "?")
    clusters = [{"company": k, "contacts": v}
                for k, v in by_company.items() if len(v) > 1]
    return {"n_clusters": len(clusters), "clusters": clusters}


TOOLS = [
    {"type": "function", "function": {
        "name": "list_event_captures",
        "description": "Everyone the rep captured at this event: name, company, "
        "title, arc verdict, whether we can reach them (email/phone/LinkedIn), "
        "and touch count. Call this FIRST.",
        "parameters": {"type": "object", "properties": {
            "conference_id": {"type": "string"}}, "required": ["conference_id"]}}},
    {"type": "function", "function": {
        "name": "check_cross_conference",
        "description": "Check whether a captured contact has also been met at "
        "OTHER events (a warming repeat relationship worth prioritising). Use on "
        "the notable warming contacts.",
        "parameters": {"type": "object", "properties": {
            "contact_id": {"type": "string"}}, "required": ["contact_id"]}}},
    {"type": "function", "function": {
        "name": "account_clusters",
        "description": "Companies where the rep captured MORE THAN ONE person here "
        "— an account-play opportunity a per-contact view misses.",
        "parameters": {"type": "object", "properties": {
            "conference_id": {"type": "string"}}, "required": ["conference_id"]}}},
    {"type": "function", "function": {
        "name": "finalize_wrap",
        "description": "Emit the final end-of-event summary once you've reviewed "
        "the captures, missing info, cross-conference repeats, and drafts.",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string",
                        "description": "2-4 sentence reasoned recap for the rep."},
            "urgent": {"type": "array", "items": {"type": "string"},
                       "description": "Names needing action NOW (warming + meeting/repeat)."},
            "missing_info": {"type": "array", "items": {"type": "string"},
                             "description": "Captured people we can't reach yet."},
            "account_plays": {"type": "array", "items": {"type": "string"},
                              "description": "Companies with 2+ contacts to pursue as an account."},
        }, "required": ["summary"]}}},
]

_DISPATCH = {
    "list_event_captures": lambda a: _tool_list_event_captures(a["conference_id"]),
    "check_cross_conference": lambda a: _tool_check_cross_conference(a["contact_id"]),
    "account_clusters": lambda a: _tool_account_clusters(a["conference_id"]),
}

SYSTEM_PROMPT = (
    "You are Grain's post-event wrap-up agent. The rep just finished a "
    "conference. Your job: review who they captured and hand them a tight, "
    "reasoned close-out — not a data dump.\n"
    "Process: (1) list_event_captures. (2) For the notable WARMING contacts, "
    "check_cross_conference to spot repeat relationships worth closing. "
    "(3) account_clusters to find multi-contact accounts. (4) finalize_wrap "
    "with: a short reasoned summary, the URGENT names (warming + meeting/repeat), "
    "who we can't reach yet (missing_info), and any account_plays. Be selective "
    "and efficient — a few tool calls, then finalize. Skip tire-kickers. Never "
    "invent people; only use what the tools return. (Ready-to-send follow-up "
    "drafts are produced separately — you focus on the reasoning.)"
)

MAX_RESULT_BYTES = 2000


def run_wrap_agent(conference_id: str, *, max_tools: int = 8) -> dict | None:
    """Run the tool-calling wrap agent. Returns a structured final wrap, or None
    if the LLM is unavailable / the agent can't complete (caller falls back to the
    deterministic digest)."""
    if not llm.config.OPENROUTER_API_KEY:
        return None
    conn = db.get_conn()
    try:
        conf = conn.execute(
            "SELECT name FROM conferences WHERE id = ?", (conference_id,)
        ).fetchone()
    finally:
        conn.close()
    if not conf:
        return None

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content":
            f"Event: {conf['name']} (id `{conference_id}`). Wrap it up."},
    ]
    trace: list[str] = []
    tools_used = 0
    try:
        for _ in range(MAX_ITERATIONS):
            resp = llm.chat_with_tools(messages, TOOLS, temperature=0.2)
            msg = resp["choices"][0]["message"]
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                # model answered in prose without finalize — accept as summary
                return {"summary": msg.get("content") or "", "urgent": [],
                        "missing_info": [], "account_plays": [], "trace": trace}
            for tc in calls:
                fn = (tc.get("function") or {}).get("name") or ""
                try:
                    args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if fn == "finalize_wrap":
                    args["trace"] = trace
                    return args
                trace.append(fn)
                tools_used += 1
                handler = _DISPATCH.get(fn)
                result: Any = (handler(args) if handler
                               else {"error": f"unknown tool {fn}"})
                payload = json.dumps(result)[:MAX_RESULT_BYTES]
                messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                 "name": fn, "content": payload})
            if tools_used >= max_tools:
                # budget hit — ask for the summary explicitly next turn
                messages.append({"role": "user",
                                 "content": "Tool budget reached. Call finalize_wrap now."})
    except (llm.LLMError, KeyError, IndexError, TypeError):
        return None
    return None
