const TIER_STYLE: Record<string, string> = {
  A: "bg-emerald-100 text-emerald-800 border border-emerald-200",
  B: "bg-amber-100 text-amber-800 border border-amber-200",
  C: "bg-ink-100 text-ink-500 border border-ink-200",
};

export function TierBadge({ tier }: { tier?: string | null }) {
  const t = (tier || "C").toUpperCase();
  return <span className={`badge ${TIER_STYLE[t] || TIER_STYLE.C}`}>Tier {t}</span>;
}

const ARC_STYLE: Record<string, { bg: string; emoji: string }> = {
  warming:     { bg: "bg-emerald-100 text-emerald-800 border border-emerald-200", emoji: "📈" },
  flat:        { bg: "bg-ink-100 text-ink-700 border border-ink-200", emoji: "▫️" },
  cooling:     { bg: "bg-blue-100 text-blue-800 border border-blue-200", emoji: "📉" },
  tire_kicker: { bg: "bg-orange-100 text-orange-800 border border-orange-200", emoji: "⚠️" },
};

export function ArcBadge({ kind }: { kind?: string | null }) {
  if (!kind) return <span className="badge bg-ink-100 text-ink-500">No arc</span>;
  const s = ARC_STYLE[kind] || ARC_STYLE.flat;
  return (
    <span className={`badge ${s.bg}`}>
      <span className="mr-1">{s.emoji}</span>
      {kind.replace("_", "-")}
    </span>
  );
}

const PERSONA_STYLE: Record<string, string> = {
  BUYER:       "bg-emerald-100 text-emerald-800",
  CHAMPION:    "bg-cyan-100 text-cyan-800",
  PAIN_OWNER:  "bg-purple-100 text-purple-800",
  GATEKEEPER:  "bg-rose-100 text-rose-800",
  ENTRY_POINT: "bg-amber-100 text-amber-800",
  INFLUENCER:  "bg-blue-100 text-blue-800",
};

export function PersonaBadge({ persona }: { persona?: string | null }) {
  if (!persona) return null;
  const style = PERSONA_STYLE[persona] || "bg-ink-100 text-ink-700";
  return <span className={`badge ${style}`}>{persona.replace("_", " ")}</span>;
}
