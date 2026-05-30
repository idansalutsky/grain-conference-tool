# syntax=docker/dockerfile:1.6
# Single-stage slim image — runs the FastAPI backend.
# Frontend is served separately (Vercel/Netlify); this image is the API.
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

ENV PYTHONPATH=/app/backend

# Data directory persists across container restarts via the docker-compose volume.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:${PORT}/healthz > /dev/null || exit 1

# Seed conferences/people (idempotent) + the cross-conference demo contacts
# (only when the DB has no contacts yet), then start the API. uvicorn runs
# regardless of seed outcome.
CMD ["sh", "-c", "python -m backend.seed_db || true; python -m backend.seed_demo || true; uvicorn grain.api.main:app --host 0.0.0.0 --port ${PORT}"]
