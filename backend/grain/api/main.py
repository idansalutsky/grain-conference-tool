"""FastAPI app — Grain Conference Intelligence."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import config, db
from .routers import (
    agents, brain, briefs, companies, conferences, contacts, discovery,
    encounters, followups, hubspot, nudges, people, planning, reps, review,
    settings, telegram, today,
)

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="Grain Conference Intelligence",
    version="1.0.0",
    lifespan=lifespan,
    description=(
        "Decide which conferences to attend, plan team coverage, capture "
        "leads in the field, track relationships across conferences, push "
        "intelligence-enriched contacts to HubSpot."
    ),
)

# CORS. The production deploy is single-origin (this service serves the SPA and
# the API together), so the browser never makes a cross-origin call and CORS is
# effectively unused. The default stays permissive for the optional split
# (Vercel frontend + separate API) and local tooling; lock it down in production
# by setting CORS_ALLOW_ORIGINS to a comma-separated allowlist of exact origins.
_cors_origins = [
    o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if o.strip()
] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", tags=["health"])
def healthz() -> dict:
    return {"ok": True, "config": config.summary(), "row_counts": db.counts()}


for r in (
    today.router, conferences.router, companies.router, people.router,
    contacts.router, encounters.router, briefs.router, nudges.router,
    planning.router, settings.router, hubspot.router, telegram.router,
    discovery.router, review.router, agents.router, reps.router,
    brain.router, followups.router,
):
    app.include_router(r)


# ---------------------------------------------------------------------------
# Serve the built React SPA (single origin) — only when a production build
# exists. In local dev there is no `frontend/dist`, so this whole block is a
# no-op and the API behaves exactly as before (Vite dev server on :5173 proxies
# /api → :8000). With a build present (Docker / Render), the same service
# serves both the API and the SPA, so api.ts's relative base ("") just works.
# ---------------------------------------------------------------------------
def _resolve_frontend_dist() -> Path | None:
    """Locate `frontend/dist` robustly.

    Honours an explicit FRONTEND_DIST env override, otherwise walks up from this
    file (backend/grain/api/main.py -> repo root) looking for `frontend/dist`.
    Returns None if no build is present.
    """
    override = os.getenv("FRONTEND_DIST")
    candidates = []
    if override:
        candidates.append(Path(override))
    here = Path(__file__).resolve()
    # parents[3] == repo root both locally and in the Docker image (/app).
    for parent in here.parents:
        candidates.append(parent / "frontend" / "dist")
    for c in candidates:
        if c.is_dir() and (c / "index.html").is_file():
            return c
    return None


_DIST = _resolve_frontend_dist()

if _DIST is not None:
    _INDEX = _DIST / "index.html"
    _ASSETS = _DIST / "assets"
    if _ASSETS.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(_ASSETS)),
            name="assets",
        )

    # Reserved server prefixes that must NOT be swallowed by the SPA catch-all.
    _RESERVED = ("api", "healthz", "docs", "redoc", "openapi.json", "assets")

    @app.get("/", include_in_schema=False)
    def _spa_root() -> FileResponse:
        return FileResponse(str(_INDEX))

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa_catch_all(full_path: str) -> FileResponse:
        # Let real backend routes / docs / static 404 normally instead of
        # masking them with the SPA shell.
        first = full_path.split("/", 1)[0]
        if first in _RESERVED:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not Found")
        # Serve any concrete file that exists in dist (e.g. favicon), else the
        # SPA shell so client-side routes deep-link / refresh correctly.
        candidate = (_DIST / full_path).resolve()
        try:
            candidate.relative_to(_DIST.resolve())
        except ValueError:
            candidate = _INDEX  # path traversal guard -> fall back to shell
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_INDEX))

    logging.getLogger(__name__).info("Serving SPA from %s", _DIST)
else:
    # No production build present — keep the dev-friendly JSON root.
    @app.get("/", tags=["health"])
    def root() -> dict:
        return {
            "service": "grain-conference-intel",
            "docs": "/docs",
            "healthz": "/healthz",
        }
