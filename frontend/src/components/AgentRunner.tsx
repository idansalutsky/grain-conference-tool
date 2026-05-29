import { useEffect, useRef, useState } from "react";
import type { AgentPlan, AgentTraceEntry } from "@/lib/types";

interface Props {
  conferenceId: string;
  /** Vite proxy → /api → :8000 in dev; absolute URL in prod via VITE_API_BASE_URL */
  apiBase?: string;
}

type Status = "idle" | "running" | "done" | "error";

interface RunState {
  status: Status;
  conferenceName?: string;
  toolCalls: Array<AgentTraceEntry & { in_progress?: boolean }>;
  plan: AgentPlan | null;
  errorMessage?: string;
  elapsedMs: number;
}

/**
 * Runs the "plan my prep" agent over SSE so the rep sees each tool call as
 * it lands. The agent typically takes 30-60s; without streaming UI the
 * button just sits there silent. With streaming we show the agent's
 * reasoning live — that's the "watch AI judgment in action" moment.
 */
export function AgentRunner({ conferenceId, apiBase }: Props) {
  const BASE = (apiBase ?? (import.meta.env.VITE_API_BASE_URL || "")).replace(/\/$/, "");
  const [state, setState] = useState<RunState>({
    status: "idle",
    toolCalls: [],
    plan: null,
    elapsedMs: 0,
  });
  const esRef = useRef<EventSource | null>(null);
  const startedAtRef = useRef<number>(0);
  const tickRef = useRef<number | null>(null);

  function stop() {
    esRef.current?.close();
    esRef.current = null;
    if (tickRef.current !== null) {
      window.clearInterval(tickRef.current);
      tickRef.current = null;
    }
  }

  useEffect(() => () => stop(), []);

  function run() {
    stop();
    startedAtRef.current = Date.now();
    setState({
      status: "running",
      toolCalls: [],
      plan: null,
      elapsedMs: 0,
    });

    tickRef.current = window.setInterval(() => {
      setState((s) =>
        s.status === "running"
          ? { ...s, elapsedMs: Date.now() - startedAtRef.current }
          : s,
      );
    }, 200);

    const url = `${BASE}/api/agents/plan-prep/stream?conference_id=${encodeURIComponent(
      conferenceId,
    )}&max_tools=14`;
    const es = new EventSource(url);
    esRef.current = es;

    es.addEventListener("start", (e: MessageEvent) => {
      const d = safeParse(e.data);
      setState((s) => ({ ...s, conferenceName: d?.conference?.name }));
    });

    es.addEventListener("tool_call_start", (e: MessageEvent) => {
      const d = safeParse(e.data);
      if (!d) return;
      setState((s) => ({
        ...s,
        toolCalls: [
          ...s.toolCalls,
          {
            iteration: d.iteration,
            name: d.name,
            args: d.args,
            result_summary: "running…",
            in_progress: true,
          },
        ],
      }));
    });

    es.addEventListener("tool_call_done", (e: MessageEvent) => {
      const d = safeParse(e.data);
      if (!d) return;
      setState((s) => {
        const last = s.toolCalls[s.toolCalls.length - 1];
        if (!last || last.in_progress !== true) {
          return {
            ...s,
            toolCalls: [
              ...s.toolCalls,
              {
                iteration: d.iteration,
                name: d.name,
                result_summary: d.result_summary,
              },
            ],
          };
        }
        const replaced = {
          ...last,
          result_summary: d.result_summary,
          in_progress: false,
        };
        return { ...s, toolCalls: [...s.toolCalls.slice(0, -1), replaced] };
      });
    });

    es.addEventListener("final_plan", (e: MessageEvent) => {
      const d = safeParse(e.data);
      setState((s) => ({ ...s, plan: d?.plan ?? null, status: "done" }));
    });

    es.addEventListener("error", (e: MessageEvent) => {
      const d = safeParse(e.data);
      setState((s) => ({
        ...s,
        status: "error",
        errorMessage: d?.message || "Agent run failed",
      }));
      stop();
    });

    es.addEventListener("end", () => {
      stop();
    });

    es.onerror = () => {
      // If we ended cleanly, status was already 'done' — leave it.
      setState((s) =>
        s.status === "running"
          ? { ...s, status: "error", errorMessage: "Stream connection lost" }
          : s,
      );
      stop();
    };
  }

  const isRunning = state.status === "running";
  const isDone = state.status === "done";
  const hasError = state.status === "error";
  const elapsedS = Math.floor(state.elapsedMs / 1000);

  return (
    <section className="card p-4 border-2 border-purple-300 bg-gradient-to-br from-purple-50 to-indigo-50">
      <div className="flex justify-between items-start gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-purple-900">
            🤖 AI agent — plan my prep
          </h2>
          <p className="text-xs text-purple-800 mt-0.5">
            An LLM with 5 tools (target list, contact history, news, competitors,
            brief gen) decides the priority order and reasoning. Watch it
            think — each tool call streams live.
          </p>
        </div>
        <button
          onClick={run}
          disabled={isRunning}
          className="btn-primary text-sm shrink-0 bg-purple-700 hover:bg-purple-800 disabled:bg-purple-400"
        >
          {isRunning ? `Thinking… ${elapsedS}s` : isDone || hasError ? "Re-run agent" : "Run agent"}
        </button>
      </div>

      {(isRunning || state.toolCalls.length > 0) && (
        <div className="mt-3 border-t border-purple-200 pt-3">
          <div className="text-[10px] uppercase tracking-wider text-purple-700 mb-1.5">
            Trace · {state.toolCalls.length} tool call{state.toolCalls.length === 1 ? "" : "s"}
          </div>
          <div className="space-y-1 max-h-64 overflow-y-auto pr-1">
            {state.toolCalls.map((tc, i) => (
              <div
                key={i}
                className="flex items-start gap-2 text-xs bg-white rounded px-2 py-1.5 border border-purple-100"
              >
                <span className="text-purple-700 font-mono text-[10px] shrink-0 mt-0.5">
                  [{tc.iteration}]
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-ink-900">{tc.name}</span>
                    {tc.in_progress && (
                      <span
                        aria-hidden="true"
                        className="inline-block w-1.5 h-1.5 bg-purple-500 rounded-full animate-pulse"
                      />
                    )}
                  </div>
                  <div className="text-ink-600">{tc.result_summary}</div>
                </div>
              </div>
            ))}
            {isRunning && state.toolCalls.length === 0 && (
              <div className="text-xs text-purple-700 italic flex items-center gap-2">
                <span className="inline-block w-2 h-2 bg-purple-500 rounded-full animate-pulse" />
                Agent starting…
              </div>
            )}
          </div>
        </div>
      )}

      {hasError && (
        <div className="mt-3 text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
          ⚠ {state.errorMessage}
        </div>
      )}

      {state.plan && <PlanView plan={state.plan} />}
    </section>
  );
}

