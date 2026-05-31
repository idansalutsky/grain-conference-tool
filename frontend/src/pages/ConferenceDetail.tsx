import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TierBadge, PersonaBadge } from "@/components/Badges";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import { AgentRunner } from "@/components/AgentRunner";
import { InlineReason } from "@/components/InlineReason";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useToast, toastErrorMessage } from "@/components/Toast";

export function ConferenceDetailPage() {
  const { id } = useParams();
  const { push: toast } = useToast();
  // Which score delta is currently being argued (its reason input is open).
  const [arguingDelta, setArguingDelta] = useState<number | null>(null);
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
  // Prep is handled entirely by the streaming agent (see <AgentRunner />) — a
  // single mechanism, not an agent + a deterministic duplicate. The deterministic
  // /api/briefs/prep endpoint still exists server-side; we just don't surface a
  // competing card for it.

  // HIL: human can argue with the 7-factor score (e.g. "this event matters
  // more than the model thinks because we landed 2 deals here in 2024").
  const adjustScore = useMutation({
    mutationFn: ({ delta, reason }: { delta: number; reason: string }) =>
      api.post<any>(`/api/conferences/${id}/score/adjust`, {
        delta, reason, decided_by: "ui",
      }),
    onSuccess: (d) => {
      conf.refetch();
      setArguingDelta(null);
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
  // Verified-first within each persona — confirmed leads surface above AI-surfaced ones.
  for (const k of Object.keys(byPersona)) {
    byPersona[k].sort((a, b) => Number(!!b.verified) - Number(!!a.verified));
  }
  const order = ["BUYER", "CHAMPION", "PAIN_OWNER", "ENTRY_POINT", "GATEKEEPER", "INFLUENCER"];
  const verifiedCount = items.filter((p) => p.verified).length;

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

      {/* The event page follows the rep's journey top-to-bottom:
          DECIDE (why this event — score + intel) · PLAN (who covers it) ·
          PREP (committee + pre-event briefs) · AFTER (follow-ups). */}
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
                    onClick={() => setArguingDelta((cur) => (cur === d ? null : d))}
                    disabled={adjustScore.isPending}
                    aria-pressed={arguingDelta === d}
                    className={
                      "btn-secondary text-xs px-2 py-1 " +
                      (arguingDelta === d ? "bg-ink-100 border-ink-300" : "")
                    }
                  >
                    {d > 0 ? `+${d}` : d}
                  </button>
                ))}
              </div>
              <InlineReason
                open={arguingDelta !== null}
                title={
                  arguingDelta !== null
                    ? `Reason for ${arguingDelta > 0 ? "+" : ""}${arguingDelta} score`
                    : ""
                }
                placeholder={
                  arguingDelta !== null && arguingDelta > 0
                    ? "high-value past attendance"
                    : "underwhelming agenda"
                }
                confirmLabel="Apply adjustment"
                pending={adjustScore.isPending}
                onConfirm={(reason) => {
                  if (arguingDelta !== null) {
                    adjustScore.mutate({ delta: arguingDelta, reason });
                  }
                }}
                onCancel={() => setArguingDelta(null)}
              />
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

          <section className="card p-4">
            <div className="flex justify-between items-baseline mb-1">
              <h2 className="label">Buying committee — who to approach</h2>
              {verifiedCount > 0 ? (
                <span className="text-xs text-ink-500">
                  {verifiedCount} verified · {items.length} total
                </span>
              ) : items.length > 0 ? (
                <span className="text-xs text-ink-500">
                  {items.length} AI-surfaced lead{items.length === 1 ? "" : "s"} · verify before approaching
                </span>
              ) : null}
            </div>
            {items.length > 0 && (
              <p className="text-xs text-ink-500 mb-3">
                <span style={{ color: "oklch(0.45 0.11 158)" }}>✓ verified</span> = confirmed
                against the live web by the agent today. Others are AI-surfaced leads —
                verify before you approach (public attendee data goes stale fast).
              </p>
            )}
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
              <EmptyCommittee raw={c.audience_composition_json} />
            )}
          </section>
        </div>
      </div>

      {/* AFTER the event — the wrap-up lives last, where it belongs in the flow. */}
      {id && <PostEventFollowups conferenceId={id} conferenceName={c.name} />}
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

