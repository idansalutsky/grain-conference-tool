import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

      {id && <Coverage conferenceId={id} conferenceName={c.name} />}

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
          <section className="card p-4 space-y-3">
            <h2 className="label">Event intel</h2>
            {c.agenda_summary && (
              <p className="text-xs text-ink-700 leading-relaxed">{c.agenda_summary}</p>
            )}
            <AudienceMix raw={c.audience_composition_json} />
            <div className="text-xs space-y-1 text-ink-700 pt-1">
              <div>Attendance estimate: {c.estimated_attendance?.toLocaleString() || "—"}</div>
              <div>Conference pass: {c.cost_pass_usd ? `$${c.cost_pass_usd}` : "—"}</div>
              <div>Booth: {c.cost_booth_usd ? `$${c.cost_booth_usd}` : "—"}</div>
            </div>
            {c.source_url && (
              <a href={c.source_url} target="_blank" rel="noreferrer"
                 className="text-xs text-brand hover:underline">data source ↗</a>
            )}
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
            <div className="flex justify-between items-baseline mb-1">
              <h2 className="label">Buying committee — who to approach</h2>
              <span className="text-xs text-ink-500">
                {items.filter((p) => p.verified).length} verified · {items.length} total
              </span>
            </div>
            <p className="text-xs text-ink-500 mb-3">
              <span style={{ color: "oklch(0.45 0.11 158)" }}>✓ verified</span> = confirmed
              against the live web by the agent today. Others are AI-surfaced leads —
              verify before you approach (public attendee data goes stale fast).
            </p>
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

// Audience composition — the measured buyer-density signal (grounded), shown
// as a stacked bar that leads with finance/treasury %.
function AudienceMix({ raw }: { raw?: string | null }) {
  if (!raw) return null;
  let c: any;
  try { c = typeof raw === "string" ? JSON.parse(raw) : raw; } catch { return null; }
  const fin = c.cfo_treasury_finance_pct;
  if (fin == null) return null;
  const segs = [
    { pct: fin, color: "oklch(0.6 0.13 158)", label: "Finance / treasury" },
    { pct: c.engineering_product_pct || 0, color: "oklch(0.62 0.1 245)", label: "Eng / product" },
    { pct: c.marketing_sales_pct || 0, color: "oklch(0.66 0.12 62)", label: "Marketing / sales" },
    { pct: c.other_pct || 0, color: "oklch(0.82 0.012 160)", label: "Other" },
  ].filter((s) => s.pct > 0);
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <span className="label">Audience (measured)</span>
        <span className="text-xs font-semibold" style={{ color: "oklch(0.45 0.11 158)" }}>
          {fin}% finance / treasury
        </span>
      </div>
      <div className="flex h-2.5 rounded overflow-hidden bg-ink-100" title="Scraped audience composition">
        {segs.map((s) => <div key={s.label} style={{ flex: s.pct, background: s.color }} title={`${s.label}: ${s.pct}%`} />)}
      </div>
    </div>
  );
}

