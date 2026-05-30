import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useToast, toastErrorMessage } from "@/components/Toast";
import { SubTabs } from "@/components/SubTabs";

const REGIONS = ["any", "NA", "EU", "APAC", "MEA", "LATAM"];
const EVENTS_TABS = [
  { to: "/conferences", label: "Browse events" },
  { to: "/discovery", label: "✨ Find new" },
];

export function DiscoveryPage() {
  useDocumentTitle("Find events");
  const { push: toast } = useToast();
  const qc = useQueryClient();
  const [region, setRegion] = useState("any");
  const [maxResults, setMaxResults] = useState(5);

  const pending = useQuery({
    queryKey: ["discovery-pending"],
    queryFn: () =>
      api.get<{ proposals: any[] }>("/api/discovery/pending"),
  });

  // Events your own buyers told reps they attend — ground-up event intelligence
  // from real conversations (a second discovery source besides web search).
  const mentioned = useQuery({
    queryKey: ["discovery-mentioned"],
    queryFn: () => api.get<{ events: any[] }>("/api/discovery/mentioned"),
  });

  const search = useMutation({
    mutationFn: () =>
      api.post<{ proposals: any[] }>("/api/discovery/conferences", {
        region: region === "any" ? null : region,
        max_results: maxResults,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["discovery-pending"] }),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const approve = useMutation({
    mutationFn: (id: string) => api.post<any>(`/api/discovery/${id}/approve`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["discovery-pending"] });
      qc.invalidateQueries({ queryKey: ["conferences"] });
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const reject = useMutation({
    mutationFn: (id: string) =>
      api.post<any>(`/api/discovery/${id}/reject`, { reason: "not relevant" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["discovery-pending"] }),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  return (
    <div>
      <h1 className="text-2xl mb-1">Events</h1>
      <SubTabs items={EVENTS_TABS} />
      <h2 className="text-lg mb-1">Find events you don't already know about</h2>
      <p className="text-sm text-ink-500 mb-4 max-w-[60ch]">
        Ask Perplexity Sonar to surface upcoming events relevant to Grain's ICP
        that aren't in our database. Approve the ones that look right — they
        get auto-scored against the same 7-factor model.
      </p>

      <section className="card p-4 mb-4">
        <div className="flex flex-wrap gap-3 items-center">
          <div>
            <span className="label">Region</span>
            <div className="flex gap-1 mt-1">
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
          </div>
          <div>
            <span className="label">Results</span>
            <input
              type="number"
              value={maxResults}
              onChange={(e) => setMaxResults(Number(e.target.value))}
              min={1} max={10}
              className="input w-16 mt-1 block"
            />
          </div>
          <button
            onClick={() => search.mutate()}
            disabled={search.isPending}
            className="btn-primary text-sm ml-auto"
          >
            {search.isPending ? "Searching the web…" : "🔍 Discover new events"}
          </button>
        </div>
      </section>

      {search.error && (
        <div className="card p-3 mb-4 text-red-700 text-sm">
          {toastErrorMessage(search.error)}
        </div>
      )}

      {!!mentioned.data?.events?.length && (
        <section className="card p-4 mb-4">
          <div className="label mb-1">Events your buyers mention</div>
          <p className="text-xs text-ink-500 mb-3 max-w-[60ch]">
            Pulled from real conversations — when a contact tells a rep where
            they go, that's a signal. An event several buyers mention that we
            don't track yet is a strong one to add.
          </p>
          <div className="flex flex-wrap gap-2">
            {mentioned.data.events.map((e: any) => (
              <span
                key={e.name}
                className={
                  "badge " +
                  (e.tracked
                    ? "bg-ink-100 text-ink-600"
                    : "bg-amber-100 text-amber-900 border border-amber-300")
                }
                title={`${e.contacts} contact(s) mentioned this`}
              >
                {e.name} · {e.contacts}
                {!e.tracked && " · not tracked"}
              </span>
            ))}
          </div>
        </section>
      )}

      <div className="label mb-2">
        Pending approval ({pending.data?.proposals?.length || 0})
      </div>
      <div className="space-y-2">
        {pending.data?.proposals?.map((p) => (
          <div key={p.proposal_id} className="card p-4">
            <div className="flex justify-between items-start gap-3">
              <div className="flex-1">
                <div className="font-semibold">{p.name}</div>
                <div className="text-xs text-ink-500 mt-0.5">
                  {p.start_date || "?"} · {p.city || "?"}, {p.country || "?"} ·{" "}
                  <span className="text-ink-700">{p.vertical || "?"}</span>
                </div>
                {p.why_relevant && (
                  <p className="text-sm text-ink-700 italic mt-2">
                    "{p.why_relevant}"
                  </p>
                )}
                {p.source_url && (
                  <a
                    href={p.source_url}
                    target="_blank" rel="noreferrer"
                    className="text-xs text-brand hover:underline mt-1 inline-block"
                  >
                    source ↗
                  </a>
                )}
              </div>
              <div className="flex flex-col gap-1.5">
                <button
                  onClick={() => approve.mutate(p.proposal_id)}
                  disabled={approve.isPending}
                  className="btn-primary text-xs"
                >
                  Approve
                </button>
                <button
                  onClick={() => reject.mutate(p.proposal_id)}
                  disabled={reject.isPending}
                  className="btn-secondary text-xs"
                >
                  Reject
                </button>
              </div>
            </div>
          </div>
        ))}
        {pending.data && pending.data.proposals.length === 0 && (
          <div className="card p-6 text-sm text-ink-500 text-center">
            No pending proposals. Click "Discover new events" above.
          </div>
        )}
      </div>
    </div>
  );
}
