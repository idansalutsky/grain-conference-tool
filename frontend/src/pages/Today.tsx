import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TierBadge, ArcBadge } from "@/components/Badges";
import { toastErrorMessage } from "@/components/Toast";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// The dashboard is the MANAGER's ground view — everything across the team, not
// one rep. We still hit the per-rep aggregator (it carries the team-wide blocks
// too); the rep id is only a handle, none of the single-rep fields are shown.
const LENS_REP_ID = "rep-na-01";

interface PriorityEvent {
  id: string; name: string; start_date: string | null; end_date: string | null;
  city: string | null; country: string | null; region: string | null;
  score: number | null; tier: string | null; vertical: string | null;
  estimated_attendance: number | null; cost_pass_usd: number | null;
  buyer_density_pct: number | null; agenda_summary: string | null;
  covering_reps: string[]; reps_assigned: number; days_until: number | null;
}

interface Floor {
  events_ahead: number; covered: number; uncovered: number;
  uncovered_tier_a: number; reps_total: number; reps_deployed: number;
  next_uncovered_name: string | null; next_uncovered_date: string | null;
}

interface TodayPayload {
  nudges: any[];
  warming_count: number;
  priority_events: PriorityEvent[];
  floor: Floor;
  under_invested_segment: { vertical: string; ahead: number; uncovered: number } | null;
  pending_discovery_count: number;
  review_needed_count: number;
}

const STAMP_WARN = { color: "oklch(0.48 0.13 55)", background: "oklch(0.96 0.04 70)", borderColor: "oklch(0.86 0.07 65)" };
const STAMP_OK = { color: "oklch(0.42 0.09 158)", background: "oklch(0.95 0.03 158)", borderColor: "oklch(0.86 0.05 158)" };
const STAMP_QUIET = { color: "oklch(0.5 0.015 160)", background: "oklch(0.95 0.006 160)", borderColor: "oklch(0.88 0.01 160)" };

function whenLabel(d: number | null): string {
  if (d == null) return "—";
  if (d <= 0) return "now";
  if (d < 31) return `${d}d`;
  return `${Math.round(d / 30)}mo`;
}
function money(n: number | null): string {
  if (n == null || n <= 0) return "—";
  return "$" + Math.round(n).toLocaleString();
}

