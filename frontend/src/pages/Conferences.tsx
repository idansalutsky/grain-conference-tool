import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TierBadge } from "@/components/Badges";
import { useToast, toastErrorMessage } from "@/components/Toast";

interface Conference {
  id: string; name: string; start_date?: string; end_date?: string;
  city?: string; country?: string; region?: string; vertical?: string;
  format?: string; estimated_attendance?: number; score?: number; tier?: string;
}

const TIERS = ["All", "A", "B", "C"];
const REGIONS = ["All", "NA", "EU", "APAC", "MEA", "LATAM"];

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
  const [adding, setAdding] = useState(false);
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

  const filtered = data?.items.filter((c) =>
    search.trim()
      ? (c.name + " " + (c.city || "") + " " + (c.vertical || "")).toLowerCase().includes(search.toLowerCase())
      : true,
  ) || [];

  const set = (k: string) => (e: any) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div>
      <header className="flex items-end justify-between gap-4 mb-5">
        <div>
          <h1 className="text-2xl">Events</h1>
          <p className="text-sm text-ink-500 mt-1 max-w-[60ch]">
            {data?.total ?? "—"} events, ranked by Grain ICP fit — finance/treasury
            buyer density and FX exposure, not raw size.
          </p>
        </div>
        <button className="btn-secondary shrink-0" onClick={() => setAdding((v) => !v)}>
          {adding ? "Cancel" : "+ New event"}
        </button>
      </header>

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
          <span className="label mr-1">Tier</span>
          {TIERS.map((t) => <Chip key={t} active={tier === t} onClick={() => setTier(t)}>{t}</Chip>)}
        </div>
        <div className="flex gap-1 items-center">
          <span className="label mr-1">Region</span>
          {REGIONS.map((r) => <Chip key={r} active={region === r} onClick={() => setRegion(r)}>{r}</Chip>)}
        </div>
        <span className="sm:ml-auto text-xs text-ink-500 tabular-nums">{filtered.length} shown</span>
      </div>

      {isLoading && <div className="text-sm text-ink-500">Loading…</div>}
      {error && <div className="card p-4 text-tire text-sm">Error: {toastErrorMessage(error)}</div>}

      <div className="card divide-y divide-ink-100 overflow-hidden">
        {filtered.map((c) => (
          <Link to={`/conferences/${c.id}`} key={c.id}
                className="flex items-center gap-4 px-4 py-3 hover:bg-ink-50 transition-colors">
            <div className="font-display text-2xl font-bold w-12 text-right tabular-nums text-ink-900">
              {c.score?.toFixed(0) ?? "—"}
            </div>
            <TierBadge tier={c.tier} />
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-ink-900 truncate">{c.name}</div>
              <div className="text-xs text-ink-500 mt-0.5 truncate">
                {c.start_date} · {c.city || "?"}, {c.country || "?"} · {c.format || "?"}
                {c.estimated_attendance ? ` · ${c.estimated_attendance.toLocaleString()} attendees` : ""}
                {" · "}<span className="text-ink-700 font-medium">{c.vertical || "?"}</span>
              </div>
            </div>
            <div className="text-ink-300 text-lg">→</div>
          </Link>
        ))}
        {filtered.length === 0 && !isLoading && (
          <div className="p-8 text-center text-sm text-ink-500">No events match these filters.</div>
        )}
      </div>
    </div>
  );
}
