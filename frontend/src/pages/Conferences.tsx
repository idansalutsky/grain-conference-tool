import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { SubTabs } from "@/components/SubTabs";
import { EVENTS_TABS } from "@/components/eventsTabs";
import { EventRow } from "@/components/EventRow";
import { useToast, toastErrorMessage } from "@/components/Toast";

const TODAY = new Date().toISOString().slice(0, 10);

interface Conference {
  id: string; name: string; start_date?: string; end_date?: string;
  city?: string; country?: string; region?: string; vertical?: string;
  format?: string; estimated_attendance?: number; score?: number; tier?: string;
  cost_pass_usd?: number | null; audience_composition_json?: string | null;
  reps_assigned?: number;
}

const TIERS = ["All", "A", "B", "C"];
const REGIONS = ["All", "NA", "EU", "APAC", "MEA", "LATAM"];

// Each factor, with the label AND the one line that says what it actually
// MEASURES — so the model reads as a defensible glass box, not arbitrary knobs.
// Keys match the live scoring factors in backend/grain/scoring.py.
const FACTOR_META: Record<string, { label: string; measures: string }> = {
  "scoring.buyer_density": { label: "Buyer density", measures: "measured % finance/treasury (the buyer) + reachable commercial committee" },
  "scoring.fx_exposure": { label: "FX exposure", measures: "does the agenda centre on cross-border / FX / settlement — Grain's product" },
  "scoring.vertical_fit": { label: "Vertical fit", measures: "event sits on a Grain wedge (travel / payments / treasury) + ICP-shaped room" },
  "scoring.access": { label: "Access", measures: "can a rep work it — format + size, weighted by travel cost" },
};
// Show the factors in priority order, not registry order.
const FACTOR_ORDER = [
  "scoring.buyer_density", "scoring.fx_exposure", "scoring.vertical_fit", "scoring.access",
];

