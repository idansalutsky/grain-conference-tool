"""FastAPI app — Grain Conference Intelligence."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import config, db
from .routers import (
    agents, briefs, companies, conferences, contacts, discovery, encounters,
    hubspot, insights, nudges, people, planning, review, search, settings,
    telegram, today,
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", tags=["health"])
def healthz() -> dict:
    return {"ok": True, "config": config.summary(), "row_counts": db.counts()}


@app.get("/", tags=["health"])
def root() -> dict:
    return {"service": "grain-conference-intel", "docs": "/docs", "healthz": "/healthz"}


for r in (
    today.router, conferences.router, companies.router, people.router,
    contacts.router, encounters.router, briefs.router, nudges.router,
    planning.router, settings.router, hubspot.router, telegram.router,
    discovery.router, review.router, search.router, agents.router,
    insights.router,
):
    app.include_router(r)
