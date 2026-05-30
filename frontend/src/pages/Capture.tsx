import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { CaptureResultCard } from "@/components/CaptureResultCard";
import { useToast, toastErrorMessage } from "@/components/Toast";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import type { CaptureResult, Conference } from "@/lib/types";

// Default rep for the demo. In a real prod deploy this would come from auth.
const DEFAULT_REP_ID = "rep-na-01";

export function CapturePage() {
  useDocumentTitle("Capture");
  const { push: toast } = useToast();
  const [text, setText] = useState("");
  const [confId, setConfId] = useState<string>("");
  const [result, setResult] = useState<CaptureResult | null>(null);

  const confs = useQuery({
    queryKey: ["conferences-for-capture"],
    queryFn: () =>
      api.get<{ items: Conference[] }>("/api/conferences", { query: { limit: 200 } }),
  });

  // Field capture (voice / photo / contact-card / LinkedIn) lives in the
  // TELEGRAM bot — it's what a rep has in their hand on the floor, and the bot
  // has the tools to handle whatever they send. The web app keeps just a quick
  // typed note for when you're at a desk, plus connect + wrap.
  const text_mut = useMutation({
    mutationFn: (override?: string) =>
      api.post<CaptureResult>("/api/encounters/text", {
        text: override ?? text,
        rep_id: DEFAULT_REP_ID,
        conference_id: confId || null,
      }),
    onSuccess: (d) => {
      setResult(d);
      setText("");
      toast("success", "Captured");
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  // Telegram connect — mirrors Settings.tsx: POST /api/telegram/issue-token
  // returns a one-time deep_link the rep opens on their phone to bind the bot.
  const [tgToken, setTgToken] = useState<any | null>(null);
  const [copied, setCopied] = useState(false);
  const issueTgToken = useMutation({
    mutationFn: () =>
      api.post<any>("/api/telegram/issue-token", { rep_id: DEFAULT_REP_ID }),
    onSuccess: (d) => {
      setTgToken(d);
      setCopied(false);
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const selectedConf = confs.data?.items.find((c) => c.id === confId);

  return (
    <div>
      <h1 className="text-2xl mb-1">Capture</h1>
      <p className="text-sm text-ink-500 mb-6 max-w-[64ch]">
        On the floor, you capture in your pocket — connect Telegram and fire off a
        voice memo, badge photo, or a line of text and it lands as a structured
        lead. At a desk, jot a quick note below.
      </p>

      <div className="card p-5 mb-6">
        <label htmlFor="capture-event" className="label">
          Which event?
        </label>
        <select
          id="capture-event"
          value={confId}
          onChange={(e) => setConfId(e.target.value)}
          className="input w-full mt-1"
        >
          <option value="">— select event (or leave blank) —</option>
          {confs.data?.items.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} {c.start_date ? `· ${c.start_date}` : ""}
            </option>
          ))}
        </select>
      </div>

      {/* ---- The field tool: Telegram (voice / photo / text in your hand) ---- */}
      <div className="rule-label mb-3">Your field tool — Telegram</div>
      <section className="card p-5 mb-6">
        <p className="text-sm text-ink-700 max-w-[60ch]">
          The bot handles whatever you send on the floor — a voice memo, a snap of
          a badge, a typed line, a shared contact, or a LinkedIn URL — and turns it
          into a structured, resolved lead, hands-free. Connect it once.
        </p>
        <button
          onClick={() => issueTgToken.mutate()}
          disabled={issueTgToken.isPending}
          className="btn-primary mt-4"
        >
          {issueTgToken.isPending ? "Generating…" : "Connect Telegram"}
        </button>

        {tgToken && (
          <div className="mt-4 border-t border-ink-100 pt-4">
            <div className="label mb-1">Open this on your phone</div>
            <div className="flex flex-col sm:flex-row sm:items-center gap-2">
              <a
                href={tgToken.deep_link}
                target="_blank"
                rel="noreferrer"
                className="text-brand font-mono text-xs break-all hover:underline flex-1"
              >
                {tgToken.deep_link}
              </a>
              <button
                onClick={() => {
                  navigator.clipboard
                    ?.writeText(tgToken.deep_link)
                    .then(() => {
                      setCopied(true);
                      toast("success", "Link copied");
                    })
                    .catch(() => toast("error", "Couldn't copy — long-press to copy"));
                }}
                className="btn-secondary text-sm shrink-0"
              >
                {copied ? "Copied ✓" : "Copy link"}
              </button>
            </div>
            <p className="text-xs text-ink-500 mt-2">
              One-time link. Opening it binds the bot{" "}
              <span className="font-mono">@{tgToken.bot_username || "GrainSales_bot"}</span>{" "}
              to your rep profile. You can also bind per-event from any event's page.
            </p>
          </div>
        )}
      </section>

      {/* ---- Desk fallback: a quick typed note ----------------------------- */}
      <div className="rule-label mb-3">Quick note (at the desk)</div>
      <section className="card p-5 mb-2">
        <label htmlFor="capture-text" className="sr-only">
          Capture text
        </label>
        <textarea
          id="capture-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={'e.g. "Just met Sarah Cohen, CFO of Booking, runs treasury, wants to talk hedging next week" — or paste a linkedin.com/in/… URL'}
          className="input w-full h-24 resize-none"
        />
        <button
          onClick={() => text_mut.mutate(text)}
          disabled={!text.trim() || text_mut.isPending}
          className="btn-primary mt-3"
        >
          {text_mut.isPending ? "Processing…" : "Capture note"}
        </button>
      </section>

      {result && (
        <CaptureResultCard
          key={result.encounter_id}
          result={result}
          onDeleted={() => setResult(null)}
        />
      )}

      {/* ---- End of event → follow-ups ------------------------------------ */}
      <div className="rule-label mt-10 mb-3">End of event</div>
      <section className="card p-5">
        <div className="label mb-2">🏁 Wrap up &amp; turn captures into follow-ups</div>
        <p className="text-sm text-ink-700 max-w-[60ch]">
          When the event's done, turn your captures into ready-to-send follow-ups
          — drafted per lead and waiting for your review.
        </p>
        {selectedConf ? (
          <Link
            to={`/conferences/${selectedConf.id}`}
            className="btn-primary mt-4 inline-flex"
          >
            Wrap up {selectedConf.name} → draft follow-ups
          </Link>
        ) : (
          <p className="text-xs text-ink-500 mt-3">
            Pick an event above, then come back here to wrap it up.
          </p>
        )}
      </section>
    </div>
  );
}
