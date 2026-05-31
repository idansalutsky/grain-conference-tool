import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useToast, toastErrorMessage } from "@/components/Toast";

export function SettingsPage() {
  useDocumentTitle("Settings");
  const { data, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<any>("/api/settings"),
  });

  if (isLoading) return <div className="text-sm text-ink-500">Loading…</div>;

  return (
    <div>
      <h1 className="text-2xl mb-1">Settings</h1>
      <p className="text-sm text-ink-500 mb-6 max-w-[68ch]">
        Your API keys and the ideal-customer profile the whole tool scores
        against. Two things deliberately live elsewhere, where they belong:
        scoring weights are on the <a href="/conferences" className="text-brand hover:underline">Events</a> page
        (watch events re-rank as you tune), and reps connect Telegram per-event
        from each event's <span className="text-ink-700">Coverage</span> panel (the
        manager assigns a rep, then sends them their bind link).
      </p>

      <IntegrationsSection />

      <IcpView icp={data?.icp} />
    </div>
  );
}

function Chips({ items }: { items: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5 mt-1">
      {items.map((x) => (
        <span key={x} className="px-2 py-0.5 rounded-md bg-ink-100 text-ink-700 text-xs">{x}</span>
      ))}
    </div>
  );
}

function IcpView({ icp }: { icp: any }) {
  if (!icp) return null;
  const verticals: string[] = icp.verticals || icp.company_level?.verticals || [];
  const titles: string[] = icp.target_titles || icp.person_level?.target_titles || [];
  const competitors: string[] = icp.competitors || [];
  return (
    <section className="mt-8">
      <div className="rule-label mb-3">Ideal Customer Profile — what "fit" means</div>
      <div className="card p-4 space-y-4">
        <div>
          <div className="label mb-1">Target verticals</div>
          <Chips items={verticals} />
        </div>
        <div>
          <div className="label mb-1">Buyer titles we're hunting</div>
          <Chips items={titles} />
        </div>
        {competitors.length > 0 && (
          <div>
            <div className="label mb-1">Competitors (presence = market validation)</div>
            <Chips items={competitors} />
          </div>
        )}
        <p className="text-xs text-ink-500">
          Every score, target ranking and brief reads from this profile.
        </p>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Integrations / API keys — user-configurable secrets.
// GET /api/settings/integrations returns, keyed by field name, a masked status:
//   { integrations: { openrouter_api_key: { configured: bool, masked: "…abcd",
//     source: "env" | "in_app" | null }, perplexity_api_key: {…}, … } }
// PUT /api/settings/integrations accepts only the fields the user filled in.
// We never render full secrets — only the configured flag and the masked hint.
// ---------------------------------------------------------------------------
const INTEGRATIONS: { provider: string; field: string; label: string; help: string }[] = [
  { provider: "openrouter", field: "openrouter_api_key", label: "OpenRouter", help: "Powers LLM enrichment & briefs." },
  { provider: "perplexity", field: "perplexity_api_key", label: "Perplexity (Sonar)", help: "Event & prospect discovery search." },
  { provider: "hubspot", field: "hubspot_token", label: "HubSpot", help: "CRM sync for contacts & companies." },
  { provider: "telegram", field: "telegram_bot_token", label: "Telegram bot", help: "Field capture bot token." },
];

function IntegrationsSection() {
  const { push: toast } = useToast();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["integrations"],
    queryFn: () =>
      api.get<{ integrations?: Record<string, any> }>("/api/settings/integrations"),
  });

  // Only non-empty fields get sent — empty inputs leave the existing key alone.
  const [values, setValues] = useState<Record<string, string>>({});

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, string> = {};
      for (const { field } of INTEGRATIONS) {
        const v = (values[field] || "").trim();
        if (v) body[field] = v;
      }
      return api.put("/api/settings/integrations", body);
    },
    onSuccess: () => {
      toast("success", "Keys saved.");
      setValues({});
      qc.invalidateQueries({ queryKey: ["integrations"] });
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const anyFilled = INTEGRATIONS.some(({ field }) => (values[field] || "").trim());

  return (
    <section className="card p-5 mb-6">
      <h2 className="label mb-1">🔑 API keys &amp; integrations</h2>
      <p className="text-xs text-ink-500 mb-4 max-w-[65ch]">
        Bring your own keys. Stored server-side and never shown back in full —
        leave a field blank to keep the current value. Only the fields you fill
        in are saved.
      </p>

      {isLoading && <div className="text-sm text-ink-500">Loading…</div>}

      {!isLoading && (
        <div className="space-y-3">
          {INTEGRATIONS.map(({ field, label, help }) => {
            const status = (data?.integrations && data.integrations[field]) || {};
            const configured = !!status.configured;
            const hint: string = status.masked || "";
            const source: string = status.source || "";
            return (
              <div key={field}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-semibold text-ink-900">{label}</span>
                  {configured ? (
                    <span className="badge bg-emerald-100 text-emerald-800">configured ✓</span>
                  ) : (
                    <span className="badge bg-ink-100 text-ink-500">not set</span>
                  )}
                  {configured && hint && (
                    <span className="text-xs font-mono text-ink-500">{hint}</span>
                  )}
                  {configured && source && (
                    <span className="text-[10px] uppercase tracking-wider text-ink-400">
                      {source === "env" ? "from env" : source === "in_app" ? "saved" : source}
                    </span>
                  )}
                </div>
                <div className="text-xs text-ink-500 mb-1">{help}</div>
                <input
                  type="password"
                  autoComplete="off"
                  placeholder={configured ? "Replace key…" : "Paste key…"}
                  value={values[field] || ""}
                  onChange={(e) => setValues((m) => ({ ...m, [field]: e.target.value }))}
                  className="input w-full sm:max-w-md"
                />
              </div>
            );
          })}

          <div className="flex items-center gap-3 pt-1">
            <button
              className="btn-primary text-sm"
              disabled={!anyFilled || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? "Saving…" : "Save keys"}
            </button>
            <span className="text-xs text-ink-500">
              Blank fields are ignored — existing keys stay put.
            </span>
          </div>
        </div>
      )}
    </section>
  );
}
