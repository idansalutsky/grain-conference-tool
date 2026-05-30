# syntax=docker/dockerfile:1.6
# Single image that serves BOTH the FastAPI backend and the built React SPA,
# so the whole app is one clickable URL (Render / docker compose / etc.).
#
# Stage 1 builds the frontend into frontend/dist.
# Stage 2 installs the Python API and copies that dist in, so FastAPI serves it.

# ---------------------------------------------------------------------------
# Stage 1 — build the React/Vite frontend
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /frontend

# Install deps first (better layer caching).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build.
COPY frontend/ ./
RUN npm run build   # -> /frontend/dist

# ---------------------------------------------------------------------------
# Stage 2 — Python API that also serves the built SPA
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend /app/backend

# Bring the built SPA into the location main.py looks for: <repo root>/frontend/dist.
# In this image the repo root is /app, so /app/frontend/dist is what
# _resolve_frontend_dist() (parents[3]/frontend/dist) finds.
COPY --from=frontend /frontend/dist /app/frontend/dist

ENV PYTHONPATH=/app/backend

# SQLite DB lives here and persists across restarts (Render disk / compose volume).
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:${PORT}/healthz > /dev/null || exit 1

# Seed conferences/people (idempotent) + cross-conference demo contacts (only
# when there are no contacts yet) + 2 ambiguous-match examples so the Review
# Queue has something to show on a fresh deploy, then start the API on Render's
# $PORT (default 8000). uvicorn runs regardless of seed outcome.
CMD ["sh", "-c", "python -m backend.seed_db || true; python -m backend.seed_demo || true; python -m backend.seed_review_examples || true; uvicorn grain.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
