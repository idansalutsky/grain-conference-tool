import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useToast, toastErrorMessage } from "@/components/Toast";
import { SubTabs } from "@/components/SubTabs";
import { EVENTS_TABS } from "@/components/eventsTabs";

const REGIONS = ["any", "NA", "EU", "APAC", "MEA", "LATAM"];

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

  // Close the loop: research the untracked events buyers mentioned — verify each
  // + find its next occurrence → pending proposals you approve.
  const researchMentioned = useMutation({
    mutationFn: () =>
      api.post<{ researched: number; proposals: any[]; not_found: string[] }>(
        "/api/discovery/mentioned/research",
      ),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["discovery-pending"] });
      qc.invalidateQueries({ queryKey: ["discovery-mentioned"] });
      const found = d.proposals?.length || 0;
      toast(
        "success",
        found
          ? `Verified ${found} — added to the approval queue below`
          : "Researched — none could be confirmed as upcoming events",
      );
    },
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
      <p className="text-sm text-ink-500 mb-4 max-w-[66ch]">
        Two engines, one queue. We <span className="text-ink-700 font-medium">scan the web</span> for
        ICP-fit conferences you don't track, and we <span className="text-ink-700 font-medium">listen to
        your conversations</span> — the events your own buyers mention in the field. Both feed the
        approval queue below; nothing is added without your call, and anything approved is auto-scored.
      </p>

      <div className="rule-label mb-2"><span>1 · Scan the web</span></div>
      <section className="card p-4 mb-5">
        <p className="text-xs text-ink-500 mb-3 max-w-[62ch]">
          Perplexity Sonar searches for upcoming events relevant to Grain's ICP that aren't in the database.
        </p>
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

      <div className="rule-label mb-2"><span>2 · From your conversations</span></div>
      {mentioned.data?.events?.length ? (
        <section className="card p-4 mb-5">
          <div className="flex items-start justify-between gap-3 mb-1">
            <p className="text-xs text-ink-500 max-w-[58ch]">
              When a buyer tells a rep where they go, that's a signal. Untracked events
              several buyers mention are strong candidates — research one and the agent
              verifies it + finds its next date before it reaches the queue.
            </p>
            {mentioned.data.events.some((e: any) => !e.tracked) && (
              <button
                onClick={() => researchMentioned.mutate()}
                disabled={researchMentioned.isPending}
                className="btn-primary text-xs shrink-0"
              >
                {researchMentioned.isPending ? "Researching…" : "Research the untracked →"}
              </button>
            )}
          </div>
          <div className="divide-y divide-ink-100 mt-2">
            {mentioned.data.events.map((e: any) => (
              <div key={e.name} className="py-2.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-display font-semibold text-ink-900">{e.name}</span>
                  {e.tracked ? (
                    <span className="stamp" style={{ color: "oklch(0.5 0.015 160)", background: "oklch(0.95 0.006 160)", borderColor: "oklch(0.88 0.01 160)" }}>tracked</span>
                  ) : (
                    <span className="stamp" style={{ color: "oklch(0.48 0.13 55)", background: "oklch(0.96 0.04 70)", borderColor: "oklch(0.86 0.07 65)" }}>not tracked</span>
                  )}
                  <span className="text-xs text-ink-400">{e.contacts} buyer{e.contacts === 1 ? "" : "s"} mentioned</span>
                </div>
                {/* provenance — which buyer (and which ICP company) told which rep */}
                {e.sources?.length ? (
                  <div className="text-xs text-ink-500 mt-1 flex flex-wrap gap-x-2 gap-y-0.5">
                    {e.sources.map((s: any, i: number) => (
                      <span key={i}>
                        <span className="text-ink-700">{s.contact}</span>
                        {s.company ? ` · ${s.company}` : ""}
                        {s.rep ? <span className="text-ink-400"> → told {s.rep}</span> : ""}
                        {i < e.sources.length - 1 ? <span className="text-ink-300"> ·</span> : ""}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
          {researchMentioned.data?.not_found?.length ? (
            <p className="text-xs text-ink-500 mt-3">
              Couldn't confirm as upcoming events: {researchMentioned.data.not_found.join(", ")}.
            </p>
          ) : null}
        </section>
      ) : (
        <section className="card p-4 mb-5 text-sm text-ink-500">
          No events mentioned in conversations yet — they appear here as reps capture
          encounters where a buyer names an event they attend.
        </section>
      )}

      <div className="rule-label mb-2">
        <span>The queue · {pending.data?.proposals?.length || 0} awaiting your approval</span>
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
