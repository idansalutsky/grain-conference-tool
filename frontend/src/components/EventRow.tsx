import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TierBadge } from "@/components/Badges";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";

// One dense, expandable event row — shared by the Dashboard ("Where to invest")
// and the Events list, so they read identically. The header uses whatever the
// caller already has; the score breakdown + agenda + coverage are fetched lazily
// only when a row is opened (cheap — never on the whole list at once).
export interface EventRowData {
  id: string; name: string; score?: number | null; tier?: string | null;
  vertical?: string | null; city?: string | null; country?: string | null;
  start_date?: string | null;
  days_until?: number | null;
  cost_pass_usd?: number | null;
  buyer_density_pct?: number | null;
  reps_assigned?: number;
  audience_composition_json?: string | null;
}

const STAMP_WARN = { color: "oklch(0.48 0.13 55)", background: "oklch(0.96 0.04 70)", borderColor: "oklch(0.86 0.07 65)" };
const STAMP_OK = { color: "oklch(0.42 0.09 158)", background: "oklch(0.95 0.03 158)", borderColor: "oklch(0.86 0.05 158)" };

function money(n?: number | null): string {
  if (n == null || n <= 0) return "—";
  return "$" + Math.round(n).toLocaleString();
}
function whenLabel(days?: number | null, start?: string | null): string {
  let d = days;
  if (d == null && start) d = Math.round((new Date(start).getTime() - Date.now()) / 86400000);
  if (d == null) return "—";
  if (d < -1) { const a = Math.abs(d); return a < 31 ? `${a}d` : `${Math.round(a / 30)}mo`; }
  if (d <= 0) return "now";
  if (d < 31) return `${d}d`;
  return `${Math.round(d / 30)}mo`;
}
function densityOf(e: EventRowData): number | null {
  if (e.buyer_density_pct != null) return e.buyer_density_pct;
  if (e.audience_composition_json) {
    try {
      const c = typeof e.audience_composition_json === "string"
        ? JSON.parse(e.audience_composition_json) : e.audience_composition_json;
      return c?.cfo_treasury_finance_pct ?? null;
    } catch { return null; }
  }
  return null;
}

