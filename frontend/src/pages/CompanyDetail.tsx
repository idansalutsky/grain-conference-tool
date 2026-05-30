import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { TierBadge, PersonaBadge, ArcBadge } from "@/components/Badges";
import { toastErrorMessage } from "@/components/Toast";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

interface CompanyDetail {
  id: string;
  name: string;
  domain?: string | null;
  logo_url?: string | null;
  hq_country?: string | null;
  industry?: string | null;
  vertical?: string | null;
  employee_band?: string | null;
  fx_exposure_hint?: string | null;
  why_grain_fit?: string | null;
  source_kind?: string;
  source_url?: string | null;
  account_tier?: string | null;
  icp_score?: number | null;
  icp_breakdown?: Record<string, any>;
  name_variants?: string[];
  people: Array<{
    id: string;
    full_name: string;
    title?: string | null;
    persona?: string | null;
    persona_weight?: number | null;
    conference_id?: string | null;
    conference_name?: string | null;
  }>;
  contacts: Array<{
    id: string;
    primary_name: string;
    primary_title?: string | null;
    arc_verdict?: string | null;
    arc_confidence?: number | null;
    nudge_active?: number;
  }>;
  encounters: Array<{
    id: string;
    contact_id?: string | null;
    captured_at: string;
    capture_mode?: string | null;
    sentiment?: number | null;
    meeting_requested?: number;
    conference_id?: string | null;
    conference_name?: string | null;
  }>;
  arc_counts: Record<string, number>;
  conferences: Array<{ id: string; name: string; start_date?: string; tier?: string }>;
  encounter_count: number;
  meeting_count: number;
}

