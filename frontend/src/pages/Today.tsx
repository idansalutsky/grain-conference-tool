import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TierBadge, ArcBadge } from "@/components/Badges";
import { toastErrorMessage } from "@/components/Toast";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

const DEFAULT_REP_ID = "rep-na-01";

interface PriorityEvent {
  id: string; name: string; start_date: string | null; end_date: string | null;
  city: string | null; country: string | null; score: number | null;
  tier: string | null; vertical: string | null; reps_assigned: number;
  days_until: number | null;
}

interface TodayPayload {
  rep_id: string;
  event: any;
  targets: any[];
  nudges: any[];
  warming_count: number;
  priority_events: PriorityEvent[];
  uncovered_high_value_count: number;
  recent_captures: any[];
  pending_discovery_count: number;
  review_needed_count: number;
}

const STAMP_WARN = { color: "oklch(0.48 0.13 55)", background: "oklch(0.96 0.04 70)", borderColor: "oklch(0.86 0.07 65)" };
const STAMP_OK = { color: "oklch(0.42 0.09 158)", background: "oklch(0.95 0.03 158)", borderColor: "oklch(0.86 0.05 158)" };
const STAMP_QUIET = { color: "oklch(0.5 0.015 160)", background: "oklch(0.95 0.006 160)", borderColor: "oklch(0.88 0.01 160)" };

function whenLabel(e: PriorityEvent): string {
  if (e.days_until == null) return e.start_date || "";
  if (e.days_until <= 0) return "now";
  if (e.days_until < 31) return `${e.days_until}d`;
  return `${Math.round(e.days_until / 30)}mo`;
}

