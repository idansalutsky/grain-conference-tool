"""SQLite schema + helpers. Single-tenant, single-file, no migrations.

Tables:
  conferences   — events the sales team can attend
  people        — surfaced targets (speakers, sponsors, attendees) per conference
  contacts      — canonical contact entity (after cross-conference resolution)
  encounters    — every floor capture (voice/text). Resolves to a contact.
  briefs        — cached approach brief per (contact, conference)
  feedback      — every AI decision + human override (audit trail)
  reps          — sales reps (used by the Telegram bot to attribute captures)
  settings      — tunable parameters (ICP weights, thresholds)
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import config

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conferences (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    start_date      TEXT,           -- YYYY-MM-DD
    end_date        TEXT,
    city            TEXT,
    country         TEXT,
    region          TEXT,           -- NA / EU / APAC / MEA / LATAM
    website         TEXT,
    format          TEXT,           -- expo / summit / conference / webinar
    estimated_attendance INTEGER,
    themes          TEXT,           -- comma-separated
    vertical        TEXT,           -- fintech / payments / travel / saas / treasury / crypto / other
    agenda_summary  TEXT,           -- grounded 1-2 sentence summary of the agenda/audience
    audience_composition_json TEXT, -- {cfo_treasury_finance_pct, engineering_product_pct, ...}
    source_url      TEXT,           -- where the event data was scraped from
    cost_pass_usd   REAL,
    cost_booth_usd  REAL,
    score           REAL,
    tier            TEXT,
    score_breakdown_json TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conf_score ON conferences(score DESC);
CREATE INDEX IF NOT EXISTS idx_conf_tier ON conferences(tier);
CREATE INDEX IF NOT EXISTS idx_conf_dates ON conferences(start_date);

CREATE TABLE IF NOT EXISTS people (
    id              TEXT PRIMARY KEY,
    full_name       TEXT NOT NULL,
    first_name      TEXT,
    last_name       TEXT,
    title           TEXT,
    company_name    TEXT,
    email           TEXT,
    linkedin_url    TEXT,
    vertical        TEXT,
    source_kind     TEXT,           -- speaker / sponsor / attendee / manual
    conference_id   TEXT,
    persona         TEXT,           -- BUYER / CHAMPION / PAIN_OWNER / GATEKEEPER / ENTRY_POINT / INFLUENCER
    persona_weight  REAL,
    icp_score       REAL,
    verified        INTEGER NOT NULL DEFAULT 0,  -- 1 = web-verified by the agent
    created_at      TEXT NOT NULL,
    FOREIGN KEY (conference_id) REFERENCES conferences(id)
);
CREATE INDEX IF NOT EXISTS idx_people_conf ON people(conference_id);
CREATE INDEX IF NOT EXISTS idx_people_persona ON people(persona);

CREATE TABLE IF NOT EXISTS contacts (
    id              TEXT PRIMARY KEY,
    primary_name    TEXT NOT NULL,
    primary_email   TEXT,
    primary_company TEXT,
    primary_title   TEXT,
    linkedin_handle TEXT,
    name_variants_json TEXT,
    arc_verdict     TEXT,           -- warming / flat / cooling / tire_kicker
    arc_summary     TEXT,
    arc_confidence  REAL,
    nudge_active    INTEGER NOT NULL DEFAULT 0,
    nudge_text      TEXT,
    hubspot_contact_id TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_arc ON contacts(arc_verdict);
CREATE INDEX IF NOT EXISTS idx_contacts_nudge ON contacts(nudge_active);

CREATE TABLE IF NOT EXISTS encounters (
    id              TEXT PRIMARY KEY,
    contact_id      TEXT,
    conference_id   TEXT,
    rep_id          TEXT,
    captured_at     TEXT NOT NULL,
    capture_mode    TEXT,           -- voice / text / web / telegram
    raw_input       TEXT,
    audio_path      TEXT,
    structured_json TEXT,
    soft_signals_json TEXT,
    sentiment       INTEGER,        -- 1..5
    meeting_requested INTEGER NOT NULL DEFAULT 0,
    followup_draft  TEXT,
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (conference_id) REFERENCES conferences(id)
);
CREATE INDEX IF NOT EXISTS idx_enc_contact ON encounters(contact_id);
CREATE INDEX IF NOT EXISTS idx_enc_conf ON encounters(conference_id);
CREATE INDEX IF NOT EXISTS idx_enc_rep ON encounters(rep_id);

CREATE TABLE IF NOT EXISTS briefs (
    id              TEXT PRIMARY KEY,
    contact_id      TEXT,
    conference_id   TEXT,
    person_id       TEXT,
    brief_text      TEXT,
    brief_json      TEXT,
    generated_at    TEXT NOT NULL,
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (conference_id) REFERENCES conferences(id)
);
CREATE INDEX IF NOT EXISTS idx_briefs_contact ON briefs(contact_id);
CREATE INDEX IF NOT EXISTS idx_briefs_person ON briefs(person_id);

CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_kind   TEXT NOT NULL,  -- match_approve / match_reject / arc_override / nudge_dismiss / brief_rate / parameter_update
    target_kind     TEXT,
    target_id       TEXT,
    before_value    TEXT,
    after_value     TEXT,
    reason          TEXT,
    decided_by      TEXT,
    decided_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_kind ON feedback(decision_kind);

CREATE TABLE IF NOT EXISTS reps (
    id              TEXT PRIMARY KEY,
    full_name       TEXT NOT NULL,
    email           TEXT,
    region          TEXT,
    telegram_user_id INTEGER,
    telegram_link_token TEXT,
    telegram_link_token_event_id TEXT,
    active_conference_id TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reps_tg ON reps(telegram_user_id);
CREATE INDEX IF NOT EXISTS idx_reps_active_conf ON reps(active_conference_id);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,        -- canonical display name
    name_normalized TEXT NOT NULL UNIQUE,        -- lower-cased, stripped, used for dedupe
    domain          TEXT,                        -- wise.com
    logo_url        TEXT,                        -- google s2 favicon by default
    hq_country      TEXT,
    industry        TEXT,
    vertical        TEXT,                        -- fintech / travel / payments / saas / treasury / crypto / industrial / other
    employee_band   TEXT,                        -- 1-50 / 51-200 / 201-1000 / 1001-5000 / 5000+
    fx_exposure_hint TEXT,                       -- high | medium | low | unknown
    why_grain_fit   TEXT,                        -- short LLM-written rationale (for discovered prospects)
    source_kind     TEXT NOT NULL DEFAULT 'backfilled', -- backfilled | discovered | manual
    source_url      TEXT,
    account_tier    TEXT,                        -- A | B | C
    icp_score       REAL,
    icp_breakdown_json TEXT,
    is_prospect     INTEGER NOT NULL DEFAULT 0,  -- 1 = discovered but not yet engaged
    approved        INTEGER NOT NULL DEFAULT 1,  -- 0 for pending discovery review
    name_variants_json TEXT,                     -- ["Maersk","AP Moller Maersk"]
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_companies_norm ON companies(name_normalized);
CREATE INDEX IF NOT EXISTS idx_companies_tier ON companies(account_tier);
CREATE INDEX IF NOT EXISTS idx_companies_score ON companies(icp_score DESC);
CREATE INDEX IF NOT EXISTS idx_companies_prospect ON companies(is_prospect, approved);

CREATE TABLE IF NOT EXISTS coverage (
    id              TEXT PRIMARY KEY,
    conference_id   TEXT NOT NULL,
    rep_id          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(conference_id, rep_id),
    FOREIGN KEY (conference_id) REFERENCES conferences(id),
    FOREIGN KEY (rep_id) REFERENCES reps(id)
);
CREATE INDEX IF NOT EXISTS idx_coverage_conf ON coverage(conference_id);
CREATE INDEX IF NOT EXISTS idx_coverage_rep ON coverage(rep_id);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_INITED = False


# Idempotent column additions for existing DBs (new schema versions).
# Each tuple is (table, column, sql_type_and_default).
_INCREMENTAL_COLUMNS = [
    ("reps", "telegram_link_token_event_id", "TEXT"),
    ("reps", "active_conference_id", "TEXT"),
    ("people", "company_id", "TEXT"),
    ("contacts", "company_id", "TEXT"),
    ("conferences", "agenda_summary", "TEXT"),
    ("conferences", "audience_composition_json", "TEXT"),
    ("conferences", "source_url", "TEXT"),
    ("people", "verified", "INTEGER DEFAULT 0"),
]


def _ensure_incremental_columns(conn) -> None:
    for table, col, type_ in _INCREMENTAL_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in existing:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {type_}")
            except sqlite3.OperationalError:
                pass


def init_db() -> None:
    """Idempotent. Called at app startup.

    Order matters: we ensure incremental columns exist BEFORE running the
    SCHEMA executescript, because SCHEMA contains indexes on those columns.
    """
    global _INITED
    conn = get_conn()
    try:
        # First — pick up any new columns added since the DB was first created.
        # Wrapped in try because a brand-new DB will have no tables yet.
        try:
            _ensure_incremental_columns(conn)
        except sqlite3.OperationalError:
            pass
        # Then — run the full schema (CREATE IF NOT EXISTS for everything).
        conn.executescript(SCHEMA)
        # Re-run incremental columns now that the tables definitely exist,
        # in case the first attempt failed.
        _ensure_incremental_columns(conn)
        _INITED = True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Settings helpers (used by sliders + scoring + nudge thresholds)
# ---------------------------------------------------------------------------
def get_setting(key: str) -> Optional[str]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_setting(key: str, value: Any) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, str(value), now_iso()),
        )
    finally:
        conn.close()


def get_settings_many(keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    conn = get_conn()
    try:
        qmarks = ",".join("?" * len(keys))
        rows = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({qmarks})", keys
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Feedback log (every AI decision auditable)
# ---------------------------------------------------------------------------
def log_feedback(
    *,
    decision_kind: str,
    target_kind: Optional[str] = None,
    target_id: Optional[str] = None,
    before: Any = None,
    after: Any = None,
    reason: Optional[str] = None,
    decided_by: Optional[str] = None,
) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO feedback (decision_kind, target_kind, target_id, "
            "before_value, after_value, reason, decided_by, decided_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                decision_kind, target_kind, target_id,
                json.dumps(before, ensure_ascii=False) if before is not None else None,
                json.dumps(after, ensure_ascii=False) if after is not None else None,
                reason, decided_by, now_iso(),
            ),
        )
        return int(cur.lastrowid)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Generic row helpers
# ---------------------------------------------------------------------------
def insert_row(table: str, row: dict) -> str:
    """INSERT a dict into a table. Adds id if missing. Returns id."""
    if "id" not in row:
        row = {**row, "id": uuid.uuid4().hex}
    if table in ("conferences", "contacts", "reps", "companies") and "created_at" not in row:
        row["created_at"] = now_iso()
    if table in ("conferences", "contacts", "companies") and "updated_at" not in row:
        row["updated_at"] = now_iso()
    cols = ",".join(row.keys())
    ph = ",".join("?" * len(row))
    conn = get_conn()
    try:
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({ph})", tuple(row.values()))
        return row["id"]
    finally:
        conn.close()


def counts() -> dict[str, int]:
    """Row counts per table — used by /healthz."""
    out: dict[str, int] = {}
    conn = get_conn()
    try:
        for t in ("conferences", "people", "contacts", "encounters",
                  "briefs", "feedback", "reps"):
            try:
                out[t] = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            except sqlite3.OperationalError:
                out[t] = -1
    finally:
        conn.close()
    return out
