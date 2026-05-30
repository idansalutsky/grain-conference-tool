# Deploy guide

The whole app — the FastAPI API **and** the built React frontend — ships as a
**single Docker image served at one URL**. There is no separate frontend to
deploy: open the URL and you get the app; the API lives at `/api` on the same
origin.

You can run it with **one command** and **zero API keys**, then add keys later
from inside the app. Pick the path that fits you:

| You want… | Use | Time | Cost |
|---|---|---|---|
| A public URL on managed hosting (one click) | **Path A — Render Blueprint** (recommended) | ~10 min | ~$7/mo (or free w/ cold starts) |
| It running on your own machine / a VPS | **Path B — Docker Compose** | ~5 min | free (your box/VPS) |

> **No keys needed to start.** With no OpenRouter / Perplexity / HubSpot /
> Telegram keys set, the app still boots, seeds its data, serves
> conferences / scoring / planning / companies, captures leads through the
> Brain (deterministic extraction), and runs the LangGraph brain with
> deterministic fallbacks. You add keys later — in the app at **Settings →
> Integrations**, or as host env vars. See *"What works without any key"* below.

---

## Path A — Render Blueprint (recommended)

This gives you **one public HTTPS URL** that serves both the API and the app.
The repo already contains a `render.yaml` Blueprint, so Render configures
everything for you. No command line needed.

1. **Push this repo to GitHub** (any account; the repo can be private).
2. Go to **[render.com](https://render.com)** and sign in (you can sign in with
   GitHub).
3. Click **New → Blueprint**.
4. **Pick this repository.** Render auto-detects `render.yaml` and shows a
   single web service named `grain-conference-tool`.
5. Click **Apply**. Render now:
   - builds the `Dockerfile` (frontend + API in one image),
   - attaches a **1 GB persistent disk at `/app/data`** (this is where the
     SQLite database lives — without it your data would reset on every deploy),
   - sets the health check to **`/healthz`**,
   - injects `$PORT` (the container honours it automatically).
6. Wait for the first build (~3–5 min). On first boot the container seeds the
   database (conferences, people, and the cross-conference demo contacts).
7. You get a URL like **`https://grain-conference-tool.onrender.com`**.
   Open it → the app loads. Confirm health at
   **`https://grain-conference-tool.onrender.com/healthz`** (returns `{"ok": true, …}`
   with row counts).

That's it — **one URL, both halves of the app.**

### Add your API keys (optional)

