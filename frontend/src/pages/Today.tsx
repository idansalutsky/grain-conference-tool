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
  useDocumentTitle("Today");
  const { data, isLoading, error } = useQuery({
    queryKey: ["today", DEFAULT_REP_ID],
    queryFn: () => api.get<TodayPayload>(`/api/today/${DEFAULT_REP_ID}`),
  });

  if (isLoading) return <div className="text-sm text-ink-500">Loading…</div>;
  if (error) return <div className="card p-4 text-red-700 text-sm">Error: {toastErrorMessage(error)}</div>;
  if (!data) return null;

  const ev = data.event || {};
  const hasEvent = !!ev.id;

  return (
    <div className="space-y-4">
      {/* === Hero — active or next event === */}
      {hasEvent ? (
        <section className={
          "card p-5 border-l-4 " +
          (ev.is_active_now ? "border-emerald-500" : "border-brand")
        }>
          <div className="flex justify-between items-start gap-3">
            <div className="flex-1">
              <div className="text-xs uppercase tracking-wider text-ink-500 mb-1">
                {ev.is_active_now
                  ? (ev.is_explicit_bind ? "You're capturing for" : "Happening now")
                  : `Next event in ${ev.days_until ?? "?"} days`}
              </div>
              <Link to={`/conferences/${ev.id}`} className="text-2xl font-bold hover:underline">
                {ev.name}
              </Link>
              <div className="text-sm text-ink-500 mt-1 flex items-center gap-2">
                <TierBadge tier={ev.tier} />
                <span>
                  {ev.start_date}
                  {ev.end_date && ev.end_date !== ev.start_date && ` → ${ev.end_date}`}
                  {" · "}{ev.city}, {ev.country}
                  {" · "}<span className="text-ink-700">{ev.vertical}</span>
                </span>
              </div>
            </div>
            <Link to="/capture" className="btn-primary text-sm shrink-0">
              🎙️ Capture
            </Link>
          </div>
        </section>
      ) : (
        <section className="card p-5">
          <div className="text-sm text-ink-500">No active or upcoming events with targets.</div>
          <Link to="/conferences" className="text-sm text-brand hover:underline mt-1 inline-block">
            Browse all conferences →
          </Link>
        </section>
      )}

      {/* === Two-column: top targets + nudges === */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <section className="card p-4">
          <div className="flex justify-between items-baseline mb-3">
            <h2 className="text-sm font-semibold">Top targets for this event</h2>
            {hasEvent && (
              <Link to={`/conferences/${ev.id}`} className="text-xs text-brand hover:underline">
                See full committee →
              </Link>
            )}
          </div>
          <div className="space-y-2">
            {data.targets.map((t) => (
              <div key={t.id} className="flex items-center gap-3 py-1.5 border-b last:border-0 border-ink-100">
                <PersonaBadge persona={t.persona} />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm truncate">{t.full_name}</div>
                  <div className="text-xs text-ink-500 truncate">
                    {t.title || "?"} @ {t.company_name || "?"}
                  </div>
                </div>
                <span className={"text-xs " + (t.has_brief ? "text-emerald-700" : "text-ink-400")}>
                  {t.has_brief ? "📄 brief ready" : "no brief yet"}
                </span>
              </div>
            ))}
            {data.targets.length === 0 && (
              <div className="text-sm text-ink-500">No ICP-fit targets surfaced yet.</div>
            )}
          </div>
        </section>

        <section className="card p-4">
          <div className="flex justify-between items-baseline mb-3">
            <h2 className="text-sm font-semibold">Warming nudges to clear</h2>
            <Link to="/nudges" className="text-xs text-brand hover:underline">
              All nudges →
            </Link>
          </div>
          <div className="space-y-2">
            {data.nudges.map((n) => (
              <Link
                key={n.id}
                to={`/contacts/${n.id}`}
                className="block py-1.5 border-b last:border-0 border-ink-100 hover:bg-ink-50 -mx-1 px-1 rounded"
              >
                <div className="flex items-center gap-2">
                  <ArcBadge kind={n.arc_verdict} />
                  <div className="font-medium text-sm truncate">{n.primary_name}</div>
                  <span className="text-xs text-ink-500">@ {n.primary_company || "?"}</span>
                </div>
                <div className="text-xs text-ink-600 italic mt-0.5 line-clamp-1">
                  💡 {n.nudge_text}
                </div>
              </Link>
            ))}
            {data.nudges.length === 0 && (
              <div className="text-sm text-ink-500">
                No nudges firing — by design. Calibrated to stay silent on weak signal.
              </div>
            )}
          </div>
        </section>
      </div>

      {/* === Two-column: recent captures + pending action chips === */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <section className="card p-4 lg:col-span-2">
          <h2 className="text-sm font-semibold mb-3">Your recent captures</h2>
          <div className="space-y-1.5">
            {data.recent_captures.map((c) => {
              const s = c.structured || {};
              return (
                <div key={c.id} className="flex items-center gap-2 py-1.5 border-b last:border-0 border-ink-100 text-sm">
                  <span className="text-xs font-mono text-ink-500 w-20 shrink-0">
                    {(c.captured_at || "").slice(0, 10)}
                  </span>
                  <span className="text-xs text-ink-500 w-14 shrink-0">{c.capture_mode}</span>
                  {c.contact_id ? (
                    <Link to={`/contacts/${c.contact_id}`} className="font-medium hover:underline truncate">
                      {s.name || c.contact_name || "Unknown"}
                    </Link>
                  ) : (
                    <span className="font-medium truncate">{s.name || "?"}</span>
                  )}
                  <span className="text-ink-500 truncate">@ {s.company || "?"}</span>
                  {c.meeting_requested && (
                    <span className="badge bg-emerald-100 text-emerald-800 ml-auto shrink-0">📅</span>
                  )}
                </div>
              );
            })}
            {data.recent_captures.length === 0 && (
              <div className="text-sm text-ink-500">
                No captures yet. <Link to="/capture" className="text-brand hover:underline">Make one →</Link>
              </div>
            )}
          </div>
        </section>

        <section className="card p-4">
          <h2 className="text-sm font-semibold mb-3">Needs your attention</h2>
          <div className="space-y-2">
            {data.pending_discovery_count > 0 && (
              <Link to="/discovery" className="block card p-3 bg-amber-50 border-amber-200 hover:bg-amber-100 transition">
                <div className="font-medium text-sm text-amber-900">
                  {data.pending_discovery_count} new event{data.pending_discovery_count === 1 ? "" : "s"} to approve
                </div>
                <div className="text-xs text-amber-800 mt-0.5">
                  Discovery agent found events worth attending
                </div>
              </Link>
            )}
            {data.review_needed_count > 0 && (
              <Link to="/contacts" className="block card p-3 bg-blue-50 border-blue-200 hover:bg-blue-100 transition">
                <div className="font-medium text-sm text-blue-900">
                  {data.review_needed_count} match{data.review_needed_count === 1 ? "" : "es"} need review
                </div>
                <div className="text-xs text-blue-800 mt-0.5">
                  Resolver wasn't sure — your call
                </div>
              </Link>
            )}
            {data.pending_discovery_count === 0 && data.review_needed_count === 0 && (
              <div className="text-sm text-ink-500">Nothing pending. Quiet is good.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
