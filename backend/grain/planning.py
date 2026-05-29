"""Planning view — coverage, clusters, gaps.

Three deliverables from the brief:
  1. Coverage across the year (rep-allocated vs gaps)
  2. Where multiple events cluster (geographically + temporally)
  3. Where we're under-invested

This module is deterministic — fast, defensible, no LLM. Returns
JSON the frontend renders directly.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from . import db


def _all_active_conferences() -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, name, start_date, end_date, city, country, region, "
            "score, tier, vertical, estimated_attendance, cost_pass_usd "
            "FROM conferences WHERE start_date IS NOT NULL "
            "ORDER BY start_date ASC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def coverage() -> dict:
    """Per-month conference coverage across a rolling 12-month planning window.

    The window starts at the first day of the current month and spans the next
    12 months (inclusive of the start month). Events outside that window are
    excluded so the "year" view is an honest 12 months, not the 17-month
    2026+2027 sprawl the old "calendar year" label implied (DEFECT P2). The
    returned `window` makes the scope explicit for the UI.
    """
    from datetime import date

    confs = _all_active_conferences()

    today = date.today()
    start_year, start_month = today.year, today.month
    # 12 inclusive months: e.g. start 2026-05 -> through 2027-04.
    end_index = (start_year * 12 + (start_month - 1)) + 11
    end_year, end_month = divmod(end_index, 12)
    end_month += 1
    window_start = f"{start_year:04d}-{start_month:02d}"
    window_end = f"{end_year:04d}-{end_month:02d}"

    by_month: dict[str, dict] = defaultdict(
        lambda: {"month": "", "n_conferences": 0, "by_tier": {"A": 0, "B": 0, "C": 0},
                 "by_region": defaultdict(int), "total_attendance": 0})

    n_in_window = 0
    for c in confs:
        d = c.get("start_date")
        if not d:
            continue
        try:
            month_key = d[:7]  # YYYY-MM
        except Exception:
            continue
        # Restrict to the rolling 12-month window (string compare is safe for
        # zero-padded YYYY-MM keys).
        if not (window_start <= month_key <= window_end):
            continue
        n_in_window += 1
        slot = by_month[month_key]
        slot["month"] = month_key
        slot["n_conferences"] += 1
        slot["by_tier"][c.get("tier") or "C"] += 1
        if c.get("region"):
            slot["by_region"][c["region"]] += 1
        slot["total_attendance"] += int(c.get("estimated_attendance") or 0)

    months = sorted(by_month.values(), key=lambda x: x["month"])
    for m in months:
        m["by_region"] = dict(m["by_region"])
    return {
        "months": months,
        "n_total": n_in_window,            # events within the 12-month window
        "n_total_all": len(confs),         # all dated events in the DB
        "window": {"start": window_start, "end": window_end, "months": 12},
    }


# ---------------------------------------------------------------------------
# Trip clustering — geographic + temporal proximity
# ---------------------------------------------------------------------------
TEMPORAL_WINDOW_DAYS = 21    # events within 3 weeks can be a single trip
GEO_CLUSTERS = {
    # Compact, defensible regional groupings — not perfect, but explainable.
    "EU_CENTRAL": {"Germany", "Netherlands", "Belgium", "Luxembourg", "France", "Switzerland"},
    "EU_NORTH":   {"United Kingdom", "Ireland", "Sweden", "Norway", "Denmark", "Finland"},
    "EU_SOUTH":   {"Spain", "Portugal", "Italy", "Greece"},
    "EU_EAST":    {"Poland", "Czechia", "Hungary", "Romania", "Bulgaria"},
    "NA_EAST":    {"United States", "Canada"},
    "APAC_SEA":   {"Singapore", "Malaysia", "Thailand", "Indonesia", "Vietnam", "Philippines"},
    "APAC_NE":    {"Japan", "South Korea", "China", "Taiwan", "Hong Kong"},
    "MEA":        {"UAE", "Saudi Arabia", "Qatar", "Bahrain", "Egypt", "Israel"},
    "LATAM":      {"Brazil", "Argentina", "Mexico", "Colombia", "Chile"},
}


def _geo_cluster(country: Optional[str]) -> Optional[str]:
    if not country:
        return None
    for cluster, members in GEO_CLUSTERS.items():
        if country in members:
            return cluster
    return None


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def trip_clusters(min_size: int = 2) -> list[dict]:
    """Find groups of conferences in the same geo cluster within a
    `TEMPORAL_WINDOW_DAYS` window. Returns the swings ranked by total score.
    """
    confs = _all_active_conferences()
    by_geo: dict[str, list[dict]] = defaultdict(list)
    for c in confs:
        g = _geo_cluster(c.get("country"))
        if not g:
            continue
        c["_dt"] = _parse_date(c.get("start_date"))
        if c["_dt"] is None:
            continue
        c["_geo"] = g
        by_geo[g].append(c)

    swings: list[dict] = []
    for g, items in by_geo.items():
        items.sort(key=lambda x: x["_dt"])  # type: ignore[index]
        i = 0
        while i < len(items):
            cluster = [items[i]]
            j = i + 1
            while j < len(items):
                if (items[j]["_dt"] - cluster[-1]["_dt"]).days <= TEMPORAL_WINDOW_DAYS:
                    cluster.append(items[j])
                    j += 1
                else:
                    break
            if len(cluster) >= min_size:
                total_score = sum(c.get("score") or 0 for c in cluster)
                # Estimated savings: 1 flight per trip vs n flights
                # (rough rule of thumb: $400 transatlantic/transpacific savings each)
                savings_usd = 400 * (len(cluster) - 1) if g.startswith(("EU_", "APAC_")) else 200 * (len(cluster) - 1)
                swings.append({
                    "geo_cluster": g,
                    "start_date": cluster[0]["start_date"],
                    "end_date": cluster[-1]["end_date"] or cluster[-1]["start_date"],
                    "span_days": (cluster[-1]["_dt"] - cluster[0]["_dt"]).days,
                    "conferences": [
                        {"id": c["id"], "name": c["name"], "city": c["city"],
                         "country": c["country"], "start_date": c["start_date"],
                         "score": c["score"], "tier": c["tier"]}
                        for c in cluster
                    ],
                    "total_score": round(total_score, 1),
                    "estimated_savings_usd": savings_usd,
                })
            i = j if j > i + 1 else i + 1

    swings.sort(key=lambda s: s["total_score"], reverse=True)
    return swings


# ---------------------------------------------------------------------------
# Gaps — where we're under-invested
# ---------------------------------------------------------------------------
def gaps() -> dict:
    """Surface tier-A/B conferences with zero rep encounters (so far)."""
    confs = _all_active_conferences()
    conn = db.get_conn()
    try:
        cids_with_enc = {
            r["conference_id"] for r in conn.execute(
                "SELECT DISTINCT conference_id FROM encounters "
                "WHERE conference_id IS NOT NULL"
            ).fetchall()
        }
    finally:
        conn.close()
    uncovered_a = [c for c in confs if c.get("tier") == "A" and c["id"] not in cids_with_enc]
    uncovered_b = [c for c in confs if c.get("tier") == "B" and c["id"] not in cids_with_enc]
    return {
        "uncovered_tier_a": [
            {"id": c["id"], "name": c["name"], "start_date": c["start_date"],
             "city": c["city"], "country": c["country"], "score": c["score"]}
            for c in uncovered_a[:10]
        ],
        "uncovered_tier_b": [
            {"id": c["id"], "name": c["name"], "start_date": c["start_date"],
             "city": c["city"], "country": c["country"], "score": c["score"]}
            for c in uncovered_b[:10]
        ],
        "total_uncovered_a": len(uncovered_a),
        "total_uncovered_b": len(uncovered_b),
    }
