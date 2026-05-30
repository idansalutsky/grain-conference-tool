import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { useToast, toastErrorMessage } from "@/components/Toast";
import { SubTabs } from "@/components/SubTabs";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

const ADMIN_TABS = [
  { to: "/team", label: "Team" },
  { to: "/settings", label: "Settings" },
];

interface Rep {
  id: string; full_name: string; email: string | null; region: string | null;
  events_covered: number; captures: number;
}
interface CoverageItem {
  conference_id: string; conference_name: string; start_date: string | null;
  city: string | null; country: string | null; tier: string | null; rep_id: string;
}
interface EventLink {
  id: string; name: string; start_date: string | null; city: string | null; tier: string | null;
}
interface EventLinksResponse {
  rep_id: string; rep_name: string; deep_link: string;
  events: EventLink[]; message_text: string;
}

const REGIONS = ["NA", "EU", "APAC", "MEA", "LATAM"];

export function TeamPage() {
  useDocumentTitle("Team");
  const { push: toast } = useToast();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [region, setRegion] = useState("EU");
  const [openRep, setOpenRep] = useState<string | null>(null);
  const [tripRep, setTripRep] = useState<string | null>(null);
  const [trip, setTrip] = useState<EventLinksResponse | null>(null);
  const [tripLoading, setTripLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  async function sendTrip(id: string) {
    if (tripRep === id) { setTripRep(null); setTrip(null); return; }
    setTripRep(id); setTrip(null); setCopied(false); setTripLoading(true);
    try {
      const data = await api.get<EventLinksResponse>(`/api/reps/${id}/event-links`);
      setTrip(data);
    } catch (e) {
      toast("error", toastErrorMessage(e));
      setTripRep(null);
    } finally {
      setTripLoading(false);
    }
  }

  async function copyTrip() {
    if (!trip) return;
    try {
      await navigator.clipboard.writeText(trip.message_text);
      setCopied(true);
      toast("success", "Message copied — paste it to your rep");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast("error", "Couldn't copy — select the text and copy manually");
    }
  }

  const reps = useQuery({ queryKey: ["reps"], queryFn: () => api.get<{ items: Rep[] }>("/api/reps") });
  const coverage = useQuery({
    queryKey: ["coverage-all"],
    queryFn: () => api.get<{ items: CoverageItem[] }>("/api/coverage"),
  });

  const add = useMutation({
    mutationFn: () => api.post("/api/reps", { full_name: name, email: email || null, region }),
    onSuccess: () => { setName(""); setEmail(""); toast("success", "Rep added"); qc.invalidateQueries({ queryKey: ["reps"] }); },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/api/reps/${id}`),
    onSuccess: () => { toast("success", "Rep removed"); qc.invalidateQueries({ queryKey: ["reps"] }); qc.invalidateQueries({ queryKey: ["coverage-all"] }); },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const covByRep = (id: string) => coverage.data?.items.filter((c) => c.rep_id === id) ?? [];

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-2xl mb-1">Admin</h1>
        <SubTabs items={ADMIN_TABS} />
        <h2 className="text-lg">The team</h2>
        <p className="text-sm text-ink-500 mt-1 max-w-[65ch]">
          Who's on the floor, and who covers which events. Add a rep, then assign
          them to events from any event's page — each gets a one-tap Telegram bind
          so their captures attribute automatically.
        </p>
      </header>

      {/* Add rep — quiet inline form, not a modal */}
      <section className="card p-4 sm:p-5">
        <div className="rule-label mb-4">Add a rep</div>
        <div className="flex flex-col sm:flex-row gap-2">
          <input className="input flex-1" placeholder="Full name" value={name}
                 onChange={(e) => setName(e.target.value)} />
          <input className="input flex-1" placeholder="Email (optional)" value={email}
                 onChange={(e) => setEmail(e.target.value)} />
          <select className="input sm:w-32" value={region} onChange={(e) => setRegion(e.target.value)}>
            {REGIONS.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <button className="btn-primary sm:w-auto" disabled={!name.trim() || add.isPending}
                  onClick={() => add.mutate()}>
            {add.isPending ? "Adding…" : "Add rep"}
          </button>
        </div>
      </section>

      {/* Roster */}
      <section>
        <div className="rule-label mb-3">Roster · {reps.data?.items.length ?? 0}</div>
        <div className="card divide-y divide-ink-100">
          {reps.data?.items.map((r) => {
            const cov = covByRep(r.id);
            const isOpen = openRep === r.id;
            return (
              <div key={r.id} className="p-4">
                <div className="flex items-center gap-3">
                  <div className="grid place-items-center w-9 h-9 rounded-full bg-ink-100 text-ink-700 font-semibold text-sm shrink-0">
                    {r.full_name.split(" ").map((p) => p[0]).slice(0, 2).join("")}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-ink-900 truncate">{r.full_name}</div>
                    <div className="text-xs text-ink-500 truncate">{r.email || "—"}</div>
                  </div>
                  {r.region && <span className="stamp" style={{ color: "oklch(0.36 0.02 164)", background: "oklch(0.95 0.01 164)", borderColor: "oklch(0.88 0.012 164)" }}>{r.region}</span>}
                  <div className="text-right hidden sm:block">
                    <div className="text-sm font-semibold tabular-nums">{r.events_covered}</div>
                    <div className="text-[0.65rem] uppercase tracking-wider text-ink-500">events</div>
                  </div>
                  <div className="text-right hidden sm:block">
                    <div className="text-sm font-semibold tabular-nums">{r.captures}</div>
                    <div className="text-[0.65rem] uppercase tracking-wider text-ink-500">captures</div>
                  </div>
                  <button className="btn-ghost h-8 !px-2 text-xs" onClick={() => setOpenRep(isOpen ? null : r.id)}>
                    {cov.length} {cov.length === 1 ? "event" : "events"} {isOpen ? "▲" : "▼"}
                  </button>
                  <button className="btn-ghost h-8 !px-2 text-xs" title="Get a paste-ready trip message + bind link to send this rep"
                          disabled={tripRep === r.id && tripLoading}
                          onClick={() => sendTrip(r.id)}>
                    {tripRep === r.id && tripLoading ? "…" : "📤 Send links"}
                  </button>
                  <button className="btn-ghost h-8 !px-2 text-ink-500 hover:text-tire" title="Remove rep"
                          onClick={() => remove.mutate(r.id)}>✕</button>
                </div>
                {tripRep === r.id && trip && (
                  <div className="mt-3 pl-12 space-y-2">
                    <div className="rule-label">Trip handoff for {trip.rep_name}</div>
                    <pre className="card p-3 text-sm text-ink-800 whitespace-pre-wrap font-sans bg-ink-50">{trip.message_text}</pre>
                    {trip.events.length > 0 && (
                      <div className="text-xs text-ink-500">
                        {trip.events.length} {trip.events.length === 1 ? "event" : "events"}:{" "}
                        {trip.events.map((e) => e.name).join(" · ")}
                      </div>
                    )}
                    <div className="flex gap-2">
                      <button className="btn-primary h-8 !px-3 text-xs" onClick={copyTrip}>
                        {copied ? "✓ Copied" : "Copy message"}
                      </button>
                      <button className="btn-ghost h-8 !px-3 text-xs" onClick={() => { setTripRep(null); setTrip(null); }}>
                        Close
                      </button>
                    </div>
                  </div>
                )}
                {isOpen && (
                  <div className="mt-3 pl-12 space-y-1.5">
                    {cov.length === 0 && <div className="text-sm text-ink-500">Not assigned to any event yet.</div>}
                    {cov.map((c) => (
                      <Link key={c.conference_id} to={`/conferences/${c.conference_id}`}
                            className="flex items-center gap-2 text-sm hover:text-brand">
                        <span className="text-ink-400 tabular-nums text-xs w-20">{(c.start_date || "").slice(0, 10)}</span>
                        <span className="truncate">{c.conference_name}</span>
                        <span className="text-ink-400 text-xs">· {c.city || c.country}</span>
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          {reps.data?.items.length === 0 && (
            <div className="p-6 text-sm text-ink-500">No reps yet — add your first above.</div>
          )}
        </div>
      </section>
    </div>
  );
}
