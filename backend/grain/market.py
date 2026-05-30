"""Voice-of-the-market intelligence, aggregated from real conversations.

Captures don't only produce leads — when a buyer says what bothers them, what
they wish existed, or which competitor they use, that's GTM/product signal. The
extractor files those into encounter.structured (`competitor_signals`,
`product_signals`); this rolls them up across every conversation so the team can
see what the market is telling them — surfaced smartly (deduped, ranked, with the
person/company context), not as raw noise.
"""
from __future__ import annotations

import json
import re

from . import db, icp


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _known_competitors() -> list[str]:
    try:
        return list(icp.IcpConfig.default().competitors)
    except Exception:  # noqa: BLE001
        return []


def market_signals(limit_each: int = 12) -> dict:
    """Aggregate conversation-derived market intelligence.

    Returns:
      competitors: [{name, count, samples:[note...]}]  — ranked by how many
                   conversations raised that competitor (matched against the ICP
                   competitor list, case-insensitive substring).
      product:     [{note, who}]                        — product/PMF/sales notes,
                   newest first, each with its contact + company for context.
    Read-only.
    """
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT e.structured_json, e.captured_at, "
            "c.primary_name, c.primary_company "
            "FROM encounters e LEFT JOIN contacts c ON c.id = e.contact_id "
            "WHERE e.structured_json LIKE '%_signals%' "
            "ORDER BY e.captured_at DESC"
        ).fetchall()
    finally:
        conn.close()

    competitors = _known_competitors()
    comp_agg: dict[str, dict] = {}
    product: list[dict] = []
    seen_product: set[str] = set()

    for r in rows:
        try:
            s = json.loads(r["structured_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        who = " · ".join(
            [x for x in (r["primary_name"], r["primary_company"]) if x]
        ) or "a contact"

        for note in (s.get("competitor_signals") or []):
            if not isinstance(note, str) or not note.strip():
                continue
            low = note.lower()
            # Attribute to a known competitor if one is named, else "other".
            named = next((c for c in competitors if c.lower() in low), None)
            key = _norm(named) if named else "_other"
            entry = comp_agg.setdefault(
                key, {"name": named or "Other / unspecified",
                      "count": 0, "samples": []})
            entry["count"] += 1
            if len(entry["samples"]) < 3:
                entry["samples"].append(f"{note.strip()} — {who}")

        for note in (s.get("product_signals") or []):
            if not isinstance(note, str) or not note.strip():
                continue
            k = _norm(note)
            if k in seen_product:
                continue
            seen_product.add(k)
            product.append({"note": note.strip(), "who": who})

    comp_list = sorted(comp_agg.values(),
                       key=lambda x: x["count"], reverse=True)[:limit_each]
    return {"competitors": comp_list, "product": product[:limit_each]}
