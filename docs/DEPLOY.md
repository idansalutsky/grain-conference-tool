# Deploy guide — ~30 minutes

Two pieces: the API (Docker container) and the frontend (static React build).

## Recommended path

| Component | Host | Why |
|---|---|---|
| API | **Render** ($7/mo starter) | Auto-deploys from a Docker image, persistent disk for `/app/data`. Simpler than Fly for non-developers. |
| Frontend | **Vercel** (free tier) | Static build, instant deploys, custom domain in one click. |

Both have generous free tiers. Total cost: $7/month.

## Option A — Render + Vercel (recommended)

### Backend (Render)

1. Push the repo to GitHub.
2. Render dashboard → **New → Web Service** → connect repo.
3. Render auto-detects `Dockerfile`. Set:
   - **Name**: `grain-api`
   - **Region**: nearest your users
   - **Instance Type**: Starter ($7/mo) — 512MB RAM is enough
   - **Disk**: Add 1 GB persistent disk mounted at `/app/data`
4. Environment variables:
   - `OPENROUTER_API_KEY` — required
   - `TELEGRAM_BOT_TOKEN` — optional
   - `TELEGRAM_BOT_USERNAME` — optional
   - `HUBSPOT_PRIVATE_APP_TOKEN` — optional
5. Click **Create Web Service**. First boot takes ~3 minutes (builds image,
   then seeds the DB via `python -m backend.seed_db` + `python -m backend.seed_demo`
   — conferences/people, then the cross-conference demo contacts. Both are
   idempotent: restarts won't clobber leads captured live).
6. You'll get a URL like `https://grain-api.onrender.com`. Hit `/healthz`
   to confirm.

### Frontend (Vercel)

1. Vercel dashboard → **New Project** → import the same repo.
2. **Root directory**: `frontend`
3. **Build command**: `npm run build` (default)
4. **Output directory**: `dist`
5. Environment variable:
   - `VITE_API_BASE_URL` = `https://grain-api.onrender.com`
6. Deploy. You get a URL like `https://grain-conference.vercel.app`.

### Connecting the Telegram webhook (if using the bot)

The bot only receives messages once Telegram knows the public URL to POST
updates to. There's a one-call helper that does this *and* installs a
spoof-proof secret (so randoms can't POST fake updates to your public
endpoint). After the API is live and `TELEGRAM_BOT_TOKEN` is set (env var or
Settings → API keys), run **once**:

```bash
curl -X POST https://grain-api.onrender.com/api/telegram/set-webhook \
  -H "Content-Type: application/json" \
  -d '{"base_url":"https://grain-api.onrender.com"}'
# → {"ok": true, "webhook_url": ".../api/telegram/webhook", ...}
```

That's it — it derives the webhook path, generates+stores the secret, and
registers everything with Telegram in one shot. To check health any time:

```bash
curl https://grain-api.onrender.com/api/telegram/webhook-info
# shows the registered URL, pending update count, and last error (if any)
```

`DELETE /api/telegram/webhook` unregisters it. (The raw Telegram
`setWebhook` API still works if you prefer doing it by hand, but you'd then
have to manage the secret yourself.)

## Option B — Fly.io

If you prefer Fly:
```bash
fly launch                  # auto-generates fly.toml
fly volumes create grain_data --size 1
fly secrets set OPENROUTER_API_KEY=…
fly deploy
```
Add a `[[mounts]]` block to `fly.toml`:
```toml
[[mounts]]
  source = "grain_data"
  destination = "/app/data"
```

## Option C — Self-host (Docker Compose)

For non-developers who want to run on a VPS:
```bash
git clone <repo>
cd grain-conference-tool
cp .env.example .env       # fill in keys
docker compose up -d --build
# → http://localhost:8000
```
Nginx reverse-proxy + a Let's Encrypt cert (Caddy is easier) handle HTTPS.

## First-boot checklist

- [ ] `/healthz` returns 200 (and shows row counts)
- [ ] `/api/conferences?limit=3` returns 3 conferences (proves base seed loaded)
- [ ] `/api/nudges` returns active nudges (proves demo seed loaded)
- [ ] Frontend loads + shows the conference list
- [ ] Capture page: tap mic, speak (Chrome/Edge transcribes in-browser), get a structured lead — or type it
- [ ] (If using HubSpot) `POST /api/hubspot/push/<contact-id>` returns `ok:true`
- [ ] (If using Telegram) `/api/telegram/bot-info` returns the bot username
- [ ] (If using Telegram) ran `POST /api/telegram/set-webhook` once → `webhook-info` shows the URL with no `last_error_message`

## Backups

SQLite lives in `/app/data/grain.db`. Render's persistent disk is replicated,
but for prod you'd want a daily snapshot:
```bash
# Cron job on the API host:
sqlite3 /app/data/grain.db ".backup /tmp/grain-$(date +%F).db"
aws s3 cp /tmp/grain-$(date +%F).db s3://your-backups/
```

## Scaling notes

The single-process SQLite design tops out around ~50 concurrent users and
~10,000 contacts. Beyond that, swap SQLite for Postgres — `db.py` is the
only module that needs touching. Adapter pattern, not a rewrite.