You can run the demo with **zero keys**. To unlock the full AI (voice / photo /
LinkedIn extraction, richer briefs, discovery), add an **OpenRouter** key (a
free one from [openrouter.ai](https://openrouter.ai) covers the demo). Two ways:

- **In-app (recommended):** open the app → **Settings → Integrations** → paste
  the key. It's stored in the database and takes effect immediately — no
  restart, no redeploy.
- **Render env var:** in the Render dashboard, open the service → **Environment**
  → set `OPENROUTER_API_KEY` (and optionally `HUBSPOT_PRIVATE_APP_TOKEN`,
  `TELEGRAM_BOT_TOKEN`). The Blueprint already lists these as optional
  (`sync:false`) so they start empty.

> **In-app overrides env.** If a key is set both in Render and in
> Settings → Integrations, the in-app value wins. Keys are never required in
> source.

---

## Path B — Docker Compose (your machine / a VPS)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/)
(or Docker Engine + the compose plugin). Nothing else — no Node, no Python.

```bash
git clone <your-repo-url> grain-conference-tool
cd grain-conference-tool
docker compose up --build
```

One command builds the image (frontend + API) and starts it. On first boot the
container automatically seeds the database (conferences, people, and the
cross-conference demo contacts). It's idempotent — restarts won't clobber leads
you capture later, and the DB persists in `./data` on your host.

Open **http://localhost:8000** → the app. The API is same-origin under `/api`
(interactive API docs at `/docs`). Confirm it's up:

```bash
curl http://localhost:8000/healthz
# {"ok": true, "config": {...}, "row_counts": {"conferences": 195, ...}}
```

### Add your API keys (optional)

Same as Path A: open the app → **Settings → Integrations** → paste your
**OpenRouter** key (free one at https://openrouter.ai). Saved keys are stored in
the database and **override** any env var immediately — no restart. Add
HubSpot / Telegram tokens the same way.

If you'd rather use env vars, copy `.env.example` to `.env` **before**
`docker compose up` (compose loads it if present). Every var in it is optional;
the in-app value still wins over the env var.

---

## What works without any key (graceful degradation)

This was verified end-to-end (clean DB, all integration env vars cleared, in a
fresh container with no keys):

| Feature | No key | With key |
|---|---|---|
| Boot + seed (`/healthz` 200, conferences, demo contacts) | ✅ works | ✅ |
| Browse conferences, scores, planning (coverage/clusters/gaps), companies, nudges | ✅ works | ✅ |
| **Capture a lead** via the Brain (`POST /api/brain/run`) | ✅ deterministic extraction (pulls company/title/sentiment/meeting-intent from the text, writes to memory) | ✅ richer LLM extraction |
| Brain query + discovery runs | ✅ deterministic answers/proposals | ✅ LLM-grounded |
| HubSpot push | ✅ **dry-run** (shows what would be sent) | ✅ live push |
| Settings → Integrations (configure keys) | ✅ works | ✅ |
| Voice / photo / LinkedIn lead extraction | ⚠️ needs OpenRouter (these are LLM-only paths) | ✅ |
| Telegram field-capture bot | ⚠️ needs a bot token | ✅ |

So a non-developer can host the whole thing, click around, and capture leads
**before** ever touching a key — then unlock the AI extraction quality by
pasting one OpenRouter key in Settings.

---

## First-boot checklist

- [ ] Open the URL → the app loads (conference list visible)
- [ ] `GET /healthz` → 200 with row counts (proves seed ran)
- [ ] `GET /api/conferences?limit=3` → 3 conferences (base seed)
- [ ] `GET /api/nudges` → active nudges (demo seed)
- [ ] `POST /api/brain/run {"input_text":"Met the CFO of Acme, warm chat, wants a follow-up"}` → `status:"complete"`, a lead written (works with no key)
- [ ] (with OpenRouter key) Capture page: tap mic / type a note → structured lead
- [ ] (if using HubSpot) `POST /api/hubspot/push/<contact-id>` → `ok:true`
- [ ] (if using Telegram) see the Telegram footnote below

---

## Advanced / alternative: split frontend + backend

The single-URL deploy above is the recommended path. A **split** deploy — the
frontend on Vercel and the API on Render — is still supported but **no longer
recommended** (two URLs, two services, CORS to think about). If you want it:

1. **API on Render** as a plain web service (the Dockerfile alone serves the API;
   it'll also serve the SPA, but you can ignore that half). Add the same 1 GB
   disk at `/app/data`. You get `https://<api>.onrender.com`.
2. **Frontend on Vercel** → New Project → import the same repo → **Root
   Directory** `frontend` (the included `frontend/vercel.json` sets the Vite
   build, `dist` output, and SPA routing). Set the env var
   **`VITE_API_BASE_URL = https://<api>.onrender.com`** so the SPA calls the
   remote API instead of its own origin.

That's the only reason `VITE_API_BASE_URL` and `frontend/vercel.json` exist. In
the single-URL deploy the SPA uses a relative base (`""`) and just works.

---

## Footnotes

### Telegram webhook (only if you use the bot)

After the app is public and a `TELEGRAM_BOT_TOKEN` is set (env or
Settings → Integrations) and an `ADMIN_API_KEY` is set, register the webhook
**once**:

```bash
curl -X POST https://grain-conference-tool.onrender.com/api/telegram/set-webhook \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_API_KEY" \
  -d '{"base_url":"https://grain-conference-tool.onrender.com"}'
```

These management endpoints are admin-gated (the `X-Admin-Token` must match the
server's `ADMIN_API_KEY`) so nobody can hijack your bot. With no `ADMIN_API_KEY`
they fail closed (503). Check health with
`GET /api/telegram/webhook-info` (same header). `DELETE /api/telegram/webhook`
unregisters it.

### Fly.io instead of Render

The same single image runs anywhere that takes a Dockerfile. On Fly:

```bash
fly launch                       # generates fly.toml from the Dockerfile
fly volumes create grain_data --size 1
fly deploy
```

Add to `fly.toml` so the DB persists:

```toml
[[mounts]]
  source = "grain_data"
  destination = "/app/data"
```

### Backups

The whole app state is one SQLite file at `/app/data/grain.db`. Snapshot it:

```bash
sqlite3 /app/data/grain.db ".backup /tmp/grain-$(date +%F).db"
```

On Render the persistent disk is already replicated; for production add a daily
cron that copies the snapshot to object storage.

### Windows note

If you run the seed scripts directly on a Windows console (not Docker), set
`PYTHONIOENCODING=utf-8` first so emoji in the seed output don't crash the
script (the Docker image already sets this).

### Scaling

Single-process SQLite comfortably handles ~50 concurrent users / ~10k contacts.
Beyond that, swap SQLite for Postgres — `backend/grain/db.py` is the only module
that changes (adapter pattern, not a rewrite).