// Empty buying-committee state — grounded in the MEASURED audience signal we
// already have (audience_composition_json), not a blank. Honest about why we
// don't pre-scrape named attendees, and points at the real next step that
// already lives on this page (Coverage → per-rep Telegram capture link).
function EmptyCommittee({ raw }: { raw?: string | null }) {
  let comp: any = null;
  if (raw) {
    try { comp = typeof raw === "string" ? JSON.parse(raw) : raw; } catch { comp = null; }
  }
  const fin: number | null = comp?.cfo_treasury_finance_pct ?? null;
  const commercial: number | null = comp?.marketing_sales_pct ?? null;
  // Frame to the number: high finance density → buyers are here; otherwise lead
  // with the commercial / entry-point crowd. Honest to whatever the data says.
  const buyerHeavy = fin != null && fin >= 40;
  let headline: string;
  if (buyerHeavy) {
    headline = `This event draws a ${fin}% finance / treasury audience — the buyers are in the room.`;
  } else if (commercial != null && commercial > 0) {
    headline = `Lighter on treasury, but ~${commercial}% commercial — work the entry points to get to the buyer.`;
  } else if (fin != null) {
    headline = `Measured at ${fin}% finance / treasury — read the audience mix before you commit a rep.`;
  } else {
    headline = "Named attendees are mapped in the field, not pre-scraped.";
  }

  return (
    <div className="rounded-md border border-ink-200 bg-ink-50/40 p-4">
      <p className="text-sm font-semibold text-ink-900">{headline}</p>
      <p className="text-xs text-ink-500 mt-1.5 max-w-[58ch]">
        We haven't mapped named attendees yet — committee intel is captured in the
        field, not pre-scraped (public attendee data goes stale fast). Here's how
        to work it:
      </p>
      <div className="flex flex-wrap gap-2 mt-3">
        <a
          href="#coverage"
          onClick={(e) => {
            e.preventDefault();
            document.getElementById("coverage")?.scrollIntoView({ behavior: "smooth", block: "start" });
          }}
          className="btn-primary text-xs"
        >
          ↓ Assign a rep & get their Telegram capture link
        </a>
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
    <section id="coverage" className="card p-4 sm:p-5 mb-6 scroll-mt-4">
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
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="text-ink-500">Send to {a.rep_name?.split(" ")[0]} — connects them for this event:</span>
                    <button
                      className="btn-ghost h-6 !px-2 text-xs shrink-0"
                      onClick={() => {
                        const msg = `Connect your Telegram for ${conferenceName} — tap on your phone: ${links[a.rep_id].deep_link}`;
                        navigator.clipboard?.writeText(msg)
                          .then(() => toast("success", `Copied — send it to ${a.rep_name?.split(" ")[0]}`))
                          .catch(() => toast("error", "Couldn't copy"));
                      }}
                    >📋 Copy</button>
                  </div>
                  <a href={links[a.rep_id].deep_link} target="_blank" rel="noreferrer"
                     className="text-brand break-all hover:underline">{links[a.rep_id].deep_link}</a>
                  <div className="text-ink-500 mt-1">@{links[a.rep_id].bot_username || "GrainSales_bot"} · one-time · binds to this event</div>
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
  // Which persona is being justified (its reason input is open).
  const [pendingPersona, setPendingPersona] = useState<string | null>(null);
  const override = useMutation({
    mutationFn: ({ persona, reason }: { persona: string; reason: string }) =>
      api.post<any>(`/api/people/${p.id}/icp/override`, {
        persona, reason, decided_by: "ui:conference_detail",
      }),
    onSuccess: () => {
      toast("success", "Persona overridden");
      setPendingPersona(null);
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
        <div className="absolute right-0 mt-1 bg-white rounded shadow-lg border border-ink-200 p-2 z-10 w-64">
          <div className="text-[10px] uppercase text-ink-500 mb-1">Override persona</div>
          <div className="flex flex-wrap gap-0.5">
            {PERSONA_OVERRIDES.map((k) => (
              <button
                key={k}
                onClick={() => setPendingPersona((cur) => (cur === k ? null : k))}
                disabled={override.isPending}
                aria-pressed={pendingPersona === k}
                className={
                  "px-1.5 py-0.5 text-[10px] rounded border " +
                  (p.persona === k
                    ? "bg-brand text-white border-brand"
                    : pendingPersona === k
                    ? "bg-ink-100 text-ink-900 border-ink-300"
                    : "bg-ink-50 text-ink-700 border-ink-200 hover:bg-ink-100")
                }
              >
                {k.replace("_", " ")}
              </button>
            ))}
          </div>
          <InlineReason
            open={pendingPersona !== null}
            title={pendingPersona ? `Why classify as ${pendingPersona.replace("_", " ")}?` : ""}
            placeholder="rep judgment on the ground"
            confirmLabel="Override"
            pending={override.isPending}
            onConfirm={(reason) => {
              if (pendingPersona) override.mutate({ persona: pendingPersona, reason });
            }}
            onCancel={() => setPendingPersona(null)}
          />
        </div>
      </details>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Post-event follow-ups — draft for everyone met here (event + conversation
// grounded), edit in place, then push the batch to HubSpot. Draft-and-review:
// nothing is sent automatically.
// ---------------------------------------------------------------------------
interface Draft {
  contact_id: string; encounter_id: string; name: string;
  company?: string; title?: string; subject: string; body: string;
  event_name?: string; recommended: boolean; is_repeat: boolean;
  arc?: string | null;
}

function PostEventFollowups({ conferenceId, conferenceName }: { conferenceId: string; conferenceName: string }) {
  const { push: toast } = useToast();
  const [drafts, setDrafts] = useState<Draft[] | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});

  const draftAll = useMutation({
    mutationFn: () => api.post<{ drafts: Draft[]; count: number; recommended_count: number }>(
      `/api/followups/event/${conferenceId}`),
    onSuccess: (d) => {
      setDrafts(d.drafts);
      setEdits({});
      toast("success", `Drafted ${d.count} follow-up${d.count === 1 ? "" : "s"}`);
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const saveEdit = useMutation({
    mutationFn: (d: Draft) =>
      api.put("/api/followups/draft", { encounter_id: d.encounter_id, body: edits[d.encounter_id] ?? d.body }),
    onSuccess: () => toast("success", "Draft saved"),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const pushAll = useMutation({
    mutationFn: () => api.post<{ pushed: number; skipped: number; failed: number }>(
      `/api/hubspot/push-event/${conferenceId}`),
    onSuccess: (d) =>
      toast("success", `HubSpot: ${d.pushed} pushed, ${d.skipped} skipped (no email), ${d.failed} failed`),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  return (
    <section className="card p-4 sm:p-5 mb-6">
      <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
        <div>
          <h2 className="text-base font-semibold">After the event — follow-ups</h2>
          <p className="text-xs text-ink-500 mt-0.5 max-w-[64ch]">
            Draft a follow-up for everyone met at{" "}
            <span className="text-ink-700 font-medium">{conferenceName}</span> — each
            one references the event and what was actually discussed. Edit, then
            push the batch to HubSpot. Nothing sends automatically.
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn-primary" disabled={draftAll.isPending}
                  onClick={() => draftAll.mutate()}>
            {draftAll.isPending ? "Drafting…" : "✍️ Draft follow-ups"}
          </button>
          {drafts && drafts.length > 0 && (
            <button className="btn-secondary" disabled={pushAll.isPending}
                    onClick={() => pushAll.mutate()}>
              {pushAll.isPending ? "Syncing…" : "⇪ Push all to HubSpot"}
            </button>
          )}
        </div>
      </div>

      {drafts && drafts.length === 0 && (
        <div className="text-sm text-ink-500">No captured contacts at this event yet.</div>
      )}

      {drafts && drafts.length > 0 && (
        <div className="space-y-3">
          {drafts.map((d) => (
            <div key={d.encounter_id} className="rounded-md border border-ink-200 p-3">
              <div className="flex items-center gap-2 flex-wrap mb-1.5">
                <span className="text-sm font-semibold">{d.name}</span>
                {d.company && <span className="text-xs text-ink-500">· {d.company}</span>}
                {!d.recommended && (
                  <span className="text-[0.6rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-ink-100 text-ink-600">
                    low priority{d.arc ? ` · ${d.arc}` : ""}
                  </span>
                )}
                {d.is_repeat && (
                  <span className="text-[0.6rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700">
                    repeat contact
                  </span>
                )}
              </div>
              <div className="text-xs text-ink-500 mb-1">Subject: {d.subject}</div>
              <textarea
                className="input w-full h-28 resize-none text-sm"
                value={edits[d.encounter_id] ?? d.body}
                onChange={(e) => setEdits((p) => ({ ...p, [d.encounter_id]: e.target.value }))}
              />
              <div className="flex justify-end mt-2">
                <button className="btn-ghost h-8 text-xs"
                        disabled={saveEdit.isPending}
                        onClick={() => saveEdit.mutate(d)}>
                  Save edit
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
