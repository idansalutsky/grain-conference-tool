import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useToast, toastErrorMessage } from "@/components/Toast";
import { ArcBadge } from "@/components/Badges";

// ---------------------------------------------------------------------------
// Grain Brain — a window into the LangGraph memory subsystem.
//
// Three movements, top to bottom:
//   1. SPACES  — what the brain knows, compressed (the memory).
//   2. RUN     — the loop, live: classify → … → gate → memory_writer.
//   3. GRAPH   — the architecture, made tangible (nodes + edges + interrupt).
//
// Everything reads the /api/brain contract verbatim. No optimism, no guessing.
// ---------------------------------------------------------------------------

// ----- contract types ------------------------------------------------------

interface SpaceSummary {
  name: string;
  item_count: number;
  summary: string;
  updated_at: string;
}

interface BrainItem {
  id: string;
  space: string;
  item_key: string;
  content: Record<string, unknown>;
  salience?: number;
  provenance?: unknown;
  created_at?: string;
  updated_at?: string;
}

interface SpaceDetail {
  space: string;
  summary: string;
  item_count: number;
  updated_at: string;
  items: BrainItem[];
}

interface Proposal {
  id: string;
  name: string;
  city?: string;
  country?: string;
  region?: string;
  start_date?: string;
  vertical?: string;
  why_relevant?: string;
  estimated_attendance?: number;
  source_url?: string;
  provenance?: string;
}

interface WriteRecord {
  space: string;
  item_key: string;
  content?: Record<string, unknown>;
  [k: string]: unknown;
}

interface RunComplete {
  status: "complete";
  kind: string;
  trace: string[];
  result?: Record<string, unknown>;
  gate_decisions?: unknown[];
  writes?: WriteRecord[];
  thread_id: string;
}

interface RunAwaiting {
  status: "awaiting_approval";
  kind: string;
  trace: string[];
  proposals: Proposal[];
  thread_id: string;
}

type RunResponse = RunComplete | RunAwaiting;

interface ResumeResponse {
  status: "complete";
  trace: string[];
  gate_decisions?: unknown[];
  writes: WriteRecord[];
  result: { updated_summaries?: Record<string, unknown>; [k: string]: unknown };
  thread_id: string;
}

interface GraphNode {
  id: string;
  kind: string;
  desc?: string;
}
interface GraphEdge {
  from: string;
  to: string;
  when?: string;
}
interface GraphSpec {
  nodes: GraphNode[];
  edges: GraphEdge[];
  interrupts: string[];
  spaces: unknown[];
}

// ----- rollups (L1) contract -----------------------------------------------

type RollupScope = "account" | "event" | "segment";

interface ArcMix {
  warming?: number;
  flat?: number;
  cooling?: number;
  tire_kicker?: number;
}

interface Rollup {
  id: string;
  scope_type: string;
  scope_id: string;
  title: string;
  summary: string;
  features: Record<string, unknown>;
  priority: number;
  source_count: number;
  updated_at: string;
}

interface RollupsResponse {
  scope: string;
  sort: string;
  count: number; // TOTAL — nothing dropped
  returned: number;
  rollups: Rollup[];
}

// ----- presentation helpers ------------------------------------------------

const SPACE_META: Record<string, { glyph: string; blurb: string; hue: string }> = {
  icp: { glyph: "◎", blurb: "Who we sell to — the profile every score reads from.", hue: "164" },
  events: { glyph: "▤", blurb: "Events worth our time, and why.", hue: "245" },
  playbook: { glyph: "✎", blurb: "What works on the floor — moves that landed.", hue: "300" },
  gaps: { glyph: "◌", blurb: "What we're missing — coverage holes to close.", hue: "62" },
  relationship: { glyph: "❉", blurb: "Where each account stands, warming or cooling.", hue: "158" },
};

const SPACE_ORDER = ["icp", "events", "playbook", "gaps", "relationship"];

const KIND_META: Record<string, { label: string; line: string; hue: string }> = {
  capture: { label: "Capture", line: "fact to remember — extract, resolve, file it", hue: "164" },
  discover_events: { label: "Discover events", line: "go find candidates — then ask before committing", hue: "62" },
  query: { label: "Query", line: "answer from what the brain already knows", hue: "245" },
};

const EXAMPLE_CAPTURE =
  "Met the CFO of Klook at Money20/20 — warm, asked for a follow-up";
const EXAMPLE_DISCOVERY = "find new events we don't already track";
// The money moment: an off-target lead the gate should refuse outright.
const EXAMPLE_REFUSE =
  "Met the Head of FX at Convera at Money20/20";

// Suggested questions — asking an intelligence, not a debug box.
const SUGGESTED: { label: string; prompt: string }[] = [
  { label: "Where are we under-invested?", prompt: "Where are we under-invested?" },
  { label: "Who's warming?", prompt: "Which accounts are warming right now?" },
  { label: "Which events are worth returning to?", prompt: "Which events are worth returning to next year?" },
];

