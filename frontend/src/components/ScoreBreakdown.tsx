interface Factor {
  key: string;
  raw: number;
  weight: number;
  weighted: number;
  evidence: string;
}

interface Props {
  breakdown?: { total: number; tier: string; factors: Factor[] };
}

const LABELS: Record<string, string> = {
  vertical_concentration: "Vertical concentration",
  buyer_density: "Buyer density",
  fx_exposure_proxy: "FX exposure proxy",
  reachability: "Reachability",
  geo_cost_efficiency: "Geo-cost efficiency",
  competitive_validation: "Competitive validation",
  historical_yield: "Historical yield",
};

export function ScoreBreakdown({ breakdown }: Props) {
  if (!breakdown || !breakdown.factors) {
    return <div className="text-sm text-ink-500">No score breakdown yet.</div>;
  }
  return (
    <div className="space-y-2">
      <div className="flex items-baseline gap-2">
        <div className="text-3xl font-bold">{breakdown.total.toFixed(1)}</div>
        <div className="text-sm text-ink-500">/ 100</div>
      </div>
      <div className="space-y-2">
        {breakdown.factors.map((f) => (
          <div key={f.key} className="text-xs">
            <div className="flex justify-between items-baseline">
              <span className="font-medium">{LABELS[f.key] || f.key}</span>
              <span className="text-ink-500 font-mono">
                {f.raw.toFixed(2)} × {f.weight.toFixed(2)} ={" "}
                <span className="text-ink-900 font-semibold">{f.weighted.toFixed(1)}</span>
              </span>
            </div>
            <div className="w-full h-1.5 bg-ink-100 rounded-full overflow-hidden mt-1">
              <div
                className="h-full bg-brand"
                style={{ width: `${Math.min(100, f.weighted)}%` }}
              />
            </div>
            <div className="text-ink-500 italic mt-0.5">{f.evidence}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
