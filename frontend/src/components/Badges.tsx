// Status as stamped classifications (see .stamp in index.css), not pills.
// Each uses a single semantic hue via inline OKLCH so tints stay perceptually even.

const TIER: Record<string, { c: string; label: string }> = {
  A: { c: "164", label: "Tier A" }, // brand green — priority
  B: { c: "245", label: "Tier B" }, // cool blue — solid
  C: { c: "62", label: "Tier C" },  // muted — low priority
};

function stampStyle(hue: string, muted = false): React.CSSProperties {
  const L = muted ? 0.55 : 0.42;
  const C = muted ? 0.02 : 0.09;
  return {
    color: `oklch(${L} ${C} ${hue})`,
    backgroundColor: `oklch(0.97 ${muted ? 0.006 : 0.03} ${hue})`,
    borderColor: `oklch(0.86 ${muted ? 0.01 : 0.05} ${hue})`,
    // ring uses the same hue, set via boxShadow to avoid tailwind ring color
    boxShadow: `inset 0 0 0 1px oklch(0.9 ${muted ? 0.008 : 0.04} ${hue})`,
  };
}

export function TierBadge({ tier }: { tier?: string | null }) {
  const t = (tier || "C").toUpperCase();
  const def = TIER[t] || TIER.C;
  return (
    <span className="stamp" style={stampStyle(def.c, t === "C")}>
      {def.label}
    </span>
  );
}

const ARC: Record<string, { c: string; muted?: boolean }> = {
  warming: { c: "158" },
  flat: { c: "160", muted: true },
  cooling: { c: "245" },
  tire_kicker: { c: "62" },
};

export function ArcBadge({ kind }: { kind?: string | null }) {
  if (!kind) return <span className="stamp" style={stampStyle("160", true)}>no read</span>;
  const s = ARC[kind] || ARC.flat;
  return (
    <span className="stamp" style={stampStyle(s.c, s.muted)}>
      {kind.replace("_", "-")}
    </span>
  );
}

// Personas: ordered by buying influence; BUYER is brand-green, others stay quiet
// so the committee reads as a hierarchy, not a rainbow.
const PERSONA: Record<string, { c: string; muted?: boolean }> = {
  BUYER: { c: "164" },
  CHAMPION: { c: "245" },
  PAIN_OWNER: { c: "300" },
  ENTRY_POINT: { c: "62" },
  GATEKEEPER: { c: "20", muted: true },
  INFLUENCER: { c: "160", muted: true },
};

export function PersonaBadge({ persona }: { persona?: string | null }) {
  if (!persona) return null;
  const s = PERSONA[persona] || { c: "160", muted: true };
  return (
    <span className="stamp" style={stampStyle(s.c, s.muted)}>
      {persona.replace("_", " ")}
    </span>
  );
}
