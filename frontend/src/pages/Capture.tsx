import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { AudioRecorder } from "@/components/AudioRecorder";
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

  const voice_mut = useMutation({
    mutationFn: (blob: Blob) =>
      api.uploadAudio<CaptureResult>("/api/encounters/voice", blob, {
        rep_id: DEFAULT_REP_ID,
        conference_id: confId || "",
      }),
    onSuccess: (d) => {
      setResult(d);
      toast("success", "Voice captured");
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const badge_mut = useMutation({
    mutationFn: (file: File) =>
      api.uploadImage<CaptureResult & { ok?: boolean; reason?: string }>(
        "/api/encounters/image", file,
        { rep_id: DEFAULT_REP_ID, conference_id: confId || "" },
      ),
    onSuccess: (d) => {
      if ((d as any).ok === false) {
        toast("error", (d as any).reason || "Couldn't read the badge — retry.");
        return;
      }
      setResult(d);
      toast("success", "Badge captured");
    },
    onError: (e) => toast("error", toastErrorMessage(e)),
  });

  const busy = voice_mut.isPending || text_mut.isPending || badge_mut.isPending;

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
      <h1 className="text-2xl mb-1">Field Capture</h1>
      <p className="text-sm text-ink-500 mb-6">
        You're on the floor. You just met someone. Voice or type — both work.
      </p>

      <div className="card p-5 mb-4">
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

      <div className="rule-label mb-3">Capture a lead</div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card p-5">
          <div className="label mb-2">🎙️ Voice memo (1-tap)</div>
          <AudioRecorder
            onTranscript={(t) => text_mut.mutate(t)}
            onComplete={(blob) => voice_mut.mutate(blob)}
            disabled={busy}
            processingLabel="Transcribing + extracting…"
            onError={(msg) => toast("error", msg)}
          />
          <p className="text-xs text-ink-500 mt-3">
            Speak naturally — who you met, what they do, what they said. It
            transcribes in your browser, then the AI structures the lead.
          </p>
        </div>

        <div className="card p-5">
          <div className="label mb-2">📷 Snap their badge</div>
          <label
            className={
              "block w-full py-6 rounded-lg text-lg font-semibold text-center cursor-pointer transition " +
              (busy ? "bg-ink-200 text-ink-500 cursor-not-allowed" : "bg-ink-900 text-white hover:bg-ink-700")
            }
          >
            {badge_mut.isPending ? "Reading badge…" : "Tap to photograph"}
            <input
              type="file"
              accept="image/*"
              capture="environment"
              disabled={busy}
              className="sr-only"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) badge_mut.mutate(f);
                e.target.value = ""; // allow re-selecting the same file
              }}
            />
          </label>
          <p className="text-xs text-ink-500 mt-3">
            Faster than typing on a loud floor — the AI reads name, company and
            title off the badge or business card. Bad read? It asks you to retry
            rather than inventing a name.
          </p>
        </div>

        <div className="card p-5 md:col-span-2">
          <div className="label mb-2">⌨️ Or type it — or paste a LinkedIn URL</div>
          <label htmlFor="capture-text" className="sr-only">
            Capture text
          </label>
          <textarea
            id="capture-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={'e.g. "Just met Sarah Cohen, CFO of Booking, runs treasury, wants to talk hedging next week" — or just paste linkedin.com/in/…'}
            className="input w-full h-28 resize-none"
          />
          <button
            onClick={() => text_mut.mutate(text)}
            disabled={!text.trim() || busy}
            className="btn-primary w-full mt-3"
          >
            {text_mut.isPending ? "Processing…" : "Capture"}
          </button>
        </div>
      </div>

      {result && (
        <CaptureResultCard
          key={result.encounter_id}
          result={result}
          onDeleted={() => setResult(null)}
        />
      )}

      {/* ---- Supporting: capture from your phone via Telegram ---------- */}
      <div className="rule-label mt-10 mb-3">Capture on your phone</div>

      <section className="card p-5 mb-4">
        <div className="label mb-2">📱 Telegram — voice, photo &amp; text from the floor</div>
        <p className="text-sm text-ink-700 max-w-[60ch]">
          Reps capture hands-free in Telegram — fire off a voice memo, snap a
          badge, or type a line, and it lands here as a structured lead. Best for
          when you're walking the floor and don't want to open the app.
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

      {/* ---- Supporting: end of event → follow-ups -------------------- */}
      <div className="rule-label mt-10 mb-3">End of event</div>

      <section className="card p-5">
        <div className="label mb-2">🏁 Wrap up &amp; turn captures into follow-ups</div>
        <p className="text-sm text-ink-700 max-w-[60ch]">
          When the event's done, turn your captures into ready-to-send
          follow-ups — drafted per lead and waiting for your review.
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
