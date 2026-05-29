"""Semantic search via embeddings — the AI-native upgrade to global search.

Why embeddings, not SQL LIKE:
  - LIKE matches strings. "treasury head at travel co" finds NOTHING in a
    record that says "VP Finance at Booking Holdings".
  - Embeddings match MEANING. The same query finds Sarah Cohen because the
    vector for "treasury head at travel co" is close to the vector for
    "CFO at Booking Holdings — covers FX and treasury operations".

Approach (deliberately simple, fits the brief's "no complex pipeline"):
  - OpenRouter exposes Gemini text-embedding-004 (768 dims, $0.000025 / 1k tokens)
  - We embed each row's "search text" on insert / update
  - Stored as JSON-serialised float lists in `embeddings` table
  - Query: embed the query text, cosine similarity in Python over ~300 rows
  - 300 rows × 768 dims = 230k floats = ~2ms cosine math, no index needed

A vector index (sqlite-vss, faiss) would be needed at 100k+ rows. Not now.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Optional

import httpx

from . import config, db

log = logging.getLogger("grain.embeddings")

EMBED_MODEL = "google/gemini-embedding-001"
EMBED_DIM = 3072  # gemini-embedding-001 returns 3072-dim vectors

# How many embedding requests to fire concurrently during bulk backfill.
BACKFILL_PARALLELISM = 8


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            entity_kind   TEXT NOT NULL,        -- conference | person | contact
            entity_id     TEXT NOT NULL,
            search_text   TEXT NOT NULL,
            vector_json   TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            PRIMARY KEY (entity_kind, entity_id)
        )
    """)


