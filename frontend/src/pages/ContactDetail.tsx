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

  // The cross-conference STORY, computed from the encounter trail — turns a count
  // into an arc the rep can read at a glance (the brief's whole point).
  const encs: any[] = c.encounters || [];
  const dated = encs.filter((e) => e.captured_at).sort((a, b) => (a.captured_at < b.captured_at ? -1 : 1));
  const nConfs = new Set(encs.map((e) => e.conference_id).filter(Boolean)).size;
  const spanMonths = dated.length > 1
    ? Math.max(1, Math.round((new Date(dated[dated.length - 1].captured_at).getTime() - new Date(dated[0].captured_at).getTime()) / (86400000 * 30)))
    : 0;
  const sent = dated.filter((e) => e.sentiment != null);
  const firstS = sent[0]?.sentiment;
  const lastS = sent[sent.length - 1]?.sentiment;
  const meetingsAsked = encs.filter((e) => e.meeting_requested).length;
  const isCrossConf = nConfs >= 2;

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

      {/* THE CROSS-CONFERENCE READ — the headline of the whole product: not a
          count, but the trajectory across events, with the interpretation. */}
      <section className="card p-5 mb-4" style={{ background: "oklch(0.98 0.012 160)", borderColor: "oklch(0.9 0.02 160)" }}>
        <div className="flex items-center gap-2 mb-2 flex-wrap">
          <ArcBadge kind={c.arc_verdict} />
          <span className="font-display font-semibold text-ink-900">
            {isCrossConf
              ? `Met at ${nConfs} conferences${spanMonths ? ` over ${spanMonths} month${spanMonths === 1 ? "" : "s"}` : ""}`
              : `Met at ${nConfs || 1} conference${(nConfs || 1) === 1 ? "" : "s"}`}
          </span>
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm mb-2">
          <span><span className="font-semibold tabular-nums">{nConfs || encs.length}</span> <span className="text-ink-500">conference{(nConfs || encs.length) === 1 ? "" : "s"}</span></span>
          {spanMonths > 0 && <span><span className="font-semibold tabular-nums">{spanMonths}</span> <span className="text-ink-500">month span</span></span>}
          {firstS != null && (
            <span className="text-ink-500">sentiment <span className="font-semibold text-ink-900 tabular-nums">{firstS}{lastS != null && lastS !== firstS ? ` → ${lastS}` : ""}</span>/5</span>
          )}
          <span><span className="font-semibold tabular-nums">{meetingsAsked}</span> <span className="text-ink-500">meeting{meetingsAsked === 1 ? "" : "s"} asked</span></span>
        </div>
        {c.arc_summary && <p className="text-sm text-ink-800 leading-relaxed max-w-[74ch]">{c.arc_summary}</p>}
        {isCrossConf && meetingsAsked === 0 && (
          <p className="text-xs text-ink-500 mt-1.5">
            Across {nConfs} events and {spanMonths || "several"} months, no meeting yet — read the trail below before you decide warm vs. tire-kicker.
          </p>
        )}
      </section>

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
                <div key={e.id} className="border-l border-ink-200 pl-3">
                  <div className="text-xs text-ink-500 font-mono">
                    {e.captured_at?.slice(0, 10)} · {e.capture_mode}
                    {e.conference_id && (
                      <>
                        {" · "}
                        <Link
                          to={`/conferences/${e.conference_id}`}
                          className="text-brand hover:underline"
                        >
                          {e.conference_name || e.conference_id.replace(/^conf-/, "")}
                        </Link>
                      </>
                    )}
                  </div>
                  <div className="text-sm text-ink-700 mt-0.5">
                    {e.structured?.what_discussed || e.raw_input?.slice(0, 200) || "—"}
                  </div>
                  <div className="flex gap-1 mt-1 flex-wrap">
                    {e.meeting_requested ? (
                      <span className="badge bg-emerald-100 text-emerald-800">meeting requested</span>
                    ) : null}
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
              <div className="rounded-md bg-ink-50/60 border border-ink-100 p-4 text-sm leading-relaxed text-ink-800 space-y-2">
                {String(c.briefs[0].brief_text || "")
                  .split(/\n{2,}/)
                  .map((para: string, i: number) =>
                    para.trim() ? (
                      <p key={i} className="whitespace-pre-wrap">
                        {para.trim()}
                      </p>
                    ) : null,
                  )}
              </div>
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

          {c.nudge_active ? (
            <section className="card p-4" style={{ background: "oklch(0.97 0.03 62)", borderColor: "oklch(0.86 0.06 62)" }}>
              <h2 className="label mb-2" style={{ color: "oklch(0.45 0.1 62)" }}>Active nudge</h2>
              <div className="text-sm text-ink-700">{c.nudge_text}</div>
            </section>
          ) : null}

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
