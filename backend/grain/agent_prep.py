"""'Plan my prep' agent — a real tool-calling LLM that decides the rep's day.

This is the **only agentic feature** in the tool. Every other AI surface is
a single LLM call with structured output. The agent here uses OpenAI-style
tool calling: the LLM picks which tools to invoke, in what order, with what
arguments. We execute each, return results, and loop until the agent calls
`finalize_plan`.

Why an agent here and not elsewhere:
  - Pre-event prep is the one moment a sales rep needs SELECTIVE judgment.
    "Generate briefs for everyone" is wasteful. "Generate briefs for the
    right 3-4 and skip the tire-kickers" requires reasoning.
  - The agent can branch: check history → if tire_kicker, skip; if warming,
    pull existing brief; if new, generate; if competitor at booth, flag.
  - The output is a structured plan (priority order + reasoning + flags),
    not a deterministic for-loop.

Cost guardrail: tools are budgeted (max_tools default 12). The agent is
prompted to be selective.

Cost per run: ~$0.02 (1-2 grounded searches + brief generations + tool
chatter). The deterministic "Prep me" button is still available for callers
who want a cheaper fixed-priority run.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from . import brief as brief_mod
from . import db, llm
from .icp import IcpConfig

log = logging.getLogger("grain.agent_prep")


# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling schema)
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_targets",
            "description": (
                "List the buying-committee targets at a conference, ranked by "
                "ICP-fit (persona_weight). BUYER (1.0) > CHAMPION (0.75) > "
                "ENTRY_POINT (0.65) > PAIN_OWNER (0.70). Call this first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "conference_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 12},
                },
                "required": ["conference_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_contact_history",
            "description": (
                "Check if we have prior history with a person (by name + "
                "company). Returns arc verdict (warming / flat / cooling / "
                "tire_kicker), encounter count, last-touch date. Use this to "
                "skip tire-kickers and prioritize warming contacts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "company": {"type": "string"},
                },
                "required": ["name", "company"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_company_news",
            "description": (
                "Grounded web search for recent (last 12 months) news about a "
                "company relevant to FX / cross-border / treasury. Returns "
                "citations. **Cost ~$0.005 per call — use sparingly, only for "
                "top 2-3 prospects.**"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "vertical_hint": {"type": "string"},
                },
                "required": ["company"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_competitor_attendance",
            "description": (
                "Detect Grain competitors among the conference attendees "
                "(Currencycloud, Wise Business, Convera, OFX, etc.). "
                "Competitor presence is VALIDATING (good signal — ICP is here), "
                "not a deterrent. Use this to flag positioning angles."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "conference_id": {"type": "string"},
                },
                "required": ["conference_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_brief",
            "description": (
                "Generate a full approach brief (FX angle + grounded trigger "
                "news + talk track + follow-up email draft) for a specific "
                "person. **Cost ~$0.005 per call. Don't generate more than 5 "
                "briefs total per plan. Skip tire-kickers.**"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "person_id": {"type": "string"},
                },
                "required": ["person_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_plan",
            "description": (
                "Call this when you're done. Provide the ordered priority list "
                "with reasoning for each, briefs generated, skipped people "
                "with reasons, competitor flags, and any warnings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "2-3 sentence executive summary of the plan",
                    },
                    "priority_order": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "person_name": {"type": "string"},
                                "company": {"type": "string"},
                                "title": {"type": "string"},
                                "priority": {
                                    "type": "integer",
                                    "description": "1 is highest",
                                },
                                "reason": {"type": "string"},
                                "has_brief": {"type": "boolean"},
                            },
                            "required": ["person_name", "company", "priority", "reason"],
                        },
                    },
                    "briefs_generated_count": {"type": "integer"},
                    "skipped_with_reason": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "person": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["person", "reason"],
                        },
                    },
                    "competitor_flags": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "positioning_notes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["summary", "priority_order"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------
def _tool_list_targets(conference_id: str, limit: int = 12) -> dict:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, full_name, title, company_name, persona, persona_weight, "
            "vertical FROM people WHERE conference_id = ? "
            "AND persona IN ('BUYER','CHAMPION','PAIN_OWNER','ENTRY_POINT') "
            "ORDER BY persona_weight DESC LIMIT ?",
            (conference_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return {
        "targets": [
            {
                "person_id": r["id"],
                "name": r["full_name"],
                "title": r["title"],
                "company": r["company_name"],
                "persona": r["persona"],
                "persona_weight": r["persona_weight"],
                "vertical": r["vertical"],
            }
            for r in rows
        ],
        "n_total": len(rows),
    }


def _tool_get_contact_history(name: str, company: str) -> dict:
    conn = db.get_conn()
    try:
        # Try name match first (most specific)
        rows = conn.execute(
            "SELECT id, primary_name, primary_company, primary_title, arc_verdict, "
            "arc_confidence, arc_summary, nudge_active, updated_at FROM contacts "
            "WHERE lower(primary_name) LIKE ?",
            (f"%{name.lower()}%",),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return {"found": False, "interpretation": "no prior history — fresh contact"}
    best = dict(rows[0])
    conn = db.get_conn()
    try:
        n_enc = conn.execute(
            "SELECT COUNT(*) FROM encounters WHERE contact_id = ?", (best["id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "found": True,
        "contact_id": best["id"],
        "primary_name": best["primary_name"],
        "primary_company": best["primary_company"],
        "primary_title": best["primary_title"],
        "arc_verdict": best["arc_verdict"],
        "arc_confidence": best["arc_confidence"],
        "arc_summary": best["arc_summary"],
        "nudge_active": bool(best["nudge_active"]),
        "n_encounters": n_enc,
        "last_touch_date": (best["updated_at"] or "")[:10],
    }


def _tool_lookup_company_news(company: str,
                              vertical_hint: Optional[str] = None) -> dict:
    angle = ("FX exposure, cross-border revenue, multi-market expansion, "
             "treasury hires, new payment corridors")
    if vertical_hint:
        angle += f"; vertical context: {vertical_hint}"
    query = (
        f"Find recent (last 12 months) news about \"{company}\" relevant to: "
        f"{angle}. List 0-3 items, each with: headline, date, url, "
        "one-sentence relevance. If nothing notable, say so explicitly."
    )
    system = ("You're a sales-research analyst for Grain Finance, a fintech "
              "selling embedded FX hedging.")
    try:
        text, citations = llm.search_grounded(query, system=system)
    except llm.LLMError as exc:
        return {"error": str(exc)[:200], "n_citations": 0}
    return {
        "summary": text[:600],
        "n_citations": len(citations),
        "citations": [c["url"] for c in citations[:3]],
    }


def _tool_check_competitor_attendance(conference_id: str) -> dict:
    icp = IcpConfig.default()
    competitors = [c.lower() for c in icp.competitors]
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT full_name, title, company_name FROM people "
            "WHERE conference_id = ?", (conference_id,),
        ).fetchall()
    finally:
        conn.close()
    found: list[dict] = []
    for r in rows:
        co = (r["company_name"] or "").lower()
        for comp in competitors:
            if comp in co:
                found.append({
                    "person": r["full_name"],
                    "title": r["title"],
                    "company": r["company_name"],
                    "matched_competitor": comp,
                })
                break
    return {
        "competitors_attending": found[:8],
        "n_found": len(found),
        "interpretation": (
            "competitor presence validates ICP fit (good signal); useful for "
            "positioning conversations"
        ),
    }


def _tool_generate_brief(person_id: str) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id, full_name, title, company_name, vertical, conference_id "
            "FROM people WHERE id = ?", (person_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"error": "person_id not found"}
    p = dict(row)
    try:
        out = brief_mod.generate(
            name=p["full_name"],
            company=p["company_name"] or "Unknown",
            title=p["title"], vertical=p["vertical"],
            person_id=p["id"], conference_id=p["conference_id"],
            use_web_search=True, persist=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}
    bj = out.get("brief_json", {})
    return {
        "brief_id": out["brief_id"],
        "person_id": person_id,
        "person_name": p["full_name"],
        "company": p["company_name"],
        "fx_angle_preview": (bj.get("fx_angle") or "")[:240],
        "trigger_news_count": len(bj.get("trigger_news", [])),
        "follow_up_draft_chars": len(bj.get("follow_up_draft", "")),
    }


_TOOL_DISPATCH = {
    "list_targets": _tool_list_targets,
    "get_contact_history": _tool_get_contact_history,
    "lookup_company_news": _tool_lookup_company_news,
    "check_competitor_attendance": _tool_check_competitor_attendance,
    "generate_brief": _tool_generate_brief,
}


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a sales-strategy agent for Grain Finance, a Tel Aviv fintech "
    "selling embedded cross-currency FX hedging to PSPs, travel platforms, "
    "and cross-border payment companies. A sales rep is about to attend a "
    "conference. Your job: plan their prep efficiently and SELECTIVELY.\n\n"
    "Use these tools in roughly this order:\n"
    "  1. list_targets — see the buying committee\n"
    "  2. get_contact_history — for 3-6 names you suspect we may know; SKIP "
    "any tire_kickers and de-prioritise cooling ones\n"
    "  3. check_competitor_attendance — find Grain competitors at the event\n"
    "  4. lookup_company_news — for top 2-3 prospects only (each call costs)\n"
    "  5. generate_brief — for the FINAL 3-4 top priorities (each call costs)\n"
    "  6. finalize_plan — when done\n\n"
    "RULES:\n"
    "- Do NOT generate more than 5 briefs total.\n"
    "- Do NOT generate briefs for tire-kickers.\n"
    "- For warming contacts with existing nudge: skip brief, mention in plan.\n"
    "- Be selective — quality > quantity. Rep has limited time on the floor.\n"
    "- When you call finalize_plan, include: priority_order (ranked), "
    "skipped_with_reason, competitor_flags, positioning_notes."
)

MAX_ITERATIONS = 10
MAX_RESULT_BYTES = 2200  # truncate large tool results to keep context small


def _summarize_for_trace(name: str, result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)[:120]
    if "error" in result:
        return f"error: {str(result['error'])[:100]}"
    if name == "list_targets":
        return f"{result.get('n_total', 0)} targets surfaced"
    if name == "get_contact_history":
        if not result.get("found"):
            return "no prior history (fresh contact)"
        return (f"history found: arc={result.get('arc_verdict')}, "
                f"{result.get('n_encounters')} encs, "
                f"last={result.get('last_touch_date')}")
    if name == "check_competitor_attendance":
        return f"{result.get('n_found', 0)} competitor people on attendee list"
    if name == "lookup_company_news":
        return f"{result.get('n_citations', 0)} citations"
    if name == "generate_brief":
        return (f"brief {result.get('brief_id')} for {result.get('person_name')} "
                f"({result.get('trigger_news_count', 0)} news items)")
    return str(result)[:120]


def plan_prep_for_event_stream(conference_id: str, *,
                               max_tools: int = 14):
    """Streaming generator version. Yields trace events as they happen.

    Each yielded value is a dict shaped like:
      {"kind": "start", "conference": {...}}
      {"kind": "tool_call_start", "iteration": int, "name": str, "args": dict}
      {"kind": "tool_call_done", "iteration": int, "name": str, "result_summary": str}
      {"kind": "final_plan", "plan": dict}
      {"kind": "error", "message": str}
      {"kind": "end"}
    """
    conn = db.get_conn()
    try:
        conf_row = conn.execute(
            "SELECT id, name, vertical, themes, city, country, start_date "
            "FROM conferences WHERE id = ?", (conference_id,),
        ).fetchone()
    finally:
        conn.close()
    if not conf_row:
        yield {"kind": "error", "message": f"conference {conference_id} not found"}
        yield {"kind": "end"}
        return
    conf = dict(conf_row)
    yield {"kind": "start", "conference": {"id": conference_id, "name": conf["name"]}}

    user_msg = (
        f"Conference: **{conf['name']}** ({conf['start_date']}, "
        f"{conf['city']}, {conf['country']})\n"
        f"Vertical focus: {conf['vertical']}\n"
        f"Themes: {conf.get('themes') or '(none recorded)'}\n"
        f"Conference ID: `{conference_id}`\n\n"
        "Plan the rep's prep. Start by listing targets. Be selective and "
        "efficient. Call finalize_plan when done."
    )

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    tools_used = 0

    for it in range(MAX_ITERATIONS):
        try:
            response = llm.chat_with_tools(messages, TOOLS, temperature=0.2)
        except llm.LLMError as exc:
            yield {"kind": "error", "message": f"LLM call failed: {exc}"}
            yield {"kind": "end"}
            return

        try:
            choice_msg = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            yield {"kind": "error", "message": "malformed LLM response"}
            yield {"kind": "end"}
            return

        messages.append(choice_msg)
        tool_calls = choice_msg.get("tool_calls") or []
        if not tool_calls:
            content = choice_msg.get("content") or ""
            yield {"kind": "final_plan", "plan": {"raw_text": content}}
            break

        for tc in tool_calls:
            fn_name = (tc.get("function") or {}).get("name") or ""
            args_raw = (tc.get("function") or {}).get("arguments") or "{}"
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {}

            if fn_name == "finalize_plan":
                yield {"kind": "final_plan", "plan": args}
                yield {"kind": "end"}
                return

            yield {"kind": "tool_call_start", "iteration": it + 1,
                   "name": fn_name, "args": args}

            tool_fn = _TOOL_DISPATCH.get(fn_name)
            if tool_fn is None:
                result: Any = {"error": f"unknown tool {fn_name!r}"}
            else:
                try:
                    result = tool_fn(**args)
                except Exception as exc:  # noqa: BLE001
                    result = {"error": str(exc)[:200]}
                tools_used += 1

            summary = _summarize_for_trace(fn_name, result)
            yield {"kind": "tool_call_done", "iteration": it + 1,
                   "name": fn_name, "result_summary": summary}

            tool_result_json = json.dumps(result, ensure_ascii=False)
            if len(tool_result_json) > MAX_RESULT_BYTES:
                tool_result_json = tool_result_json[:MAX_RESULT_BYTES] + " ..."
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": tool_result_json,
            })

            if tools_used >= max_tools:
                messages.append({
                    "role": "user",
                    "content": (
                        "Hit the tool budget. Call finalize_plan NOW with the "
                        "best plan you can from what you've gathered."
                    ),
                })
                break

    yield {"kind": "end"}


def plan_prep_for_event(conference_id: str, *,
                        max_tools: int = 14) -> dict:
    """Run the agent loop. Returns {plan, trace} or {error}."""
    conn = db.get_conn()
    try:
        conf_row = conn.execute(
            "SELECT id, name, vertical, themes, city, country, start_date "
            "FROM conferences WHERE id = ?", (conference_id,),
        ).fetchone()
    finally:
        conn.close()
    if not conf_row:
        return {"error": f"conference {conference_id} not found"}
    conf = dict(conf_row)

    user_msg = (
        f"Conference: **{conf['name']}** ({conf['start_date']}, "
        f"{conf['city']}, {conf['country']})\n"
        f"Vertical focus: {conf['vertical']}\n"
        f"Themes: {conf.get('themes') or '(none recorded)'}\n"
        f"Conference ID: `{conference_id}`\n\n"
        "Plan the rep's prep. Start by listing targets. Be selective and "
        "efficient. Call finalize_plan when done."
    )

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    trace: dict = {
        "iterations": 0,
        "tool_calls": [],
        "final_plan": None,
        "conference": {"id": conference_id, "name": conf["name"]},
    }
    tools_used = 0

    for it in range(MAX_ITERATIONS):
        trace["iterations"] = it + 1
        try:
            response = llm.chat_with_tools(messages, TOOLS, temperature=0.2)
        except llm.LLMError as exc:
            return {"error": f"LLM call failed: {exc}", "trace": trace}

        try:
            choice_msg = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return {"error": "malformed LLM response", "trace": trace}

        # Append assistant message verbatim — required by tool-call protocol
        messages.append(choice_msg)

        tool_calls = choice_msg.get("tool_calls") or []
        if not tool_calls:
            content = choice_msg.get("content") or ""
            trace["final_plan"] = {"raw_text": content}
            break

        for tc in tool_calls:
            fn_name = (tc.get("function") or {}).get("name") or ""
            args_raw = (tc.get("function") or {}).get("arguments") or "{}"
            try:
                args = json.loads(args_raw)
            except json.JSONDecodeError:
                args = {}

            if fn_name == "finalize_plan":
                trace["tool_calls"].append({
                    "iteration": it + 1, "name": fn_name,
                    "args_summary": f"plan: {len(args.get('priority_order', []))} priorities",
                    "result_summary": "(end of loop)",
                })
                trace["final_plan"] = args
                return {"plan": args, "trace": trace, "ok": True}

            tool_fn = _TOOL_DISPATCH.get(fn_name)
            if tool_fn is None:
                result: Any = {"error": f"unknown tool {fn_name!r}"}
            else:
                try:
                    result = tool_fn(**args)
                except Exception as exc:  # noqa: BLE001
                    result = {"error": str(exc)[:200]}
                tools_used += 1

            trace["tool_calls"].append({
                "iteration": it + 1,
                "name": fn_name,
                "args": args,
                "result_summary": _summarize_for_trace(fn_name, result),
            })

            tool_result_json = json.dumps(result, ensure_ascii=False)
            if len(tool_result_json) > MAX_RESULT_BYTES:
                tool_result_json = tool_result_json[:MAX_RESULT_BYTES] + " ..."

            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": tool_result_json,
            })

            if tools_used >= max_tools:
                # Inject a directive to wrap up
                messages.append({
                    "role": "user",
                    "content": (
                        "Hit the tool budget. Call finalize_plan NOW with the "
                        "best plan you can from what you've gathered."
                    ),
                })
                break

    if trace["final_plan"] is None:
        trace["final_plan"] = {"raw_text": "(agent did not call finalize_plan)"}
    return {"plan": trace["final_plan"], "trace": trace,
            "ok": trace["final_plan"] is not None}
