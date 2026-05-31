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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <section className="card p-4">
          <h2 className="label mb-3">Events per month, ahead</h2>
          {coverage.data?.months?.filter((m: any) => m.month >= NOW_MONTH).length > 0 ? (
            <div className="space-y-1.5">
              {coverage.data.months.filter((m: any) => m.month >= NOW_MONTH).map((m: any) => (
                <div key={m.month} className="flex items-center gap-3 text-xs">
                  <span className="w-20 font-mono text-ink-700">{m.month}</span>
                  <div className="flex-1 flex h-5 rounded overflow-hidden bg-ink-100">
                    {(["A", "B", "C"] as const).map((t) =>
                      m.by_tier[t] > 0 ? (
                        <div key={t} style={{ flex: m.by_tier[t], background: TIER_FILL[t] }}
                             title={`Tier ${t}: ${m.by_tier[t]}`} />
                      ) : null,
                    )}
                  </div>
                  <span className="text-ink-500 w-12 text-right">
                    {m.n_conferences} event{m.n_conferences > 1 ? "s" : ""}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-ink-500">No coverage data yet.</div>
          )}
          <div className="text-[10px] text-ink-500 mt-3 flex gap-3">
            {(["A", "B", "C"] as const).map((t) => (
              <span key={t}>
                <span className="inline-block w-3 h-2 align-middle mr-1" style={{ background: TIER_FILL[t] }} />
                Tier {t}
              </span>
            ))}
          </div>
        </section>

        <section className="card p-4">
          <h2 className="label mb-3">Gaps — under-invested high-tier events</h2>
          {gaps.data?.uncovered_tier_a?.length > 0 ? (
            <div>
              <div className="text-xs font-medium text-ink-700 mb-1">
                Tier A · {gaps.data.total_uncovered_a} uncovered
              </div>
              <ul className="space-y-1 mb-3">
                {gaps.data.uncovered_tier_a.map((c: any) => (
                  <li key={c.id} className="text-xs">
                    <Link to={`/conferences/${c.id}`} className="hover:underline">
                      {c.name}
                    </Link>
                    <span className="text-ink-500"> — {c.start_date} · {c.city}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="text-xs text-ink-500">All tier-A events covered.</div>
          )}
          {gaps.data?.uncovered_tier_b?.length > 0 && (
            <div>
              <div className="text-xs font-medium text-ink-700 mb-1">
                Tier B · {gaps.data.total_uncovered_b} uncovered
              </div>
              <ul className="space-y-1">
                {gaps.data.uncovered_tier_b.slice(0, 6).map((c: any) => (
                  <li key={c.id} className="text-xs">
                    <Link to={`/conferences/${c.id}`} className="hover:underline">
                      {c.name}
                    </Link>
                    <span className="text-ink-500"> — {c.start_date} · {c.city}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>

      <section className="card p-4 mt-4">
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
                  {cl.total_pass_cost_usd != null ? (
                    <>
                      <span className="font-semibold tabular-nums">${cl.total_pass_cost_usd.toLocaleString()}</span>
                      <span className="text-ink-500"> in passes</span>
                      {cl.passes_priced < cl.conferences.length && <span className="text-ink-400"> ({cl.passes_priced} priced)</span>}
                    </>
                  ) : (
                    <span className="text-ink-400">pass cost n/a</span>
                  )}
                  <span className="text-ink-300"> · </span>
                  <span className="text-emerald-700">save ~${cl.estimated_savings_usd.toLocaleString()} on travel</span>
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
