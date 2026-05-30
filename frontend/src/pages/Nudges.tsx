import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { ArcBadge } from "@/components/Badges";
import { SubTabs } from "@/components/SubTabs";
import { useToast, toastErrorMessage } from "@/components/Toast";

const PEOPLE_TABS = [
  { to: "/contacts", label: "Contacts" },
  { to: "/companies", label: "Companies" },
  { to: "/nudges", label: "Follow-ups" },
];

export function NudgesPage() {
  useDocumentTitle("Follow-ups");
  const { push: toast } = useToast();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["nudges"],
    queryFn: () => api.get<{ count: number; items: any[] }>("/api/nudges"),
  });

  const recompute = useMutation({
    mutationFn: () => api.post<any>("/api/nudges/recompute"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["nudges"] }),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const dismiss = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.post<any>(`/api/nudges/${id}/dismiss`, { reason, decided_by: "ui" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["nudges"] }),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const accept = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      api.post<any>(`/api/nudges/${id}/accept`, { reason, decided_by: "ui" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["nudges"] }),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  return (
    <div>
      <p className="text-sm text-ink-500 mb-4">
        Who to act on now — the contacts worth a follow-up today.
      </p>
      <SubTabs items={PEOPLE_TABS} />

      <div className="flex justify-between items-end mb-1">
        <h2 className="text-lg">Follow-ups worth making now</h2>
        <button
          onClick={() => recompute.mutate()}
          disabled={recompute.isPending}
          className="btn-secondary text-xs"
        >
          {recompute.isPending ? "Recomputing…" : "↻ Re-run gate"}
        </button>
      </div>
      <p className="text-sm text-ink-500 mb-4">
        Calibrated — only fires on{" "}
        <span className="font-medium" style={{ color: "oklch(0.55 0.12 158)" }}>warming</span> contacts
        with ≥ 2 encounters, recent touch, and no prior meeting. Silent on
        weak signal by design.
      </p>

      <div className="space-y-2">
        {isLoading && <div className="text-sm text-ink-500">Loading…</div>}
        {data?.items.map((c) => (
          <div key={c.id} className="card p-4">
            <div className="flex justify-between items-start gap-3">
              <div className="flex-1">
                <Link
                  to={`/contacts/${c.id}`}
                  className="font-semibold text-ink-900 hover:underline"
                >
                  {c.primary_name}
                </Link>
                <span className="text-sm text-ink-500"> — {c.primary_title || "?"} @ {c.primary_company || "?"}</span>
                <div className="flex gap-2 mt-2">
                  <ArcBadge kind={c.arc_verdict} />
                  <span className="badge bg-ink-100 text-ink-500">
                    {((c.arc_confidence || 0) * 100).toFixed(0)}% conf
                  </span>
                </div>
                <div className="text-sm text-ink-700 mt-2 italic">
                  💡 {c.nudge_text}
                </div>
              </div>
              <div className="flex flex-col gap-1.5">
                <button
                  onClick={() => accept.mutate({ id: c.id, reason: "I'll reach out" })}
                  className="btn-primary text-xs"
                >
                  Accept
                </button>
                <button
                  onClick={() => dismiss.mutate({ id: c.id, reason: "not now" })}
                  className="btn-secondary text-xs"
                >
                  Dismiss
                </button>
              </div>
            </div>
          </div>
        ))}
        {data && data.items.length === 0 && (
          <div className="card p-8 text-center text-sm text-ink-500">
            No nudges firing. That's by design — we only surface contacts where
            multiple signals agree. Capture more encounters, or loosen the
            thresholds in Settings.
          </div>
        )}
      </div>
    </div>
  );
}
