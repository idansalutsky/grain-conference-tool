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

// Grain green — the one accent. Matches --btn-primary / brand in index.css.
const GREEN = "oklch(0.605 0.122 164)";
const GREEN_INK = "oklch(0.45 0.11 164)";
const GREEN_TINT_BG = "oklch(0.97 0.03 164)";
const GREEN_TINT_BORDER = "oklch(0.86 0.05 164)";

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
    <section className="card p-4">
      <div className="flex justify-between items-start gap-3 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold">Plan my prep</h2>
            {isRunning && (
              <span
                className="stamp"
                style={{
                  color: GREEN_INK,
                  backgroundColor: GREEN_TINT_BG,
                  borderColor: GREEN_TINT_BORDER,
                }}
              >
                <span
                  aria-hidden="true"
                  className="inline-block w-1.5 h-1.5 rounded-full motion-safe:animate-pulse"
                  style={{ backgroundColor: GREEN }}
                />
                Thinking
              </span>
            )}
          </div>
          <p className="text-xs text-ink-500 mt-0.5 max-w-[60ch]">
            An agent with five tools — target list, contact history, news,
            competitors, brief generation — decides the priority order and the
            reasoning behind it. Each tool call streams live below.
          </p>
        </div>
        <button onClick={run} disabled={isRunning} className="btn-primary text-sm shrink-0">
          {isRunning
            ? `Thinking… ${elapsedS}s`
            : isDone || hasError
            ? "Re-run agent"
            : "Run agent"}
        </button>
      </div>

      {(isRunning || state.toolCalls.length > 0) && (
        <div className="mt-3 pt-3 border-t border-ink-100">
          <div className="rule-label mb-2">
            Trace · {state.toolCalls.length} tool call
            {state.toolCalls.length === 1 ? "" : "s"}
          </div>
          <div className="space-y-1 max-h-64 overflow-y-auto pr-1">
            {state.toolCalls.map((tc, i) => (
              <div
                key={i}
                className="flex items-start gap-2.5 text-xs py-1.5 border-b border-ink-100 last:border-0"
              >
                <span className="font-mono text-[10px] text-ink-500 shrink-0 mt-0.5 tabular-nums">
                  {String(tc.iteration).padStart(2, "0")}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="label !text-ink-900 !tracking-[0.08em] normal-case">
                      {tc.name}
                    </span>
                    {tc.in_progress && (
                      <span
                        aria-hidden="true"
                        className="inline-block w-1.5 h-1.5 rounded-full motion-safe:animate-pulse"
                        style={{ backgroundColor: GREEN }}
                      />
                    )}
                  </div>
                  <div className="text-ink-600 mt-0.5">{tc.result_summary}</div>
                </div>
              </div>
            ))}
            {isRunning && state.toolCalls.length === 0 && (
              <div className="text-xs text-ink-500 flex items-center gap-2">
                <span
                  className="inline-block w-1.5 h-1.5 rounded-full motion-safe:animate-pulse"
                  style={{ backgroundColor: GREEN }}
                />
                Agent starting…
              </div>
            )}
          </div>
        </div>
      )}

      {hasError && (
        <div className="mt-3 text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
          {state.errorMessage}
        </div>
      )}

      {state.plan && <PlanView plan={state.plan} />}
    </section>
  );
}

function PlanView({ plan }: { plan: AgentPlan & { raw_text?: string } }) {
  if (plan.raw_text && !plan.priority_order) {
    return (
      <div className="mt-3 pt-3 border-t border-ink-100 text-xs">
        <div className="label mb-1">Agent output</div>
        <pre className="whitespace-pre-wrap text-ink-700 font-sans">{plan.raw_text}</pre>
      </div>
    );
  }
  return (
    <div className="mt-3 pt-3 border-t border-ink-100 space-y-3">
      {plan.summary && (
        <div className="text-sm text-ink-700 italic">"{plan.summary}"</div>
      )}
      {plan.priority_order && plan.priority_order.length > 0 && (
        <div>
          <div className="label mb-1.5">
            Priority order ({plan.priority_order.length})
          </div>
          <div className="space-y-1">
            {plan.priority_order.map((p, i) => (
              <div
                key={i}
                className="flex items-start gap-2.5 text-xs py-1.5 border-b border-ink-100 last:border-0"
              >
                <span
                  className="font-mono text-[10px] shrink-0 mt-0.5 tabular-nums font-semibold"
                  style={{ color: GREEN_INK }}
                >
                  {String(p.priority).padStart(2, "0")}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-semibold text-ink-900">{p.person_name}</span>
                    <span className="text-ink-500">@ {p.company}</span>
                    {p.has_brief && (
                      <span
                        className="stamp"
                        style={{
                          color: GREEN_INK,
                          backgroundColor: GREEN_TINT_BG,
                          borderColor: GREEN_TINT_BORDER,
                        }}
                      >
                        brief
                      </span>
                    )}
                  </div>
                  <div className="text-ink-700 mt-0.5">{p.reason}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {plan.competitor_flags && plan.competitor_flags.length > 0 && (
        <div className="text-xs">
          <span className="label">Competitors at this event</span>
          <span className="text-ink-700"> — {plan.competitor_flags.join(", ")}</span>
        </div>
      )}
      {plan.positioning_notes && plan.positioning_notes.length > 0 && (
        <div className="text-xs">
          <span className="label">Positioning</span>
          <ul className="list-disc list-inside text-ink-700 mt-1">
            {plan.positioning_notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}
      {plan.skipped_with_reason && plan.skipped_with_reason.length > 0 && (
        <details className="text-xs">
          <summary className="label cursor-pointer">
            Skipped ({plan.skipped_with_reason.length})
          </summary>
          <div className="mt-1 space-y-0.5 text-ink-600">
            {plan.skipped_with_reason.map((s, i) => (
              <div key={i}>
                <span className="font-medium text-ink-900">{s.person}</span> — {s.reason}
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