// Live scoring tuner — drag a weight, every event re-scores and the list
// re-ranks. The control sits next to its effect, not buried in Settings.
// Weights are RELATIVE EMPHASIS: the % share each factor carries (normalised to
// 100%) is shown live, so the manager sees what the score is actually built from.
function ScoringTuner() {
  const qc = useQueryClient();
  const { push: toast } = useToast();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: () => api.get<any>("/api/settings") });
  const weights = (data?.parameters || []).filter((p: any) => String(p.key).startsWith("scoring."));
  const [vals, setVals] = useState<Record<string, number>>({});

  const commit = useMutation({
    mutationFn: async ({ key, value }: { key: string; value: number }) => {
      await api.put("/api/settings", { key, value, decided_by: "ui" });
      await api.post("/api/conferences/rescore");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conferences"] }),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  if (weights.length === 0) return null;
  const valueOf = (p: any) => vals[p.key] ?? Number(p.current);
  const sorted = [...weights].sort(
    (a, b) => FACTOR_ORDER.indexOf(a.key) - FACTOR_ORDER.indexOf(b.key));
  const totalW = sorted.reduce((s, p) => s + Math.max(0, valueOf(p)), 0) || 1;

  return (
    <section className="card p-4 sm:p-5 mb-5">
      <div className="rule-label mb-1">Tune scoring — events re-rank as you drag</div>
      <p className="text-xs text-ink-500 mb-4 max-w-[70ch]">
        Every factor is a measured or grounded signal — no magic numbers. Weights are
        relative <em>emphasis</em>, normalised to 100% when scoring, so the score always
        stays 0–100. The share each factor carries is shown live.
      </p>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-4">
        {sorted.map((p: any) => {
          const v = valueOf(p);
          const meta = FACTOR_META[p.key] || { label: p.key.replace("scoring.", "").replace(/_/g, " "), measures: "" };
          const share = Math.round((Math.max(0, v) / totalW) * 100);
          return (
            <div key={p.key}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-semibold text-ink-900">{meta.label}</span>
                <span className="text-sm tabular-nums font-semibold text-ink-900">{share}%</span>
              </div>
              {meta.measures && <div className="text-xs text-ink-500 mt-0.5 leading-tight">{meta.measures}</div>}
              <input type="range" min={0} max={1} step={0.05} value={v}
                     onChange={(e) => setVals((m) => ({ ...m, [p.key]: Number(e.target.value) }))}
                     onMouseUp={() => commit.mutate({ key: p.key, value: v })}
                     onTouchEnd={() => commit.mutate({ key: p.key, value: v })}
                     className="w-full accent-brand mt-2" />
            </div>
          );
        })}
      </div>
      <p className="text-xs text-ink-500 mt-3">
        {commit.isPending ? "Re-scoring…" : "Drag to change what matters; the list re-ranks and every score stays on the same 0–100 scale."}
      </p>
    </section>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={
        "px-2.5 h-7 rounded-md text-xs font-semibold transition-colors " +
        (active ? "bg-ink-900 text-white" : "bg-ink-100 text-ink-500 hover:bg-ink-200")
      }
    >
      {children}
    </button>
  );
}

export function ConferencesPage() {
  useDocumentTitle("Events");
  const { push: toast } = useToast();
  const qc = useQueryClient();
  const [tier, setTier] = useState("All");
  const [region, setRegion] = useState("All");
  const [search, setSearch] = useState("");
  const [when, setWhen] = useState<"upcoming" | "past" | "all">("upcoming");
  const [adding, setAdding] = useState(false);
  const [tuning, setTuning] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({ region: "EU", format: "expo" });

  const { data, isLoading, error } = useQuery({
    queryKey: ["conferences", tier, region],
    queryFn: () =>
      api.get<{ total: number; count: number; items: Conference[] }>("/api/conferences", {
        query: { tier: tier === "All" ? undefined : tier, region: region === "All" ? undefined : region, limit: 300 },
      }),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<any>("/api/conferences", {
        name: form.name, start_date: form.start_date || null, city: form.city || null,
        country: form.country || null, region: form.region || null, vertical: form.vertical || null,
        format: form.format || null, themes: form.themes || null,
        estimated_attendance: form.attendance ? Number(form.attendance) : null,
      }),
    onSuccess: (d) => {
      toast("success", `Added — scored ${d.score}, Tier ${d.tier}`);
      setAdding(false); setForm({ region: "EU", format: "expo" });
      qc.invalidateQueries({ queryKey: ["conferences"] });
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const filtered = (data?.items || []).filter((c) => {
    if (when !== "all") {
      const end = c.end_date || c.start_date || "";
      const upcoming = end >= TODAY;
      if (when === "upcoming" && !upcoming) return false;
      if (when === "past" && upcoming) return false;
    }
    if (search.trim()) {
      return (c.name + " " + (c.city || "") + " " + (c.vertical || ""))
        .toLowerCase().includes(search.toLowerCase());
    }
    return true;
  });

  const set = (k: string) => (e: any) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div>
      <h1 className="text-2xl mb-1">Events</h1>
      <SubTabs items={EVENTS_TABS} />
      <div className="flex items-end justify-between gap-4 mb-5">
        <p className="text-sm text-ink-500 max-w-[58ch]">
          Ranked by Grain ICP fit — finance/treasury buyer density and FX
          exposure, not raw size.
        </p>
        <div className="flex gap-2 shrink-0">
          <button className="btn-secondary" onClick={() => setTuning((v) => !v)}>
            {tuning ? "Done tuning" : "Tune scoring"}
          </button>
          <button className="btn-secondary" onClick={() => setAdding((v) => !v)}>
            {adding ? "Cancel" : "+ New event"}
          </button>
        </div>
      </div>

      {tuning && <ScoringTuner />}

      {adding && (
        <section className="card p-4 sm:p-5 mb-5">
          <div className="rule-label mb-4">Add an event to track</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <input className="input col-span-2 sm:col-span-3" placeholder="Event name *" value={form.name || ""} onChange={set("name")} />
            <input className="input" type="date" value={form.start_date || ""} onChange={set("start_date")} />
            <input className="input" placeholder="City" value={form.city || ""} onChange={set("city")} />
            <input className="input" placeholder="Country" value={form.country || ""} onChange={set("country")} />
            <select className="input" value={form.region} onChange={set("region")}>
              {["NA", "EU", "APAC", "MEA", "LATAM"].map((r) => <option key={r}>{r}</option>)}
            </select>
            <input className="input" placeholder="Vertical (payments, travel…)" value={form.vertical || ""} onChange={set("vertical")} />
            <select className="input" value={form.format} onChange={set("format")}>
              {["expo", "summit", "conference", "webinar"].map((f) => <option key={f}>{f}</option>)}
            </select>
            <input className="input col-span-2" placeholder="Themes (comma-separated: cross-border, FX, treasury)" value={form.themes || ""} onChange={set("themes")} />
            <input className="input" type="number" placeholder="Attendance" value={form.attendance || ""} onChange={set("attendance")} />
          </div>
          <div className="flex items-center gap-3 mt-3">
            <button className="btn-primary" disabled={!form.name?.trim() || create.isPending} onClick={() => create.mutate()}>
              {create.isPending ? "Scoring…" : "Add & score"}
            </button>
            <span className="text-xs text-ink-500">Scored instantly by the same 7-factor model.</span>
          </div>
        </section>
      )}

      <div className="flex flex-wrap gap-x-5 gap-y-2 items-center mb-4">
        <input type="search" placeholder="Search name, city, vertical…" value={search}
               onChange={(e) => setSearch(e.target.value)} className="input w-full sm:w-72" />
        <div className="flex gap-1 items-center">
          <span className="label mr-1">When</span>
          {(["upcoming", "past", "all"] as const).map((w) => (
            <Chip key={w} active={when === w} onClick={() => setWhen(w)}>
              {w[0].toUpperCase() + w.slice(1)}
            </Chip>
          ))}
        </div>
        <div className="flex gap-1 items-center">
          <span className="label mr-1">Tier</span>
          {TIERS.map((t) => <Chip key={t} active={tier === t} onClick={() => setTier(t)}>{t}</Chip>)}
        </div>
        <div className="flex gap-1 items-center">
          <span className="label mr-1">Region</span>
          {REGIONS.map((r) => <Chip key={r} active={region === r} onClick={() => setRegion(r)}>{r}</Chip>)}
        </div>
      </div>

      {isLoading && <div className="text-sm text-ink-500">Loading…</div>}
      {error && <div className="card p-4 text-tire text-sm">Error: {toastErrorMessage(error)}</div>}

      <div className="card divide-y divide-ink-100 overflow-hidden">
        {filtered.map((c) => <EventRow key={c.id} e={c} hideCoverage />)}
        {filtered.length === 0 && !isLoading && (
          <div className="p-8 text-center text-sm text-ink-500">No events match these filters.</div>
        )}
      </div>
    </div>
  );
}