function PlanView({ plan }: { plan: AgentPlan & { raw_text?: string } }) {
  if (plan.raw_text && !plan.priority_order) {
    return (
      <div className="mt-3 pt-3 border-t border-purple-200 text-xs">
        <div className="text-[10px] uppercase tracking-wider text-purple-700 mb-1">
          Agent output
        </div>
        <pre className="whitespace-pre-wrap text-ink-700">{plan.raw_text}</pre>
      </div>
    );
  }
  return (
    <div className="mt-3 pt-3 border-t border-purple-200 space-y-3">
      {plan.summary && (
        <div className="text-sm text-purple-900 italic">"{plan.summary}"</div>
      )}
      {plan.priority_order && plan.priority_order.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-purple-700 mb-1">
            Priority order ({plan.priority_order.length})
          </div>
          <div className="space-y-1">
            {plan.priority_order.map((p, i) => (
              <div
                key={i}
                className="bg-white rounded p-2 text-xs border border-purple-100"
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="badge bg-purple-600 text-white text-[9px] w-5 justify-center">
                    {p.priority}
                  </span>
                  <span className="font-semibold">{p.person_name}</span>
                  <span className="text-ink-500">@ {p.company}</span>
                  {p.has_brief && (
                    <span className="badge bg-emerald-100 text-emerald-800 text-[9px]">
                      📄 brief
                    </span>
                  )}
                </div>
                <div className="text-ink-700 mt-1">{p.reason}</div>
              </div>
            ))}
          </div>
        </div>
      )}
      {plan.competitor_flags && plan.competitor_flags.length > 0 && (
        <div className="text-xs">
          <span className="text-purple-700 font-semibold">⚠ Competitors at this event:</span>{" "}
          {plan.competitor_flags.join(", ")}
        </div>
      )}
      {plan.positioning_notes && plan.positioning_notes.length > 0 && (
        <div className="text-xs">
          <span className="text-purple-700 font-semibold">Positioning:</span>
          <ul className="list-disc list-inside text-ink-700 mt-1">
            {plan.positioning_notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}
      {plan.skipped_with_reason && plan.skipped_with_reason.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-purple-700 font-semibold">
            Skipped ({plan.skipped_with_reason.length})
          </summary>
          <div className="mt-1 space-y-0.5 text-ink-600">
            {plan.skipped_with_reason.map((s, i) => (
              <div key={i}>
                <span className="font-medium">{s.person}</span> — {s.reason}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function safeParse(s: string): any {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}
