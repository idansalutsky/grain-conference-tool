import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ArcBadge } from "@/components/Badges";
import { StarRating } from "@/components/StarRating";
import { useToast, toastErrorMessage } from "@/components/Toast";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export function ContactDetailPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const { push: toast } = useToast();
  const contact = useQuery({
    queryKey: ["contact", id],
    queryFn: () => api.get<any>(`/api/contacts/${id}`),
    enabled: !!id,
  });
  useDocumentTitle(contact.data?.primary_name || "Contact");

  const reclassify = useMutation({
    mutationFn: () => api.post<any>(`/api/contacts/${id}/arc/reclassify`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contact", id] });
      toast("success", "Arc re-classified");
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const override = useMutation({
    mutationFn: (verdict: string) =>
      api.post<any>(`/api/contacts/${id}/arc/override`, {
        arc_verdict: verdict, decided_by: "ui",
      }),
    onSuccess: (_, v) => {
      qc.invalidateQueries({ queryKey: ["contact", id] });
      toast("info", `Arc set to ${v.replace("_", "-")}`);
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const pushHs = useMutation({
    mutationFn: () => api.post<any>(`/api/hubspot/push/${id}`),
    onSuccess: (d) =>
      toast("success", d?.dry_run ? "Pushed (dry-run)" : "Pushed to HubSpot"),
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const genBrief = useMutation({
    mutationFn: () =>
      api.post<any>("/api/briefs/generate", {
        name: contact.data?.primary_name,
        company: contact.data?.primary_company,
        title: contact.data?.primary_title,
        contact_id: id,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contact", id] });
      toast("success", "Brief generated");
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  if (contact.isLoading) return <div className="text-sm text-ink-500">Loading…</div>;
  if (!contact.data) return <div className="text-sm text-ink-500">Contact not found.</div>;
  const c = contact.data;

  return (
    <div>
      <Link to="/contacts" className="text-xs text-ink-500 hover:text-ink-900">
        ← All contacts
      </Link>
      <div className="flex items-center gap-3 mt-1 mb-4">
        <h1 className="text-2xl">{c.primary_name}</h1>
        <ArcBadge kind={c.arc_verdict} />
      </div>
      <p className="text-sm text-ink-500 mb-6">
        {c.primary_title || "?"} @{" "}
        {c.company_id ? (
          <Link to={`/companies/${c.company_id}`} className="text-brand hover:underline">
            {c.primary_company || "?"}
          </Link>
        ) : (
          c.primary_company || "?"
        )}
        {c.primary_email && <> · {c.primary_email}</>}
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-4">
          <section className="card p-4">
            <div className="flex justify-between items-baseline">
              <h2 className="label">Encounter history</h2>
              <span className="text-xs text-ink-500">
                {c.encounters?.length || 0} encounter{c.encounters?.length === 1 ? "" : "s"}
              </span>
            </div>
            <div className="space-y-3 mt-3">
              {c.encounters?.map((e: any) => (
                <div key={e.id} className="border-l-2 border-ink-200 pl-3">
                  <div className="text-xs text-ink-500 font-mono">
                    {e.captured_at?.slice(0, 10)} · {e.capture_mode}
                    {e.conference_id && <> · {e.conference_id}</>}
                  </div>
                  <div className="text-sm text-ink-700 mt-0.5">
                    {e.structured?.what_discussed || e.raw_input?.slice(0, 200) || "—"}
                  </div>
                  <div className="flex gap-1 mt-1 flex-wrap">
                    {e.meeting_requested && (
                      <span className="badge bg-emerald-100 text-emerald-800">meeting requested</span>
                    )}
                    {e.sentiment != null && (
                      <span className="badge bg-ink-100 text-ink-700">sentiment {e.sentiment}/5</span>
                    )}
                    {(e.soft_signals || []).slice(0, 4).map((s: string) => (
                      <span key={s} className="badge bg-ink-100 text-ink-700">{s}</span>
                    ))}
                  </div>
                </div>
              ))}
              {!c.encounters?.length && (
                <div className="text-sm text-ink-500">No encounters yet.</div>
              )}
            </div>
          </section>

          {(c.briefs || []).length > 0 && (
            <section className="card p-4">
              <div className="flex justify-between items-baseline mb-3 gap-3 flex-wrap">
                <h2 className="label">Latest approach brief</h2>
                <BriefRateInline briefId={c.briefs[0].id} />
              </div>
              <pre className="text-xs whitespace-pre-wrap text-ink-700 bg-ink-50 p-3 rounded">
                {c.briefs[0].brief_text}
              </pre>
            </section>
          )}
        </div>

        <div className="space-y-4">
          <section className="card p-4">
            <h2 className="label mb-2">Arc verdict</h2>
            <div className="text-sm text-ink-700">{c.arc_summary || "—"}</div>
            <div className="text-xs text-ink-500 mt-1">
              confidence: {((c.arc_confidence || 0) * 100).toFixed(0)}%
            </div>
            <div className="flex gap-1 mt-3 flex-wrap">
              {["warming", "flat", "cooling", "tire_kicker"].map((v) => (
                <button
                  key={v}
                  onClick={() => override.mutate(v)}
                  disabled={override.isPending}
                  className={
                    "btn text-xs " +
                    (c.arc_verdict === v
                      ? "bg-brand text-white"
                      : "bg-ink-100 text-ink-500 border border-ink-200")
                  }
                >
                  {v.replace("_", "-")}
                </button>
              ))}
            </div>
            <button
              onClick={() => reclassify.mutate()}
              disabled={reclassify.isPending}
              className="btn-secondary text-xs mt-3 w-full"
            >
              {reclassify.isPending ? "Re-classifying…" : "↻ Re-run AI classifier"}
            </button>
          </section>

          {c.nudge_active && (
            <section className="card p-4 border-l-4 border-amber-500">
              <h2 className="label mb-2">💡 Active nudge</h2>
              <div className="text-sm text-ink-700">{c.nudge_text}</div>
            </section>
          )}

          <section className="card p-4 space-y-2">
            <h2 className="label">Actions</h2>
            <button
              onClick={() => genBrief.mutate()}
              disabled={genBrief.isPending}
              className="btn-secondary text-xs w-full"
            >
              {genBrief.isPending ? "Generating brief…" : "📄 Generate approach brief"}
            </button>
            <button
              onClick={() => pushHs.mutate()}
              disabled={pushHs.isPending}
              className="btn-secondary text-xs w-full"
            >
              {pushHs.isPending ? "Pushing…" : "↗ Push to HubSpot"}
            </button>
            {pushHs.data && (
              <div className="text-xs text-ink-500 mt-1">
                {pushHs.data.dry_run ? "✅ Dry-run OK (no live token set)" : "✅ Pushed"}
                {pushHs.data.hubspot_id && (
                  <> · HubSpot id <span className="font-mono">{pushHs.data.hubspot_id}</span></>
                )}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function BriefRateInline({ briefId }: { briefId: string }) {
  const { push: toast } = useToast();
  const [rated, setRated] = useState<number | null>(null);
  const rate = useMutation({
    mutationFn: (rating: number) =>
      api.post(`/api/briefs/${briefId}/rate`, {
        rating, decided_by: "ui:contact_detail",
      }),
    onSuccess: (_, rating) => {
      setRated(rating);
      toast("success", `Brief rated ${rating}/5`);
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });
  return (
    <StarRating
      value={rated || 0}
      onChange={(v) => rate.mutate(v)}
      disabled={rate.isPending}
      size="sm"
      label="Rate brief:"
    />
  );
}