export function CompanyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, error } = useQuery({
    queryKey: ["company", id],
    queryFn: () => api.get<CompanyDetail>(`/api/companies/${id}`),
    enabled: !!id,
  });
  useDocumentTitle(data?.name || "Company");

  if (isLoading) return <div className="text-sm text-ink-500">Loading…</div>;
  if (error) return <div className="card p-4 text-red-700 text-sm">Error: {toastErrorMessage(error)}</div>;
  if (!data) return null;

  return (
    <div className="space-y-4">
      <Link to="/companies" className="text-sm text-ink-500 hover:text-brand">
        ← All companies
      </Link>

      {/* Header card */}
      <section className="card p-5 flex items-start gap-4">
        <Logo url={data.logo_url} name={data.name} size={80} />
        <div className="flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-2xl font-bold">{data.name}</h1>
            {data.account_tier && <TierBadge tier={data.account_tier} />}
            {data.source_kind === "discovered" && (
              <span className="badge bg-purple-100 text-purple-800">🔍 discovered</span>
            )}
          </div>
          <div className="text-sm text-ink-500 mt-1 flex items-center gap-2 flex-wrap">
            {data.domain && (
              <a href={`https://${data.domain}`} target="_blank" rel="noreferrer" className="hover:text-brand">
                {data.domain}
              </a>
            )}
            {data.hq_country && <span>· {data.hq_country}</span>}
            {data.industry && <span>· {data.industry}</span>}
            {data.vertical && <span>· {data.vertical}</span>}
            {data.employee_band && <span>· {data.employee_band} employees</span>}
          </div>
          {data.why_grain_fit && (
            <p className="text-sm text-ink-700 mt-2 italic">💡 {data.why_grain_fit}</p>
          )}
          {data.source_url && (
            <a
              href={data.source_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-brand hover:underline mt-1 inline-block"
              title={data.source_url}
            >
              📎 source: {data.source_url.replace(/^https?:\/\//, "").split("/")[0]}
            </a>
          )}
          {data.name_variants && data.name_variants.length > 1 && (
            <div className="text-xs text-ink-500 mt-2">
              Known as:{" "}
              {data.name_variants.map((v, i) => (
                <span key={v}>
                  <code className="bg-ink-100 px-1 py-0.5 rounded">{v}</code>
                  {i < data.name_variants!.length - 1 && " · "}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="text-right shrink-0">
          {data.icp_score != null && (
            <>
              <div className="text-3xl font-bold">{(data.icp_score * 100).toFixed(0)}</div>
              <div className="text-xs text-ink-500">ICP score</div>
            </>
          )}
        </div>
      </section>

      {/* Quick stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label="People" value={data.people.length} />
        <Stat label="Conferences" value={data.conferences.length} />
        <Stat
          label="Encounters"
          value={data.encounter_count}
          href={data.encounters.length > 0 ? "#encounter-timeline" : undefined}
        />
        <Stat
          label="Meetings booked"
          value={data.meeting_count}
          accent
          href={data.encounters.length > 0 ? "#encounter-timeline" : undefined}
        />
      </div>

      {/* ICP breakdown */}
      {data.icp_breakdown && Object.keys(data.icp_breakdown).length > 0 && (
        <section className="card p-4">
          <h2 className="text-sm font-semibold mb-2">ICP score breakdown</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
            <BreakdownPart
              label="Avg persona weight (50%)"
              value={data.icp_breakdown.avg_persona_weight}
              hint={`${data.icp_breakdown.people_count} people`}
            />
            <BreakdownPart
              label="Multi-conf signal (20%)"
              value={data.icp_breakdown.multi_conf_factor}
              hint={`${data.icp_breakdown.conference_count} confs`}
            />
            <BreakdownPart
              label="Vertical match (15%)"
              value={data.icp_breakdown.vertical_match}
              hint={data.vertical || "no vertical"}
            />
            <BreakdownPart
              label="FX exposure (15%)"
              value={data.icp_breakdown.fx_exposure_factor}
              hint={data.fx_exposure_hint || "unknown"}
            />
          </div>
        </section>
      )}

      {/* People across conferences */}
      <section className="card p-4">
        <h2 className="text-sm font-semibold mb-2">
          People we've surfaced ({data.people.length})
        </h2>
        {data.people.length === 0 ? (
          <div className="text-xs text-ink-500 italic">
            No people surfaced yet for this account.
          </div>
        ) : (
          <div className="space-y-1.5">
            {data.people.map((p) => (
              <div
                key={p.id}
                className="flex items-center gap-3 py-1.5 border-b last:border-0 border-ink-100"
              >
                <PersonaBadge persona={p.persona} />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm truncate">{p.full_name}</div>
                  <div className="text-xs text-ink-500 truncate">
                    {p.title || "?"}
                  </div>
                </div>
                {p.conference_name && (
                  <Link
                    to={`/conferences/${p.conference_id}`}
                    className="text-xs text-ink-500 hover:text-brand shrink-0"
                  >
                    @ {p.conference_name}
                  </Link>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Captured contacts + arc aggregate */}
      {data.contacts.length > 0 && (
        <section className="card p-4">
          <h2 className="text-sm font-semibold mb-2">
            Captured contacts ({data.contacts.length})
          </h2>
          <div className="flex flex-wrap gap-2 mb-3 text-xs">
            {Object.entries(data.arc_counts).map(([arc, n]) => (
              <div key={arc} className="badge bg-ink-100 text-ink-700">
                {arc}: <span className="font-bold ml-1">{n}</span>
              </div>
            ))}
          </div>
          <div className="space-y-1.5">
            {data.contacts.map((c) => (
              <Link
                key={c.id}
                to={`/contacts/${c.id}`}
                className="flex items-center gap-3 py-1.5 border-b last:border-0 border-ink-100 hover:bg-ink-50 -mx-2 px-2 rounded"
              >
                <ArcBadge kind={c.arc_verdict} />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm truncate">{c.primary_name}</div>
                  <div className="text-xs text-ink-500 truncate">{c.primary_title || "?"}</div>
                </div>
                {c.nudge_active === 1 && (
                  <span className="badge bg-emerald-100 text-emerald-800">💡 nudge</span>
                )}
              </Link>
            ))}
          </div>
        </section>
      )}

      {/* Encounters timeline */}
      {data.encounters.length > 0 && (
        <section id="encounter-timeline" className="card p-4">
          <h2 className="text-sm font-semibold mb-2">
            Encounter timeline ({data.encounters.length})
          </h2>
          <div className="space-y-1.5 text-sm">
            {data.encounters.map((e) => (
              <div key={e.id} className="flex items-center gap-2 py-1.5 border-b last:border-0 border-ink-100">
                <span className="text-xs font-mono text-ink-500 w-20 shrink-0">
                  {(e.captured_at || "").slice(0, 10)}
                </span>
                <span className="text-xs text-ink-500 w-16 shrink-0">{e.capture_mode}</span>
                <span className="flex-1 text-xs">
                  {e.conference_id ? (
                    <Link
                      to={`/conferences/${e.conference_id}`}
                      className="text-ink-500 hover:text-brand"
                    >
                      @ {e.conference_name || e.conference_id.replace(/^conf-/, "")}
                    </Link>
                  ) : (
                    <>@ {e.conference_name || "—"}</>
                  )}
                </span>
                {e.meeting_requested ? (
                  <span className="badge bg-emerald-100 text-emerald-800">📅 mtg</span>
                ) : null}
                {e.sentiment != null && (
                  <span className="text-xs text-ink-500">sent {e.sentiment}/5</span>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Conferences this company appears at */}
      {data.conferences.length > 0 && (
        <section className="card p-4">
          <h2 className="text-sm font-semibold mb-2">
            Conferences ({data.conferences.length})
          </h2>
          <div className="flex flex-wrap gap-2">
            {data.conferences.map((c) => (
              <Link
                key={c.id}
                to={`/conferences/${c.id}`}
                className="badge bg-ink-100 text-ink-700 hover:bg-ink-200 transition"
              >
                {c.tier && (
                  <span className="font-bold mr-1.5 text-emerald-700">{c.tier}</span>
                )}
                {c.name}
                {c.start_date && (
                  <span className="text-ink-500 ml-1">· {c.start_date.slice(0, 7)}</span>
                )}
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function Stat({
  label, value, accent, href,
}: { label: string; value: number; accent?: boolean; href?: string }) {
  const cls = "card p-3 block " + (accent ? "bg-emerald-50 border-emerald-200" : "");
  const inner = (
    <>
      <div className={"text-2xl font-bold " + (accent ? "text-emerald-800" : "")}>{value}</div>
      <div className="text-xs text-ink-500">{label}</div>
    </>
  );
  if (href) {
    return (
      <a href={href} className={cls + " hover:border-ink-300 transition"}>
        {inner}
      </a>
    );
  }
  return <div className={cls}>{inner}</div>;
}

function BreakdownPart({
  label, value, hint,
}: { label: string; value?: number; hint?: string }) {
  const v = value || 0;
  return (
    <div>
      <div className="text-ink-500 mb-1">{label}</div>
      <div className="font-mono text-sm font-bold">{(v * 100).toFixed(0)}%</div>
      {hint && <div className="text-ink-500 mt-0.5">{hint}</div>}
    </div>
  );
}

function Logo({ url, name, size }: { url?: string | null; name: string; size: number }) {
  const [failed, setFailed] = useState(false);
  const showImg = url && !failed;
  return (
    <div
      className="rounded bg-ink-100 flex items-center justify-center shrink-0 overflow-hidden"
      style={{ width: size, height: size }}
    >
      {showImg ? (
        <img
          src={url!}
          alt={name}
          className="object-contain"
          style={{ width: size * 0.8, height: size * 0.8 }}
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="text-2xl font-bold text-ink-500">
          {name.slice(0, 2).toUpperCase()}
        </span>
      )}
    </div>
  );
}
