import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function PlanningPage() {
  useDocumentTitle("Planning");
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
      <h1 className="text-2xl mb-1">Planning</h1>
      <p className="text-sm text-ink-500 mb-6">
        Where the team is concentrated vs under-invested, and where trips can cluster.
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <section className="card p-4">
          <h2 className="label mb-3">Coverage across the year</h2>
          {coverage.data?.months?.length > 0 ? (
            <div className="space-y-1.5">
              {coverage.data.months.map((m: any) => (
                <div key={m.month} className="flex items-center gap-3 text-xs">
                  <span className="w-20 font-mono text-ink-700">{m.month}</span>
                  <div className="flex-1 flex h-5 rounded overflow-hidden bg-ink-100">
                    {m.by_tier.A > 0 && (
                      <div
                        className="bg-emerald-500"
                        style={{ flex: m.by_tier.A }}
                        title={`Tier A: ${m.by_tier.A}`}
                      />
                    )}
                    {m.by_tier.B > 0 && (
                      <div
                        className="bg-amber-400"
                        style={{ flex: m.by_tier.B }}
                        title={`Tier B: ${m.by_tier.B}`}
                      />
                    )}
                    {m.by_tier.C > 0 && (
                      <div
                        className="bg-ink-300"
                        style={{ flex: m.by_tier.C }}
                        title={`Tier C: ${m.by_tier.C}`}
                      />
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
            <span><span className="inline-block w-3 h-2 bg-emerald-500 align-middle mr-1" />A</span>
            <span><span className="inline-block w-3 h-2 bg-amber-400 align-middle mr-1" />B</span>
            <span><span className="inline-block w-3 h-2 bg-ink-300 align-middle mr-1" />C</span>
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
              <div className="flex justify-between items-baseline mb-1">
                <div className="font-semibold text-sm">
                  {cl.geo_cluster} · {cl.start_date} → {cl.end_date}
                </div>
                <div className="text-xs text-ink-500">
                  Σ score {cl.total_score} · est. saving ${cl.estimated_savings_usd}
                </div>
              </div>
              <div className="text-xs space-y-0.5">
                {cl.conferences.map((c: any) => (
                  <div key={c.id} className="flex gap-2 text-ink-700">
                    <span className="font-mono text-ink-500 w-20">{c.start_date}</span>
                    <Link to={`/conferences/${c.id}`} className="hover:underline">
                      {c.name}
                    </Link>
                    <span className="text-ink-500">— {c.city}, {c.country}</span>
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
