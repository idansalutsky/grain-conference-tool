import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ArcBadge } from "@/components/Badges";
import { useToast, toastErrorMessage } from "@/components/Toast";
import type { CaptureResult } from "@/lib/types";

interface Props {
  result: CaptureResult;
  onDeleted?: () => void;
}

/**
 * Renders the result of a capture (voice OR text). Polls the contact for the
 * arc + nudge update if the cascade is still running in the background.
 * Exposes one-tap HubSpot push.
 */
export function CaptureResultCard({ result, onDeleted }: Props) {
  const { push: toast } = useToast();
  const [s, setS] = useState<any>(result.structured || {});
  const [contactId, setContactId] = useState(result.contact_id);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});

  const saveEdit = useMutation({
    mutationFn: () => api.put<any>(`/api/encounters/${result.encounter_id}`, draft),
    onSuccess: (d) => {
      setS(d.structured || {});
      setContactId(d.contact_id);
      setEditing(false);
      setDraft({});
      toast("success", "Capture corrected — re-resolved");
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  function startEdit() {
    setDraft({
      name: s.name || "", company: s.company || "",
      title: s.title || "", phone: (s as any).phone || "",
    });
    setEditing(true);
  }

  const initialArc = result.arc;
  const initialNudge = result.nudge;
  const cascadePending = result.cascade_status === "pending";

  // Poll the contact for arc + nudge while the background cascade runs.
  // First poll at 4s, give up after ~30s.
  const contactQuery = useQuery({
    queryKey: ["contact-after-capture", result.contact_id],
    queryFn: () => api.get<any>(`/api/contacts/${result.contact_id}`),
    enabled: !!result.contact_id && cascadePending,
    refetchInterval: (q) => {
      const d: any = q.state.data;
      if (
        d?.arc_verdict &&
        (!initialArc ||
          d.arc_verdict !== initialArc.kind ||
          d.arc_confidence !== initialArc.confidence)
      ) {
        return false;
      }
      if ((q.state.dataUpdateCount || 0) >= 6) return false;
      return 4000;
    },
  });

  const liveArc = contactQuery.data
    ? {
        kind: contactQuery.data.arc_verdict,
        confidence: contactQuery.data.arc_confidence,
        summary: contactQuery.data.arc_summary,
      }
    : initialArc;
  const liveNudge = contactQuery.data
    ? {
        nudge_active: !!contactQuery.data.nudge_active,
        nudge_text: contactQuery.data.nudge_text,
        why_suppressed: undefined as string[] | undefined,
      }
    : initialNudge;
  const arc = liveArc;
  const nudge = liveNudge;
  const cascadeStillRunning = cascadePending && !contactQuery.data?.arc_verdict;

  const push = useMutation({
    mutationFn: () => api.post<any>(`/api/hubspot/push/${contactId}`),
    onSuccess: (d) => {
      toast("success", d?.dry_run ? "Pushed to HubSpot (dry-run)" : "Pushed to HubSpot");
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const discard = useMutation({
    mutationFn: () => api.delete(`/api/encounters/${result.encounter_id}`),
    onSuccess: () => {
      toast("success", "Capture discarded");
      onDeleted?.();
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  return (
    <div className="card p-5 mt-4" style={{ background: "oklch(0.98 0.02 158)", borderColor: "oklch(0.85 0.06 158)" }}>
      <div className="flex items-start justify-between gap-2">
        <div className="label mb-1" style={{ color: "oklch(0.45 0.1 158)" }}>
          Captured{result.stitched && " · merged into this encounter"}
        </div>
        {!editing && (
          <button onClick={startEdit} className="btn-ghost h-7 text-xs !px-2 text-ink-500">
            ✎ Fix details
          </button>
        )}
      </div>

      {editing ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-1">
          {(["name", "company", "title", "phone"] as const).map((f) => (
            <label key={f} className="text-xs">
              <span className="text-ink-500 capitalize">{f}</span>
              <input
                className="input w-full mt-0.5"
                value={draft[f] ?? ""}
                onChange={(e) => setDraft((p) => ({ ...p, [f]: e.target.value }))}
              />
            </label>
          ))}
          <div className="sm:col-span-2 flex gap-2 mt-1">
            <button className="btn-primary text-xs" disabled={saveEdit.isPending}
                    onClick={() => saveEdit.mutate()}>
              {saveEdit.isPending ? "Saving…" : "Save & re-resolve"}
            </button>
            <button className="btn-ghost text-xs" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      ) : (
        <>
          <div className="font-semibold text-lg">
            {s.name || "?"}
            <span className="text-ink-500 font-normal"> — {s.title || "?"}</span>
          </div>
          <div className="text-sm text-ink-500">
            {s.company || "?"} · {s.vertical || "?"}
            {(s as any).phone && <> · {(s as any).phone}</>}
          </div>
          {s.what_discussed && (
            <p className="text-sm mt-2 text-ink-700 italic">"{s.what_discussed}"</p>
          )}
        </>
      )}

      <div className="flex flex-wrap gap-2 mt-3">
        {arc?.kind && <ArcBadge kind={arc.kind} />}
        {(s.soft_signals || []).map((sg: string) => (
          <span key={sg} className="badge bg-ink-100 text-ink-700">
            {sg}
          </span>
        ))}
        {s.meeting_requested && (
          <span className="badge bg-emerald-100 text-emerald-800">
            📅 meeting wanted
          </span>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-4 text-xs">
        <div>
          <div className="label">Resolution</div>
          <div className="text-ink-700 mt-1">
            {result.resolution?.decision === "created_new" && "New contact created"}
            {result.resolution?.decision === "auto_merged" &&
              "Auto-merged into existing contact"}
            {result.resolution?.decision === "review_needed" && "Needs human review"}
            {contactId && (
              <>
                {" · "}
                <Link
                  to={`/contacts/${contactId}`}
                  className="text-brand hover:underline"
                >
                  open contact →
                </Link>
              </>
            )}
          </div>
        </div>
        <div>
          <div className="label">Arc verdict</div>
          <div className="text-ink-700 mt-1">
            {cascadeStillRunning ? (
              <span className="text-ink-500 italic flex items-center gap-1.5">
                <span className="inline-block w-2 h-2 bg-brand rounded-full animate-pulse" />
                AI classifying
                {initialArc?.from_prior_encounters
                  ? " (based on prior history)…"
                  : " (from this encounter)…"}
              </span>
            ) : arc?.kind ? (
              <>
                {arc.summary || arc.kind}{" "}
                <span className="text-ink-500">
                  ({((arc.confidence || 0) * 100).toFixed(0)}%)
                </span>
              </>
            ) : (
              "—"
            )}
          </div>
        </div>
      </div>

      {nudge && (
        <div className="mt-3 text-xs">
          <div className="label">Nudge</div>
          {nudge.nudge_active ? (
            <div className="text-emerald-700 mt-1">💡 {nudge.nudge_text}</div>
          ) : (
            <div className="text-ink-500 mt-1">
              Silent — {nudge.why_suppressed?.join("; ") || "no trigger fired"}
            </div>
          )}
        </div>
      )}

      {contactId && (
        <div className="mt-4 pt-3 border-t border-ink-200 flex gap-2 items-center flex-wrap">
          <button
            onClick={() => push.mutate()}
            disabled={push.isPending}
            className="btn-primary text-xs"
          >
            {push.isPending ? "Pushing…" : "↗ Push to HubSpot"}
          </button>
          <Link
            to={`/contacts/${contactId}`}
            className="btn-secondary text-xs"
          >
            Open contact →
          </Link>
          <button
            onClick={() => discard.mutate()}
            disabled={discard.isPending}
            className="btn-ghost text-xs text-ink-500 hover:text-tire ml-auto"
          >
            {discard.isPending ? "Discarding…" : "🗑 Discard"}
          </button>
          {push.data && (
            <span className="text-xs text-ink-500">
              {push.data.dry_run ? "✓ Dry-run OK" : "✓ Pushed"}
              {push.data.hubspot_id && (
                <>
                  {" "}· id{" "}
                  <span className="font-mono">
                    {String(push.data.hubspot_id).slice(0, 12)}
                  </span>
                </>
              )}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
