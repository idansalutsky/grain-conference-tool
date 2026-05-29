import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ArcBadge } from "@/components/Badges";
import { ReviewQueue } from "@/components/ReviewQueue";

const ARCS = ["All", "warming", "flat", "cooling", "tire_kicker"];

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
      <h1 className="text-2xl mb-1">Contacts</h1>
      <p className="text-sm text-ink-500 mb-4">
        Canonical people across every conference — with the arc verdict.
      </p>

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

      <div className="space-y-2">
        {isLoading && <div className="text-sm text-ink-500">Loading…</div>}
        {data?.items.map((c) => (
          <Link
            key={c.id}
            to={`/contacts/${c.id}`}
            className="card p-3 flex items-center gap-3 hover:shadow-md transition-shadow"
          >
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-ink-900 flex items-center gap-2">
                {c.primary_name}
                <ArcBadge kind={c.arc_verdict} />
                {c.nudge_active && (
                  <span className="badge bg-amber-100 text-amber-800">💡 nudge</span>
                )}
              </div>
              <div className="text-xs text-ink-500 mt-0.5">
                {c.primary_title || "?"} @ {c.primary_company || "?"}
                {c.arc_summary && <span className="italic"> — "{c.arc_summary.slice(0, 100)}"</span>}
              </div>
            </div>
            <div className="text-ink-400">→</div>
          </Link>
        ))}
        {data && data.items.length === 0 && (
          <div className="card p-6 text-center text-sm text-ink-500">
            No contacts yet. Capture a few leads from the Capture tab.
          </div>
        )}
      </div>
    </div>
  );
}
