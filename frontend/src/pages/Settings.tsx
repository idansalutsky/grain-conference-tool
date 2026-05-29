import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

const DEFAULT_REP_ID = "rep-na-01";

export function SettingsPage() {
  useDocumentTitle("Settings");
  const qc = useQueryClient();
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
    },
  });

  const rescore = useMutation({
    mutationFn: () => api.post("/api/conferences/rescore"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conferences"] }),
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
      <p className="text-sm text-ink-500 mb-6">
        Tune the scoring + matching + nudge thresholds. Changes take effect
        immediately; conference scores re-compute on demand.
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

      <div className="flex justify-between items-baseline mb-3">
        <h2 className="label">Parameters</h2>
        <button
          onClick={() => rescore.mutate()}
          disabled={rescore.isPending}
          className="btn-secondary text-xs"
        >
          {rescore.isPending ? "Re-scoring…" : "↻ Re-score all conferences"}
        </button>
      </div>
      <div className="space-y-2">
        {data?.parameters.map((p: any) => (
          <div key={p.key} className="card p-3">
            <div className="text-xs text-ink-500 font-mono">{p.key}</div>
            <div className="text-sm text-ink-700">{p.description}</div>
            {p.ui === "slider" && (
              <SliderRow p={p} onCommit={(v) => update.mutate({ key: p.key, value: v })} />
            )}
            {p.ui === "number" && (
              <NumberRow p={p} onCommit={(v) => update.mutate({ key: p.key, value: v })} />
            )}
          </div>
        ))}
      </div>

      <details className="mt-6">
        <summary className="cursor-pointer text-xs text-ink-500">ICP config</summary>
        <pre className="text-xs bg-ink-50 p-3 rounded mt-2 overflow-auto">
          {JSON.stringify(data?.icp, null, 2)}
        </pre>
      </details>
    </div>
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