export function TodayPage() {
  useDocumentTitle("Dashboard");
  const [openId, setOpenId] = useState<string | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["today", LENS_REP_ID],
    queryFn: () => api.get<TodayPayload>(`/api/today/${LENS_REP_ID}`),
  });

  if (isLoading) return <div className="text-sm text-ink-500">Loading…</div>;
  if (error) return <div className="card p-4 text-red-700 text-sm">Error: {toastErrorMessage(error)}</div>;
  if (!data) return null;

  const f = data.floor;
  const seg = data.under_invested_segment;
  const nudges = data.nudges || [];
  const events = data.priority_events || [];
  const attentionCount = (data.pending_discovery_count || 0) + (data.review_needed_count || 0);
  const coveredPct = f.events_ahead > 0 ? Math.round((f.covered / f.events_ahead) * 100) : 0;
  const rise = (i: number): React.CSSProperties => ({ animationDelay: `${i * 70}ms` });

  return (
    <div className="space-y-10">
      {/* === Masthead === */}
      <div className="flex items-baseline justify-between gap-3 rise" style={rise(0)}>
        <div>
          <h1 className="masthead text-2xl leading-none">The floor</h1>
          <p className="text-sm text-ink-500 mt-1">
            The whole team's ground — where we're committed, where we're exposed, who to close.
          </p>
        </div>
        <span className="stamp shrink-0" style={STAMP_QUIET}
              title="Relationships shown here are sample data for the demo.">
          sample data
        </span>
      </div>

      {/* === STATE OF THE FLOOR — data + the one intelligence read, combined === */}
      <section className="card p-5 sm:p-6 rise" style={rise(1)}>
        <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-ink-100">
          <Figure label="high-value events ahead" value={f.events_ahead} />
          <Figure label="covered" value={`${f.covered}`} sub={`${coveredPct}% of the field`} />
          <Figure label="tier-A exposed" value={f.uncovered_tier_a}
                  tone={f.uncovered_tier_a > 0 ? "warn" : "ok"} sub="no one assigned" />
          <Figure label="team deployed" value={`${f.reps_deployed}/${f.reps_total}`} sub="reps on events" />
        </div>

        {/* the read: combine the coverage data with the segment + timing intelligence */}
        <div className="mt-5 pt-4 border-t border-ink-100 space-y-2 text-sm">
          {seg && seg.uncovered > 0 && (
            <p className="text-ink-700">
              <span className="font-display font-semibold text-ink-900 capitalize">{seg.vertical}</span> is our
              biggest blind spot — <span className="font-semibold">{seg.uncovered}</span> of {seg.ahead} high-value
              {" "}events ahead with nobody on them.
            </p>
          )}
          {f.next_uncovered_name && (
            <p className="text-ink-700">
              Soonest uncovered tier-A: <Link to="/conferences" className="font-display font-semibold text-ink-900 hover:underline">
                {f.next_uncovered_name}</Link>
              {f.next_uncovered_date && <span className="text-ink-500"> · {f.next_uncovered_date}</span>} — decide who goes.
            </p>
          )}
        </div>
      </section>

      {/* === WHERE TO INVEST — ranked events, expandable for the full read === */}
      <section className="rise" style={rise(2)}>
        <div className="flex items-baseline justify-between gap-3 mb-4">
          <div>
            <h2 className="masthead text-xl leading-none">Where to invest</h2>
            <p className="text-sm text-ink-500 mt-1.5 max-w-[62ch]">
              Highest-value events still ahead, ranked by buyer density. Tap a row for the
              cost, the audience, and who's on it. Anything tier-A with no one is money left out.
            </p>
          </div>
          <Link to="/planning" className="text-xs text-brand hover:underline shrink-0 whitespace-nowrap">
            Plan the year →
          </Link>
        </div>

        <div className="card divide-y divide-ink-100">
          {events.map((e) => {
            const open = openId === e.id;
            const uncovered = e.reps_assigned === 0;
            const exposed = uncovered && e.tier === "A";
            return (
              <div key={e.id}>
                <button
                  onClick={() => setOpenId(open ? null : e.id)}
                  aria-expanded={open}
                  className="w-full flex items-center gap-3 sm:gap-4 px-4 py-3 text-left hover:bg-ink-50 transition-colors"
                >
                  <div className="w-10 shrink-0 text-center">
                    <div className="masthead text-base leading-none text-ink-900">{whenLabel(e.days_until)}</div>
                    <div className="text-[0.6rem] uppercase tracking-wider text-ink-400 mt-0.5">out</div>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-display font-semibold text-ink-900 truncate">{e.name}</div>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-ink-500 mt-0.5">
                      <span className="text-ink-700 font-medium">{e.vertical}</span>
                      <span className="text-ink-300">·</span>
                      <span>{e.city}, {e.country}</span>
                      {e.buyer_density_pct != null && (
                        <>
                          <span className="text-ink-300">·</span>
                          <span className="tabular-nums">{e.buyer_density_pct}% buyers</span>
                        </>
                      )}
                      <span className="text-ink-300">·</span>
                      <span className="tabular-nums">{money(e.cost_pass_usd)} pass</span>
                    </div>
                  </div>
                  <TierBadge tier={e.tier} />
                  <div className="w-12 shrink-0 text-right hidden sm:block">
                    <div className="masthead text-base leading-none tabular-nums text-ink-900">{Math.round(e.score ?? 0)}</div>
                    <div className="text-[0.6rem] uppercase tracking-wider text-ink-400 mt-0.5">score</div>
                  </div>
                  <div className="w-[5.5rem] shrink-0 text-right">
                    {exposed ? (
                      <span className="stamp" style={STAMP_WARN}>cover it</span>
                    ) : uncovered ? (
                      <span className="text-xs text-ink-400">open</span>
                    ) : (
                      <span className="stamp" style={STAMP_OK}>{e.reps_assigned} rep{e.reps_assigned === 1 ? "" : "s"}</span>
                    )}
                  </div>
                  <span className={"text-ink-300 shrink-0 transition-transform " + (open ? "rotate-180" : "")} aria-hidden>▾</span>
                </button>

                {/* expandable detail — grid-rows transition, no layout thrash */}
                <div className="grid transition-[grid-template-rows] duration-300 ease-out"
                     style={{ gridTemplateRows: open ? "1fr" : "0fr" }}>
                  <div className="overflow-hidden">
                    <div className="px-4 pb-4 pt-1 pl-[4.5rem]">
                      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2 text-sm">
                        <Detail term="Dates" val={`${e.start_date || "?"}${e.end_date && e.end_date !== e.start_date ? ` → ${e.end_date}` : ""}`} />
                        <Detail term="Region" val={e.region || "—"} />
                        <Detail term="Audience" val={e.estimated_attendance ? `${e.estimated_attendance.toLocaleString()} att.` : "—"} />
                        <Detail term="Pass" val={money(e.cost_pass_usd)} />
                      </dl>
                      {e.agenda_summary && (
                        <p className="text-sm text-ink-600 mt-3 max-w-[70ch] leading-relaxed">{e.agenda_summary}</p>
                      )}
                      <div className="flex flex-wrap items-center gap-2 mt-3">
                        <span className="rule-label !mb-0"><span>Coverage</span></span>
                        {e.covering_reps.length > 0 ? (
                          e.covering_reps.map((r) => (
                            <span key={r} className="stamp" style={STAMP_OK}>{r}</span>
                          ))
                        ) : (
                          <span className="text-sm text-ink-500">No one assigned yet.</span>
                        )}
                        <Link to={`/conferences/${e.id}`} className="btn-primary text-xs ml-auto">
                          {uncovered ? "Assign a rep →" : "Open event →"}
                        </Link>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
          <div className="px-4 py-2.5 text-right">
            <Link to="/conferences" className="text-xs text-brand hover:underline">All events & scoring →</Link>
          </div>
        </div>
      </section>

      {/* === CLOSE NOW — the relationship intelligence, across the whole team === */}
      <section className="rise" style={rise(3)}>
        <div className="flex items-baseline justify-between gap-3 mb-4">
          <div>
            <h2 className="masthead text-xl leading-none">Close now</h2>
            <p className="text-sm text-ink-500 mt-1.5 max-w-[60ch]">
              Relationships warming across every conference the team has worked — read
              by the engine, not one badge scan.
            </p>
          </div>
          {data.warming_count > 0 && (
            <Link to="/nudges" className="text-xs text-brand hover:underline shrink-0 whitespace-nowrap">
              {data.warming_count} warming →
            </Link>
          )}
        </div>

        {nudges.length > 0 ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {nudges.map((n, i) => (
              <article key={n.id} className="card p-5 flex flex-col gap-4 hover:shadow-lift transition-shadow rise" style={rise(4 + i)}>
                <blockquote className="text-lg leading-snug text-ink-900 font-display font-medium">
                  <span className="text-ink-300 mr-1">“</span>{n.nudge_text}<span className="text-ink-300 ml-0.5">”</span>
                </blockquote>
                <div className="flex items-center gap-2 flex-wrap">
                  <ArcBadge kind={n.arc_verdict} />
                  <span className="text-sm font-semibold text-ink-900">{n.primary_name}</span>
                  <span className="text-sm text-ink-500 truncate">
                    {n.primary_title ? `${n.primary_title} · ` : ""}{n.primary_company || "?"}
                  </span>
                </div>
                <div className="pt-1 mt-auto border-t border-ink-100">
                  <Link to={`/contacts/${n.id}`} className="btn-primary text-xs mt-3">Open contact →</Link>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="card p-6 flex items-start gap-3">
            <span className="stamp shrink-0 mt-0.5" style={STAMP_QUIET}>quiet</span>
            <p className="text-sm text-ink-700 max-w-prose">
              No relationships warming yet — by design. As reps capture encounters in the
              field via Telegram, the ones worth a call surface here.
            </p>
          </div>
        )}
      </section>

      {/* === Quiet footer — what needs a human + the door to the full engine === */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 rise" style={rise(5)}>
        <section className="card p-4 lg:col-span-2">
          <div className="rule-label mb-3">
            <span>{attentionCount > 0 ? `Needs you · ${attentionCount}` : "Needs you"}</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-4">
            {data.pending_discovery_count > 0 && (
              <Link to="/discovery" className="block group">
                <div className="flex items-baseline gap-2">
                  <span className="masthead text-2xl leading-none text-ink-900">{data.pending_discovery_count}</span>
                  <span className="text-sm font-medium text-ink-900 group-hover:underline">
                    new event{data.pending_discovery_count === 1 ? "" : "s"} to approve
                  </span>
                </div>
                <div className="text-xs text-ink-500 mt-1">Discovery agent found events worth the trip →</div>
              </Link>
            )}
            {data.review_needed_count > 0 && (
              <Link to="/contacts" className="block group">
                <div className="flex items-baseline gap-2">
                  <span className="masthead text-2xl leading-none text-ink-900">{data.review_needed_count}</span>
                  <span className="text-sm font-medium text-ink-900 group-hover:underline">
                    {data.review_needed_count === 1 ? "match needs" : "matches need"} review
                  </span>
                </div>
                <div className="text-xs text-ink-500 mt-1">Resolver wasn't sure — confirm or split →</div>
              </Link>
            )}
            {attentionCount === 0 && (
              <div className="text-sm text-ink-500 sm:col-span-2">Nothing pending. Quiet is good.</div>
            )}
          </div>
        </section>

        <Link to="/brain" className="card p-4 group hover:shadow-lift transition-shadow flex flex-col">
          <div className="rule-label mb-3"><span>Under the hood</span></div>
          <div className="font-display font-semibold text-ink-900 group-hover:underline">Intelligence →</div>
          <p className="text-xs text-ink-500 mt-1.5 leading-relaxed">
            How the floor read above is built — the gate that refuses bad leads, the
            cross-conference memory, the events it's surfacing next.
          </p>
        </Link>
      </div>
    </div>
  );
}

function Figure({ label, value, sub, tone }: { label: string; value: number | string; sub?: string; tone?: "warn" | "ok" }) {
  const color = tone === "warn" ? "oklch(0.48 0.13 55)" : "oklch(0.22 0.02 160)";
  return (
    <div className="px-3 first:pl-0 sm:px-4 py-1">
      <div className="masthead text-3xl leading-none tabular-nums" style={{ color }}>{value}</div>
      <div className="text-xs text-ink-500 mt-1.5 leading-tight">{label}</div>
      {sub && <div className="text-[0.7rem] text-ink-400 mt-0.5">{sub}</div>}
    </div>
  );
}

function Detail({ term, val }: { term: string; val: string }) {
  return (
    <div>
      <dt className="text-[0.65rem] uppercase tracking-wider text-ink-400">{term}</dt>
      <dd className="text-ink-800 tabular-nums">{val}</dd>
    </div>
  );
}
