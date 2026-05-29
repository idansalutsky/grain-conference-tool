import { useState } from "react";
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

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card p-5">
          <div className="label mb-2">🎙️ Voice memo (1-tap)</div>
          <AudioRecorder
            onTranscript={(t) => text_mut.mutate(t)}
            onComplete={(blob) => voice_mut.mutate(blob)}
            disabled={voice_mut.isPending || text_mut.isPending}
            processingLabel="Transcribing + extracting…"
            onError={(msg) => toast("error", msg)}
          />
          <p className="text-xs text-ink-500 mt-3">
            Speak naturally — who you met, what they do, what they said. It
            transcribes in your browser, then the AI structures the lead.
          </p>
        </div>

        <div className="card p-5">
          <div className="label mb-2">⌨️ Or type it</div>
          <label htmlFor="capture-text" className="sr-only">
            Capture text
          </label>
          <textarea
            id="capture-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder='e.g. "Just met Sarah Cohen, CFO of Booking, runs treasury, wants to talk hedging next week"'
            className="input w-full h-36 resize-none"
          />
          <button
            onClick={() => text_mut.mutate(text)}
            disabled={!text.trim() || text_mut.isPending}
            className="btn-primary w-full mt-3"
          >
            {text_mut.isPending ? "Processing…" : "Capture"}
          </button>
        </div>
      </div>

      {result && <CaptureResultCard result={result} />}
    </div>
  );
}
