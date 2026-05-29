# AI strategy — how voice + briefs + arc actually work

A common question: *"You're doing voice-to-text. Are you downloading Whisper?"*

No. Here's the actual strategy.

## Single LLM gateway — OpenRouter

OpenRouter is a universal HTTP API in front of every major model. We use **one
API key** (`OPENROUTER_API_KEY`) and switch models per task by ID string. No
SDKs to install per provider; no per-model auth flows.

| Task | Model | Why |
|---|---|---|
| Voice → structured lead | browser Web Speech API → `google/gemini-2.5-flash` | Transcribe **in-browser** (keyless), then one LLM call structures the text. Fallback path sends the audio blob to Gemini multimodal. |
| Text → structured lead | `google/gemini-2.5-flash` | Same model, same prompt. Web-typed capture path. |
| Approach brief generation | `google/gemini-2.5-flash` | Long-context JSON synthesis is what Gemini Flash is best at. |
| Web-grounded discovery + news | `perplexity/sonar` | Built-in citation: returns URLs alongside text. We trust nothing else for "recent news about Stripe". |
| Arc classifier — LLM judge | `google/gemini-2.5-flash` | Light reasoning task; JSON output. |

That's it. **3 models. 1 API key. 1 SDK (`httpx`).**

## Voice flow — what actually happens in a few seconds

**Primary path (Chrome/Edge — keyless transcription):**
```
1. Rep taps the mic button on Capture; the browser Web Speech API
   transcribes speech to text IN-BROWSER (no key, no upload of audio).
2. On stop, the transcript is POSTed to /api/encounters/text.
3. ONE OpenRouter call (gemini-2.5-flash) structures it into JSON:
      {name, title, company, vertical, sentiment, soft_signals,
       meeting_requested, what_discussed, transcript}
4. Entity resolver finds-or-creates the contact.
5. Arc classifier (deterministic, then optional LLM judge) + nudge run
   in the background; the structured lead returns to the rep immediately.
```

**Fallback path (browsers without speech recognition):**
```
1. MediaRecorder captures an audio blob → POST /api/encounters/voice.
2. ONE OpenRouter call to Gemini multimodal: audio in, structured JSON out.
3. Same resolver → arc → nudge cascade.
```

No Whisper download, no self-hosted STT, no multi-model chain. The transcription
is either free (browser) or a single multimodal call.

## Why this design

- **One prompt to debug.** The extraction prompt is the single place to fix
  if structuring returns garbage — independent of how the text was transcribed.
- **Language-agnostic.** The structuring prompt asks for the same JSON schema
  regardless of input language, and translates `what_discussed` to English so
  HubSpot stays consistent. Browser transcription covers the languages the
  browser supports; the multimodal fallback handles multilingual audio.
- **Cost is negligible.** ~$0.0012 per 30-second memo. A rep doing 50
  captures across a conference spends $0.06 of LLM budget.
- **No infrastructure.** We're not hosting Whisper. We're not paying for
  GPU. We're not running an inference server. One HTTPS call to OpenRouter.

## Where the LLM is **not** used (deliberately)

| Component | Why no LLM |
|---|---|
| Conference scoring (7 factors) | Defensibility > sophistication. A salesperson must be able to argue with the score factor-by-factor. Template-driven evidence strings. |
| Entity resolution rules | Deterministic confidence math is auditable. We don't want a model deciding to merge two contacts. |
| Arc classifier rules | Same. Deterministic verdict first; LLM judge runs as a second opinion. They have to AGREE for confidence to lift. |
| Nudge gate | 5 hard rules + 1 bypass. No LLM. Explainable as "warming + ≥2 encounters + no meeting yet + recent touch". |
| Planning (clusters + gaps) | Geo + temporal math. No reason to involve an LLM. |

## Where the LLM **is** used

| Component | Job | Fallback if it fails |
|---|---|---|
| Voice extraction | Audio → JSON | No web-mic? Type into the text box instead. Same prompt. |
| Text extraction | Text → JSON | n/a |
| Brief synthesis | Search results + person context → brief JSON | `_fallback_brief()` returns a vertical-specific template. |
| Web search (briefs) | Recent news + citations | Empty `trigger_news` array; brief still produced. |
| Arc judge | Encounter history → verdict | Deterministic classifier is the ground truth; LLM judge is the second opinion. |
| Conference discovery | Find new events | Returns empty list; user keeps using the seeded ones. |

## The "no Whisper download" answer in one sentence

*Transcription is free in the browser (Web Speech API); a single OpenRouter call
structures the text into a lead; the multimodal fallback routes audio straight
to Gemini Flash — no local model, no separate STT service to host.*

To force the multimodal path everywhere (e.g. a kiosk browser without speech
recognition), the audio endpoint already accepts the blob; or point
`OPENROUTER_AUDIO_MODEL` at any audio-capable model ID.

## Cost projection (real numbers)

| Scenario | LLM ops / month | $/month |
|---|---|---|
| 1 rep, 50 captures + 5 briefs + 1 discovery | ~$0.10 |
| 5 reps, 250 captures + 25 briefs + 4 discoveries | ~$0.55 |
| Whole sales team (20 reps), 1000 captures + 100 briefs + 20 discoveries | ~$2.20 |

The variable cost is not a constraint to growth at any plausible scale.
