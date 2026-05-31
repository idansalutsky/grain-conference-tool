import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ArcBadge } from "@/components/Badges";
import { EventRow } from "@/components/EventRow";
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
  recent_results: { id: string; name: string; city: string | null; country: string | null; tier: string | null; contacts: number; meetings: number; encounters: number }[];
  pending_discovery_count: number;
  review_needed_count: number;
}

const STAMP_QUIET = { color: "oklch(0.5 0.015 160)", background: "oklch(0.95 0.006 160)", borderColor: "oklch(0.88 0.01 160)" };

export function TodayPage() {
  useDocumentTitle("Dashboard");
  const [topN, setTopN] = useState(6);
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
          <div className="flex items-center gap-3 shrink-0">
            <div className="flex items-center gap-1">
              <span className="text-[0.65rem] uppercase tracking-wider text-ink-400 mr-1">top</span>
              {[6, 10, 15].map((n) => (
                <button key={n} onClick={() => setTopN(n)}
                        className={"px-2 h-6 rounded text-xs font-semibold transition-colors " +
                          (topN === n ? "bg-ink-900 text-white" : "bg-ink-100 text-ink-500 hover:bg-ink-200")}>
                  {n}
                </button>
              ))}
            </div>
            <Link to="/planning" className="text-xs text-brand hover:underline whitespace-nowrap">Plan the year →</Link>
          </div>
        </div>

        <div className="card divide-y divide-ink-100">
          {events.slice(0, topN).map((e) => <EventRow key={e.id} e={e} />)}
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
                  {n.n_conferences > 1 && (
                    <span className="text-xs text-ink-400 ml-auto shrink-0">met at {n.n_conferences} events</span>
                  )}
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

      {/* === Recent results — what the events we worked actually returned === */}
      {data.recent_results && data.recent_results.length > 0 && (
        <section className="rise" style={rise(4)}>
          <div className="flex items-baseline justify-between gap-3 mb-4">
            <div>
              <h2 className="masthead text-xl leading-none">What's come back</h2>
              <p className="text-sm text-ink-500 mt-1.5 max-w-[60ch]">
                The events the team has worked, and the connections they returned — open
                any for the full recap and follow-ups.
              </p>
            </div>
          </div>
          <div className="card divide-y divide-ink-100">
            {data.recent_results.map((r) => (
              <Link key={r.id} to={`/conferences/${r.id}`}
                    className="flex items-center gap-4 px-4 py-3 hover:bg-ink-50 transition-colors">
                <div className="flex-1 min-w-0">
                  <div className="font-display font-semibold text-ink-900 truncate">{r.name}</div>
                  <div className="text-xs text-ink-500 mt-0.5">{r.city}{r.country ? `, ${r.country}` : ""}</div>
                </div>
                <div className="flex items-center gap-5 shrink-0 text-right">
                  <div><div className="font-semibold tabular-nums text-ink-900">{r.contacts}</div><div className="text-[0.6rem] uppercase tracking-wider text-ink-400">connections</div></div>
                  <div><div className="font-semibold tabular-nums text-ink-900">{r.meetings}</div><div className="text-[0.6rem] uppercase tracking-wider text-ink-400">meetings</div></div>
                  <span className="text-ink-300">→</span>
                </div>
              </Link>
            ))}
          </div>
        </section>
      )}

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