export function TodayPage() {
  useDocumentTitle("Dashboard");
  const { data, isLoading, error } = useQuery({
    queryKey: ["today", DEFAULT_REP_ID],
    queryFn: () => api.get<TodayPayload>(`/api/today/${DEFAULT_REP_ID}`),
  });

  if (isLoading) return <div className="text-sm text-ink-500">Loading…</div>;
  if (error) return <div className="card p-4 text-red-700 text-sm">Error: {toastErrorMessage(error)}</div>;
  if (!data) return null;

  const ev = data.event || {};
  const hasEvent = !!ev.id;
  const nudges = data.nudges || [];
  const events = data.priority_events || [];
  const attentionCount = (data.pending_discovery_count || 0) + (data.review_needed_count || 0);

  // quiet, cheap stagger — respects reduced-motion (.rise)
  const rise = (i: number): React.CSSProperties => ({ animationDelay: `${i * 70}ms` });

  return (
    <div className="space-y-10">
      {/* === Masthead === */}
      <div className="flex items-baseline justify-between gap-3 rise" style={rise(0)}>
        <div>
          <h1 className="masthead text-2xl leading-none">The Brief</h1>
          <p className="text-sm text-ink-500 mt-1">
            Two decisions for the day — who to close, where to send the team.
          </p>
        </div>
        <span className="stamp shrink-0" style={STAMP_QUIET}
              title="Contacts & captures shown here are sample data for the demo.">
          sample data
        </span>
      </div>

      {/* slim next-event context — one line, not a hero block */}
      {hasEvent && (
        <Link
          to={`/conferences/${ev.id}`}
          className="rise flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm group -mt-4"
          style={rise(1)}
        >
          <span className="rule-label !mb-0">
            <span>{ev.is_active_now ? (ev.is_explicit_bind ? "Capturing for" : "Live now") : `Next · ${ev.days_until ?? "?"}d out`}</span>
          </span>
          <span className="font-display font-semibold text-ink-900 group-hover:underline underline-offset-4 decoration-1">
            {ev.name}
          </span>
          <TierBadge tier={ev.tier} />
          <span className="text-ink-500">{ev.city}, {ev.country}</span>
        </Link>
      )}

      {/* === DECISION 1 — who to close now (the cross-conference engine) === */}
      <section className="rise" style={rise(2)}>
        <div className="flex items-baseline justify-between gap-3 mb-4">
          <div>
            <h2 className="masthead text-xl leading-none">Close now</h2>
            <p className="text-sm text-ink-500 mt-1.5 max-w-[60ch]">
              People whose situation just shifted — read across every conference the
              team has worked, not one badge scan.
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
              <article key={n.id}
                       className="card p-5 flex flex-col gap-4 hover:shadow-lift transition-shadow rise"
                       style={rise(3 + i)}>
                <blockquote className="text-lg leading-snug text-ink-900 font-display font-medium">
                  <span className="text-ink-300 mr-1">“</span>
                  {n.nudge_text}
                  <span className="text-ink-300 ml-0.5">”</span>
                </blockquote>
                <div className="flex items-center gap-2 flex-wrap">
                  <ArcBadge kind={n.arc_verdict} />
                  <span className="text-sm font-semibold text-ink-900">{n.primary_name}</span>
                  <span className="text-sm text-ink-500 truncate">
                    {n.primary_title ? `${n.primary_title} · ` : ""}{n.primary_company || "?"}
                  </span>
                </div>
                <div className="pt-1 mt-auto border-t border-ink-100">
                  <Link to={`/contacts/${n.id}`} className="btn-primary text-xs mt-3">
                    Open contact →
                  </Link>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="card p-6 flex items-start gap-3">
            <span className="stamp shrink-0 mt-0.5" style={STAMP_QUIET}>quiet</span>
            <p className="text-sm text-ink-700 max-w-prose">
              No relationships warming yet — by design. As reps capture encounters in
              the field, the ones worth a call surface here.
            </p>
          </div>
        )}
      </section>

      {/* === DECISION 2 — where to send the team === */}
      <section className="rise" style={rise(3)}>
        <div className="flex items-baseline justify-between gap-3 mb-4">
          <div>
            <h2 className="masthead text-xl leading-none">Where to invest</h2>
            <p className="text-sm text-ink-500 mt-1.5 max-w-[60ch]">
              Highest-value events still ahead, by buyer density. Anything tier-A with
              no one covering it is money left on the table.
            </p>
          </div>
          {data.uncovered_high_value_count > 0 && (
            <span className="stamp shrink-0 whitespace-nowrap" style={STAMP_WARN}
                  title="Tier-A events ahead with no rep assigned">
              {data.uncovered_high_value_count} uncovered tier-A
            </span>
          )}
        </div>

        {events.length > 0 ? (
          <div className="card divide-y divide-ink-100">
            {events.map((e) => {
              const uncovered = e.reps_assigned === 0;
              const exposed = uncovered && e.tier === "A";
              return (
                <Link key={e.id} to={`/conferences/${e.id}`}
                      className="flex items-center gap-3 sm:gap-4 px-4 py-3 hover:bg-ink-50 transition-colors">
                  <div className="w-10 shrink-0 text-center">
                    <div className="masthead text-base leading-none text-ink-900">{whenLabel(e)}</div>
                    <div className="text-[0.6rem] uppercase tracking-wider text-ink-400 mt-0.5">out</div>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-display font-semibold text-ink-900 truncate">{e.name}</div>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-ink-500 mt-0.5">
                      <span className="text-ink-700 font-medium">{e.vertical}</span>
                      <span className="text-ink-300">·</span>
                      <span>{e.city}, {e.country}</span>
                    </div>
                  </div>
                  <TierBadge tier={e.tier} />
                  <div className="w-14 shrink-0 text-right hidden sm:block">
                    <div className="masthead text-base leading-none tabular-nums text-ink-900">
                      {Math.round(e.score ?? 0)}
                    </div>
                    <div className="text-[0.6rem] uppercase tracking-wider text-ink-400 mt-0.5">score</div>
                  </div>
                  <div className="w-[5.5rem] shrink-0 text-right">
                    {exposed ? (
                      <span className="stamp" style={STAMP_WARN}>cover it</span>
                    ) : uncovered ? (
                      <span className="text-xs text-ink-400">open</span>
                    ) : (
                      <span className="stamp" style={STAMP_OK}>
                        {e.reps_assigned} rep{e.reps_assigned === 1 ? "" : "s"}
                      </span>
                    )}
                  </div>
                </Link>
              );
            })}
            <div className="px-4 py-2.5 text-right">
              <Link to="/conferences" className="text-xs text-brand hover:underline">
                All events & scoring →
              </Link>
            </div>
          </div>
        ) : (
          <div className="card p-6 text-sm text-ink-500">
            No upcoming tier-A/B events. <Link to="/conferences" className="text-brand hover:underline">Browse all →</Link>
          </div>
        )}
      </section>

      {/* === Quiet footer — what needs a human, + the deeper intelligence === */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 rise" style={rise(4)}>
        {/* needs you — discovery + resolver */}
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

        {/* intelligence — demoted, a door not a headline */}
        <Link to="/brain" className="card p-4 group hover:shadow-lift transition-shadow flex flex-col">
          <div className="rule-label mb-3"><span>Under the hood</span></div>
          <div className="font-display font-semibold text-ink-900 group-hover:underline">Intelligence →</div>
          <p className="text-xs text-ink-500 mt-1.5 leading-relaxed">
            What the system is learning between the lines — market shifts buyers
            mentioned, events worth researching, what it chose to ignore.
          </p>
        </Link>
      </div>
    </div>
  );
}
