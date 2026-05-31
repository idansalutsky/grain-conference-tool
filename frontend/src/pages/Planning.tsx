import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { SubTabs } from "@/components/SubTabs";
import { EVENTS_TABS } from "@/components/eventsTabs";

const NOW_MONTH = new Date().toISOString().slice(0, 7);
// Tier colours aligned with the stamp palette (A green, B blue, C neutral).
const TIER_FILL: Record<string, string> = {
  A: "oklch(0.6 0.13 158)",
  B: "oklch(0.62 0.1 245)",
  C: "oklch(0.78 0.012 160)",
};

export function PlanningPage() {
  useDocumentTitle("Plan the year");
  const coverage = useQuery({
    queryKey: ["coverage"],
    queryFn: () => api.get<any>("/api/planning/coverage"),
  });
  const clusters = useQuery({
    queryKey: ["clusters"],
    queryFn: () => api.get<{ clusters: any[] }>("/api/planning/clusters"),
  });
  const gaps = useQuery({
    queryKey: ["gaps"],
    queryFn: () => api.get<any>("/api/planning/gaps"),
  });

  return (
    <div>
      <h1 className="text-2xl mb-1">Events</h1>
      <SubTabs items={EVENTS_TABS} />
      <p className="text-sm text-ink-500 mb-6 max-w-[62ch]">
        The year ahead — how many events fall in each month (by tier), which
        high-fit events still have no one assigned, and which trips can be
        batched into one swing.
      </p>

      {/* Compact density strip — how the year is loaded by month, by tier. Kept
          small so the actionable trip clusters sit high on the page. */}
      <section className="card p-4 mb-4">
        <div className="flex items-baseline justify-between gap-3 mb-3">
          <h2 className="label">Events per month, ahead</h2>
          <div className="text-[10px] text-ink-500 flex gap-3">
            {(["A", "B", "C"] as const).map((t) => (
              <span key={t}>
                <span className="inline-block w-3 h-2 align-middle mr-1" style={{ background: TIER_FILL[t] }} />
                Tier {t}
              </span>
            ))}
          </div>
        </div>
        {coverage.data?.months?.filter((m: any) => m.month >= NOW_MONTH).length > 0 ? (
          <div className="flex items-end gap-1.5 h-28">
            {(() => {
              const months = coverage.data.months.filter((m: any) => m.month >= NOW_MONTH);
              const max = Math.max(...months.map((x: any) => x.n_conferences), 1);
              return months.map((m: any) => (
                <div key={m.month} className="flex-1 flex flex-col items-center justify-end h-full"
                     title={`${m.month}: ${m.n_conferences} events`}>
                  <div className="w-full flex flex-col-reverse rounded-t overflow-hidden"
                       style={{ height: `${Math.max(3, (m.n_conferences / max) * 86)}%` }}>
                    {(["A", "B", "C"] as const).map((t) =>
                      m.by_tier[t] > 0 ? (
                        <div key={t} style={{ flex: m.by_tier[t], background: TIER_FILL[t] }} />
                      ) : null,
                    )}
                  </div>
                  <span className="text-[0.6rem] font-mono text-ink-400 mt-1">{m.month.slice(2)}</span>
                </div>
              ));
            })()}
          </div>
        ) : (
          <div className="text-sm text-ink-500">No coverage data yet.</div>
        )}
      </section>

      {/* Where we're under-invested — high-fit events with nobody assigned (a
          brief requirement). Grounded in coverage: tier-A/B with zero reps. */}
      {(gaps.data?.total_uncovered_a > 0 || gaps.data?.total_uncovered_b > 0) && (
        <section className="card p-4 mb-4">
          <div className="flex items-baseline justify-between gap-3 mb-3">
            <h2 className="label">Where we're under-invested</h2>
            <span className="text-xs text-ink-500">high-fit events with no one assigned</span>
          </div>
          {gaps.data?.uncovered_tier_a?.length > 0 && (
            <div className="mb-3">
              <div className="text-xs font-semibold text-ink-700 mb-1.5">
                Tier A · {gaps.data.total_uncovered_a} uncovered
              </div>
              <div className="divide-y divide-ink-100">
                {gaps.data.uncovered_tier_a.slice(0, 6).map((c: any) => (
                  <Link key={c.id} to={`/conferences/${c.id}`}
                        className="flex items-baseline gap-2 py-1.5 text-sm hover:text-brand">
                    <span className="font-mono text-ink-400 w-20 shrink-0 text-xs">{(c.start_date || "").slice(0, 10)}</span>
                    <span className="truncate">{c.name}</span>
                    <span className="text-ink-400 text-xs ml-auto shrink-0">{c.city}</span>
                  </Link>
                ))}
              </div>
            </div>
          )}
          {gaps.data?.total_uncovered_b > 0 && (
            <div className="text-xs text-ink-500">
              + {gaps.data.total_uncovered_b} tier-B events also uncovered.
            </div>
          )}
        </section>
      )}

      <section className="card p-4">
        <div className="flex justify-between items-baseline mb-3">
          <h2 className="label">Trip clusters</h2>
          <span className="text-xs text-ink-500">
            Events ≤21 days apart in the same geo cluster
          </span>
        </div>
        <div className="space-y-2">
          {clusters.data?.clusters?.map((cl: any, i: number) => (
            <div key={i} className="border border-ink-200 rounded p-3">
              <div className="flex justify-between items-baseline gap-3 mb-2">
                <div className="font-semibold text-sm">
                  {cl.geo_cluster} · {cl.start_date} → {cl.end_date}
                  <span className="text-ink-500 font-normal"> · {cl.conferences.length} events / {cl.span_days}d</span>
                </div>
                <div className="text-xs text-ink-700 text-right shrink-0">
                  {cl.total_pass_cost_usd != null && cl.passes_priced > 0 ? (
                    cl.passes_priced === cl.conferences.length ? (
                      <><span className="font-semibold tabular-nums">${cl.total_pass_cost_usd.toLocaleString()}</span><span className="text-ink-500"> in passes</span></>
                    ) : (
                      <><span className="text-ink-500">from </span><span className="font-semibold tabular-nums">${cl.total_pass_cost_usd.toLocaleString()}</span><span className="text-ink-400"> · {cl.passes_priced}/{cl.conferences.length} priced</span></>
                    )
                  ) : (
                    <span className="text-ink-400">pass cost not published</span>
                  )}
                </div>
              </div>
              <div className="text-xs space-y-0.5">
                {cl.conferences.map((c: any) => (
                  <div key={c.id} className="flex items-baseline gap-2 text-ink-700">
                    <span className="font-mono text-ink-500 w-20 shrink-0">{c.start_date}</span>
                    <Link to={`/conferences/${c.id}`} className="hover:underline">
                      {c.name}
                    </Link>
                    <span className="text-ink-500 truncate">— {c.city}, {c.country}</span>
                    <span className="ml-auto tabular-nums text-ink-500 shrink-0">
                      {c.cost_pass_usd ? `$${Math.round(c.cost_pass_usd).toLocaleString()}` : "—"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {!clusters.data?.clusters?.length && (
            <div className="text-sm text-ink-500">No clusters detected.</div>
          )}
        </div>
      </section>
    </div>
  );
}
