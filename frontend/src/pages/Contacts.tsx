import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ArcBadge } from "@/components/Badges";
import { ReviewQueue } from "@/components/ReviewQueue";
import { SubTabs } from "@/components/SubTabs";

const ARCS = ["All", "warming", "flat", "cooling", "tire_kicker"];
const PEOPLE_TABS = [
  { to: "/contacts", label: "Contacts" },
  { to: "/companies", label: "Companies" },
  { to: "/nudges", label: "Follow-ups" },
];

export function ContactsPage() {
  useDocumentTitle("Contacts");
  const [arcFilter, setArcFilter] = useState("All");
  const { data, isLoading } = useQuery({
    queryKey: ["contacts", arcFilter],
    queryFn: () =>
      api.get<{ items: any[]; total: number }>("/api/contacts", {
        query: { arc_verdict: arcFilter === "All" ? undefined : arcFilter, limit: 200 },
      }),
  });

  return (
    <div>
      <p className="text-sm text-ink-500 mb-4">
        Everyone you've met, resolved into one record per person.
      </p>
      <SubTabs items={PEOPLE_TABS} />

      <ReviewQueue />

      <div className="card p-3 mb-4 flex gap-2 items-center">
        <span className="label">Arc:</span>
        {ARCS.map((a) => (
          <button
            key={a}
            onClick={() => setArcFilter(a)}
            className={
              "btn text-xs " +
              (arcFilter === a
                ? "bg-brand text-white"
                : "bg-ink-100 text-ink-500 border border-ink-200")
            }
          >
            {a.replace("_", "-")}
          </button>
        ))}
        <span className="ml-auto text-xs text-ink-500">
          {data?.total ?? "—"} total
        </span>
      </div>

      {isLoading && <div className="text-sm text-ink-500">Loading…</div>}
      <div className="card divide-y divide-ink-100 overflow-hidden">
        {data?.items.map((c) => (
          <Link
            key={c.id}
            to={`/contacts/${c.id}`}
            className="flex items-center gap-3 px-4 py-3 hover:bg-ink-50 transition-colors"
          >
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-ink-900 flex items-center gap-2">
                {c.primary_name}
                <ArcBadge kind={c.arc_verdict} />
                {c.nudge_active ? (
                  <span className="stamp" style={{ color: "oklch(0.45 0.1 62)", background: "oklch(0.96 0.04 62)", borderColor: "oklch(0.88 0.06 62)" }}>nudge</span>
                ) : null}
              </div>
              <div className="text-xs text-ink-500 mt-0.5 truncate">
                {c.primary_title || "?"} @ {c.primary_company || "?"}
                {c.arc_summary && <span className="italic"> — "{c.arc_summary.slice(0, 110)}"</span>}
              </div>
            </div>
            <div className="text-ink-300">→</div>
          </Link>
        ))}
        {data && data.items.length === 0 && (
          <div className="p-8 text-center text-sm text-ink-500">
            No contacts yet. Capture a few leads from the Capture tab.
          </div>
        )}
      </div>
    </div>
  );
}
