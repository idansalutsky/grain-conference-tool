"""/api/agents — tool-calling agents (sync + streaming)."""
from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ... import agent_prep

router = APIRouter(prefix="/api/agents", tags=["agents"])


class PlanPrepRequest(BaseModel):
    conference_id: str
    max_tools: int = 14


@router.post("/plan-prep")
def plan_prep(body: PlanPrepRequest) -> dict:
    """Sync version. Returns the full {plan, trace} dict after the agent
    finishes. Use this if you don't need progressive UI updates."""
    return agent_prep.plan_prep_for_event(
        body.conference_id, max_tools=body.max_tools,
    )


@router.get("/plan-prep/stream")
def plan_prep_stream(conference_id: str, max_tools: int = 14):
    """Server-Sent Events stream. Emits each tool call as it lands so the
    frontend can show the agent's reasoning live. Format:

        event: <kind>
        data: <JSON>

    Kinds: start | tool_call_start | tool_call_done | final_plan | error | end
    """

    def gen():
        try:
            for event in agent_prep.plan_prep_for_event_stream(
                conference_id, max_tools=max_tools,
            ):
                kind = event.pop("kind", "message")
                yield f"event: {kind}\n"
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield "event: error\n"
            yield f"data: {json.dumps({'message': str(exc)[:300]})}\n\n"
            yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Disable proxy buffering so events flush in real time
            "X-Accel-Buffering": "no",
        },
    )
