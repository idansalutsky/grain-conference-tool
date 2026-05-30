# Deploy guide

The app is two pieces:

- **API** — a FastAPI + SQLite backend, shipped as a Docker image.
- **Frontend** — a static React/Vite bundle.

You can run the whole thing with **one command** (no API keys required), then
add keys later from inside the app. Pick the path that fits you:

| You want… | Use | Time | Cost |
|---|---|---|---|
| The fastest possible "it works on my machine / a VPS" | **Path A — Docker Compose** | ~5 min | free (your box/VPS) |
| A public URL on managed hosting | **Path B — Render + Vercel** | ~20 min | ~$7/mo |

> **No keys needed to start.** With no OpenRouter / Perplexity / HubSpot /
> Telegram keys set, the app still boots, seeds its data, serves
> conferences / scoring / planning / companies, captures leads through the
> Brain (deterministic extraction), and runs the LangGraph brain with
> deterministic fallbacks. You add keys in the app at **Settings →
> Integrations** — never in source. See *“What works without any key”* below.

---

## Path A — Docker Compose (recommended, simplest)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/)
(or Docker Engine + the compose plugin). Nothing else.

```bash
git clone <your-repo-url> grain-conference-tool
cd grain-conference-tool
docker compose up -d --build
```

That's it. On first boot the container automatically seeds the database
(conferences, people, and the cross-conference demo contacts). It's idempotent —
restarts won't clobber leads you capture later.

Confirm it's up:

```bash
curl http://localhost:8000/healthz
# {"ok": true, "config": {...}, "row_counts": {"conferences": 195, ...}}
```

The API is now at **http://localhost:8000** (interactive docs at `/docs`).

### Run the frontend

In a second terminal:

```bash
cd frontend
npm install
echo "VITE_API_BASE_URL=http://localhost:8000" > .env.local
npm run dev          # → http://localhost:5173
```

For a production frontend on the same box, build the static bundle and serve
`frontend/dist/` with any web server (or use Path B's Vercel step):

```bash
cd frontend
VITE_API_BASE_URL=http://localhost:8000 npm run build   # → frontend/dist/
```

### Add your API keys (optional, in-app)

Open the app → **Settings → Integrations** → paste your **OpenRouter** key
(get a free one at https://openrouter.ai). Saved keys are stored in the
database and **override** any env var immediately — no restart. Add HubSpot /
Telegram tokens the same way if you want those integrations live.

(If you'd rather use env vars, copy `.env.example` to `.env` before
`docker compose up`. Every var in it is optional.)

---

## Path B — Render (API) + Vercel (frontend)

Gives you two public HTTPS URLs.

### B1. API on Render

1. Push this repo to GitHub.
2. Render → **New → Web Service** → connect the repo. Render auto-detects the
   `Dockerfile`. Set:
   - **Instance Type**: Starter ($7/mo, 512 MB is plenty)
   - **Disk**: add a 1 GB persistent disk mounted at **`/app/data`**
     (this is where the SQLite DB lives — without it, data resets on redeploy)
3. **Environment variables**: none are required. Leave them all blank — you'll
   add keys in-app. (Optionally set `OPENROUTER_API_KEY` here instead.)
4. **Create Web Service**. First boot (~3 min) builds the image and seeds the
   DB. You'll get a URL like `https://grain-api.onrender.com`. Hit `/healthz`.

### B2. Frontend on Vercel

1. Vercel → **New Project** → import the same repo.
2. **Root Directory**: `frontend` (the included `frontend/vercel.json` sets the
   Vite build, `dist` output, and SPA routing automatically).
3. **Environment variable**: `VITE_API_BASE_URL = https://grain-api.onrender.com`
4. **Deploy** → you get `https://<project>.vercel.app`.

### B3. Add your keys

Open the Vercel URL → **Settings → Integrations** → paste your OpenRouter key.
Done.

---

## What works without any key (graceful degradation)

This was verified end-to-end (clean DB, all integration env vars cleared, and
in a fresh container with no keys):

| Feature | No key | With key |
|---|---|---|
| Boot + seed (`/healthz` 200, 195 conferences, demo contacts) | ✅ works | ✅ |
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

- [ ] `GET /healthz` → 200 with row counts (proves seed ran)
- [ ] `GET /api/conferences?limit=3` → 3 conferences (base seed)
- [ ] `GET /api/nudges` → active nudges (demo seed)
- [ ] Frontend loads and shows the conference list
- [ ] `POST /api/brain/run {"input_text":"Met the CFO of Acme, warm chat, wants a follow-up"}` → `status:"complete"`, a lead written (works with no key)
- [ ] (with OpenRouter key) Capture page: tap mic / type a note → structured lead
- [ ] (if using HubSpot) `POST /api/hubspot/push/<contact-id>` → `ok:true`
- [ ] (if using Telegram) see the Telegram footnote below

---

## Footnotes

### Telegram webhook (only if you use the bot)

After the API is public and a `TELEGRAM_BOT_TOKEN` is set (env or
Settings → Integrations) and an `ADMIN_API_KEY` is set, register the webhook
**once**:

```bash
curl -X POST https://grain-api.onrender.com/api/telegram/set-webhook \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_API_KEY" \
  -d '{"base_url":"https://grain-api.onrender.com"}'
```

These management endpoints are admin-gated (the `X-Admin-Token` must match the
server's `ADMIN_API_KEY`) so nobody can hijack your bot. With no `ADMIN_API_KEY`
they fail closed (503). Check health with
`GET /api/telegram/webhook-info` (same header). `DELETE /api/telegram/webhook`
unregisters it.

### Fly.io instead of Render

```bash
fly launch                       # generates fly.toml
fly volumes create grain_data --size 1
fly deploy
```

Add to `fly.toml`:

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
