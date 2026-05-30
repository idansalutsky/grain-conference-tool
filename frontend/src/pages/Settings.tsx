import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useToast, toastErrorMessage } from "@/components/Toast";

const DEFAULT_REP_ID = "rep-na-01";

export function SettingsPage() {
  useDocumentTitle("Settings");
  const qc = useQueryClient();
  const { push: toast } = useToast();
  const { data, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<any>("/api/settings"),
  });

  const update = useMutation({
    mutationFn: (body: { key: string; value: number | string }) =>
      api.put("/api/settings", { ...body, decided_by: "ui" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["conferences"] });
      toast("success", "Threshold updated.");
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const rescore = useMutation({
    mutationFn: () => api.post("/api/conferences/rescore"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conferences"] });
      toast("success", "Events re-scored.");
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const [tgToken, setTgToken] = useState<any | null>(null);
  const issueTgToken = useMutation({
    mutationFn: () =>
      api.post<any>("/api/telegram/issue-token", { rep_id: DEFAULT_REP_ID }),
    onSuccess: (d) => setTgToken(d),
  });

  if (isLoading) return <div className="text-sm text-ink-500">Loading…</div>;

  return (
    <div>
      <h1 className="text-2xl mb-1">Settings</h1>
      <p className="text-sm text-ink-500 mb-6 max-w-[65ch]">
        Tune how matching and follow-up nudges behave, connect Telegram, and
        review the ICP the whole tool scores against. Scoring weights live on the
        Events page so you can watch events re-rank as you change them.
      </p>

      <section className="card p-5 mb-4">
        <h2 className="label mb-2">📱 Connect Telegram (field capture from your phone)</h2>
        <p className="text-xs text-ink-500 mb-3">
          Click the button to generate a one-time link, then open it on your
          phone — the bot will bind to your rep profile and every voice memo
          you send creates an encounter.
        </p>
        <button
          onClick={() => issueTgToken.mutate()}
          disabled={issueTgToken.isPending}
          className="btn-primary text-sm"
        >
          {issueTgToken.isPending ? "Generating…" : "Generate connect link"}
        </button>
        {tgToken && (
          <div className="mt-3 text-xs">
            <a
              href={tgToken.deep_link}
              target="_blank"
              rel="noreferrer"
              className="text-brand font-mono break-all hover:underline"
            >
              {tgToken.deep_link}
            </a>
            <div className="text-ink-500 mt-1">
              Bot: <span className="font-mono">@{tgToken.bot_username || "GrainSales_bot"}</span>
            </div>
          </div>
        )}
      </section>

      <IntegrationsSection />

      <div className="rule-label mb-3">Matching &amp; follow-up thresholds</div>
      <div className="space-y-2">
        {data?.parameters
          .filter((p: any) => !String(p.key).startsWith("scoring."))
          .map((p: any) => (
            <div key={p.key} className="card p-3">
              <div className="text-sm font-semibold text-ink-900">{humanLabel(p)}</div>
              {p.description && <div className="text-xs text-ink-500 mt-0.5">{p.description}</div>}
              {p.ui === "slider" && (
                <SliderRow p={p} onCommit={(v) => update.mutate({ key: p.key, value: v })} />
              )}
              {p.ui === "number" && (
                <NumberRow p={p} onCommit={(v) => update.mutate({ key: p.key, value: v })} />
              )}
            </div>
          ))}
      </div>
      <p className="text-xs text-ink-500 mt-3">
        Conference scoring weights now live on the{" "}
        <a href="/conferences" className="text-brand hover:underline">Events</a> page,
        where you can watch events re-rank as you adjust them.
      </p>

      <IcpView icp={data?.icp} />
    </div>
  );
}

// Turn "nudge.recency_days_max" into "Nudge — recency days max" when the
// backend didn't supply a friendly title.
function humanLabel(p: any): string {
  if (p.label) return p.label;
  const parts = String(p.key).split(".");
  const tail = (parts[1] || parts[0]).replace(/_/g, " ");
  const head = parts.length > 1 ? parts[0].replace(/_/g, " ") + " — " : "";
  const s = head + tail;
  return s.charAt(0).toUpperCase() + s.slice(1);
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

function SliderRow({ p, onCommit }: { p: any; onCommit: (v: number) => void }) {
  const [val, setVal] = useState<number>(Number(p.current));
  return (
    <div className="flex items-center gap-3 mt-2">
      <input
        type="range"
        min={p.min} max={p.max} step={p.max > 5 ? 1 : 0.01}
        value={val}
        onChange={(e) => setVal(Number(e.target.value))}
        onMouseUp={() => onCommit(val)}
        onTouchEnd={() => onCommit(val)}
        className="flex-1 accent-brand"
      />
      <span className="text-xs font-mono w-12 text-right">{Number(val).toFixed(2)}</span>
    </div>
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

function NumberRow({ p, onCommit }: { p: any; onCommit: (v: number) => void }) {
  return (
    <input
      type="number"
      defaultValue={Number(p.current)}
      min={p.min} max={p.max}
      onBlur={(e) => {
        const v = Number(e.target.value);
        if (v !== Number(p.current)) onCommit(v);
      }}
      className="input w-32 mt-2"
    />
  );
}
