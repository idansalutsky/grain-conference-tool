import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TierBadge, ArcBadge } from "@/components/Badges";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
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

      {/* The event page is honest to the data we actually hold:
          DECIDE (why it scores + who's measurably in the room) ·
          PLAN (who covers it + their field-capture links) ·
          AFTER (results once worked). We don't pre-scrape a named attendee list —
          named people come from the field (Telegram), shown under Results. */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4">
        {/* WHY IT SCORES — compact, not half the screen. */}
        <section className="card p-4 lg:col-span-1">
          <div className="flex items-baseline gap-2 mb-3">
            <div className="masthead text-3xl leading-none">{c.score?.toFixed(0) ?? "—"}</div>
            <div className="text-sm text-ink-500">/ 100 · tier {c.tier}</div>
          </div>
          {c.score_breakdown
            ? <ScoreBreakdown breakdown={c.score_breakdown} compact />
            : <div className="text-xs text-ink-400">No breakdown.</div>}
          <div className="mt-3 pt-3 border-t border-ink-100">
            <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1.5">Argue with the score</div>
            <div className="flex gap-1 flex-wrap">
              {[-5, -2, +2, +5].map((d) => (
                <button key={d}
                        onClick={() => setArguingDelta((cur) => (cur === d ? null : d))}
                        disabled={adjustScore.isPending}
                        aria-pressed={arguingDelta === d}
                        className={"btn-secondary text-xs px-2 py-1 " + (arguingDelta === d ? "bg-ink-100 border-ink-300" : "")}>
                  {d > 0 ? `+${d}` : d}
                </button>
              ))}
            </div>
            <InlineReason
              open={arguingDelta !== null}
              title={arguingDelta !== null ? `Reason for ${arguingDelta > 0 ? "+" : ""}${arguingDelta} score` : ""}
              placeholder={arguingDelta !== null && arguingDelta > 0 ? "high-value past attendance" : "underwhelming agenda"}
              confirmLabel="Apply adjustment"
              pending={adjustScore.isPending}
              onConfirm={(reason) => { if (arguingDelta !== null) adjustScore.mutate({ delta: arguingDelta, reason }); }}
              onCancel={() => setArguingDelta(null)}
            />
          </div>
        </section>

        {/* WHO'S IN THE ROOM — the measured audience signal (grounded), not a scraped name list. */}
        <section className="card p-4 lg:col-span-2 space-y-3">
          <div className="flex items-baseline justify-between gap-2">
            <h2 className="label">Who's in the room</h2>
            <span className="text-xs text-ink-400">measured audience mix — named people are captured in the field</span>
          </div>
          <AudienceMix raw={c.audience_composition_json} />
          {c.agenda_summary && <p className="text-sm text-ink-700 leading-relaxed max-w-[74ch]">{c.agenda_summary}</p>}
          <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2 text-sm pt-1">
            <div><dt className="text-[0.65rem] uppercase tracking-wider text-ink-400">Dates</dt><dd className="text-ink-800">{c.start_date}{c.end_date && c.end_date !== c.start_date ? ` → ${c.end_date}` : ""}</dd></div>
            <div><dt className="text-[0.65rem] uppercase tracking-wider text-ink-400">Attendance</dt><dd className="text-ink-800 tabular-nums">{c.estimated_attendance?.toLocaleString() || "—"}</dd></div>
            <div><dt className="text-[0.65rem] uppercase tracking-wider text-ink-400">Pass</dt><dd className="text-ink-800 tabular-nums">{c.cost_pass_usd ? `$${Math.round(c.cost_pass_usd).toLocaleString()}` : "—"}</dd></div>
            <div><dt className="text-[0.65rem] uppercase tracking-wider text-ink-400">Booth</dt><dd className="text-ink-800 tabular-nums">{c.cost_booth_usd ? `$${Math.round(c.cost_booth_usd).toLocaleString()}` : "—"}</dd></div>
          </dl>
          {c.source_url && (
            <a href={c.source_url} target="_blank" rel="noreferrer" className="text-xs text-brand hover:underline">data source ↗</a>
          )}
        </section>
      </div>

      {/* PLAN — who covers it + their field-capture links. */}
      {id && <Coverage conferenceId={id} conferenceName={c.name} />}

      {/* AFTER the event — the results, then the follow-up agent, last in the flow. */}
      {id && <EventOutcomes conferenceId={id} />}
      {id && <PostEventFollowups conferenceId={id} conferenceName={c.name} />}
    </div>
  );
}

