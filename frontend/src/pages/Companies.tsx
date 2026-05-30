import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TierBadge } from "@/components/Badges";
import { toastErrorMessage } from "@/components/Toast";
import { SubTabs } from "@/components/SubTabs";

const PEOPLE_TABS = [
  { to: "/contacts", label: "Contacts" },
  { to: "/companies", label: "Companies" },
  { to: "/nudges", label: "Follow-ups" },
];

interface Company {
  id: string;
  name: string;
  vertical?: string | null;
  account_tier?: string | null;
  source_kind?: string | null;
  logo_url?: string | null;
  industry?: string | null;
  people_count?: number | null;
  conference_count?: number | null;
  icp_score?: number | null;
}

// The endpoint returns {count, items}; tolerate a bare array too so we don't
// break if the shape ever settles differently.
function normalize(data: unknown): Company[] {
  if (Array.isArray(data)) return data as Company[];
  if (data && typeof data === "object" && Array.isArray((data as any).items)) {
    return (data as any).items as Company[];
  }
  return [];
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

function Logo({ url, name }: { url?: string | null; name: string }) {
  const [failed, setFailed] = useState(false);
  const showImg = url && !failed;
  return (
    <div className="rounded bg-ink-100 flex items-center justify-center shrink-0 overflow-hidden w-9 h-9">
      {showImg ? (
        <img
          src={url!}
          alt={name}
          className="object-contain w-7 h-7"
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="text-xs font-bold text-ink-500">{name.slice(0, 2).toUpperCase()}</span>
      )}
    </div>
  );
}

export function CompaniesPage() {
  useDocumentTitle("Companies");
  const [vertical, setVertical] = useState("All");
  const [search, setSearch] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["companies"],
    queryFn: () => api.get<unknown>("/api/companies", { query: { limit: 300 } }),
  });

  const companies = normalize(data);

  // Build the vertical filter list from whatever the data actually contains.
  const verticals = ["All", ...Array.from(
    new Set(companies.map((c) => c.vertical).filter((v): v is string => !!v)),
  ).sort()];

  const filtered = companies
    .filter((c) => {
      if (vertical !== "All" && (c.vertical || "") !== vertical) return false;
      if (search.trim()) {
        return (c.name + " " + (c.vertical || ""))
          .toLowerCase()
          .includes(search.toLowerCase());
      }
      return true;
    })
    .sort((a, b) => (b.people_count ?? 0) - (a.people_count ?? 0));

  return (
    <div>
      <p className="text-sm text-ink-500 mb-4 max-w-[60ch]">
        The accounts — one record per company, ranked by people met.
      </p>
      <SubTabs items={PEOPLE_TABS} />

      <div className="flex flex-wrap gap-x-5 gap-y-2 items-center mb-4">
        <input
          type="search"
          placeholder="Search name, vertical…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input w-full sm:w-72"
        />
        {verticals.length > 1 && (
          <div className="flex gap-1 items-center flex-wrap">
            <span className="label mr-1">Vertical</span>
            {verticals.map((v) => (
              <Chip key={v} active={vertical === v} onClick={() => setVertical(v)}>
                {v}
              </Chip>
            ))}
          </div>
        )}
        <span className="sm:ml-auto text-xs text-ink-500 tabular-nums">{filtered.length} shown</span>
      </div>

      {isLoading && <div className="text-sm text-ink-500">Loading…</div>}
      {error && <div className="card p-4 text-red-700 text-sm">Error: {toastErrorMessage(error)}</div>}

      <div className="card divide-y divide-ink-100 overflow-hidden">
        {filtered.map((c) => (
          <Link
            to={`/companies/${c.id}`}
            key={c.id}
            className="flex items-center gap-4 px-4 py-3 hover:bg-ink-50 transition-colors"
          >
            <Logo url={c.logo_url} name={c.name} />
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-ink-900 flex items-center gap-2 flex-wrap">
                <span className="truncate">{c.name}</span>
                {c.account_tier && <TierBadge tier={c.account_tier} />}
                {c.source_kind === "discovered" && (
                  <span className="badge bg-purple-100 text-purple-800">🔍 discovered</span>
                )}
              </div>
              <div className="text-xs text-ink-500 mt-0.5 truncate">
                <span className="text-ink-700 font-medium">{c.vertical || c.industry || "company"}</span>
                {" · "}{(c.people_count ?? 0).toLocaleString()} people
                {c.conference_count ? ` · ${c.conference_count} confs` : ""}
              </div>
            </div>
            <div className="text-ink-300 text-lg">→</div>
          </Link>
        ))}
        {filtered.length === 0 && !isLoading && !error && (
          <div className="p-8 text-center text-sm text-ink-500">
            {companies.length === 0
              ? "No companies yet. They appear once contacts and people are linked to accounts."
              : "No companies match these filters."}
          </div>
        )}
      </div>
    </div>
  );
}
