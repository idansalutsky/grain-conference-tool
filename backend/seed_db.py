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
    """Sample GTM team. Fictional reps (not real Grain employees) — this is a
    demo. IDs are stable region anchors so the UI's default rep never breaks."""
    reps = [
        ("rep-na-01", "Jordan Avery", "jordan@grain.example", "NA"),
        ("rep-eu-01", "Sofia Marsh", "sofia@grain.example", "EU"),
        ("rep-eu-02", "Lukas Berg", "lukas@grain.example", "EU"),
        ("rep-apac-01", "Mei Tan", "mei@grain.example", "APAC"),
        ("rep-bd-01", "Omar Haddad", "omar@grain.example", "EU"),
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


def _vertical_of_conference(name: str, themes: str, explicit: str | None = None) -> str:
    """Classify a conference into a Grain ICP vertical.

    Priority order (highest first):
      1. An explicit, non-empty ``vertical`` field on the conference object.
         Anchors in conferences.json set this to lock the result verbatim.
      2. NAME-based travel/booking/marketplace signal. A travel-industry event
         (Phocuswright, Web in Travel, ITB, Skift, WTM…) whose *themes* also
         mention "payments" must still classify as ``travel`` — so the event
         NAME is matched with higher priority than the theme text for the
         travel wedge, before the generic payments/treasury heuristic runs.
      3. Theme+name heuristic for the remaining verticals (treasury, payments,
         crypto, saas, …), evaluated in an order where the more specific /
         higher-intent signals win.

    The old bug: ``payments`` was checked before ``travel`` against the combined
    name+themes string, so Phocuswright/WiT (whose themes mention payments) were
    mis-tagged ``payments``. This reorders so the travel wedge wins.
    """
    # 1. Explicit override — trust the curated JSON.
    if explicit and str(explicit).strip():
        return str(explicit).strip()

    nm = (name or "").lower()
    th = (themes or "").lower()

    # 2. Travel wedge — matched on the NAME first (the event's identity), so a
    #    travel event whose agenda happens to mention payments stays travel.
    travel_name_keys = (
        "phocuswright", "web in travel", "wit (", "(wit", "itb",
        "skift", "arival", "travel", "tourism", "hospitality",
        "world travel market", "wtm", "future travel experience",
    )
    if any(k in nm for k in travel_name_keys):
        # "booking" / "ota" inside a travel-named event are still the travel wedge.
        return "travel"
    booking_name_keys = ("booking", " ota", "online travel")
    if any(k in nm for k in booking_name_keys):
        return "booking"
    if "marketplace" in nm:
        return "marketplace"

    # 2b. Payments-identity events by NAME — a payments-branded show whose agenda
    #     also lists "treasury" as one of many tracks is still a payments event
    #     (Money20/20, Visa Payments Forum, Seamless, MoneyLIVE…). Match the
    #     payments identity on the NAME before the generic treasury-theme scan so
    #     these don't get pulled to `treasury` by an incidental theme keyword.
    payments_name_keys = ("money20", "money 20", "visa payments", "merchant payments",
                          "seamless", "moneylive", "payments forum", "paytech")
    if any(k in nm for k in payments_name_keys):
        return "payments"

    # 3. Remaining heuristic — name+themes combined, ordered by intent.
    h = nm + " " + th
    for v, keys in [
        # Travel/booking/marketplace by THEME (event not named for travel but
        # the agenda is clearly travel-led).
        ("travel",    ["travel tech", "travel technology", "tourism",
                       "hospitality", "phocuswright"]),
        ("booking",   ["online travel", "ota"]),
        ("marketplace", ["marketplace", "marketplaces"]),
        # Treasury — the highest-intent finance vertical for Grain.
        ("treasury",  ["treasury", "corporate finance", "cash management"]),
        ("cross_border_payments", ["cross-border payment", "cross border payment"]),
        ("psp",       ["psp", "merchant payments", "acquiring"]),
        ("payments",  ["payment", "payments"]),
        ("crypto",    ["crypto", "blockchain", "web3", "stablecoin", "ethereum",
                       "defi", "tokeniz"]),
        ("saas",      ["saas", "product management"]),
        ("fintech_other", ["fintech", "money20", "finovate", "banking",
                           "financial services", "financial technology"]),
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
            # Respect an explicit, authoritative vertical when the seed carries
            # one (anchor events lock it); else fall back to the name/theme
            # heuristic. The explicit value is honoured inside the helper.
            vertical = _vertical_of_conference(
                c.get("name", ""), c.get("themes", "") or "", c.get("vertical"))
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
                "agenda_summary": c.get("agenda_summary"),
                "audience_composition_json": (
                    json.dumps(c["audience_composition_json"], ensure_ascii=False)
                    if isinstance(c.get("audience_composition_json"), (dict, list))
                    else c.get("audience_composition_json")
                ),
                "source_url": c.get("source_url"),
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
                "verified": int(p.get("verified") or 0),
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


# Today's reference date for "is this edition upcoming?" decisions. A
# 2026-planning tool should keep the next upcoming edition of a recurring
# event and drop past-year copies of the same event.
_TODAY_ISO = "2026-05-29"


def dedupe_conferences(today: str = _TODAY_ISO) -> int:
    """Collapse duplicate editions of the same event down to one row.

    Two cases, handled together because they're the same problem at different
    granularity:

      1. Same event, SAME year, scraped from different sources (e.g. the
         curated Money20/20 row + the paytech.events calendar copy).
      2. Same event, DIFFERENT years (e.g. AFP 2024 + AFP 2026,
         Money20/20 USA 2025 + 2026). For a 2026-planning tool the older
         past-year copies are noise.

    Strategy: group every conference by its YEAR-STRIPPED normalised name.
    Within a group, pick ONE winner:
      - Prefer an UPCOMING edition (start_date >= today). Among upcoming
        editions, the earliest upcoming one (the next time you can actually
        attend), then the most complete.
      - If NO edition is upcoming, keep the most recent past edition (latest
        start_date), then the most complete — so a lone past anchor survives
        rather than vanishing.
    People + encounters are repointed to the winner; the losers are deleted.

    The JSON seed is already cleaned of the known cross-year anchor dupes, so
    in the normal path this removes 0. It stays as a safety net so a future
    re-import of stale calendar data can't reintroduce duplicate editions.
    """
    from collections import defaultdict
    conn = db.get_conn()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM conferences").fetchall()]
        groups: dict = defaultdict(list)
        for r in rows:
            groups[_norm_conf_name(r["name"])].append(r)

        def completeness(r) -> int:
            return sum(
                1 for c in ("estimated_attendance", "themes", "website",
                            "cost_pass_usd", "city", "format", "agenda_summary",
                            "audience_composition_json", "source_url")
                if r.get(c) not in (None, "")
            )

        def sort_key(r):
            sd = r.get("start_date") or ""
            upcoming = sd >= today
            if upcoming:
                # earliest upcoming first → invert: smaller date ranks higher.
                # We sort descending overall, so map to a tuple that makes the
                # earliest upcoming the maximum.
                return (1, -_date_ordinal(sd), completeness(r), r.get("score") or 0)
            # past editions: most recent first
            return (0, _date_ordinal(sd), completeness(r), r.get("score") or 0)

        removed = 0
        for _, items in groups.items():
            if len(items) < 2:
                continue
            items.sort(key=sort_key, reverse=True)
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


def _date_ordinal(iso: str) -> int:
    """YYYY-MM-DD → comparable int (YYYYMMDD), 0 if unparseable."""
    if not iso:
        return 0
    digits = "".join(ch for ch in iso[:10] if ch.isdigit())
    return int(digits) if digits else 0


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


def _seed_coverage() -> int:
    """Give each rep a couple of region-matching high-fit events, so the Team
    and Planning views show real coverage out of the box (idempotent)."""
    import uuid as _uuid
    conn = db.get_conn()
    try:
        if conn.execute("SELECT COUNT(*) FROM coverage").fetchone()[0] > 0:
            return 0
        reps = conn.execute("SELECT id, region FROM reps").fetchall()
        n = 0
        for rep in reps:
            region = rep["region"]
            rows = conn.execute(
                "SELECT id FROM conferences WHERE region = ? AND tier IN ('A','B') "
                "AND start_date >= '2026-01-01' ORDER BY score DESC LIMIT 2",
                (region,),
            ).fetchall()
            for cf in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO coverage (id, conference_id, rep_id, created_at) "
                    "VALUES (?,?,?,?)",
                    ("cov_" + _uuid.uuid4().hex[:10], cf["id"], rep["id"], db.now_iso()),
                )
                n += 1
        return n
    finally:
        conn.close()


def _seed_companies() -> dict:
    """Backfill the `companies` table from the people we just seeded.

    The `companies` table starts empty because nothing populated it. This
    walks DISTINCT people.company_name, creates one canonical company row per
    account (deduped by normalized name + the alias table in companies.py —
    so "Maersk" and "A.P. Moller Maersk" collapse to one), links
    people.company_id to it, derives a vertical, and scores every company so
    the /api/companies endpoints return cleanly.

    Idempotent: the underlying resolver upserts by normalized name (existing
    rows are reused, not duplicated) and only links people whose company_id is
    NULL/stale, so re-running is a no-op on already-seeded data.

    Reuses the production `grain.companies` module (the exact code the
    /api/companies/backfill route calls) rather than reimplementing the
    schema-aware INSERTs — this guarantees the seeded columns match the schema
    in db.py and the fields the companies router reads (name, vertical,
    account_tier, icp_score, name_variants_json, source_kind, …).

    Field population:
      - name / name_normalized / name_variants_json  ← resolver (canonical)
      - source_kind = "seed"                         ← marks seed origin
      - vertical    ← mode(people.vertical) per company (reuses the ICP
                       verticals already attached to each person at seed time)
      - account_tier / icp_score / icp_breakdown_json ← companies.score_all()
        (uses the same IcpConfig as the rest of the tool)
      - domain / logo_url / industry / fx_exposure_hint / why_grain_fit ←
        companies.seed_enrich_curated(): a grounded, OFFLINE curated map for the
        recognisable ICP accounts (real domains + specific FX rationale). The
        long tail keeps a NULL domain (no fabrication) and an honest generic
        fit line. A later LLM/Sonar pass (POST /api/companies/enrich/*) can
        still refine these; the seed makes no network calls and is deterministic.
    """
    from grain import companies  # local import: keeps the LLM-touching module
                                 # off the import path unless we're seeding.

    conn = db.get_conn()
    try:
        distinct_names = conn.execute(
            "SELECT COUNT(DISTINCT company_name) FROM people "
            "WHERE company_name IS NOT NULL AND company_name != ''"
        ).fetchone()[0]
        pre_existing = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    finally:
        conn.close()

    if not distinct_names:
        return {"created": 0, "linked_people": 0, "note": "no people with company_name"}

    # 1. Create company rows + link people.company_id (offline: no domain LLM).
    result = companies.backfill(enrich_domains=False)

    # 2. Mark these as seed-origin (resolver defaults source_kind to "backfilled").
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE companies SET source_kind = 'seed', updated_at = ? "
            "WHERE source_kind = 'backfilled' OR source_kind IS NULL",
            (db.now_iso(),),
        )
    finally:
        conn.close()

    # 3. Derive vertical from the mode of each company's people, then re-score.
    #    inherit_vertical_from_people() calls companies.score_all() internally,
    #    which writes account_tier + icp_score + icp_breakdown_json.
    vert = companies.inherit_vertical_from_people()

    # 4. Enrich domain / logo / industry / fx_exposure_hint / why_grain_fit from
    #    the curated, grounded map (offline & deterministic — no network/LLM).
    #    Recognisable ICP accounts get real domains + a specific FX rationale;
    #    the long tail gets an honest generic fit line and a NULL domain (we
    #    don't fabricate domains). Idempotent: only fills NULL/empty fields.
    enr = companies.seed_enrich_curated()

    result.update({
        "vertical_inherited": vert.get("inherited", 0),
        "vertical_unknown": vert.get("left_unknown", 0),
        "companies_pre_existing": pre_existing,
        "enriched_curated": enr.get("curated", 0),
        "enriched_generic": enr.get("generic", 0),
        "domains_set": enr.get("domains_set", 0),
    })
    return result


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
    n_cov = _seed_coverage()
    print(f"Seeded {n_cov} coverage assignments")
    # Companies must be backfilled AFTER people exist — it reads
    # people.company_name and links people.company_id back to the new rows.
    co = _seed_companies()
    print(f"Backfilled companies: {co}")
    # Seed the Grain Brain long-term memory spaces (ICP / events / gaps /
    # playbook / relationship). Idempotent; reads the conferences + ICP we just
    # loaded. Runs LAST so the events/gaps spaces reflect the final conference set.
    from grain.brain.spaces import seed_brain_spaces  # local import
    brain_seed = seed_brain_spaces()
    print(f"Seeded brain spaces: {brain_seed['written']}")
    # L1 hierarchical memory: one judged rollup PER ENTITY (event/account/
    # segment), then L2 space summaries derived from those rollups (no top-50 cap).
    print(f"Built L1 rollups: {brain_seed['rollup_build']}; "
          f"L2 rewired from rollups: {brain_seed['l2_rewire']}")
    counts = db.counts()
    print(f"DB now contains: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