function relTime(iso?: string): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const diff = Date.now() - t;
  const m = Math.round(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d}d ago`;
  return iso.slice(0, 10);
}

function stampStyle(hue: string, muted = false): React.CSSProperties {
  const L = muted ? 0.55 : 0.42;
  const C = muted ? 0.02 : 0.09;
  return {
    color: `oklch(${L} ${C} ${hue})`,
    backgroundColor: `oklch(0.97 ${muted ? 0.006 : 0.03} ${hue})`,
    borderColor: `oklch(0.86 ${muted ? 0.01 : 0.05} ${hue})`,
    boxShadow: `inset 0 0 0 1px oklch(0.9 ${muted ? 0.008 : 0.04} ${hue})`,
  };
}

// ===========================================================================
// SECTION 1 — Spaces
// ===========================================================================

function SpacesSection() {
  const [openSpace, setOpenSpace] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["brain-spaces"],
    queryFn: () => api.get<{ spaces: SpaceSummary[] }>("/api/brain/spaces"),
  });

  const spaces = useMemo(() => {
    const byName = new Map((data?.spaces || []).map((s) => [s.name, s]));
    // Stable canonical order regardless of API ordering.
    return SPACE_ORDER.map((n) => byName.get(n)).filter(Boolean) as SpaceSummary[];
  }, [data]);

  return (
    <section>
      <div className="rule-label mb-2">The brain&apos;s spaces — what it knows</div>
      <p className="text-sm text-ink-500 mb-4 max-w-[64ch]">
        Five rolling memories. The brain summarises rather than hoards — each
        space keeps a compressed read plus the items behind it. Click a card to
        see the items and where they came from.
      </p>

      {isLoading && <div className="text-sm text-ink-500">Loading the brain&apos;s memory…</div>}
      {error && (
        <div className="card p-4 text-tire text-sm">Error: {toastErrorMessage(error)}</div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {spaces.map((s) => {
          const meta = SPACE_META[s.name] || { glyph: "◆", blurb: "", hue: "160" };
          const isOpen = openSpace === s.name;
          return (
            <button
              key={s.name}
              onClick={() => setOpenSpace(isOpen ? null : s.name)}
              className={
                "card p-4 text-left transition-shadow hover:shadow-lift " +
                (isOpen ? "ring-2" : "")
              }
              style={isOpen ? { boxShadow: `0 0 0 2px oklch(0.86 0.05 ${meta.hue})` } : undefined}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span
                    className="grid place-items-center w-7 h-7 rounded-md text-base"
                    style={{
                      color: `oklch(0.42 0.09 ${meta.hue})`,
                      background: `oklch(0.96 0.03 ${meta.hue})`,
                    }}
                    aria-hidden
                  >
                    {meta.glyph}
                  </span>
                  <span className="font-semibold text-ink-900 capitalize">{s.name}</span>
                </div>
                <span className="stamp" style={stampStyle(meta.hue, true)}>
                  {s.item_count} item{s.item_count === 1 ? "" : "s"}
                </span>
              </div>
              <p className="text-sm text-ink-700 line-clamp-3 min-h-[3.6em]">
                {s.summary || <span className="text-ink-500 italic">No summary yet — empty space.</span>}
              </p>
              <div className="flex items-center justify-between mt-3 text-xs text-ink-500">
                <span>{meta.blurb}</span>
                <span className="shrink-0 ml-2">{relTime(s.updated_at)}</span>
              </div>
            </button>
          );
        })}
      </div>

      {openSpace && <SpaceDetailPanel name={openSpace} onClose={() => setOpenSpace(null)} />}
    </section>
  );
}

function SpaceDetailPanel({ name, onClose }: { name: string; onClose: () => void }) {
  const meta = SPACE_META[name] || { glyph: "◆", blurb: "", hue: "160" };
  const { data, isLoading, error } = useQuery({
    queryKey: ["brain-space", name],
    queryFn: () =>
      api.get<SpaceDetail>(`/api/brain/space/${name}`, { query: { limit: 100 } }),
  });

  return (
    <div className="card p-4 sm:p-5 mt-3" style={{ borderColor: `oklch(0.86 0.05 ${meta.hue})` }}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <div className="flex items-center gap-2">
            <span style={{ color: `oklch(0.42 0.09 ${meta.hue})` }} aria-hidden>
              {meta.glyph}
            </span>
            <h3 className="text-lg capitalize">{name}</h3>
          </div>
          {data?.summary && (
            <p className="text-sm text-ink-700 mt-1 max-w-[70ch]">{data.summary}</p>
          )}
        </div>
        <button className="btn-ghost text-xs shrink-0" onClick={onClose}>
          Close ✕
        </button>
      </div>

      {isLoading && <div className="text-sm text-ink-500">Loading items…</div>}
      {error && <div className="text-tire text-sm">Error: {toastErrorMessage(error)}</div>}

      {data && data.items.length === 0 && !isLoading && (
        <div className="text-sm text-ink-500">
          This space is empty. Run the brain below to write into it.
        </div>
      )}

      <div className="space-y-2">
        {data?.items.map((it) => (
          <ItemRow key={it.id} item={it} hue={meta.hue} />
        ))}
      </div>
    </div>
  );
}

function ItemRow({ item, hue }: { item: BrainItem; hue: string }) {
  const [expanded, setExpanded] = useState(false);
  const title = String(
    item.content?.name ?? item.content?.title ?? item.content?.label ?? item.item_key,
  );
  return (
    <div className="border border-ink-100 rounded-md p-3">
      <div className="flex items-center gap-3">
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm text-ink-900 truncate">{title}</div>
          <div className="text-xs text-ink-500 truncate font-mono">{item.item_key}</div>
        </div>
        {typeof item.salience === "number" && (
          <span
            className="stamp"
            style={stampStyle(hue, true)}
            title={`How strongly this is weighted (salience ${item.salience.toFixed(2)})`}
          >
            {item.salience >= 0.66 ? "high" : item.salience >= 0.33 ? "medium" : "low"} relevance
          </span>
        )}
        <button className="btn-ghost text-xs !px-2 !h-7" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "hide" : "details"}
        </button>
      </div>
      {expanded && (
        <div className="mt-2 space-y-2">
          <div>
            <div className="label mb-1">Content</div>
            <pre className="text-xs bg-ink-50 rounded-md p-2 overflow-x-auto text-ink-700 whitespace-pre-wrap">
              {JSON.stringify(item.content, null, 2)}
            </pre>
          </div>
          {item.provenance != null && (
            <div>
              <div className="label mb-1">Provenance — where this came from</div>
              <pre className="text-xs bg-ink-50 rounded-md p-2 overflow-x-auto text-ink-700 whitespace-pre-wrap">
                {typeof item.provenance === "string"
                  ? item.provenance
                  : JSON.stringify(item.provenance, null, 2)}
              </pre>
            </div>
          )}
          <div className="text-xs text-ink-500">
            created {relTime(item.created_at)} · updated {relTime(item.updated_at)}
          </div>
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// SECTION 1.5 — Middle-management rollups (L1)
// ===========================================================================
//
// One judged summary per entity, sitting between the L0 dots (the tables —
// every encounter/contact, never dropped) and the L2 brain spaces above.
// The TOTAL count is shown explicitly so the no-cap property is legible.

const ARC_HUE: Record<string, string> = {
  warming: "158",
  flat: "160",
  cooling: "245",
  tire_kicker: "62",
};

const SCOPE_META: Record<
  RollupScope,
  { label: string; hue: string; noun: string; tagline: string }
> = {
  account: {
    label: "Accounts",
    hue: "158",
    noun: "account",
    tagline: "one per company, nothing dropped",
  },
  event: {
    label: "Events",
    hue: "245",
    noun: "event",
    tagline: "one per conference, nothing dropped",
  },
  segment: {
    label: "Segments",
    hue: "300",
    noun: "segment",
    tagline: "one per vertical, nothing dropped",
  },
};

const SCOPE_ORDER: RollupScope[] = ["account", "event", "segment"];

function num(v: unknown): number | undefined {
  return typeof v === "number" ? v : undefined;
}
function str(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

/** A small chip; reuses the .stamp class + shared stampStyle tints. */
function Chip({
  hue,
  muted,
  title,
  children,
}: {
  hue: string;
  muted?: boolean;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <span className="stamp" style={stampStyle(hue, muted)} title={title}>
      {children}
    </span>
  );
}

/** Render the arc mix as a row of arc-coloured count chips (only non-zero). */
function ArcMixChips({ mix }: { mix: ArcMix }) {
  const order: (keyof ArcMix)[] = ["warming", "flat", "cooling", "tire_kicker"];
  const present = order.filter((k) => (mix[k] || 0) > 0);
  if (present.length === 0) return null;
  return (
    <>
      {present.map((k) => (
        <Chip
          key={k}
          hue={ARC_HUE[k]}
          muted={k === "flat"}
          title={`${mix[k]} ${k.replace("_", "-")} read(s)`}
        >
          {mix[k]} {k.replace("_", "-")}
        </Chip>
      ))}
    </>
  );
}

/** Features → chips, dispatched by scope. The shape per scope is verified
 *  against the live /api/brain/rollups contract. */
function FeatureChips({ scope, f }: { scope: RollupScope; f: Record<string, unknown> }) {
  if (scope === "account") {
    const arc = str(f.account_arc);
    const enc = num(f.n_encounters);
    const events = num(f.events_spanned);
    const contacts = num(f.n_contacts);
    const mix = (f.arc_mix as ArcMix) || {};
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        {arc && <ArcBadge kind={arc} />}
        {contacts != null && (
          <Chip hue="160" muted title="contacts known at this account">
            {contacts} contact{contacts === 1 ? "" : "s"}
          </Chip>
        )}
        {enc != null && (
          <Chip hue="160" muted title="L0 dots rolled up — every encounter">
            {enc} encounter{enc === 1 ? "" : "s"}
          </Chip>
        )}
        {events != null && (
          <Chip hue="245" muted title="distinct events this account appeared at">
            {events} event{events === 1 ? "" : "s"} spanned
          </Chip>
        )}
        <ArcMixChips mix={mix} />
      </div>
    );
  }

  if (scope === "event") {
    const tier = str(f.tier);
    const verdict = str(f.worth_returning_verdict);
    const contacts = num(f.n_contacts_met);
    const enc = num(f.n_encounters);
    const fin = num(f.measured_finance_pct);
    const committee = num(f.buying_committee_personas_hit);
    const follow = num(f.follow_ups_drafted);
    const mix = (f.arc_mix as ArcMix) || {};
    const goodVerdict = verdict === "worth_returning";
    return (
      <div className="flex flex-wrap items-center gap-1.5">
        {tier && (
          <Chip hue={tier === "A" ? "164" : tier === "B" ? "245" : "62"} muted={tier === "C"}>
            Tier {tier}
          </Chip>
        )}
        {verdict && (
          <Chip
            hue={goodVerdict ? "164" : "62"}
            title="The judged verdict — return next year, or not"
          >
            {verdict.replace(/_/g, " ")}
          </Chip>
        )}
        {contacts != null && (
          <Chip hue="160" muted title="contacts met at this event">
            {contacts} met
          </Chip>
        )}
        {enc != null && (
          <Chip hue="160" muted title="L0 dots rolled up — every encounter">
            {enc} encounter{enc === 1 ? "" : "s"}
          </Chip>
        )}
        {committee != null && committee > 0 && (
          <Chip hue="164" muted title="buying-committee (finance/treasury) contacts">
            {committee} committee
          </Chip>
        )}
        {fin != null && (
          <Chip hue="164" muted title="measured finance/treasury share of the audience">
            {fin.toFixed(0)}% finance
          </Chip>
        )}
        {follow != null && (
          <Chip hue="62" muted title="follow-ups drafted off the back of this event">
            {follow} follow-up{follow === 1 ? "" : "s"}
          </Chip>
        )}
        <ArcMixChips mix={mix} />
      </div>
    );
  }

  // segment
  const nEvents = num(f.n_events);
  const tierMix = (f.tier_mix as Record<string, number>) || {};
  const regions = Array.isArray(f.regions) ? (f.regions as string[]) : [];
  const nAccounts = num(f.n_accounts);
  const gap = f.coverage_gap === true;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {nEvents != null && (
        <Chip hue="300" muted title="events in this segment">
          {nEvents} event{nEvents === 1 ? "" : "s"}
        </Chip>
      )}
      {(["A", "B", "C"] as const).map((t) =>
        (tierMix[t] || 0) > 0 ? (
          <Chip
            key={t}
            hue={t === "A" ? "164" : t === "B" ? "245" : "62"}
            muted={t === "C"}
            title={`${tierMix[t]} Tier ${t} event(s)`}
          >
            {tierMix[t]}× {t}
          </Chip>
        ) : null,
      )}
      {nAccounts != null && (
        <Chip hue="158" muted title="worked accounts in this segment">
          {nAccounts} worked account{nAccounts === 1 ? "" : "s"}
        </Chip>
      )}
      {regions.length > 0 && (
        <Chip hue="160" muted title="regions this segment spans">
          {regions.join(" · ")}
        </Chip>
      )}
      <Chip
        hue={gap ? "62" : "164"}
        title={gap ? "A coverage hole to close" : "Coverage looks adequate"}
      >
        {gap ? "coverage gap" : "coverage ok"}
      </Chip>
    </div>
  );
}

function RollupCard({ scope, rollup }: { scope: RollupScope; rollup: Rollup }) {
  const meta = SCOPE_META[scope];
  const [open, setOpen] = useState(false);
  const [refine, setRefine] = useState(false);

  // On-demand richer LLM prose, only when expanded + refine requested.
  const refined = useQuery({
    queryKey: ["brain-rollup", scope, rollup.scope_id, "refine"],
    queryFn: () =>
      api.get<Rollup>(`/api/brain/rollup/${rollup.scope_type}/${rollup.scope_id}`, {
        query: { refine: true },
      }),
    enabled: open && refine,
  });

  const summary = refine && refined.data?.summary ? refined.data.summary : rollup.summary;

  return (
    <div
      className={"card p-4 text-left " + (open ? "ring-2" : "")}
      style={open ? { boxShadow: `0 0 0 2px oklch(0.86 0.05 ${meta.hue})` } : undefined}
    >
      <button
        className="w-full text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <div className="flex items-start justify-between gap-3 mb-2">
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-ink-900 truncate">{rollup.title}</div>
            <div className="text-xs text-ink-500 font-mono truncate">{rollup.scope_id}</div>
          </div>
          <div className="flex flex-col items-end gap-1 shrink-0">
            <Chip
              hue={meta.hue}
              title={`How loudly this asks for attention (priority ${rollup.priority.toFixed(2)})`}
            >
              {rollup.priority >= 0.66
                ? "high priority"
                : rollup.priority >= 0.33
                  ? "medium priority"
                  : "low priority"}
            </Chip>
            <span className="text-xs text-ink-500" title="L0 dots behind this one judged summary">
              {rollup.source_count} source{rollup.source_count === 1 ? "" : "s"}
            </span>
          </div>
        </div>
        <p className={"text-sm text-ink-700 " + (open ? "" : "line-clamp-2")}>{summary}</p>
      </button>

      <div className="mt-3">
        <FeatureChips scope={scope} f={rollup.features} />
      </div>

      {open && (
        <div className="mt-3 pt-3 border-t border-ink-100">
          <div className="flex items-center justify-between gap-3 mb-2">
            <span className="label">The judged summary</span>
            <button
              className="btn-ghost text-xs !px-2 !h-7"
              disabled={refined.isFetching}
              onClick={() => setRefine(true)}
              title="Ask the LLM to rewrite this summary as richer prose"
            >
              {refined.isFetching ? "refining…" : refine ? "↻ refine again" : "✎ refine prose"}
            </button>
          </div>
          {refined.error && (
            <div className="text-tire text-xs mb-2">
              Refine failed: {toastErrorMessage(refined.error)}
            </div>
          )}
          <div className="text-xs text-ink-500 mb-2">
            updated {relTime(refined.data?.updated_at || rollup.updated_at)}
          </div>
          <div className="label mb-1">Features behind the judgement</div>
          <pre className="text-xs bg-ink-50 rounded-md p-2 overflow-x-auto text-ink-700 whitespace-pre-wrap">
            {JSON.stringify(rollup.features, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function RollupsSection() {
  const [scope, setScope] = useState<RollupScope>("account");
  const meta = SCOPE_META[scope];

  const { data, isLoading, error } = useQuery({
    queryKey: ["brain-rollups", scope],
    queryFn: () =>
      api.get<RollupsResponse>("/api/brain/rollups", {
        query: { scope, sort: "priority", limit: 60 },
      }),
  });

  const rollups = data?.rollups || [];

  return (
    <section>
      <div className="rule-label mb-2">Rolled-up view — one judged summary per account, event, segment</div>
      <p className="text-sm text-ink-500 mb-4 max-w-[72ch]">
        Every individual encounter is rolled into a single judged read per
        entity — is the account warming, is the event worth returning to, is a
        segment under-covered — with the raw counts kept as chips. Nothing is
        dropped: there is exactly one summary per entity, and the total below
        proves it.
      </p>

      {/* Scope toggle — Accounts / Events / Segments */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <span className="label mr-1">Scope</span>
        {SCOPE_ORDER.map((s) => {
          const m = SCOPE_META[s];
          const active = scope === s;
          return (
            <button
              key={s}
              onClick={() => setScope(s)}
              className={
                "px-2.5 h-7 rounded-md text-xs font-semibold transition-colors " +
                (active ? "bg-ink-900 text-white" : "bg-ink-100 text-ink-500 hover:bg-ink-200")
              }
            >
              {m.label}
            </button>
          );
        })}
        {data && (
          <span className="sm:ml-auto text-xs text-ink-700">
            <span className="font-display font-bold text-base text-ink-900 tabular-nums">
              {data.count}
            </span>{" "}
            {meta.noun} rollup{data.count === 1 ? "" : "s"} — {meta.tagline}
            {data.returned < data.count && (
              <span className="text-ink-500"> · showing top {data.returned} by priority</span>
            )}
          </span>
        )}
      </div>

      {isLoading && <div className="text-sm text-ink-500">Rolling up the dots…</div>}
      {error && (
        <div className="card p-4 text-tire text-sm">Error: {toastErrorMessage(error)}</div>
      )}
      {data && rollups.length === 0 && !isLoading && (
        <div className="card p-6 text-center text-sm text-ink-500">
          No {meta.noun} rollups yet — run the brain to capture encounters first.
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {rollups.map((r) => (
          <RollupCard key={r.id} scope={scope} rollup={r} />
        ))}
      </div>
    </section>
  );
}

// ===========================================================================
// SECTION 2 — Run the brain
// ===========================================================================

// A run of one-or-more consecutive activity items that share a label.
interface ActivityGroup {
  key: string;
  icon: string;
  label: string;
  items: any[]; // newest-first within the group
}

/** Pluralise a humanised activity label for the collapsed summary row.
 *  Handles the common cases ("match" → "matches", "...ed" stays put). */
function pluralizeLabel(label: string, n: number): string {
  const lower = label.toLowerCase();
  if (n === 1) return lower;
  if (/(ch|sh|s|x|z)$/.test(lower)) return `${lower}es`;
  if (/[^aeiou]y$/.test(lower)) return `${lower.slice(0, -1)}ies`;
  // Labels ending in a past-tense verb ("...adjusted") don't pluralise well —
  // fall back to a count-prefixed phrasing instead.
  if (/ed$/.test(lower) || / /.test(lower) === false) return lower;
  return `${lower}s`;
}

/** Collapse CONSECUTIVE same-label items into one group; distinct events stay
 *  on their own. Order is preserved, so the feed reads as a tidy stream. */
function groupActivity(items: any[]): ActivityGroup[] {
  const groups: ActivityGroup[] = [];
  for (const it of items) {
    const last = groups[groups.length - 1];
    if (last && last.label === it.label) {
      last.items.push(it);
    } else {
      groups.push({
        key: `${it.label}-${groups.length}`,
        icon: it.icon,
        label: it.label,
        items: [it],
      });
    }
  }
  return groups;
}

function ActivityRow({ it }: { it: any }) {
  return (
    <div className="min-w-0 flex-1">
      <div className="text-sm">
        <span className="font-medium">{it.label}</span>
        {it.detail && <span className="text-ink-600"> — {it.detail}</span>}
      </div>
      <div className="text-xs text-ink-400">
        {relTime(it.at)}
        {it.by ? ` · ${it.by}` : ""}
      </div>
    </div>
  );
}

function ActivityGroupRow({ group }: { group: ActivityGroup }) {
  const [open, setOpen] = useState(false);
  const n = group.items.length;

  // A single item renders as a plain row — no collapsing theatre.
  if (n === 1) {
    return (
      <li className="flex items-start gap-3 py-2">
        <span className="text-base leading-6 shrink-0">{group.icon}</span>
        <ActivityRow it={group.items[0]} />
      </li>
    );
  }

  // A run of repeats collapses into one summary row that expands on click.
  const newest = group.items[0];
  return (
    <li className="py-2">
      <button
        className="flex items-start gap-3 w-full text-left group"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="text-base leading-6 shrink-0">{group.icon}</span>
        <div className="min-w-0 flex-1">
          <div className="text-sm">
            <span className="font-display font-bold tabular-nums">{n}</span>{" "}
            <span className="font-medium">{pluralizeLabel(group.label, n)}</span>
            <span className="text-ink-400 text-xs"> · {relTime(newest.at)}</span>
          </div>
          <div className="text-xs text-ink-400 group-hover:text-ink-500">
            {open ? "hide individual items" : "show individual items"}
          </div>
        </div>
        <span className="text-ink-300 text-sm shrink-0 mt-0.5" aria-hidden>
          {open ? "▾" : "▸"}
        </span>
      </button>
      {open && (
        <ul className="mt-2 ml-9 space-y-1.5 border-l border-ink-100 pl-3">
          {group.items.map((it, i) => (
            <li key={i}>
              <ActivityRow it={it} />
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function ActivitySection() {
  const { data, isLoading } = useQuery({
    queryKey: ["brain-activity"],
    queryFn: () => api.get<{ items: any[] }>("/api/brain/activity?limit=25"),
  });
  const items = data?.items || [];
  const groups = useMemo(() => groupActivity(items), [items]);
  return (
    <section className="border-t border-ink-200 pt-6">
      <h2 className="text-lg mb-1">What the system&apos;s been doing</h2>
      <p className="text-sm text-ink-500 max-w-[68ch] mb-3">
        Every decision the capture, resolver, discovery and scoring agents make is
        logged — here&apos;s the recent stream, in plain language. Repeats are folded
        up; click to unfold them.
      </p>
      {isLoading && <div className="text-sm text-ink-500">Loading…</div>}
      {!isLoading && items.length === 0 && (
        <div className="card p-6 text-sm text-ink-500 text-center">
          No activity yet — capture a lead or discover an event to see the agents work.
        </div>
      )}
      {groups.length > 0 && (
        <ul className="card divide-y divide-ink-100 px-3">
          {groups.map((g) => (
            <ActivityGroupRow key={g.key} group={g} />
          ))}
        </ul>
      )}
    </section>
  );
}


function Stepper({ trace }: { trace: string[] }) {
  if (!trace.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {trace.map((node, i) => (
        <div key={`${node}-${i}`} className="flex items-center gap-1.5">
          <span
            className="stamp"
            style={stampStyle("164")}
            // small reveal delay so the path "lights up" left to right
            // (purely decorative; respects reduced-motion via .rise).
          >
            {node}
          </span>
          {i < trace.length - 1 && <span className="text-ink-300 text-sm">→</span>}
        </div>
      ))}
    </div>
  );
}

function RunSection({
  lastTrace,
  onTrace,
}: {
  lastTrace: string[];
  onTrace: (t: string[]) => void;
}) {
  const { push: toast } = useToast();
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [run, setRun] = useState<RunResponse | null>(null);
  const [resumed, setResumed] = useState<ResumeResponse | null>(null);
  // Per-proposal approve/reject state for the human-in-the-loop gate.
  const [decisions, setDecisions] = useState<Record<string, boolean>>({});
  // Track which action fired, so only the pressed control shows its busy state.
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const runMut = useMutation({
    mutationFn: (input_text: string) =>
      api.post<RunResponse>("/api/brain/run", { input_text }),
    onSuccess: (d) => {
      setRun(d);
      setResumed(null);
      setDecisions({});
      onTrace(d.trace || []);
      if (d.status === "awaiting_approval") {
        // Default every proposal to approved — reviewer flips the ones to drop.
        const seed: Record<string, boolean> = {};
        for (const p of d.proposals) seed[p.id] = true;
        setDecisions(seed);
      } else {
        qc.invalidateQueries({ queryKey: ["brain-spaces"] });
      }
      setBusyAction(null);
    },
    onError: (e) => {
      setBusyAction(null);
      toast("error", toastErrorMessage(e));
    },
  });

  const resumeMut = useMutation({
    mutationFn: ({ thread_id, approvals }: { thread_id: string; approvals: { id: string; approved: boolean }[] }) =>
      api.post<ResumeResponse>("/api/brain/resume", { thread_id, approvals }),
    onSuccess: (d) => {
      setResumed(d);
      onTrace(d.trace || []);
      const n = (d.writes || []).length;
      toast("success", `Committed — ${n} item${n === 1 ? "" : "s"} written to the events space.`);
      qc.invalidateQueries({ queryKey: ["brain-spaces"] });
      qc.invalidateQueries({ queryKey: ["brain-space", "events"] });
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const submit = (t: string) => {
    const v = t.trim();
    if (!v) return;
    setText(v);
    runMut.mutate(v);
  };

  const awaiting = run?.status === "awaiting_approval" ? run : null;
  const complete = run?.status === "complete" ? run : null;
  const pending = runMut.isPending;
  const fire = (key: string, prompt: string) => {
    setBusyAction(key);
    submit(prompt);
  };

  const approvedCount = awaiting
    ? awaiting.proposals.filter((p) => decisions[p.id]).length
    : 0;

  return (
    <section>
      <div className="rule-label mb-3">Put it to work</div>

      {/* ACTION-LED: two confident, deliberate moves up top. */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
        {/* (a) Discovery — the primary action. */}
        <div className="card p-4 sm:p-5 flex flex-col rise" style={{ animationDelay: "0ms" }}>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-lg leading-none" aria-hidden>🔎</span>
            <h3 className="text-base">Scan for new events</h3>
          </div>
          <p className="text-sm text-ink-500 flex-1 mb-3">
            Sweep the web for conferences worth attending that you don&apos;t
            already track — it surfaces candidates and waits for your call before
            anything enters the plan.
          </p>
          <button
            className="btn-primary self-start"
            disabled={pending}
            onClick={() => fire("scan", EXAMPLE_DISCOVERY)}
          >
            {pending && busyAction === "scan" ? "Scanning…" : "Scan for new events"}
          </button>
        </div>

        {/* (b) The refusal demo — the money moment. */}
        <div className="card p-4 sm:p-5 flex flex-col rise" style={{ animationDelay: "60ms" }}>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-lg leading-none" aria-hidden>⛔</span>
            <h3 className="text-base">Watch it refuse the wrong lead</h3>
          </div>
          <p className="text-sm text-ink-500 flex-1 mb-3">
            Feed it a competitor contact and watch the gate reject it — the trace
            and the reason, in the open. Noise control as a feature, not an
            afterthought.
          </p>
          <button
            className="btn-secondary self-start"
            disabled={pending}
            onClick={() => fire("refuse", EXAMPLE_REFUSE)}
          >
            {pending && busyAction === "refuse" ? "Running…" : "Run the refusal"}
          </button>
        </div>
      </div>

      {/* Secondary, de-emphasised: ask the intelligence a question. */}
      <div className="card p-4 mb-4 rise" style={{ animationDelay: "120ms" }}>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-2 mb-2">
          <span className="label">Ask the intelligence</span>
          {SUGGESTED.map((q) => (
            <button
              key={q.label}
              className="stamp transition-colors hover:bg-ink-100 disabled:opacity-50"
              style={stampStyle("245", true)}
              disabled={pending}
              onClick={() => fire("ask:" + q.label, q.prompt)}
            >
              {q.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <input
            className="input flex-1"
            placeholder="…or capture a signal: Met the CFO of Klook at Money20/20 — warm"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") fire("free", text);
            }}
          />
          <button
            className="btn-ghost text-xs shrink-0"
            disabled={!text.trim() || pending}
            onClick={() => fire("free", text)}
          >
            {pending && busyAction === "free" ? "…" : "Send"}
          </button>
        </div>
      </div>

      {/* The decision + trace, shown the moment a run returns. */}
      {run && (
        <div className="card p-4 sm:p-5 mb-4 rise">
          <div className="flex items-center gap-2 mb-3">
            <span className="label">The brain decided this is</span>
            {(() => {
              const km = KIND_META[run.kind] || { label: run.kind, line: "", hue: "160" };
              return (
                <span className="stamp" style={stampStyle(km.hue)}>
                  {km.label}
                </span>
              );
            })()}
            <span className="text-xs text-ink-500 italic">
              {KIND_META[run.kind]?.line}
            </span>
          </div>

          <div className="label mb-2">Node trace — the path it took</div>
          <Stepper trace={run.trace || []} />
        </div>
      )}

      {/* CAPTURE / QUERY result — what entered the brain. */}
      {complete && (
        <div className="card p-4 sm:p-5 mb-4 rise">
          <div className="rule-label mb-3">Result — what entered the brain</div>

          {(() => {
            const decisions = (complete.gate_decisions || []) as Array<{
              decision?: string; reason?: string;
            }>;
            const refused = decisions.find((d) => d?.decision === "reject");
            if (complete.writes && complete.writes.length > 0) {
              return (
                <div className="space-y-2 mb-3">
                  {complete.writes.map((w, i) => (
                    <WriteRow key={`${w.item_key}-${i}`} write={w} />
                  ))}
                </div>
              );
            }
            if (refused) {
              // The money moment: the gate refused this outright. Say it loudly.
              return (
                <div
                  className="rounded-md p-3 mb-3"
                  style={{
                    backgroundColor: "oklch(0.96 0.03 62)",
                    boxShadow: "inset 0 0 0 1px oklch(0.86 0.06 62)",
                  }}
                >
                  <div className="flex items-center gap-2">
                    <span aria-hidden>⛔</span>
                    <span
                      className="stamp"
                      style={{
                        color: "oklch(0.45 0.11 62)",
                        backgroundColor: "oklch(0.97 0.03 62)",
                        boxShadow: "inset 0 0 0 1px oklch(0.86 0.06 62)",
                      }}
                    >
                      Refused
                    </span>
                    <span className="text-sm font-medium text-ink-800">
                      Kept out of memory
                    </span>
                  </div>
                  {refused.reason && (
                    <p className="text-sm text-ink-700 mt-1.5">{refused.reason}</p>
                  )}
                </div>
              );
            }
            return (
              <p className="text-sm text-ink-500 mb-3">
                {complete.kind === "query"
                  ? "No new writes — the brain answered from existing memory."
                  : "Nothing new to record from this one."}
              </p>
            );
          })()}

          {complete.result && Object.keys(complete.result).length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer text-ink-700 font-medium">
                Full result payload
              </summary>
              <pre className="text-xs bg-ink-50 rounded-md p-2 mt-2 overflow-x-auto text-ink-700 whitespace-pre-wrap">
                {JSON.stringify(complete.result, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}

      {/* DISCOVERY — the human-in-the-loop gate. */}
      {awaiting && !resumed && (
        <div className="card p-4 sm:p-5 mb-4 rise" style={{ borderColor: "oklch(0.86 0.05 62)" }}>
          <div className="flex items-center gap-2 mb-1">
            <span className="stamp" style={stampStyle("62")}>
              approval gate
            </span>
            <h3 className="text-lg">The brain paused — it wants a human call</h3>
          </div>
          <p className="text-sm text-ink-500 mb-4 max-w-[64ch]">
            Discovery never writes on its own. Below are the candidates it found.
            Approve the ones worth keeping, reject the rest, then commit — only
            the approved events enter the <span className="font-medium">events</span> space.
          </p>

          <div className="space-y-2">
            {awaiting.proposals.map((p) => {
              const approved = decisions[p.id];
              return (
                <div
                  key={p.id}
                  className="border rounded-md p-3 transition-colors"
                  style={{
                    borderColor: approved ? "oklch(0.86 0.05 164)" : "oklch(0.885 0.011 158)",
                    background: approved ? "oklch(0.98 0.02 164)" : undefined,
                  }}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="font-semibold text-ink-900">{p.name}</div>
                      <div className="text-xs text-ink-500 mt-0.5">
                        {p.start_date || "?"} · {p.city || "?"}, {p.country || "?"}
                        {p.region ? ` · ${p.region}` : ""} ·{" "}
                        <span className="text-ink-700">{p.vertical || "?"}</span>
                        {p.estimated_attendance
                          ? ` · ${p.estimated_attendance.toLocaleString()} attendees`
                          : ""}
                      </div>
                      {p.why_relevant && (
                        <p className="text-sm text-ink-700 italic mt-2">&ldquo;{p.why_relevant}&rdquo;</p>
                      )}
                      {p.provenance && (
                        <span
                          className="stamp mt-2 inline-flex"
                          style={stampStyle("62")}
                          title="Where this came from — confirm before trusting"
                        >
                          {p.provenance}
                        </span>
                      )}
                      {p.source_url && (
                        <a
                          href={p.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs text-brand hover:underline mt-1 inline-block"
                        >
                          source ↗
                        </a>
                      )}
                    </div>
                    <div className="flex flex-col gap-1.5 shrink-0">
                      <button
                        onClick={() => setDecisions((m) => ({ ...m, [p.id]: true }))}
                        className={
                          "btn text-xs " +
                          (approved
                            ? "btn-primary"
                            : "bg-ink-100 text-ink-500 border border-ink-200")
                        }
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => setDecisions((m) => ({ ...m, [p.id]: false }))}
                        className={
                          "btn text-xs " +
                          (!approved
                            ? "bg-ink-900 text-white"
                            : "btn-secondary")
                        }
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="flex items-center gap-3 mt-4">
            <button
              className="btn-primary"
              disabled={resumeMut.isPending}
              onClick={() =>
                resumeMut.mutate({
                  thread_id: awaiting.thread_id,
                  approvals: awaiting.proposals.map((p) => ({
                    id: p.id,
                    approved: !!decisions[p.id],
                  })),
                })
              }
            >
              {resumeMut.isPending ? "Committing…" : "Commit decisions"}
            </button>
            <span className="text-xs text-ink-500">
              {approvedCount} of {awaiting.proposals.length} approved
            </span>
          </div>
        </div>
      )}

      {/* What the resume actually wrote. */}
      {resumed && (
        <div className="card p-4 sm:p-5 mb-4 rise">
          <div className="rule-label mb-3">Committed — what entered the events space</div>
          {resumed.writes.length === 0 ? (
            <p className="text-sm text-ink-500">
              Nothing written — every candidate was rejected.
            </p>
          ) : (
            <div className="space-y-2">
              {resumed.writes.map((w, i) => (
                <WriteRow key={`${w.item_key}-${i}`} write={w} />
              ))}
            </div>
          )}
          {resumed.result?.updated_summaries &&
            Object.keys(resumed.result.updated_summaries).length > 0 && (
              <div className="mt-3">
                <div className="label mb-1">Summaries the brain rewrote</div>
                <div className="space-y-1">
                  {Object.entries(resumed.result.updated_summaries).map(([space, sum]) => (
                    <div key={space} className="text-sm">
                      <span className="font-medium capitalize text-ink-900">{space}:</span>{" "}
                      <span className="text-ink-700">{String(sum)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
        </div>
      )}

      {/* keep lastTrace referenced so highlighting stays in sync if reset */}
      {lastTrace.length === 0 && run == null && (
        <p className="text-xs text-ink-500">
          Nothing run yet — pick an action above to see it decide.
        </p>
      )}
    </section>
  );
}

function WriteRow({ write }: { write: WriteRecord }) {
  const meta = SPACE_META[write.space] || { glyph: "◆", blurb: "", hue: "160" };
  const [open, setOpen] = useState(false);
  const title = String(
    write.content?.name ?? write.content?.title ?? write.item_key,
  );
  return (
    <div className="border border-ink-100 rounded-md p-3">
      <div className="flex items-center gap-3">
        <span className="stamp" style={stampStyle(meta.hue)}>
          → {write.space}
        </span>
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm text-ink-900 truncate">{title}</div>
          <div className="text-xs text-ink-500 font-mono truncate">{write.item_key}</div>
        </div>
        {write.content && (
          <button className="btn-ghost text-xs !px-2 !h-7" onClick={() => setOpen((v) => !v)}>
            {open ? "hide" : "view"}
          </button>
        )}
      </div>
      {open && write.content && (
        <pre className="text-xs bg-ink-50 rounded-md p-2 mt-2 overflow-x-auto text-ink-700 whitespace-pre-wrap">
          {JSON.stringify(write.content, null, 2)}
        </pre>
      )}
    </div>
  );
}

// ===========================================================================
// SECTION 3 — The graph
// ===========================================================================

// Group nodes into the three lanes the contract describes, so the diagram
// reads classify → {capture | discovery | query} → gate → memory_writer.
function laneFor(node: GraphNode): "entry" | "capture" | "discovery" | "query" | "gate" | "exit" {
  const id = node.id.toLowerCase();
  const kind = (node.kind || "").toLowerCase();
  if (id.includes("classif")) return "entry";
  if (kind === "interrupt" || id.includes("gate") || id.includes("approval")) return "gate";
  if (id.includes("memory") || id.includes("writer") || id.includes("commit")) return "exit";
  if (id.includes("discover") || id.includes("propos") || id.includes("search")) return "discovery";
  if (id.includes("quer") || id.includes("answer") || id.includes("retriev")) return "query";
  // extract / resolve / arc and anything else belongs to the capture chain
  return "capture";
}

const LANE_META: Record<string, { label: string; hue: string }> = {
  entry: { label: "Classify", hue: "160" },
  capture: { label: "Capture chain", hue: "164" },
  discovery: { label: "Discovery chain", hue: "62" },
  query: { label: "Query", hue: "245" },
  gate: { label: "Human gate", hue: "62" },
  exit: { label: "Commit", hue: "164" },
};

function GraphSection({ lastTrace }: { lastTrace: string[] }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["brain-graph"],
    queryFn: () => api.get<GraphSpec>("/api/brain/graph"),
  });

  const lit = useMemo(() => new Set(lastTrace.map((t) => t.toLowerCase())), [lastTrace]);
  const isLit = (id: string) => lit.has(id.toLowerCase());

  const lanes = useMemo(() => {
    const groups: Record<string, GraphNode[]> = {
      entry: [], capture: [], discovery: [], query: [], gate: [], exit: [],
    };
    for (const n of data?.nodes || []) groups[laneFor(n)].push(n);
    return groups;
  }, [data]);

  const interrupts = new Set((data?.interrupts || []).map((s) => s.toLowerCase()));

  return (
    <section>
      <div className="rule-label mb-2">The graph — the architecture</div>
      <p className="text-sm text-ink-500 mb-4 max-w-[64ch]">
        One classifier fans out into three chains, all converging on a single
        memory writer — with a human interrupt guarding discovery.
        {lastTrace.length > 0
          ? " The brand-green nodes are the path your last run actually took."
          : " Run something above to light up the path it takes."}
      </p>

      {isLoading && <div className="text-sm text-ink-500">Loading the graph…</div>}
      {error && <div className="card p-4 text-tire text-sm">Error: {toastErrorMessage(error)}</div>}

      {data && (
        <div className="card p-4 sm:p-6 overflow-x-auto">
          <div className="flex items-stretch gap-3 min-w-[640px]">
            {/* Entry */}
            <Lane lane="entry" nodes={lanes.entry} isLit={isLit} interrupts={interrupts} />

            <Connector />

            {/* The fan-out: three stacked chains */}
            <div className="flex flex-col gap-3 justify-center">
              <Lane lane="capture" nodes={lanes.capture} isLit={isLit} interrupts={interrupts} />
              <Lane lane="discovery" nodes={lanes.discovery} isLit={isLit} interrupts={interrupts} />
              <Lane lane="query" nodes={lanes.query} isLit={isLit} interrupts={interrupts} />
            </div>

            {lanes.gate.length > 0 && (
              <>
                <Connector />
                <Lane lane="gate" nodes={lanes.gate} isLit={isLit} interrupts={interrupts} />
              </>
            )}

            <Connector />

            {/* Exit — memory writer */}
            <Lane lane="exit" nodes={lanes.exit} isLit={isLit} interrupts={interrupts} />
          </div>

          <div className="flex flex-wrap items-center gap-4 mt-5 pt-4 border-t border-ink-100 text-xs text-ink-500">
            <span className="flex items-center gap-1.5">
              <span className="stamp" style={stampStyle("164")}>node</span> on last run&apos;s path
            </span>
            <span className="flex items-center gap-1.5">
              <span className="stamp" style={stampStyle("160", true)}>node</span> not taken
            </span>
            <span className="flex items-center gap-1.5">
              <span className="stamp" style={stampStyle("62")}>⏸ interrupt</span> waits for a human
            </span>
          </div>
        </div>
      )}
    </section>
  );
}

function Connector() {
  return (
    <div className="flex items-center text-ink-300 shrink-0" aria-hidden>
      <span className="text-lg">→</span>
    </div>
  );
}

function Lane({
  lane,
  nodes,
  isLit,
  interrupts,
}: {
  lane: string;
  nodes: GraphNode[];
  isLit: (id: string) => boolean;
  interrupts: Set<string>;
}) {
  if (nodes.length === 0) return null;
  const meta = LANE_META[lane];
  return (
    <div className="flex flex-col gap-1.5 min-w-[8rem]">
      <div
        className="text-[0.6rem] font-bold uppercase tracking-[0.12em]"
        style={{ color: `oklch(0.5 0.06 ${meta.hue})` }}
      >
        {meta.label}
      </div>
      <div className="flex flex-col gap-1.5">
        {nodes.map((n) => {
          const lit = isLit(n.id);
          const isInterrupt =
            (n.kind || "").toLowerCase() === "interrupt" ||
            interrupts.has(n.id.toLowerCase());
          return (
            <div
              key={n.id}
              className="rounded-md border px-2.5 py-1.5 transition-all"
              style={
                lit
                  ? {
                      color: "oklch(0.30 0.07 164)",
                      background: "oklch(0.95 0.04 164)",
                      borderColor: "oklch(0.78 0.09 164)",
                      boxShadow: "0 0 0 1px oklch(0.78 0.09 164)",
                    }
                  : {
                      color: "oklch(0.45 0.02 160)",
                      background: "oklch(0.985 0.004 150)",
                      borderColor: "oklch(0.885 0.011 158)",
                    }
              }
              title={n.desc || n.id}
            >
              <div className="text-xs font-semibold flex items-center gap-1">
                {isInterrupt && <span aria-hidden>⏸</span>}
                {n.id}
              </div>
              {n.desc && <div className="text-[0.65rem] text-ink-500 mt-0.5 leading-tight">{n.desc}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ===========================================================================
// Page
// ===========================================================================

export function BrainPage() {
  useDocumentTitle("Team Intelligence");
  // The trace of the most recent run, shared so the graph can highlight it.
  const [lastTrace, setLastTrace] = useState<string[]>([]);
  // Lets a technical reviewer reveal the under-the-hood sections on demand.
  const [showInternals, setShowInternals] = useState(false);

  return (
    <div className="space-y-10">
      <div>
        <h1 className="text-2xl mb-1">Team Intelligence</h1>
        <p className="text-base text-ink-700 max-w-[64ch] leading-relaxed">
          Your team&apos;s event and relationship intelligence — it finds new
          events worth attending, remembers the people you meet across them, and
          filters out everything that doesn&apos;t fit who you sell to.
        </p>
      </div>

      {/* VALUE-FIRST: the live loop + the human gate that refuses bad input. */}
      <RunSection lastTrace={lastTrace} onTrace={setLastTrace} />

      {/* The agents' work, made visible — humanised from the audit log. */}
      <ActivitySection />

      {/* Under-the-hood — the memory tiers, rollups, and graph. Collapsed by
          default so a salesperson sees value first; a reviewer can expand. */}
      <section>
        <div className="flex items-center justify-between gap-3 border-t border-ink-200 pt-6">
          <div>
            <h2 className="text-lg mb-1">How it works (under the hood)</h2>
            <p className="text-sm text-ink-500 max-w-[68ch]">
              The memory behind the loop, for the technically curious: what the
              team has learned so far, how each account and event rolls up, and
              the graph that decides where every input goes.
            </p>
          </div>
          <button
            className="btn-secondary text-xs shrink-0"
            onClick={() => setShowInternals((v) => !v)}
            aria-expanded={showInternals}
          >
            {showInternals ? "Hide details" : "Show how it works"}
          </button>
        </div>

        {showInternals && (
          <div className="space-y-10 mt-6 rise">
            {/* The hierarchy, in one line — three tiers, bottom to top. */}
            <div className="card p-3 sm:p-4">
              <div className="rule-label mb-2">How the memory is layered</div>
              <div className="flex flex-col sm:flex-row sm:items-center gap-2 text-sm">
                <span className="flex items-center gap-2 flex-1 min-w-0">
                  <span className="stamp shrink-0" style={stampStyle("160", true)}>Raw</span>
                  <span className="text-ink-500 truncate">
                    every encounter &amp; contact — never dropped (the tables)
                  </span>
                </span>
                <span className="text-ink-300 hidden sm:block" aria-hidden>→</span>
                <span className="flex items-center gap-2 flex-1 min-w-0">
                  <span className="stamp shrink-0" style={stampStyle("164")}>Rolled up</span>
                  <span className="text-ink-500 truncate">
                    one judged summary per account, event, segment
                  </span>
                </span>
                <span className="text-ink-300 hidden sm:block" aria-hidden>→</span>
                <span className="flex items-center gap-2 flex-1 min-w-0">
                  <span className="stamp shrink-0" style={stampStyle("245")}>Brain</span>
                  <span className="text-ink-500 truncate">
                    cross-cutting spaces the whole team shares
                  </span>
                </span>
              </div>
            </div>

            {/* L2 — the brain's compressed cross-cutting memory. */}
            <SpacesSection />
            {/* L1 — middle-management rollups, one per entity. */}
            <RollupsSection />
            {/* The architecture diagram. */}
            <GraphSection lastTrace={lastTrace} />
          </div>
        )}
      </section>
    </div>
  );
}
