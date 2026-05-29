import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TierBadge } from "@/components/Badges";
import { toastErrorMessage } from "@/components/Toast";

interface Conference {
  id: string;
  name: string;
  start_date?: string;
  end_date?: string;
  city?: string;
  country?: string;
  region?: string;
  vertical?: string;
  format?: string;
  estimated_attendance?: number;
  score?: number;
  tier?: string;
}

const TIERS = ["All", "A", "B", "C"];
const REGIONS = ["All", "NA", "EU", "APAC", "MEA", "LATAM"];

export function ConferencesPage() {
  useDocumentTitle("Conferences");
  const [tier, setTier] = useState("All");
  const [region, setRegion] = useState("All");
  const [search, setSearch] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["conferences", tier, region],
    queryFn: () =>
      api.get<{ total: number; count: number; items: Conference[] }>(
        "/api/conferences",
        {
          query: {
            tier: tier === "All" ? undefined : tier,
            region: region === "All" ? undefined : region,
            limit: 200,
          },
        },
      ),
  });

  const filtered =
    data?.items.filter((c) =>
      search.trim()
        ? (c.name + " " + (c.city || "") + " " + (c.vertical || ""))
            .toLowerCase()
            .includes(search.toLowerCase())
        : true,
    ) || [];

  return (
    <div>
      <div className="flex justify-between items-end mb-4">
        <div>
          <h1 className="text-2xl">Conferences</h1>
          <p className="text-sm text-ink-500 mt-1">
            {data?.total ?? "—"} events, ranked by Grain ICP fit (7-factor scoring)
          </p>
        </div>
      </div>

      <div className="card p-3 mb-4 flex flex-wrap gap-3 items-center">
        <input
          type="search"
          placeholder="Search by name, city, vertical…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input w-64"
        />
        <div className="flex gap-1">
          <span className="label self-center mr-1">Tier:</span>
          {TIERS.map((t) => (
            <button
              key={t}
              onClick={() => setTier(t)}
              className={
                "btn text-xs " +
                (tier === t
                  ? "bg-brand text-white"
                  : "bg-ink-100 text-ink-500 border border-ink-200")
              }
            >
              {t}
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          <span className="label self-center mr-1">Region:</span>
          {REGIONS.map((r) => (
            <button
              key={r}
              onClick={() => setRegion(r)}
              className={
                "btn text-xs " +
                (region === r
                  ? "bg-brand text-white"
                  : "bg-ink-100 text-ink-500 border border-ink-200")
              }
            >
              {r}
            </button>
          ))}
        </div>
        <span className="ml-auto text-xs text-ink-500">
          Showing {filtered.length} of {data?.count ?? 0}
        </span>
      </div>

      {isLoading && <div className="text-sm text-ink-500">Loading…</div>}
      {error && <div className="card p-4 text-red-700 text-sm">Error: {toastErrorMessage(error)}</div>}

      <div className="space-y-2">
        {filtered.map((c) => (
          <Link
            to={`/conferences/${c.id}`}
            key={c.id}
            className="card p-3 flex items-center gap-4 hover:shadow-md transition-shadow"
          >
            <div className="text-2xl font-bold w-14 text-right text-ink-900">
              {c.score?.toFixed(0) ?? "—"}
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-ink-900 truncate flex items-center gap-2">
                <TierBadge tier={c.tier} />
                {c.name}
              </div>
              <div className="text-xs text-ink-500 mt-0.5">
                {c.start_date} · {c.city || "?"}, {c.country || "?"} · {c.format || "?"} ·{" "}
                {c.estimated_attendance
                  ? `${c.estimated_attendance.toLocaleString()} attendees`
                  : "no attendance estimate"}{" "}
                · <span className="font-medium text-ink-700">{c.vertical || "?"}</span>
              </div>
            </div>
            <div className="text-ink-400 text-lg">→</div>
          </Link>
        ))}
        {filtered.length === 0 && !isLoading && (
          <div className="card p-6 text-center text-sm text-ink-500">
            No conferences match.
          </div>
        )}
      </div>
    </div>
  );
}
