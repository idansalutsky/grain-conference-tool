"""Load seed conferences + people into a fresh SQLite DB, then score everything.

Idempotent: if a conference / person id is already present, it's skipped.

Run once after first boot:
    python -m backend.seed_db
or via Docker:
    docker compose exec api python -m backend.seed_db
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

# Allow `python backend/seed_db.py` from project root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from grain import db, scoring  # noqa: E402
from grain.icp import IcpConfig  # noqa: E402

SEED_DIR = Path(__file__).resolve().parent / "seed"


def _seed_reps() -> None:
    """Reps = real Grain GTM team members (scraped from public LinkedIn).
    IDs are stable region anchors so the UI's default rep never breaks."""
    reps = [
        ("rep-na-01", "Chris Day", "chris@grain.test", "NA"),            # VP, North America
        ("rep-eu-01", "Marc Padrosa Cabello", "marc@grain.test", "EU"),  # VP of Sales
        ("rep-eu-02", "Diana Mihaylova", "diana@grain.test", "EU"),      # Director, Fintech
        ("rep-apac-01", "Eugene Lin", "eugene@grain.test", "APAC"),      # Head of Sales (ex-Expedia)
        ("rep-bd-01", "Ben Strugo", "ben@grain.test", "EU"),             # VP, Business Development
    ]
    conn = db.get_conn()
    try:
        for rep_id, name, email, region in reps:
            conn.execute(
                "INSERT OR IGNORE INTO reps "
                "(id, full_name, email, region, created_at) VALUES (?,?,?,?,?)",
                (rep_id, name, email, region, db.now_iso()),
            )
    finally:
        conn.close()


def _vertical_of_conference(name: str, themes: str) -> str:
    """Heuristic — first vertical mention wins."""
    h = (name + " " + (themes or "")).lower()
    for v, keys in [
        ("treasury",  ["treasury"]),
        ("payments",  ["payment", "payments"]),
        ("psp",       ["psp"]),
        ("cross_border_payments", ["cross-border", "cross border"]),
        ("travel",    ["travel", "tourism", "hospitality", "phocuswright", "itb"]),
        ("booking",   ["booking", "ota"]),
        ("marketplace", ["marketplace"]),
        ("fintech_other", ["fintech", "money20", "finovate"]),
        ("crypto",    ["crypto", "blockchain", "web3", "stablecoin"]),
        ("saas",      ["saas"]),
    ]:
        if any(k in h for k in keys):
            return v
    return "fintech_other"


def seed_conferences() -> int:
    path = SEED_DIR / "conferences.json"
    if not path.exists():
        print(f"WARN: {path} not found — no conferences seeded")
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    conn = db.get_conn()
    try:
        for c in data:
            cid = c["id"]
            exists = conn.execute(
                "SELECT 1 FROM conferences WHERE id = ?", (cid,)
            ).fetchone()
            if exists:
                continue
            vertical = _vertical_of_conference(c.get("name", ""), c.get("themes", "") or "")
            payload = {
                "id": cid,
                "name": c["name"],
                "start_date": c.get("start_date"),
                "end_date": c.get("end_date"),
                "city": c.get("city"),
                "country": c.get("country"),
                "region": c.get("region"),
                "website": c.get("website"),
                "format": c.get("format"),
                "estimated_attendance": c.get("estimated_attendance"),
                "themes": c.get("themes"),
                "vertical": vertical,
                "cost_pass_usd": c.get("cost_pass_usd"),
                "cost_booth_usd": c.get("cost_booth_usd"),
                "created_at": db.now_iso(),
                "updated_at": db.now_iso(),
            }
            cols = ",".join(payload.keys())
            ph = ",".join("?" * len(payload))
            conn.execute(f"INSERT INTO conferences ({cols}) VALUES ({ph})",
                         tuple(payload.values()))
            n += 1
    finally:
        conn.close()
    return n


def seed_people() -> int:
    path = SEED_DIR / "people.json"
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    icp = IcpConfig.default()
    n = 0
    conn = db.get_conn()
    try:
        for p in data:
            # Idempotency: skip if this person already exists for this event.
            dup = conn.execute(
                "SELECT 1 FROM people WHERE full_name = ? AND "
                "IFNULL(conference_id,'') = IFNULL(?,'')",
                (p["full_name"], p.get("conference_id")),
            ).fetchone()
            if dup:
                continue
            pid = "p_" + uuid.uuid4().hex[:14]
            persona, weight, _ = icp.classify_persona(p.get("title"))
            payload = {
                "id": pid,
                "full_name": p["full_name"],
                "first_name": p.get("first_name"),
                "last_name": p.get("last_name"),
                "title": p.get("title"),
                "company_name": p.get("company_name"),
                "email": p.get("email"),
                "linkedin_url": p.get("linkedin_url"),
                "vertical": p.get("vertical"),
                "source_kind": p.get("source_kind") or "seed",
                "conference_id": p.get("conference_id"),
                "persona": persona or p.get("persona"),
                "persona_weight": float(weight or p.get("persona_weight") or 0.0),
                "icp_score": p.get("icp_score"),
                "created_at": db.now_iso(),
            }
            # Only insert if the conference_id exists (some seed people point
            # at conferences we didn't import — skip those)
            if payload["conference_id"]:
                exists = conn.execute(
                    "SELECT 1 FROM conferences WHERE id = ?",
                    (payload["conference_id"],),
                ).fetchone()
                if not exists:
                    payload["conference_id"] = None
            cols = ",".join(payload.keys())
            ph = ",".join("?" * len(payload))
            conn.execute(f"INSERT INTO people ({cols}) VALUES ({ph})",
                         tuple(payload.values()))
            n += 1
    finally:
        conn.close()
    return n


