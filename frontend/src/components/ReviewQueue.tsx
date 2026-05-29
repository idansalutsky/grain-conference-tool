import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface ReviewItem {
  encounter_id: string;
  captured_at: string;
  conference_id: string | null;
  encounter_lead: {
    name: string | null;
    title: string | null;
    company: string | null;
    email: string | null;
    what_discussed: string;
  };
  candidate_contact: {
    id: string;
    primary_name: string;
    primary_email: string | null;
    primary_company: string | null;
    primary_title: string | null;
  } | null;
  confidence: number;
  factors: Record<string, number>;
  logged_at: string;
}

/**
 * Surface encounters where entity resolution returned `review_needed`.
 * The rep confirms or rejects each one. Confirming attaches the encounter
 * to the candidate contact + re-runs the arc cascade.
 */
export function ReviewQueue() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["review-queue"],
    queryFn: () => api.get<{ items: ReviewItem[] }>("/api/review"),
  });

  const confirm = useMutation({
    mutationFn: (eid: string) =>
      api.post<any>(`/api/review/${eid}/confirm`, { decided_by: "ui" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review-queue"] });
      qc.invalidateQueries({ queryKey: ["contacts"] });
      qc.invalidateQueries({ queryKey: ["today"] });
    },
  });

  const reject = useMutation({
    mutationFn: (eid: string) =>
      api.post<any>(`/api/review/${eid}/reject`, {
        decided_by: "ui", reason: "different person",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review-queue"] });
      qc.invalidateQueries({ queryKey: ["contacts"] });
      qc.invalidateQueries({ queryKey: ["today"] });
    },
  });

  const items = data?.items || [];
  if (items.length === 0) return null;

  return (
    <section className="card p-4 mb-4 border-l-4 border-blue-500 bg-blue-50">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-sm font-semibold text-blue-900">
          🔍 {items.length} match{items.length === 1 ? "" : "es"} need your review
        </h2>
        <span className="text-xs text-blue-800">
          Resolver wasn't sure — your call
        </span>
      </div>
      <div className="space-y-3">
        {items.map((it) => (
          <div key={it.encounter_id} className="bg-white rounded p-3 border border-blue-200">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1">
                  This encounter
                </div>
                <div className="font-medium text-sm">
                  {it.encounter_lead.name || "?"}
                </div>
                <div className="text-xs text-ink-500">
                  {it.encounter_lead.title || "?"} @ {it.encounter_lead.company || "?"}
                </div>
                {it.encounter_lead.email && (
                  <div className="text-xs text-ink-500 font-mono mt-1">
                    {it.encounter_lead.email}
                  </div>
                )}
                {it.encounter_lead.what_discussed && (
                  <p className="text-xs text-ink-700 italic mt-1">
                    "{it.encounter_lead.what_discussed}"
                  </p>
                )}
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1">
                  Candidate match
                </div>
                {it.candidate_contact ? (
                  <>
                    <div className="font-medium text-sm">
                      {it.candidate_contact.primary_name}
                    </div>
                    <div className="text-xs text-ink-500">
                      {it.candidate_contact.primary_title || "?"} @{" "}
                      {it.candidate_contact.primary_company || "?"}
                    </div>
                    {it.candidate_contact.primary_email && (
                      <div className="text-xs text-ink-500 font-mono mt-1">
                        {it.candidate_contact.primary_email}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="text-xs text-ink-500 italic">No candidate (orphan)</div>
                )}
              </div>
            </div>

            <div className="flex justify-between items-center gap-3 pt-2 border-t border-ink-100">
              <div className="text-xs text-ink-600">
                Confidence:{" "}
                <span className="font-mono">{(it.confidence * 100).toFixed(0)}%</span>{" "}
                ·{" "}
                <span className="text-ink-500">
                  name {(it.factors.name_similarity * 100).toFixed(0)}%, company{" "}
                  {(it.factors.company_similarity * 100).toFixed(0)}%
                  {it.factors.email_match ? ", email ✓" : ", email differs"}
                </span>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => confirm.mutate(it.encounter_id)}
                  disabled={confirm.isPending}
                  className="btn-primary text-xs"
                >
                  Same person
                </button>
                <button
                  onClick={() => reject.mutate(it.encounter_id)}
                  disabled={reject.isPending}
                  className="btn-secondary text-xs"
                >
                  Different person
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
