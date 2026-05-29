import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TierBadge, PersonaBadge } from "@/components/Badges";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import { AgentRunner } from "@/components/AgentRunner";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useToast, toastErrorMessage } from "@/components/Toast";

export function ConferenceDetailPage() {
  const { id } = useParams();
  const { push: toast } = useToast();
  const conf = useQuery({
    queryKey: ["conference", id],
    queryFn: () => api.get<any>(`/api/conferences/${id}`),
    enabled: !!id,
  });
  useDocumentTitle(conf.data?.name || "Conference");
  const targets = useQuery({
    queryKey: ["people", id],
    queryFn: () =>
      api.get<{ items: any[] }>(`/api/people`, { query: { conference_id: id, limit: 50 } }),
    enabled: !!id,
  });
  const prep = useMutation({
    mutationFn: () =>
      api.post<any>("/api/briefs/prep", { conference_id: id, top_n: 5 }),
    onSuccess: (d) =>
      toast("success", `Generated ${d?.prepared ?? 0} brief${d?.prepared === 1 ? "" : "s"}`),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  // Per-event Telegram bind — generates a connect link that, when redeemed
  // via /start, marks this conference as the rep's active event. All
  // subsequent voice memos and texts auto-tag here.
  const tgBind = useMutation({
    mutationFn: () =>
      api.post<any>("/api/telegram/issue-token", {
        rep_id: "rep-na-01",
        conference_id: id,
      }),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  // Manual fast-set: if the rep is already bound, just flip active_conference
  const tgFastSet = useMutation({
    mutationFn: () =>
      api.put<any>("/api/telegram/active-event", {
        rep_id: "rep-na-01",
        conference_id: id,
      }),
  });

  // Agent runner is now a self-contained streaming component (see <AgentRunner />)
  // — removed the old useMutation since we stream via EventSource instead.

  // HIL: human can argue with the 7-factor score (e.g. "this event matters
  // more than the model thinks because we landed 2 deals here in 2024").
  const adjustScore = useMutation({
    mutationFn: ({ delta, reason }: { delta: number; reason: string }) =>
      api.post<any>(`/api/conferences/${id}/score/adjust`, {
        delta, reason, decided_by: "ui",
      }),
    onSuccess: (d) => {
      conf.refetch();
      toast(
        "success",
        `Score ${d.delta > 0 ? "+" : ""}${d.delta} → ${d.score?.toFixed(1)} (tier ${d.tier})`,
      );
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  if (conf.isLoading) return <div className="text-sm text-ink-500">Loading…</div>;
  if (conf.error) return <div className="card p-4 text-red-700 text-sm">Error</div>;
  if (!conf.data) return null;

  const c = conf.data;
  const items = targets.data?.items || [];

  // Group people by persona for the buying-committee view
  const byPersona: Record<string, any[]> = {};
  for (const p of items) {
    const k = p.persona || "OTHER";
    (byPersona[k] = byPersona[k] || []).push(p);
  }
  const order = ["BUYER", "CHAMPION", "PAIN_OWNER", "ENTRY_POINT", "GATEKEEPER", "INFLUENCER"];

  return (
    <div>
      <Link to="/conferences" className="text-xs text-ink-500 hover:text-ink-900">
        ← All conferences
      </Link>
      <div className="flex items-center gap-3 mt-1 mb-2">
        <h1 className="text-2xl">{c.name}</h1>
        <TierBadge tier={c.tier} />
      </div>
      <div className="text-sm text-ink-500 mb-4">
        {c.start_date} → {c.end_date || c.start_date} · {c.city}, {c.country} · {c.format} · {c.vertical}
        {c.website && (
          <>
            {" · "}
            <a href={c.website} target="_blank" rel="noreferrer" className="text-brand hover:underline">
              official site ↗
            </a>
          </>
        )}
      </div>

      {/* Per-event Telegram bind — the "I'm here, every memo from now on
          tags to this event" flow. One tap. */}
      <section className="card p-4 mb-6 bg-blue-50 border-blue-200">
        <div className="flex justify-between items-center gap-3 flex-wrap">
          <div>
            <h2 className="text-sm font-semibold text-blue-900">
              📱 Capture FROM the floor — auto-tag to this event
            </h2>
            <p className="text-xs text-blue-800 mt-0.5">
              Generate a one-tap Telegram link. Once you redeem it on your
              phone, every voice memo + text auto-attributes to{" "}
              <span className="font-semibold">{c.name}</span> — no
              dropdown, no typing.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => tgFastSet.mutate()}
              disabled={tgFastSet.isPending}
              className="btn-secondary text-xs"
            >
              {tgFastSet.isPending
                ? "Setting…"
                : tgFastSet.data
                ? "✓ I'm here"
                : "Mark as active event"}
            </button>
            <button
              onClick={() => tgBind.mutate()}
              disabled={tgBind.isPending}
              className="btn-primary text-xs"
            >
              {tgBind.isPending ? "Generating…" : "📱 Get Telegram link"}
            </button>
          </div>
        </div>
        {tgBind.data && (
          <div className="mt-3 pt-3 border-t border-blue-200 text-xs">
            <div className="text-blue-900 font-medium mb-1">
              Open this on your phone:
            </div>
            <a
              href={tgBind.data.deep_link}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-brand break-all hover:underline"
            >
              {tgBind.data.deep_link}
            </a>
            <div className="text-blue-800 mt-1">
              Bot: <span className="font-mono">@{tgBind.data.bot_username || "GrainSales_bot"}</span>
              {" · "}token is one-time use
            </div>
          </div>
        )}
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-1 space-y-4">
          <section className="card p-4">
            <h2 className="label mb-2">Score breakdown</h2>
            <ScoreBreakdown breakdown={c.score_breakdown} />
            <div className="mt-3 pt-3 border-t border-ink-100">
              <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1.5">
                Argue with the score
              </div>
              <div className="flex gap-1 flex-wrap">
                {[-5, -2, +2, +5].map((d) => (
                  <button
                    key={d}
                    onClick={() => {
                      const reason = window.prompt(
                        `Reason for ${d > 0 ? "+" : ""}${d} score?`,
                        d > 0 ? "high-value past attendance" : "underwhelming agenda",
                      );
                      if (reason !== null && reason.trim()) {
                        adjustScore.mutate({ delta: d, reason: reason.trim() });
                      }
                    }}
                    disabled={adjustScore.isPending}
                    className="btn-secondary text-xs px-2 py-1"
                  >
                    {d > 0 ? `+${d}` : d}
                  </button>
                ))}
              </div>
              <p className="text-[10px] text-ink-500 mt-1.5 italic">
                Logged with your reason — auditable later.
              </p>
            </div>
          </section>
          <section className="card p-4 space-y-2">
            <h2 className="label">Event facts</h2>
            <div className="text-xs space-y-1 text-ink-700">
              <div>Themes: <span className="text-ink-500">{c.themes || "—"}</span></div>
              <div>Attendance estimate: {c.estimated_attendance?.toLocaleString() || "—"}</div>
              <div>Conference pass: {c.cost_pass_usd ? `$${c.cost_pass_usd}` : "—"}</div>
              <div>Booth: {c.cost_booth_usd ? `$${c.cost_booth_usd}` : "—"}</div>
            </div>
          </section>
        </div>

        <div className="lg:col-span-2 space-y-4">
          {id && <AgentRunner conferenceId={id} />}

          {/* === Deterministic fallback (kept for cheap fixed-order prep) === */}
          <section className="card p-4 bg-emerald-50 border-emerald-200">
            <div className="flex justify-between items-center">
              <div>
                <h2 className="text-sm font-semibold text-emerald-900">
                  Deterministic prep — top 5 by persona weight
                </h2>
                <p className="text-xs text-emerald-800 mt-0.5">
                  Fixed-order alternative if you don't need the agent's judgment.
                </p>
              </div>
              <button
                onClick={() => prep.mutate()}
                disabled={prep.isPending}
                className="btn-primary text-sm"
              >
                {prep.isPending ? "Generating…" : "📄 Quick prep"}
              </button>
            </div>
            {prep.data && (
              <div className="mt-3 space-y-2 border-t border-emerald-200 pt-3">
                <div className="text-xs text-emerald-900 font-semibold">
                  {prep.data.prepared} brief{prep.data.prepared === 1 ? "" : "s"} generated
                </div>
                {prep.data.briefs?.map((b: any) => (
                  <div key={b.person_id} className="bg-white rounded p-2 text-xs">
                    <div className="font-semibold">
                      {b.full_name} — {b.title || "?"} @ {b.company}
                    </div>
                    {b.error ? (
                      <div className="text-red-700 mt-1">Error: {b.error}</div>
                    ) : (
                      <>
                        <div className="text-ink-700 mt-1 italic">"{(b.fx_angle || "").slice(0, 200)}"</div>
                        <div className="text-ink-500 mt-1">
                          📰 {b.trigger_news_count} trigger news item(s)
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
            {prep.error && (
              <div className="text-xs text-red-700 mt-2">{toastErrorMessage(prep.error)}</div>
            )}
          </section>

          <section className="card p-4">
            <div className="flex justify-between items-baseline mb-3">
              <h2 className="label">Buying committee — discovered targets</h2>
              <span className="text-xs text-ink-500">{items.length} people</span>
            </div>
            {order.map((k) => {
              const list = byPersona[k];
              if (!list?.length) return null;
              return (
                <div key={k} className="mb-4 last:mb-0">
                  <div className="flex items-center gap-2 mb-1">
                    <PersonaBadge persona={k} />
                    <span className="text-xs text-ink-500">{list.length}</span>
                  </div>
                  <div className="space-y-1">
                    {list.slice(0, 8).map((p) => (
                      <PersonRow key={p.id} p={p} onAfterOverride={() => targets.refetch()} />
                    ))}
                  </div>
                </div>
              );
            })}
            {items.length === 0 && (
              <div className="text-sm text-ink-500">No targets discovered yet for this event.</div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

const PERSONA_OVERRIDES = [
  "BUYER", "CHAMPION", "PAIN_OWNER", "ENTRY_POINT", "GATEKEEPER", "INFLUENCER",
];

function PersonRow({ p, onAfterOverride }: { p: any; onAfterOverride: () => void }) {
  const { push: toast } = useToast();
  const override = useMutation({
    mutationFn: ({ persona, reason }: { persona: string; reason: string }) =>
      api.post<any>(`/api/people/${p.id}/icp/override`, {
        persona, reason, decided_by: "ui:conference_detail",
      }),
    onSuccess: () => {
      toast("success", "Persona overridden");
      onAfterOverride();
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });
  return (
    <div className="flex justify-between items-start text-sm group">
      <div className="flex-1 min-w-0">
        <div>
          <span className="font-medium">{p.full_name}</span>
          <span className="text-ink-500"> — {p.title || "?"}</span>
        </div>
        <div className="text-xs text-ink-500">
          {p.company_id ? (
            <Link to={`/companies/${p.company_id}`} className="hover:text-brand">
              {p.company_name}
            </Link>
          ) : (
            p.company_name
          )}
        </div>
      </div>
      <details className="ml-2 shrink-0 opacity-30 group-hover:opacity-100 transition-opacity">
        <summary className="text-xs text-ink-500 cursor-pointer hover:text-ink-900 list-none">
          ⋯
        </summary>
        <div className="absolute right-0 mt-1 bg-white rounded shadow-lg border border-ink-200 p-2 z-10 w-44">
          <div className="text-[10px] uppercase text-ink-500 mb-1">Override persona</div>
          <div className="flex flex-wrap gap-0.5">
            {PERSONA_OVERRIDES.map((k) => (
              <button
                key={k}
                onClick={() => {
                  const reason = window.prompt(
                    `Why classify as ${k}?`,
                    "rep judgment on the ground",
                  );
                  if (reason?.trim()) {
                    override.mutate({ persona: k, reason: reason.trim() });
                  }
                }}
                disabled={override.isPending}
                className={
                  "px-1.5 py-0.5 text-[10px] rounded border " +
                  (p.persona === k
                    ? "bg-brand text-white border-brand"
                    : "bg-ink-50 text-ink-700 border-ink-200 hover:bg-ink-100")
                }
              >
                {k.replace("_", " ")}
              </button>
            ))}
          </div>
        </div>
      </details>
    </div>
  );
}