# ---------------------------------------------------------------------------
# Embed via OpenRouter
# ---------------------------------------------------------------------------
def embed_text(text: str) -> Optional[list[float]]:
    """Get an embedding vector for `text`. Returns None on any failure."""
    if not text or not text.strip():
        return None
    if not config.OPENROUTER_API_KEY:
        return None
    url = f"{config.OPENROUTER_BASE_URL}/embeddings"
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/grain-finance/conference-intel",
        "X-Title": "Grain Conference Intelligence",
    }
    payload = {"model": EMBED_MODEL, "input": text}
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        log.warning("embedding request failed: %s", exc)
        return None
    if r.status_code != 200:
        log.warning("embedding %s: %s", r.status_code, r.text[:200])
        return None
    data = r.json()
    try:
        return [float(x) for x in data["data"][0]["embedding"]]
    except (KeyError, IndexError, TypeError):
        log.warning("embedding response malformed: %s", str(data)[:200])
        return None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def upsert(entity_kind: str, entity_id: str, search_text: str) -> bool:
    """Embed the text and store. Returns True if stored, False on LLM error.

    Callers should NOT block on this — embedding adds 500-1000ms. Schedule
    it as a background task after the entity is persisted.
    """
    vec = embed_text(search_text)
    if vec is None:
        return False
    conn = db.get_conn()
    try:
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO embeddings (entity_kind, entity_id, search_text, "
            "vector_json, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(entity_kind, entity_id) DO UPDATE SET "
            "search_text = excluded.search_text, "
            "vector_json = excluded.vector_json, "
            "updated_at = excluded.updated_at",
            (entity_kind, entity_id, search_text,
             json.dumps(vec), db.now_iso()),
        )
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Read / search
# ---------------------------------------------------------------------------
def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def search(query: str, *, kinds: Optional[list[str]] = None,
           limit_per_kind: int = 4) -> dict[str, list[dict]]:
    """Semantic search across all embedded entities.

    Returns dict keyed by entity_kind: each value is a ranked list of
    {entity_id, search_text, score}.
    """
    qvec = embed_text(query)
    if qvec is None:
        return {}
    conn = db.get_conn()
    try:
        _ensure_table(conn)
        where = ""
        params: list = []
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            where = f"WHERE entity_kind IN ({placeholders})"
            params = list(kinds)
        rows = conn.execute(
            f"SELECT entity_kind, entity_id, search_text, vector_json "
            f"FROM embeddings {where}", params,
        ).fetchall()
    finally:
        conn.close()

    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        try:
            vec = json.loads(r["vector_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        score = _cosine(qvec, vec)
        by_kind.setdefault(r["entity_kind"], []).append({
            "entity_id": r["entity_id"],
            "search_text": r["search_text"],
            "score": score,
        })
    for k, hits in by_kind.items():
        hits.sort(key=lambda h: -h["score"])
        by_kind[k] = hits[:limit_per_kind]
    return by_kind


# ---------------------------------------------------------------------------
# Composers — produce the search_text we embed for each row type
# ---------------------------------------------------------------------------
def conference_text(row: dict) -> str:
    parts = [
        row.get("name") or "",
        row.get("vertical") or "",
        row.get("themes") or "",
        row.get("city") or "",
        row.get("country") or "",
        row.get("format") or "",
    ]
    return " · ".join(p for p in parts if p)


def person_text(row: dict) -> str:
    parts = [
        row.get("full_name") or "",
        row.get("title") or "",
        row.get("company_name") or "",
        row.get("persona") or "",
        row.get("vertical") or "",
    ]
    return " · ".join(p for p in parts if p)


def contact_text(row: dict) -> str:
    parts = [
        row.get("primary_name") or "",
        row.get("primary_title") or "",
        row.get("primary_company") or "",
        row.get("arc_verdict") or "",
        (row.get("arc_summary") or "")[:200],
    ]
    return " · ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Bulk backfill (run after seed_db)
# ---------------------------------------------------------------------------
def backfill_all(*, limit_per_kind: Optional[int] = None,
                 skip_if_present: bool = True) -> dict[str, int]:
    """Embed every conference + person + contact. Returns counts per kind.

    Parallelised via a thread pool (`BACKFILL_PARALLELISM` requests in flight).
    Safe to re-run — `skip_if_present=True` (default) avoids re-embedding rows
    that already have a vector.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out = {"conferences": 0, "people": 0, "contacts": 0,
           "skipped_no_text": 0, "skipped_already_embedded": 0, "failed_embed": 0}

    conn = db.get_conn()
    try:
        _ensure_table(conn)
        present = set()
        if skip_if_present:
            present = {
                (r["entity_kind"], r["entity_id"])
                for r in conn.execute(
                    "SELECT entity_kind, entity_id FROM embeddings"
                ).fetchall()
            }
        cf_rows = conn.execute(
            "SELECT id, name, vertical, themes, city, country, format "
            "FROM conferences" + (f" LIMIT {limit_per_kind}" if limit_per_kind else "")
        ).fetchall()
        pp_rows = conn.execute(
            "SELECT id, full_name, title, company_name, persona, vertical "
            "FROM people" + (f" LIMIT {limit_per_kind}" if limit_per_kind else "")
        ).fetchall()
        ct_rows = conn.execute(
            "SELECT id, primary_name, primary_title, primary_company, "
            "arc_verdict, arc_summary FROM contacts"
            + (f" LIMIT {limit_per_kind}" if limit_per_kind else "")
        ).fetchall()
    finally:
        conn.close()

    tasks: list[tuple[str, str, str]] = []  # (kind, id, text)
    for r in cf_rows:
        t = conference_text(dict(r))
        if not t:
            out["skipped_no_text"] += 1; continue
        if ("conference", r["id"]) in present:
            out["skipped_already_embedded"] += 1; continue
        tasks.append(("conference", r["id"], t))
    for r in pp_rows:
        t = person_text(dict(r))
        if not t:
            out["skipped_no_text"] += 1; continue
        if ("person", r["id"]) in present:
            out["skipped_already_embedded"] += 1; continue
        tasks.append(("person", r["id"], t))
    for r in ct_rows:
        t = contact_text(dict(r))
        if not t:
            out["skipped_no_text"] += 1; continue
        if ("contact", r["id"]) in present:
            out["skipped_already_embedded"] += 1; continue
        tasks.append(("contact", r["id"], t))

    # Parallel embed + upsert
    def _do(kind: str, eid: str, text: str) -> tuple[str, bool]:
        return (kind, upsert(kind, eid, text))

    with ThreadPoolExecutor(max_workers=BACKFILL_PARALLELISM) as pool:
        futures = [pool.submit(_do, k, i, t) for k, i, t in tasks]
        for fut in as_completed(futures):
            kind, ok = fut.result()
            if not ok:
                out["failed_embed"] += 1
            elif kind == "conference":
                out["conferences"] += 1
            elif kind == "person":
                out["people"] += 1
            elif kind == "contact":
                out["contacts"] += 1
    return out
