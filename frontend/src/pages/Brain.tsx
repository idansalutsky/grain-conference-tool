import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useToast, toastErrorMessage } from "@/components/Toast";

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
const EXAMPLE_DISCOVERY =
  "Find treasury events in LATAM we don't already have";

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
          <span className="stamp" style={stampStyle(hue, true)} title="salience">
            sal {item.salience.toFixed(2)}
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
// SECTION 2 — Run the brain
// ===========================================================================

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
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
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

  const approvedCount = awaiting
    ? awaiting.proposals.filter((p) => decisions[p.id]).length
    : 0;

  return (
    <section>
      <div className="rule-label mb-2">Run the brain — watch the loop</div>
      <p className="text-sm text-ink-500 mb-4 max-w-[64ch]">
        Type anything. The brain classifies it — a fact to capture, a request to
        discover, or a question to answer — then routes it through the graph.
        The path lights up node by node so you can see how it decided.
      </p>

      <div className="card p-4 sm:p-5 mb-4">
        <textarea
          className="input w-full"
          rows={3}
          placeholder="e.g. Met the CFO of Klook at Money20/20 — warm, asked for a follow-up"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="flex flex-wrap items-center gap-2 mt-3">
          <button
            className="btn-primary"
            disabled={!text.trim() || runMut.isPending}
            onClick={() => submit(text)}
          >
            {runMut.isPending ? "Thinking…" : "Run the brain"}
          </button>
          <span className="text-xs text-ink-500 mr-1">or try:</span>
          <button
            className="btn-secondary text-xs"
            disabled={runMut.isPending}
            onClick={() => submit(EXAMPLE_CAPTURE)}
          >
            ✎ a capture
          </button>
          <button
            className="btn-secondary text-xs"
            disabled={runMut.isPending}
            onClick={() => submit(EXAMPLE_DISCOVERY)}
          >
            ◌ a discovery
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

          {complete.writes && complete.writes.length > 0 ? (
            <div className="space-y-2 mb-3">
              {complete.writes.map((w, i) => (
                <WriteRow key={`${w.item_key}-${i}`} write={w} />
              ))}
            </div>
          ) : (
            <p className="text-sm text-ink-500 mb-3">
              No new writes — the brain answered from existing memory.
            </p>
          )}

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
          Nothing run yet — the graph below shows the full architecture.
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
  useDocumentTitle("Brain");
  // The trace of the most recent run, shared so the graph can highlight it.
  const [lastTrace, setLastTrace] = useState<string[]>([]);

  return (
    <div className="space-y-10">
      <div>
        <h1 className="text-2xl mb-1">Grain Brain</h1>
        <p className="text-sm text-ink-500 max-w-[68ch]">
          A LangGraph memory subsystem behind the tool. It reads what reps tell
          it, decides what kind of thing it is, runs it through a graph of nodes
          — and pauses for a human before it acts on anything it discovers.
        </p>
      </div>

      <SpacesSection />
      <RunSection lastTrace={lastTrace} onTrace={setLastTrace} />
      <GraphSection lastTrace={lastTrace} />
    </div>
  );
}