export function EventRow({ e, hideCoverage }: { e: EventRowData; hideCoverage?: boolean }) {
  const [open, setOpen] = useState(false);
  const detail = useQuery({
    queryKey: ["conference", e.id],
    queryFn: () => api.get<any>(`/api/conferences/${e.id}`),
    enabled: open,
  });
  const cov = useQuery({
    queryKey: ["coverage", e.id],
    queryFn: () => api.get<{ items: any[] }>("/api/coverage", { query: { conference_id: e.id } }),
    enabled: open,
  });
  const outcomes = useQuery({
    queryKey: ["outcomes", e.id],
    queryFn: () => api.get<any>(`/api/conferences/${e.id}/outcomes`),
    enabled: open,
  });

  const daysVal = e.days_until ?? (e.start_date ? Math.round((new Date(e.start_date).getTime() - Date.now()) / 86400000) : null);
  const isPast = daysVal != null && daysVal < -1;
  const uncovered = (e.reps_assigned ?? 0) === 0;
  const exposed = uncovered && e.tier === "A";
  const density = densityOf(e);
  const d = detail.data;
  const o = outcomes.data;
  const hasResults = !!o && o.encounters > 0;

  return (
    <div>
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open}
              className="w-full flex items-center gap-3 sm:gap-4 px-4 py-3 text-left hover:bg-ink-50 transition-colors">
        <div className="w-10 shrink-0 text-center">
          <div className="font-display font-semibold text-base leading-none text-ink-900">{whenLabel(e.days_until, e.start_date)}</div>
          <div className="text-[0.6rem] uppercase tracking-wider text-ink-400 mt-0.5">{isPast ? "ago" : "out"}</div>
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-display font-semibold text-ink-900 truncate">{e.name}</div>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-ink-500 mt-0.5">
            <span className="text-ink-700 font-medium">{e.vertical || "?"}</span>
            <span className="text-ink-300">·</span>
            <span>{e.city || "?"}{e.country ? `, ${e.country}` : ""}</span>
            {density != null && (<><span className="text-ink-300">·</span><span className="tabular-nums">{density}% buyers</span></>)}
            <span className="text-ink-300">·</span>
            <span className="tabular-nums">{money(e.cost_pass_usd)} pass</span>
          </div>
        </div>
        <TierBadge tier={e.tier} />
        {/* score — present but quiet, not a shouting headline */}
        <div className="w-10 shrink-0 text-right hidden sm:block">
          <div className="text-base font-semibold tabular-nums text-ink-700">{Math.round(e.score ?? 0)}</div>
          <div className="text-[0.6rem] uppercase tracking-wider text-ink-400 mt-0.5">score</div>
        </div>
        {!hideCoverage && (
          <div className="w-[5.5rem] shrink-0 text-right">
            {exposed ? <span className="stamp" style={STAMP_WARN}>cover it</span>
              : uncovered ? <span className="text-xs text-ink-400">open</span>
              : <span className="stamp" style={STAMP_OK}>{e.reps_assigned} rep{e.reps_assigned === 1 ? "" : "s"}</span>}
          </div>
        )}
        <span className={"text-ink-300 shrink-0 transition-transform " + (open ? "rotate-180" : "")} aria-hidden>▾</span>
      </button>

      <div className="grid transition-[grid-template-rows] duration-300 ease-out"
           style={{ gridTemplateRows: open ? "1fr" : "0fr" }}>
        <div className="overflow-hidden">
          <div className="px-4 pb-4 pt-1 pl-[4.5rem] space-y-4">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-3">
              {/* how the score is built */}
              <div>
                <div className="rule-label mb-2"><span>Why this score</span></div>
                {detail.isLoading ? <div className="text-xs text-ink-400">Loading…</div>
                  : d?.score_breakdown ? <ScoreBreakdown breakdown={d.score_breakdown} compact />
                  : <div className="text-xs text-ink-400">No breakdown.</div>}
              </div>
              {/* the facts + coverage */}
              <div className="space-y-3">
                <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                  <Fact term="Dates" val={d ? `${d.start_date || "?"}${d.end_date && d.end_date !== d.start_date ? ` → ${d.end_date}` : ""}` : (e.start_date || "—")} />
                  <Fact term="Region" val={d?.region || "—"} />
                  <Fact term="Audience" val={d?.estimated_attendance ? `${d.estimated_attendance.toLocaleString()} att.` : "—"} />
                  <Fact term="Pass" val={money(e.cost_pass_usd ?? d?.cost_pass_usd)} />
                </dl>
                {d?.agenda_summary && <p className="text-xs text-ink-600 leading-relaxed max-w-[60ch]">{d.agenda_summary}</p>}
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rule-label !mb-0"><span>Coverage</span></span>
                  {cov.data && cov.data.items.length > 0 ? (
                    cov.data.items.map((r: any) => <span key={r.rep_id} className="stamp" style={STAMP_OK}>{r.rep_name}</span>)
                  ) : (
                    <span className="text-sm text-ink-500">{cov.isLoading ? "…" : "No one assigned yet."}</span>
                  )}
                  <Link to={`/conferences/${e.id}`} className="btn-primary text-xs ml-auto">
                    {uncovered ? "Assign a rep →" : "Open event →"}
                  </Link>
                </div>
              </div>
            </div>

            {/* what came out of it — only when there's something to show */}
            {hasResults && (
              <div className="border-t border-ink-100 pt-3">
                <div className="rule-label mb-2"><span>Results — what came back</span></div>
                <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-700">
                  <span><span className="font-semibold tabular-nums">{o.contacts}</span> <span className="text-ink-500">connections</span></span>
                  <span><span className="font-semibold tabular-nums">{o.meetings}</span> <span className="text-ink-500">meetings</span></span>
                  <span><span className="font-semibold tabular-nums">{o.briefs}</span> <span className="text-ink-500">briefs</span></span>
                  <span><span className="font-semibold tabular-nums">{o.drafts}</span> <span className="text-ink-500">follow-ups</span></span>
                </div>
                {o.connections?.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-500">
                    {o.connections.slice(0, 6).map((c: any) => (
                      <span key={c.id} className="truncate">
                        <span className="text-ink-700">{c.primary_name || "?"}</span>
                        {c.primary_company ? ` · ${c.primary_company}` : ""}
                        {c.meeting_requested ? <span className="text-emerald-700"> · meeting</span> : ""}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Fact({ term, val }: { term: string; val: string }) {
  return (
    <div>
      <dt className="text-[0.65rem] uppercase tracking-wider text-ink-400">{term}</dt>
      <dd className="text-ink-800 tabular-nums">{val}</dd>
    </div>
  );
}