def _norm_conf_name(name: str) -> str:
    """Normalised conference name for dedupe: lowercase, year stripped."""
    import re
    n = re.sub(r"\b(19|20)\d{2}\b", "", (name or "").lower())
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def dedupe_conferences() -> int:
    """Merge same-event-same-year duplicates from different sources.

    Keeps the copy with the most populated fields (ties → higher score),
    repoints people + encounters to the kept id, deletes the rest.
    """
    from collections import defaultdict
    conn = db.get_conn()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM conferences").fetchall()]
        groups: dict = defaultdict(list)
        for r in rows:
            yr = (r.get("start_date") or "")[:4]
            groups[(_norm_conf_name(r["name"]), yr)].append(r)

        removed = 0
        for (_, _yr), items in groups.items():
            if len(items) < 2:
                continue
            def completeness(r):
                filled = sum(
                    1 for c in ("estimated_attendance", "themes", "website",
                                "cost_pass_usd", "city", "format")
                    if r.get(c) not in (None, "")
                )
                return (filled, r.get("score") or 0)
            items.sort(key=completeness, reverse=True)
            keep = items[0]["id"]
            for dup in items[1:]:
                conn.execute("UPDATE people SET conference_id = ? WHERE conference_id = ?",
                             (keep, dup["id"]))
                conn.execute("UPDATE encounters SET conference_id = ? WHERE conference_id = ?",
                             (keep, dup["id"]))
                conn.execute("DELETE FROM conferences WHERE id = ?", (dup["id"],))
                removed += 1
        return removed
    finally:
        conn.close()


# Curated public attendance estimates for recognisable events. Real ballpark
# figures from public marketing — NOT invented per-row. Long-tail regional
# events are left null rather than fabricated.
_ATTENDANCE_BY_NORM = {
    "money20 20 usa": 13000, "money20 20 europe": 8500,
    "money20 20 middle east": 4000, "money20 20 asia": 5000,
    "sibos": 10000, "eurofinance international treasury management": 2200,
    "eurofinance international treasury": 2200, "afp annual conference": 6000,
    "seamless europe": 10000, "seamless middle east": 20000,
    "ifx expo international": 4000, "ifx expo": 4000,
    "finovatefall": 2000, "finovate fall": 2000, "finovate europe": 1500,
    "ebaday": 1500, "fintech meetup": 5000, "payments leaders summit": 350,
    "wit web in travel singapore": 1500, "phocuswright": 2000,
    "nordic fintech week": 2000, "open banking expo uk europe": 1500,
    "global fintech fest": 50000, "hong kong fintech week": 30000,
    "shoptalk europe": 3500, "shoptalk fall": 5000,
    "world travel market london": 46000, "fintech week london": 3000,
    "visa payments forum": 1200, "nrf retail s big show asia pacific": 3000,
    "dc fintech week": 1500, "saastr annual": 12500,
}


def backfill_attendance() -> int:
    """Fill estimated_attendance for recognisable events that lack it."""
    conn = db.get_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, name, estimated_attendance FROM conferences "
            "WHERE estimated_attendance IS NULL"
        ).fetchall()]
        n = 0
        for r in rows:
            est = _ATTENDANCE_BY_NORM.get(_norm_conf_name(r["name"]))
            if est:
                conn.execute("UPDATE conferences SET estimated_attendance = ?, "
                             "updated_at = ? WHERE id = ?",
                             (est, db.now_iso(), r["id"]))
                n += 1
        return n
    finally:
        conn.close()


def main() -> int:
    db.init_db()
    _seed_reps()
    n_conf = seed_conferences()
    n_ppl = seed_people()
    print(f"Seeded: {n_conf} new conferences, {n_ppl} new people")
    n_removed = dedupe_conferences()
    n_att = backfill_attendance()
    print(f"Deduped {n_removed} duplicate conferences; backfilled {n_att} attendance figures")
    n_scored = scoring.rescore_all()
    print(f"Re-scored {n_scored} conferences")
    counts = db.counts()
    print(f"DB now contains: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
