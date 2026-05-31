# Grain Conference Intelligence

A sales tool for Grain Finance's conference-led pipeline. It follows one
salesperson through their year — **Decide → Plan → Capture → Recognise → Act** —
on a single spine: a contact identity that persists across events.

1. **Decide** which conferences to prioritise — transparent 7-factor ICP-fit scoring + A/B/C tiers
2. **Plan** team coverage across the year + spot trip clusters and gaps; manage the team and assign who covers which event
3. **Capture** leads in the field — entirely from **Telegram** (the rep's phone is the field device). Each event page hands the rep a **per-event deep link**; one tap binds the bot to that event, and from then on everything they send Telegram — a voice memo, a badge photo, a typed line, a shared contact — is structured by the agent and filed back into the platform, auto-tagged to that event. No web page to open on the floor.
4. **Recognise** cross-conference relationships — warming / flat / cooling / tire-kicker
5. **Act** — calibrated nudges, AI follow-up drafts, and a push to HubSpot that carries the intelligence

> The web app is the **manager's command surface** (player-coach): the Dashboard
> opens on the state of the floor — high-value events ahead, what's covered vs
> exposed, the most under-invested segment, and the relationships warming enough
> to close — combining the coverage data with the engine's read.

Non-developers run it end to end: tune scoring weights (sliders), add an event,
add a rep, and assign coverage — all no-code in the UI.

> ICP is encoded once (`backend/grain/icp.py`) — travel/booking-led, heavy-FX,
> finance/treasury buyer — and every feature (event scoring, target ranking,
> approach briefs, relationship reads) derives from it. Tune the ICP, the whole
> tool re-prioritises.

## The cross-conference spine (what's judged hardest)

When a rep captures someone, the system:
- **Extracts** a structured lead from speech or text (name, title, company, vertical, sentiment, soft signals, meeting?)
- **Resolves** it against the contact pool — nickname-aware (Mike↔Michael), transliteration-aware (Müller↔Mueller), company-rebrand-aware, and **refuses to auto-merge** two real people with the same name (→ human review queue)
- **Classifies** the relationship arc — a hybrid deterministic + LLM-judge verdict, not a count
- **Calibrates** whether a nudge is worth firing — silent on weak signal, fires on a warming relationship with no meeting yet, and bypasses recency when someone changes job into an ICP role
- **Drafts** a contextual follow-up, and pushes `grain_*` properties to HubSpot so the judgment travels with the contact

See `docs/CROSS_CONFERENCE.md` for the matching + arc + nudge design and edge cases.

## Field capture — the Telegram agent (not the web app)

Capture lives entirely in the **Telegram bot**, because the field device is a
phone, not a laptop. A rep sends a voice memo, a badge photo, a typed line, or a
shared contact; the agent transcribes/reads it (Gemini via OpenRouter),
structures the lead, resolves it against the contact pool, and replies with what
it understood — hands-free. Each rep binds the bot once (Settings → Connect
Telegram, or a per-event link from any event page), so captures attribute
automatically. The web app never captures; it's the desk where the manager
reviews, fixes, and acts. See `docs/AI_STRATEGY.md`.

## Quick start (local, ~5 minutes)

```bash
# 1. (optional) configure keys — the app boots & degrades gracefully with none.
#    You can skip this and add keys later in-app (Settings → Integrations).
cp .env.example .env        # every var is optional; paste an OpenRouter key for full AI

# 2a. Run via Docker — works with zero config
docker compose up --build   # → http://localhost:8000

# 2b. …or run the two processes directly
python -m venv .venv && . .venv/Scripts/activate   # (Linux/mac: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn grain.api.main:app --app-dir backend --port 8000   # backend
cd frontend && npm install && npm run dev                  # frontend → http://localhost:5173
```

### Seed the demo data (first run only)

A fresh database is empty. Two scripts populate it (no API credits needed):

```bash
python -m backend.seed_db      # 77 real conferences (scored+tiered) + 861 real scraped people across 26 events + real Grain reps
python -m backend.seed_demo    # the cross-conference demo: 6 sample contacts, all arc states + nudges
```

> The demo contacts (Sarah Cohen, etc.) are **fictional sample data** — like the
> seed contacts in any CRM trial. Nothing is scraped about real individuals. The
> *intelligence reading them* (matching, arc, nudge) is the real engine: the
> seeder runs them through the live pipeline and persists whatever it produces.

## Env vars (all optional — none hardcoded, keys go in the in-app Settings)

Nothing here is required to boot. Keys are configured by the user in
**Settings → Integrations** (stored in the DB, override any env var at runtime);
env vars are just a convenience for headless hosting.

| Var | Required? | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | optional* | Universal LLM gateway (Gemini extraction, Perplexity Sonar discovery). *Without it, capture/brain fall back to deterministic extraction; voice/photo/LinkedIn extraction need it. |
| `TELEGRAM_BOT_TOKEN` | optional | Field capture via Telegram (the only capture channel); unset → no field capture, web app still runs fully |
| `HUBSPOT_PRIVATE_APP_TOKEN` | optional | Unset → HubSpot push runs in dry-run mode |
| `DATA_DIR` | optional | Default `./data` — SQLite + audio cache live here |

## Architecture

```
Frontend — React + Vite + Tailwind  (manager command surface, 6 tabs:
              Dashboard · Events(+Find new) · Calendar · People(Contacts+Follow-ups) · Team · Intelligence · ⚙ Settings)
              — capture is NOT a web tab; it's the Telegram field agent
        │  HTTP / JSON  (frontend calls /api/*)
Backend — FastAPI + SQLite (one file, no migrations)
        │  scoring · entity-resolution · arc · nudge · brief · voice ·
        │  planning · discovery · prep-agent · reps+coverage
        ├── OpenRouter   (Gemini extraction + Perplexity Sonar discovery)
        ├── HubSpot      (contact push, dry-run without a token)
        └── Telegram     (optional field-capture channel)
```

Scoring, planning, entity-resolution, arc and nudge are **deterministic** — the
demo runs from seeded data with zero live LLM calls. Only *new* capture,
discovery and brief generation hit the LLM.

## Deploy

The whole app — API **and** built React frontend — ships as **one Docker image
served at a single URL** (the API lives at `/api` on the same origin).

- **Public URL (recommended):** push to GitHub → Render → **New → Blueprint** →
  pick the repo (the included `render.yaml` is auto-detected) → **Apply**. You
  get one `https://…onrender.com` URL serving everything, a persistent disk for
  the SQLite DB, and `/healthz` checks — no keys required.
- **Local / VPS:** `docker compose up --build` → http://localhost:8000 serves
  the whole app (DB persists in `./data`).

A split Vercel-frontend + Render-backend deploy is still possible but no longer
the recommended path. Full steps and the keyless degradation matrix are in
`docs/DEPLOY.md`.

## Docs

- `docs/SCORING.md` — the 7-factor methodology, defended
- `docs/CROSS_CONFERENCE.md` — entity resolution + arc + nudge calibration + edge cases
- `docs/AI_STRATEGY.md` — every AI feature and why AI is the right tool for it
- `docs/SCOPE_DECISIONS.md` — what's in, what's deliberately out, and why
- `docs/DEPLOY.md` — single-URL deploy walk-through (Render Blueprint + Docker Compose)
- `docs/VIDEO_SCRIPT.md` — walkthrough talking points

## Tests

```bash
PYTHONPATH=backend python -m pytest tests/    # 58 tests, ~3s, LLM calls stubbed
```
