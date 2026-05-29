import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useToast, toastErrorMessage } from "@/components/Toast";

interface Insight {
  id: string;
  rep_id: string;
  kind: string;
  severity: "high" | "medium" | "low";
  title: string;
  body: string;
  suggested_action: string;
  evidence: Record<string, any>;
  status: "fresh" | "dismissed" | "acknowledged" | "actioned";
  created_at: string;
}

const KIND_LABELS: Record<string, { emoji: string; label: string }> = {
  follow_up_gap:        { emoji: "📤", label: "follow-up gap" },
  persona_gap:          { emoji: "🧩", label: "persona gap" },
  arc_regression:       { emoji: "📉", label: "arc regression" },
  yield_retrospective:  { emoji: "📊", label: "yield retro" },
  missed_opportunity:   { emoji: "⏰", label: "missed opportunity" },
  pattern_detection:    { emoji: "🔍", label: "pattern" },
  tire_kicker_review:   { emoji: "🪫", label: "tire-kicker review" },
  competitor_proximity: { emoji: "🎯", label: "competitor" },
};

const SEVERITY_STYLE: Record<string, string> = {
  high:   "border-red-300 bg-red-50",
  medium: "border-amber-300 bg-amber-50",
  low:    "border-blue-200 bg-blue-50",
};

const SEVERITY_BADGE: Record<string, string> = {
  high:   "bg-red-600 text-white",
  medium: "bg-amber-500 text-white",
  low:    "bg-blue-500 text-white",
};

interface Props {
  repId: string;
}

/**
 * "From the brain" — a periodic LLM synthesis of patterns the rep wouldn't
 * notice scrolling. Sits at the top of the Today page. Each insight has
 * dismiss / acknowledge / mark-as-done actions.
 */
export function BrainInsights({ repId }: Props) {
  const qc = useQueryClient();
  const { push: toast } = useToast();

  const insights = useQuery({
    queryKey: ["insights", repId],
    queryFn: () => api.get<{ items: Insight[] }>(`/api/insights?rep_id=${repId}`),
  });

  const synth = useMutation({
    mutationFn: () =>
      api.post<any>("/api/insights/synthesize", {
        rep_id: repId, lookback_days: 30,
      }),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["insights", repId] });
      toast(
        "success",
        d?.created
          ? `${d.created} new insight${d.created === 1 ? "" : "s"} from the brain`
          : "Brain pass complete — no new insights",
      );
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) =>
      api.post<any>(`/api/insights/${id}/${action}`, { decided_by: "ui" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["insights", repId] }),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const items = insights.data?.items || [];

  return (
    <section className="card p-4 border-2 border-indigo-300 bg-gradient-to-br from-indigo-50 to-purple-50">
      <div className="flex items-center justify-between gap-3 mb-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-indigo-900">
            🧠 From the brain
          </h2>
          <p className="text-xs text-indigo-800 mt-0.5">
            Periodic LLM pass over your recent activity. Surfaces leverage
            points you wouldn't notice scrolling — pattern detection, missed
            opportunities, follow-up gaps, persona coverage.
          </p>
        </div>
        <button
          onClick={() => synth.mutate()}
          disabled={synth.isPending}
          className="btn-primary text-xs shrink-0 bg-indigo-700 hover:bg-indigo-800"
        >
          {synth.isPending ? "Synthesizing… (~6s)" : "↻ Run brain pass"}
        </button>
      </div>

      {items.length === 0 ? (
        <div className="text-xs text-ink-500 italic">
          No fresh insights. Run a brain pass to synthesize from your recent activity.
        </div>
      ) : (
        <div className="space-y-2">
          {items.map((it) => (
            <div
              key={it.id}
              className={"rounded p-3 border-l-4 " + (SEVERITY_STYLE[it.severity] || SEVERITY_STYLE.low)}
            >
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className={"badge " + (SEVERITY_BADGE[it.severity] || SEVERITY_BADGE.low)}>
                  {it.severity}
                </span>
                <span className="text-xs text-ink-600">
                  {KIND_LABELS[it.kind]?.emoji} {KIND_LABELS[it.kind]?.label || it.kind}
                </span>
              </div>
              <div className="font-semibold text-sm text-ink-900">{it.title}</div>
              {it.body && (
                <p className="text-xs text-ink-700 mt-1">{it.body}</p>
              )}
              {it.suggested_action && (
                <p className="text-xs text-ink-900 mt-1 font-medium">
                  💡 {it.suggested_action}
                </p>
              )}
              <EvidenceLinks evidence={it.evidence} />
              <div className="flex gap-2 mt-2.5">
                <button
                  onClick={() => act.mutate({ id: it.id, action: "actioned" })}
                  disabled={act.isPending}
                  className="btn-primary text-[10px] py-1 px-2"
                >
                  ✓ Done it
                </button>
                <button
                  onClick={() => act.mutate({ id: it.id, action: "acknowledged" })}
                  disabled={act.isPending}
                  className="btn-secondary text-[10px] py-1 px-2"
                >
                  Acknowledged
                </button>
                <button
                  onClick={() => act.mutate({ id: it.id, action: "dismiss" })}
                  disabled={act.isPending}
                  className="btn-secondary text-[10px] py-1 px-2 text-ink-500"
                >
                  Dismiss
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function EvidenceLinks({ evidence }: { evidence: Record<string, any> }) {
  if (!evidence || Object.keys(evidence).length === 0) return null;
  const cids = evidence.contact_ids || [];
  const conf = evidence.conference_ids || [];
  const comps = evidence.companies || [];
  if (cids.length + conf.length + comps.length === 0) return null;
  return (
    <div className="text-[10px] text-ink-500 mt-1.5">
      Evidence:{" "}
      {cids.length > 0 && <span>{cids.length} contact{cids.length === 1 ? "" : "s"} · </span>}
      {conf.length > 0 && <span>{conf.length} conference{conf.length === 1 ? "" : "s"} · </span>}
      {comps.length > 0 && <span>{comps.slice(0, 3).join(", ")}{comps.length > 3 && "…"}</span>}
    </div>
  );
}