// Coverage — who's working this event, with a per-rep one-tap Telegram bind.
// This is the brief's "who covers what" + the field-capture channel, together.
function Coverage({ conferenceId, conferenceName }: { conferenceId: string; conferenceName: string }) {
  const { push: toast } = useToast();
  const qc = useQueryClient();
  const [links, setLinks] = useState<Record<string, any>>({});
  const [pick, setPick] = useState("");

  const reps = useQuery({ queryKey: ["reps"], queryFn: () => api.get<{ items: any[] }>("/api/reps") });
  const cov = useQuery({
    queryKey: ["coverage", conferenceId],
    queryFn: () => api.get<{ items: any[] }>("/api/coverage", { query: { conference_id: conferenceId } }),
  });

  const assigned = cov.data?.items ?? [];
  const assignedIds = new Set(assigned.map((a) => a.rep_id));
  const available = (reps.data?.items ?? []).filter((r) => !assignedIds.has(r.id));

  const assign = useMutation({
    mutationFn: (repId: string) => api.post("/api/coverage", { conference_id: conferenceId, rep_id: repId }),
    onSuccess: () => { setPick(""); qc.invalidateQueries({ queryKey: ["coverage", conferenceId] }); },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });
  const unassign = useMutation({
    mutationFn: (repId: string) =>
      api.delete(`/api/coverage?conference_id=${conferenceId}&rep_id=${repId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["coverage", conferenceId] }),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });
  const bind = useMutation({
    mutationFn: (repId: string) =>
      api.post<any>("/api/telegram/issue-token", { rep_id: repId, conference_id: conferenceId }),
    onSuccess: (d, repId) => setLinks((m) => ({ ...m, [repId]: d })),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  return (
    <section className="card p-4 sm:p-5 mb-6">
      <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
        <div>
          <h2 className="text-base font-semibold">Coverage</h2>
          <p className="text-xs text-ink-500 mt-0.5 max-w-[60ch]">
            Assign who's working <span className="text-ink-700 font-medium">{conferenceName}</span>.
            Each rep gets a one-tap Telegram link — once redeemed, every memo they
            send auto-tags to this event. No dropdowns on the floor.
          </p>
        </div>
        {available.length > 0 && (
          <div className="flex gap-2">
            <select className="input" value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">Assign a rep…</option>
              {available.map((r) => (
                <option key={r.id} value={r.id}>{r.full_name}{r.region ? ` · ${r.region}` : ""}</option>
              ))}
            </select>
            <button className="btn-primary" disabled={!pick || assign.isPending}
                    onClick={() => pick && assign.mutate(pick)}>Assign</button>
          </div>
        )}
      </div>

      {assigned.length === 0 ? (
        <div className="text-sm text-ink-500">
          No one assigned yet. {available.length === 0 && <Link to="/team" className="text-brand hover:underline">Add reps →</Link>}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {assigned.map((a) => (
            <div key={a.rep_id} className="rounded-md border border-ink-200 p-3">
              <div className="flex items-center gap-2">
                <div className="grid place-items-center w-8 h-8 rounded-full bg-ink-100 text-ink-700 text-xs font-semibold shrink-0">
                  {(a.rep_name || "?").split(" ").map((p: string) => p[0]).slice(0, 2).join("")}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold truncate">{a.rep_name}</div>
                  <div className="text-[0.65rem] uppercase tracking-wider text-ink-500">{a.rep_region || "—"}</div>
                </div>
                <button className="btn-secondary h-8 text-xs" disabled={bind.isPending}
                        onClick={() => bind.mutate(a.rep_id)}>
                  {links[a.rep_id] ? "↻ New link" : "📱 Telegram"}
                </button>
                <button className="btn-ghost h-8 !px-2 text-ink-500 hover:text-tire" title="Remove from event"
                        onClick={() => unassign.mutate(a.rep_id)}>✕</button>
              </div>
              {links[a.rep_id] && (
                <div className="mt-2 pt-2 border-t border-ink-100 text-xs">
                  <div className="text-ink-500 mb-1">Open on {a.rep_name?.split(" ")[0]}'s phone:</div>
                  <a href={links[a.rep_id].deep_link} target="_blank" rel="noreferrer"
                     className="text-brand break-all hover:underline">{links[a.rep_id].deep_link}</a>
                  <div className="text-ink-500 mt-1">@{links[a.rep_id].bot_username || "GrainSales_bot"} · one-time</div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
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
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="font-medium">{p.full_name}</span>
          {p.verified ? (
            <span className="text-[0.6rem] font-bold uppercase tracking-wide px-1 py-0.5 rounded"
                  style={{ color: "oklch(0.45 0.11 158)", background: "oklch(0.95 0.04 158)" }}
                  title="Verified against the live web by the agent">✓ verified</span>
          ) : (
            <span className="text-[0.6rem] uppercase tracking-wide text-ink-500" title="AI-surfaced lead — verify before approaching">unverified</span>
          )}
          {p.linkedin_url && (
            <a href={p.linkedin_url} target="_blank" rel="noreferrer" className="text-brand text-xs hover:underline" onClick={(e) => e.stopPropagation()}>in↗</a>
          )}
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
