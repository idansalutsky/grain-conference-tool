import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TierBadge, PersonaBadge, ArcBadge } from "@/components/Badges";
import { toastErrorMessage } from "@/components/Toast";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

const DEFAULT_REP_ID = "rep-na-01";

interface TodayPayload {
  rep_id: string;
  event: any;
  targets: any[];
  nudges: any[];
  recent_captures: any[];
  pending_discovery_count: number;
  review_needed_count: number;
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
  const targets = data.targets || [];
  const nudges = data.nudges || [];
  const captures = data.recent_captures || [];
  const attentionCount = (data.pending_discovery_count || 0) + (data.review_needed_count || 0);

  // animation stagger helper — quiet, cheap, respects reduced-motion (.rise)
  const rise = (i: number): React.CSSProperties => ({ animationDelay: `${i * 70}ms` });

  return (
    <div className="space-y-8">
      {/* === Masthead row === */}
      <div className="flex items-baseline justify-between gap-3 rise" style={rise(0)}>
        <div>
          <h1 className="masthead text-2xl leading-none">The Brief</h1>
          <p className="text-sm text-ink-500 mt-1">Your morning read — who to chase, what changed.</p>
        </div>
        <span
          className="stamp shrink-0"
          style={{ color: "oklch(0.5 0.015 160)", background: "oklch(0.95 0.006 160)", borderColor: "oklch(0.88 0.01 160)" }}
          title="Contacts & captures shown here are sample data for the demo."
        >
          sample data
        </span>
      </div>

      {/* === Hero — active or next event, set as a confident masthead === */}
      {hasEvent ? (
        <section
          className="card p-6 rise"
          style={{ ...rise(1), ...(ev.is_active_now ? { background: "oklch(0.97 0.03 158)", borderColor: "oklch(0.84 0.07 158)" } : {}) }}
        >
          <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-5">
            <div className="flex-1 min-w-0">
              <div className="rule-label mb-3">
                <span>
                  {ev.is_active_now
                    ? (ev.is_explicit_bind ? "You're capturing for" : "Happening now")
                    : `Next up · ${ev.days_until ?? "?"} days out`}
                </span>
              </div>
              <Link
                to={`/conferences/${ev.id}`}
                className="masthead text-3xl leading-[1.02] hover:underline underline-offset-4 decoration-1 block"
              >
                {ev.name}
              </Link>
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 mt-3 text-sm text-ink-500">
                <TierBadge tier={ev.tier} />
                <span className="text-ink-700 font-medium">{ev.vertical}</span>
                <span className="text-ink-300">·</span>
                <span>{ev.city}, {ev.country}</span>
                <span className="text-ink-300">·</span>
                <span className="font-mono text-xs text-ink-500">
                  {ev.start_date}
                  {ev.end_date && ev.end_date !== ev.start_date && ` → ${ev.end_date}`}
                </span>
              </div>
            </div>
            <div className="shrink-0 flex flex-col items-stretch md:items-end gap-2.5">
              <Link to="/capture" className="btn-primary text-sm">
                🎙️ Capture a lead
              </Link>
              <div className="text-xs text-ink-500 text-left md:text-right">
                <span className="font-semibold text-ink-700">{targets.length}</span> mapped
                {" · "}
                <span className="font-semibold text-ink-700">{nudges.length}</span> to chase
              </div>
            </div>
          </div>
        </section>
      ) : (
        <section className="card p-6 rise" style={rise(1)}>
          <div className="rule-label mb-2"><span>No live event</span></div>
          <div className="text-sm text-ink-700">Nothing active or upcoming with mapped targets.</div>
          <Link to="/conferences" className="text-sm text-brand hover:underline mt-2 inline-block">
            Browse all conferences →
          </Link>
        </section>
      )}

      {/* === HERO: cross-conference follow-ups — the real story === */}
      <section className="rise" style={rise(2)}>
        <div className="flex items-baseline justify-between gap-3 mb-4">
          <div>
            <h2 className="masthead text-xl leading-none">Make these calls today</h2>
            <p className="text-sm text-ink-500 mt-1.5">
              People whose situation just changed — caught across every conference you've worked.
            </p>
          </div>
          {nudges.length > 0 && (
            <Link to="/nudges" className="text-xs text-brand hover:underline shrink-0">
              All follow-ups →
            </Link>
          )}
        </div>

        {nudges.length > 0 ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {nudges.map((n, i) => (
              <article
                key={n.id}
                className="card p-5 flex flex-col gap-4 hover:shadow-lift transition-shadow rise"
                style={rise(3 + i)}
              >
                {/* the reason, quoted with weight — the centerpiece */}
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

                <div className="flex items-center gap-3 pt-1 mt-auto border-t border-ink-100">
                  <Link to={`/contacts/${n.id}`} className="btn-primary text-xs mt-3">
                    Open contact →
                  </Link>
                  <Link
                    to={`/contacts/${n.id}`}
                    className="text-xs text-ink-500 hover:text-ink-900 hover:underline mt-3"
                  >
                    Draft follow-up
                  </Link>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="card p-6">
            <div className="flex items-start gap-3">
              <span className="stamp shrink-0 mt-0.5" style={{ color: "oklch(0.5 0.015 160)", background: "oklch(0.95 0.006 160)", borderColor: "oklch(0.88 0.01 160)" }}>
                quiet
              </span>
              <p className="text-sm text-ink-700 max-w-prose">
                No follow-ups firing — by design. The gate stays silent on weak signal and only
                surfaces a contact when several reads agree. Capture more encounters and they'll appear here.
              </p>
            </div>
          </div>
        )}
      </section>

      {/* === Lower register: targets · captures · attention === */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 rise" style={rise(2)}>
        {/* --- Top targets (graceful sparse state) --- */}
        <section className="card p-4">
          <div className="rule-label mb-3"><span>Targets here</span></div>
          {targets.length > 0 ? (
            <>
              <div className="space-y-0">
                {targets.map((t) => (
                  <div key={t.id} className="data-row">
                    <PersonaBadge persona={t.persona} />
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-sm truncate">{t.full_name}</div>
                      <div className="text-xs text-ink-500 truncate">
                        {t.title || "?"} · {t.company_name || "?"}
                      </div>
                    </div>
                    {t.has_brief ? (
                      <span className="stamp shrink-0" style={{ color: "oklch(0.42 0.09 158)", background: "oklch(0.95 0.03 158)", borderColor: "oklch(0.86 0.05 158)" }}>
                        brief
                      </span>
                    ) : (
                      <span className="text-xs text-ink-400 shrink-0">no brief</span>
                    )}
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between gap-2 mt-3 pt-3 border-t border-ink-100">
                <span className="text-xs text-ink-500">
                  {targets.length === 1
                    ? "1 mapped target · others need verifying"
                    : `${targets.length} mapped · ${targets.filter((t) => t.has_brief).length} brief-ready`}
                </span>
                {hasEvent && (
                  <Link to={`/conferences/${ev.id}`} className="text-xs text-brand hover:underline shrink-0">
                    Full committee →
                  </Link>
                )}
              </div>
            </>
          ) : (
            <div className="py-2">
              <p className="text-sm text-ink-700">No ICP-fit targets surfaced yet.</p>
              {hasEvent && (
                <Link to={`/conferences/${ev.id}`} className="text-xs text-brand hover:underline mt-1.5 inline-block">
                  Map the committee →
                </Link>
              )}
            </div>
          )}
        </section>

        {/* --- Recent captures --- */}
        <section className="card p-4">
          <div className="rule-label mb-3"><span>From the floor</span></div>
          <div className="space-y-0">
            {captures.map((c) => {
              const s = c.structured || {};
              return (
                <div key={c.id} className="data-row text-sm py-2">
                  <span className="text-[0.7rem] font-mono text-ink-400 w-16 shrink-0">
                    {(c.captured_at || "").slice(5, 10)}
                  </span>
                  <div className="flex-1 min-w-0">
                    {c.contact_id ? (
                      <Link to={`/contacts/${c.contact_id}`} className="font-medium hover:underline truncate block">
                        {s.name || c.contact_name || "Unknown"}
                      </Link>
                    ) : (
                      <span className="font-medium truncate block">{s.name || "?"}</span>
                    )}
                    <span className="text-xs text-ink-500 truncate block">{s.company || "?"}</span>
                  </div>
                  {c.meeting_requested ? (
                    <span className="stamp ml-auto shrink-0" style={{ color: "oklch(0.42 0.09 158)", background: "oklch(0.95 0.03 158)", borderColor: "oklch(0.86 0.05 158)" }}>meeting</span>
                  ) : (
                    <span className="text-[0.7rem] text-ink-400 shrink-0">{c.capture_mode}</span>
                  )}
                </div>
              );
            })}
            {captures.length === 0 && (
              <div className="text-sm text-ink-500 py-2">
                No captures yet. <Link to="/capture" className="text-brand hover:underline">Make one →</Link>
              </div>
            )}
          </div>
        </section>

        {/* --- Needs attention — actionable, not a leftover chip --- */}
        <section className="card p-4">
          <div className="rule-label mb-3">
            <span>{attentionCount > 0 ? `Needs you · ${attentionCount}` : "Needs you"}</span>
          </div>
          <div className="space-y-3">
            {data.pending_discovery_count > 0 && (
              <Link to="/discovery" className="block group">
                <div className="flex items-baseline gap-2">
                  <span className="masthead text-2xl leading-none text-ink-900">{data.pending_discovery_count}</span>
                  <span className="text-sm font-medium text-ink-900 group-hover:underline">
                    new event{data.pending_discovery_count === 1 ? "" : "s"} to approve
                  </span>
                </div>
                <div className="text-xs text-ink-500 mt-1">
                  Discovery agent found events worth attending — review & queue them →
                </div>
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
                <div className="text-xs text-ink-500 mt-1">
                  Resolver wasn't sure — your call to confirm or split →
                </div>
              </Link>
            )}
            {attentionCount === 0 && (
              <div className="text-sm text-ink-500">Nothing pending. Quiet is good.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