// Per-event results — every connection, brief, meeting and follow-up tied to this
// event, in one place (the manager's "what came out of it" view).
function EventOutcomes({ conferenceId }: { conferenceId: string }) {
  const { push: toast } = useToast();
  const [wrap, setWrap] = useState<any | null>(null);
  const { data } = useQuery({
    queryKey: ["outcomes", conferenceId],
    queryFn: () => api.get<any>(`/api/conferences/${conferenceId}/outcomes`),
  });
  const summarize = useMutation({
    mutationFn: () => api.post<any>(`/api/conferences/${conferenceId}/wrap`),
    onSuccess: (d) => { setWrap(d); toast("success", "Summary generated"); },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });
  if (!data) return null;
  const hasResults = data.encounters > 0;
  // Don't show a wall of zeros for an event nobody's worked yet — say so plainly.
  if (!hasResults) {
    return (
      <section className="card p-4 sm:p-5 mb-6">
        <h2 className="text-base font-semibold mb-0.5">Results — what happened here</h2>
        <p className="text-sm text-ink-500 max-w-[66ch]">
          Nothing captured yet. As reps work the floor and log connections from the field via
          Telegram, the people met, meetings and follow-ups appear here — then the summary and
          drafts are one click away.
        </p>
      </section>
    );
  }
  const stats = [
    { n: data.contacts, label: "connections made" },
    { n: data.meetings, label: "meetings booked" },
    { n: data.briefs, label: "briefs prepped" },
    { n: data.drafts, label: "follow-ups drafted" },
  ];
  const meetStamp = { color: "oklch(0.42 0.09 158)", background: "oklch(0.95 0.03 158)", borderColor: "oklch(0.86 0.05 158)" };
  return (
    <section className="card p-4 sm:p-5 mb-6">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <h2 className="text-base font-semibold mb-0.5">Results — what happened here</h2>
          <p className="text-xs text-ink-500 max-w-[64ch]">
            Every connection, brief, meeting and follow-up tied to this event, in one place.
          </p>
        </div>
        {hasResults && (
          <button className="btn-primary text-xs shrink-0" disabled={summarize.isPending}
                  onClick={() => summarize.mutate()}>
            {summarize.isPending ? "Summarising…" : "🧠 Generate event summary"}
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-ink-100">
        {stats.map((s) => (
          <div key={s.label} className="px-3 first:pl-0">
            <div className="masthead text-2xl leading-none text-ink-900">{s.n}</div>
            <div className="text-xs text-ink-500 mt-1">{s.label}</div>
          </div>
        ))}
      </div>

      {/* The generated narrative recap. */}
      {wrap && (
        <div className="mt-4 border-t border-ink-100 pt-3">
          <div className="rule-label mb-1">
            <span>Event summary{wrap.source === "deterministic" ? " (no-LLM recap)" : ""}</span>
          </div>
          <p className="text-sm text-ink-800 leading-relaxed max-w-[72ch]">{wrap.summary}</p>
          {wrap.urgent?.length > 0 && (
            <div className="mt-2">
              <div className="text-[0.65rem] uppercase tracking-wider text-ink-400 mb-1">Urgent</div>
              <ul className="text-sm text-ink-700 space-y-0.5">
                {wrap.urgent.map((u: string, i: number) => <li key={i}>• {u}</li>)}
              </ul>
            </div>
          )}
          {wrap.account_plays?.length > 0 && (
            <div className="mt-2">
              <div className="text-[0.65rem] uppercase tracking-wider text-ink-400 mb-1">Account plays</div>
              <ul className="text-sm text-ink-700 space-y-0.5">
                {wrap.account_plays.map((p: any, i: number) => (
                  <li key={i}>• {typeof p === "string" ? p : (p.account ? `${p.account}: ${p.play || p.note || ""}` : JSON.stringify(p))}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Who logged what — per-rep field activity (from Telegram). */}
      {hasResults && data.by_rep?.length > 0 && (
        <div className="mt-4 border-t border-ink-100 pt-3">
          <div className="rule-label mb-1.5">By rep — captured from the field</div>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
            {data.by_rep.map((r: any) => (
              <span key={r.rep} className="text-ink-700">
                <span className="font-semibold">{r.rep}</span>
                <span className="text-ink-500"> — {r.captures} logged{r.meetings ? `, ${r.meetings} meeting${r.meetings === 1 ? "" : "s"}` : ""}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {data.connections.length > 0 ? (
        <div className="mt-4 border-t border-ink-100 pt-3 space-y-1.5">
          <div className="rule-label">Connections</div>
          {data.connections.map((c: any) => (
            <div key={c.id} className="flex items-center gap-2 text-sm">
              <span className="text-[0.7rem] font-mono text-ink-400 w-14 shrink-0">{(c.captured_at || "").slice(5, 10)}</span>
              {c.contact_id ? (
                <Link to={`/contacts/${c.contact_id}`} className="font-medium hover:underline truncate shrink-0">{c.primary_name || "Unknown"}</Link>
              ) : (
                <span className="font-medium truncate shrink-0">{c.primary_name || "?"}</span>
              )}
              <span className="text-xs text-ink-500 truncate">{c.primary_company || ""}{c.what ? ` — ${c.what}` : ""}</span>
              {c.arc_verdict && <ArcBadge kind={c.arc_verdict} />}
              {c.meeting_requested ? (
                <span className="stamp ml-auto shrink-0" style={meetStamp}>meeting</span>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-ink-500 mt-4 border-t border-ink-100 pt-3">
          No connections captured here yet — they appear as reps capture in the field via Telegram.
        </p>
      )}
    </section>
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
          <h2 className="text-base font-semibold">After the event — the follow-up agent</h2>
          <p className="text-xs text-ink-500 mt-0.5 max-w-[64ch]">
            One press: the agent drafts a follow-up for everyone met at{" "}
            <span className="text-ink-700 font-medium">{conferenceName}</span> — each
            referencing the event and what was actually discussed, prioritised by who's
            worth chasing. Edit, then push the batch to HubSpot. Nothing sends automatically.
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn-primary" disabled={draftAll.isPending}
                  onClick={() => draftAll.mutate()}>
            {draftAll.isPending ? "Running…" : "🤖 Run the follow-up agent"}
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
