"""/api/search — semantic search across conferences + people + contacts."""
from __future__ import annotations

from fastapi import APIRouter

from ... import db, embeddings

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
def search(q: str, limit_per_kind: int = 4) -> dict:
    """Semantic (embedding-based) search. Returns up to `limit_per_kind`
    per entity type. Cosine similarity in Python — fast at ~300 rows."""
    if not q.strip():
        return {"query": q, "results": {}}
    by_kind = embeddings.search(q, limit_per_kind=limit_per_kind)
    # Hydrate the hits with display fields per kind
    out: dict[str, list[dict]] = {}
    conn = db.get_conn()
    try:
        for kind, hits in by_kind.items():
            ids = [h["entity_id"] for h in hits]
            if not ids:
                continue
            placeholders = ",".join("?" * len(ids))
            if kind == "conference":
                rows = conn.execute(
                    f"SELECT id, name, start_date, city, country, vertical, "
                    f"score, tier FROM conferences WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()
            elif kind == "person":
                rows = conn.execute(
                    f"SELECT id, full_name, title, company_name, persona, "
                    f"conference_id FROM people WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()
            elif kind == "contact":
                rows = conn.execute(
                    f"SELECT id, primary_name, primary_title, primary_company, "
                    f"arc_verdict FROM contacts WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()
            else:
                continue
            by_id = {r["id"]: dict(r) for r in rows}
            out[kind] = [
                {**by_id[h["entity_id"]], "score": round(h["score"], 4)}
                for h in hits if h["entity_id"] in by_id
            ]
    finally:
        conn.close()
    return {"query": q, "results": out}


@router.post("/backfill")
def backfill() -> dict:
    """Embed all conferences / people / contacts. Run once after seeding."""
    return embeddings.backfill_all()
