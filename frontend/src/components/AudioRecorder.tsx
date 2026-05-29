import { useEffect, useRef, useState } from "react";

interface Props {
  /** Called with a live transcript when the browser supports speech recognition. */
  onTranscript?: (text: string) => void;
  /** Fallback: called with the recorded blob when speech recognition is unavailable. */
  onComplete?: (blob: Blob) => void;
  /** Disables the button (e.g. while the upload is processing). */
  disabled?: boolean;
  /** Shown when disabled and processing. */
  processingLabel?: string;
  /** Called with the human error message on mic-permission failure. */
  onError?: (message: string) => void;
}

// Browser SpeechRecognition (Chrome/Edge) — keyless, in-browser transcription.
const SpeechRecognition: any =
  (typeof window !== "undefined" &&
    ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)) ||
  null;

/**
 * Floor-empathic mic button. Two paths, picked automatically:
 *
 *   1. Web Speech API (Chrome/Edge) — transcribes IN-BROWSER, keyless. The
 *      transcript goes to the text→lead path, which is fast and reliable. This
 *      is the primary path: no audio-format fragility, no extra LLM audio call.
 *   2. Fallback (no SpeechRecognition) — records an audio blob and uploads it to
 *      the multimodal voice endpoint.
 *
 * Either way the rep just taps once, talks, and taps to stop.
 */
export function AudioRecorder({
  onTranscript, onComplete, disabled, processingLabel, onError,
}: Props) {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [interim, setInterim] = useState("");

  const mediaRecRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const tickRef = useRef<number | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number>(0);
  const recognitionRef = useRef<any>(null);
  const transcriptRef = useRef<string>("");

  const useSpeech = Boolean(SpeechRecognition && onTranscript);

  function cleanup() {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    if (tickRef.current !== null) window.clearInterval(tickRef.current);
    rafRef.current = null;
    tickRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
      audioCtxRef.current.close().catch(() => {});
    }
    audioCtxRef.current = null;
    mediaRecRef.current = null;
    recognitionRef.current = null;
    setRecording(false);
    setLevel(0);
    setInterim("");
  }

  useEffect(() => () => cleanup(), []);

  function startTimer() {
    startedAtRef.current = Date.now();
    setSeconds(0);
    tickRef.current = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAtRef.current) / 1000));
    }, 500);
  }

  async function start() {
    // ---- Path 1: in-browser speech recognition (preferred) ----
    if (useSpeech) {
      try {
        const rec = new SpeechRecognition();
        rec.lang = "en-US";
        rec.continuous = true;
        rec.interimResults = true;
        transcriptRef.current = "";
        rec.onresult = (ev: any) => {
          let interimText = "";
          for (let i = ev.resultIndex; i < ev.results.length; i++) {
            const chunk = ev.results[i][0].transcript;
            if (ev.results[i].isFinal) transcriptRef.current += chunk + " ";
            else interimText += chunk;
          }
          setInterim(interimText);
        };
        rec.onerror = (e: any) => {
          if (e?.error && e.error !== "no-speech" && e.error !== "aborted") {
            onError?.(`Speech recognition: ${e.error}`);
          }
        };
        rec.onend = () => {
          const text = transcriptRef.current.trim();
          cleanup();
          if (text) onTranscript?.(text);
          else onError?.("Didn't catch that — try again or type it.");
        };
        recognitionRef.current = rec;
        rec.start();
        startTimer();
        setRecording(true);
        return;
      } catch (e: any) {
        // fall through to blob recording
      }
    }

    // ---- Path 2: record an audio blob, upload to multimodal endpoint ----
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      const buf = new Uint8Array(analyser.frequencyBinCount);
      const tickLevel = () => {
        analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 3.5));
        rafRef.current = requestAnimationFrame(tickLevel);
      };
      rafRef.current = requestAnimationFrame(tickLevel);

      const mr = new MediaRecorder(stream);
      chunksRef.current = [];
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        cleanup();
        if (blob.size > 0) onComplete?.(blob);
      };
      mr.start();
      mediaRecRef.current = mr;
      startTimer();
      setRecording(true);
    } catch (e: any) {
      onError?.(e?.message || "Microphone permission denied");
      cleanup();
    }
  }

  function stop() {
    if (recognitionRef.current) recognitionRef.current.stop();
    else mediaRecRef.current?.stop();
  }

  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  const bgClass = disabled
    ? "bg-ink-200 text-ink-500"
    : recording
    ? "bg-red-600 text-white"
    : "bg-brand text-white hover:bg-emerald-600";

  return (
    <div>
      <button
        onClick={recording ? stop : start}
        disabled={disabled}
        aria-label={recording ? "Stop recording" : "Start recording"}
        className={
          "w-full py-6 rounded-lg text-lg font-semibold transition relative overflow-hidden " +
          bgClass
        }
      >
        {disabled
          ? processingLabel || "Processing…"
          : recording
          ? `● Recording — ${mm}:${ss}  (tap to stop)`
          : "Tap to record"}
        {recording && !useSpeech && (
          <span
            aria-hidden="true"
            className="absolute inset-x-0 bottom-0 h-1 bg-white/60 origin-left transition-transform"
            style={{ transform: `scaleX(${level.toFixed(3)})` }}
          />
        )}
      </button>

      {recording && useSpeech && (
        <p className="mt-2 text-xs text-ink-500 min-h-[1rem]">
          {interim ? <span className="italic">“{interim}”</span> : "Listening…"}
        </p>
      )}
      {recording && !useSpeech && (
        <div className="mt-2 flex items-center gap-2 text-xs text-ink-500">
          <div className="flex-1 h-1.5 bg-ink-100 rounded overflow-hidden">
            <div
              className="h-full bg-emerald-500 transition-[width] duration-100"
              style={{ width: `${Math.round(level * 100)}%` }}
            />
          </div>
          <span className="font-mono w-12 text-right">{Math.round(level * 100)}%</span>
        </div>
      )}
    </div>
  );
}
