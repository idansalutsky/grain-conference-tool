interface Factor {
  key: string;
  raw: number;      // 0..1 — how well the event did on this factor
  weight: number;   // 0..1 — normalized emphasis
  weighted: number; // raw * weight * 100 — points contributed to the total
  evidence: string;
}

interface Props {
  breakdown?: { total: number; tier: string; factors: Factor[] };
  /** Compact mode for the dashboard/list expandable rows. */
  compact?: boolean;
}

// The four factors, with what each one MEASURES — so the number is explainable.
const META: Record<string, { label: string; measures: string }> = {
  buyer_density: { label: "Buyer density", measures: "measured % finance/treasury + reachable committee" },
  fx_exposure: { label: "FX exposure", measures: "agenda on cross-border / FX / settlement" },
  vertical_fit: { label: "Vertical fit", measures: "on a Grain wedge + ICP-shaped room" },
  access: { label: "Access", measures: "format + size, weighted by travel cost" },
};

export function ScoreBreakdown({ breakdown, compact }: Props) {
  if (!breakdown || !breakdown.factors) {
    return <div className="text-sm text-ink-500">No score breakdown yet.</div>;
  }
  return (
    <div className="space-y-3">
      {!compact && (
        <div className="flex items-baseline gap-2">
          <div className="masthead text-3xl leading-none">{breakdown.total.toFixed(0)}</div>
          <div className="text-sm text-ink-500">/ 100 · tier {breakdown.tier}</div>
        </div>
      )}
      <div className="space-y-2.5">
        {breakdown.factors.map((f) => {
          const m = META[f.key] || { label: f.key, measures: "" };
          return (
            <div key={f.key} className="text-xs">
              <div className="flex justify-between items-baseline gap-2">
                <span className="font-semibold text-ink-900">{m.label}</span>
                <span className="text-ink-500 tabular-nums">
                  {Math.round(f.raw * 100)}/100 × {Math.round(f.weight * 100)}%
                  {" = "}<span className="text-ink-900 font-semibold">+{f.weighted.toFixed(0)}</span>
                </span>
              </div>
              {/* bar shows the RAW factor score (how strong the event is on this
                  axis), not the weighted contribution — so a weak factor reads weak. */}
              <div className="w-full h-1.5 bg-ink-100 rounded-full overflow-hidden mt-1">
                <div className="h-full" style={{ width: `${Math.min(100, f.raw * 100)}%`, background: "oklch(0.55 0.11 158)" }} />
              </div>
              <div className="text-ink-500 mt-0.5">
                {m.measures && <span className="text-ink-400">{m.measures} — </span>}
                {f.evidence}
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-[0.7rem] text-ink-400 pt-1 border-t border-ink-100">
        Score = the four factor points added up (each = how the event did × its weight).
      </p>
    </div>
  );
}
