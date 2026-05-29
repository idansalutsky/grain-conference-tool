"""Seed a deterministic cross-conference demo narrative.

The base seed (`seed_db.py`) loads conferences + people. This script builds the
*relationship* layer the demo hinges on: a handful of contacts whose encounter
histories exercise every branch of the cross-conference intelligence, WITHOUT
any LLM call — so the demo is reproducible, credit-free, and identical every run.

It does this the honest way: it inserts raw encounters and runs them through the
REAL pipeline — `entity_resolution.resolve_and_attach` (fuzzy match / auto-merge /
review), then `arc.classify(use_llm=False)` (deterministic verdict), then
`nudge.evaluate`. Nothing is hand-stamped; the verdicts are what the engine
actually produces. That's the point — in the video you can say "I didn't write
these labels, the resolver and the classifier did."

What it produces (every arc state + every nudge branch + the hard edge cases):

  Sarah Cohen   — WARMING, nudge FIRES.  3 events, rising sentiment, explicit
                  pain, never asked for a meeting yet → the one to close.
                  Captured as "Sarah Cohen" then "Sara Cohen" (nickname/spelling
                  variant) → resolver merges them.
  Michael Schmidt — WARMING, nudge FIRES. Captured "Michael Schmidt" then
                  "Mike Schmidt" at different events (same company) → merge via
                  nickname canonicalisation.
  Daniel Roth   — TIRE_KICKER, nudge SILENT (by design). 4 events over a year,
                  flat/lukewarm, never a meeting → "listening, never buying."
  Lena Novak    — WARMING + JOB-CHANGE bypass, nudge FIRES. Email+company change
                  (Acme Pay → Trip.com), but same LinkedIn handle → merge via
                  LinkedIn; promoted into an ICP buyer role (Head of Treasury).
  Tom Becker    — COOLING, nudge SILENT. Took a meeting early, then went quiet.
  Maria Garcia  — REVIEW-NEEDED. Two real people, same name + company, different
                  emails → the resolver refuses to auto-merge and queues it for
                  a human. (Name-collision protection.)

Run after the base seed:
    python -m backend.seed_demo
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from grain import arc, db, entity_resolution, nudge  # noqa: E402


# ---------------------------------------------------------------------------
# Conference id resolution — match real seeded events by name (robust to id drift)
# ---------------------------------------------------------------------------
def _conf_id(like: str) -> str | None:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM conferences WHERE name LIKE ? ORDER BY start_date LIMIT 1",
            (f"%{like}%",),
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _ts(date_str: str) -> str:
    """YYYY-MM-DD → ISO timestamp at noon UTC (deterministic)."""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=12, tzinfo=timezone.utc
    ).isoformat()


# ---------------------------------------------------------------------------
# One encounter row → persisted → resolved to a contact (real pipeline)
# ---------------------------------------------------------------------------
def _capture(
    *, name, company, title, vertical, discussed, signals, sentiment,
    meeting, when, conf_like, email=None, linkedin=None, rep_id="rep-na-01",
) -> dict:
    structured = {
        "name": name, "company": company, "title": title, "vertical": vertical,
        "what_discussed": discussed, "soft_signals": signals,
        "sentiment": sentiment, "meeting_requested": meeting,
        "email": email, "linkedin": linkedin, "transcript": discussed,
    }
    enc_id = "enc_demo_" + uuid.uuid4().hex[:12]
    conf_id = _conf_id(conf_like)
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO encounters (id, contact_id, conference_id, rep_id, "
            "captured_at, capture_mode, raw_input, structured_json, "
            "soft_signals_json, sentiment, meeting_requested) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (enc_id, None, conf_id, rep_id, _ts(when), "voice", discussed,
             json.dumps(structured, ensure_ascii=False),
             json.dumps(signals, ensure_ascii=False),
             sentiment, 1 if meeting else 0),
        )
    finally:
        conn.close()
    # Run the REAL resolver — it decides new / merge / review.
    resolution = entity_resolution.resolve_and_attach(enc_id)
    return resolution


def _finalize(contact_id: str) -> None:
    """Run the real arc classifier (deterministic) + nudge gate, persist."""
    if not contact_id:
        return
    arc.classify(contact_id, use_llm=False)
    nudge.evaluate(contact_id)


# ---------------------------------------------------------------------------
# Clear any prior relationship data so the demo is identical every run
# ---------------------------------------------------------------------------
def _reset() -> None:
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM encounters")
        conn.execute("DELETE FROM contacts")
        conn.execute("DELETE FROM briefs")
        # drop only the resolver/arc/nudge audit rows from prior test captures
        conn.execute(
            "DELETE FROM feedback WHERE decision_kind IN "
            "('entity_resolution','arc_classify','nudge_evaluate')"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The narrative
# ---------------------------------------------------------------------------
EURO = "EuroFinance"           # EuroFinance Intl Treasury (Barcelona)
M2020_USA = "Money20/20 USA"   # Las Vegas
M2020_EU = "Money20/20 Europe" # Amsterdam
PLS = "Payments Leaders"       # Payments Leaders Summit (London) — most recent


def build() -> None:
    _reset()

    # 1) Sarah Cohen — WARMING, nudge fires. Name/spelling variant across events.
    r = _capture(name="Sarah Cohen", company="Booking.com", title="VP Treasury",
                 vertical="travel", when="2025-10-01", conf_like=EURO,
                 discussed="Big FX pain on hotel payouts across EUR/GBP/USD; "
                 "curious how embedded hedging would work for their platform.",
                 signals=["explicit_pain", "strong_fit_signal"], sentiment=4,
                 meeting=False, linkedin="linkedin.com/in/sarahcohen")
    sarah_id = r.get("contact_id")
    _capture(name="Sara Cohen", company="Booking.com", title="VP Treasury",
             vertical="travel", when="2025-10-26", conf_like=M2020_USA,
             discussed="Followed up — asked about pricing and how rates are locked.",
             signals=["asked_about_pricing"], sentiment=4, meeting=False,
             linkedin="linkedin.com/in/sarahcohen")
    _capture(name="Sarah Cohen", company="Booking.com", title="VP Treasury",
             vertical="travel", when="2026-04-21", conf_like=PLS,
             discussed="Said the FX exposure problem is now a board-level topic; "
             "wants to understand integration effort.",
             signals=["explicit_pain", "time_sensitive"], sentiment=5,
             meeting=False, linkedin="linkedin.com/in/sarahcohen")
    _finalize(sarah_id)

    # 2) Michael Schmidt — WARMING, nudge fires. Nickname merge (Mike↔Michael).
    #    Three events so the arc has enough history to call a trajectory.
    r = _capture(name="Michael Schmidt", company="Adyen", title="Head of Payments",
                 vertical="payments", when="2025-06-03", conf_like=M2020_EU,
                 discussed="Cross-border settlement volume growing fast; "
                 "interested in risk-adjusted pricing.",
                 signals=["strong_fit_signal"], sentiment=4, meeting=False,
                 email="m.schmidt@adyen.com")
    mike_id = r.get("contact_id")
    _capture(name="Mike Schmidt", company="Adyen", title="Head of Payments",
             vertical="payments", when="2025-10-26", conf_like=M2020_USA,
             discussed="Re-connected; pain on multi-currency merchant payouts.",
             signals=["explicit_pain"], sentiment=4, meeting=False,
             email="m.schmidt@adyen.com")
    _capture(name="Mike Schmidt", company="Adyen", title="Head of Payments",
             vertical="payments", when="2026-04-21", conf_like=PLS,
             discussed="Explicit pain on EUR/USD payout spreads; wants a deeper "
             "technical conversation on integration.",
             signals=["explicit_pain", "time_sensitive"], sentiment=5,
             meeting=False, email="m.schmidt@adyen.com")
    _finalize(mike_id)

    # 3) Daniel Roth — TIRE_KICKER, nudge stays silent (the hard one).
    r = _capture(name="Daniel Roth", company="GlobeTrip", title="CFO",
                 vertical="travel", when="2025-06-03", conf_like=M2020_EU,
                 discussed="Polite chat about the category; no specific pain named.",
                 signals=["lukewarm"], sentiment=3, meeting=False,
                 email="daniel.roth@globetrip.com")
    daniel_id = r.get("contact_id")
    for when, conf, disc in [
        ("2025-10-01", EURO, "Said hi again; 'still just keeping an eye on the space'."),
        ("2025-10-26", M2020_USA, "Stopped by the booth; non-committal, comparing options."),
        ("2026-04-21", PLS, "Another friendly chat; deflected when timeline came up."),
    ]:
        _capture(name="Daniel Roth", company="GlobeTrip", title="CFO",
                 vertical="travel", when=when, conf_like=conf, discussed=disc,
                 signals=["lukewarm"], sentiment=3 if when != "2025-10-26" else 2,
                 meeting=False, email="daniel.roth@globetrip.com")
    _finalize(daniel_id)

    # 4) Lena Novak — WARMING + JOB-CHANGE bypass. Merge via LinkedIn (email+company changed).
    r = _capture(name="Lena Novak", company="Acme Pay", title="Finance Manager",
                 vertical="payments", when="2025-06-03", conf_like=M2020_EU,
                 discussed="Early-stage interest; FX handled manually today.",
                 signals=["strong_fit_signal"], sentiment=4, meeting=False,
                 email="lena.novak@acmepay.com", linkedin="linkedin.com/in/lenanovak")
    lena_id = r.get("contact_id")
    _capture(name="Lena Novak", company="Acme Pay", title="Finance Manager",
             vertical="payments", when="2025-10-01", conf_like=EURO,
             discussed="Manual hedging is getting painful as volume grows.",
             signals=["explicit_pain"], sentiment=4, meeting=False,
             email="lena.novak@acmepay.com", linkedin="linkedin.com/in/lenanovak")
    _capture(name="Lena Novak", company="Trip.com", title="Head of Treasury",
             vertical="travel", when="2026-04-21", conf_like=PLS,
             discussed="Now Head of Treasury at Trip.com — owns the FX mandate. "
             "Strong fit, wants to evaluate.",
             signals=["explicit_pain", "strong_fit_signal"], sentiment=5,
             meeting=False, linkedin="linkedin.com/in/lenanovak",
             email="lena.novak@trip.com")
    _finalize(lena_id)

    # 5) Tom Becker — COOLING, nudge silent. Took a meeting early, went quiet.
    r = _capture(name="Tom Becker", company="Trivago", title="VP Finance",
                 vertical="travel", when="2025-06-03", conf_like=M2020_EU,
                 discussed="Hot lead — asked for a follow-up meeting on the spot.",
                 signals=["wants_meeting", "strong_fit_signal"], sentiment=5,
                 meeting=True, email="tom.becker@trivago.com")
    tom_id = r.get("contact_id")
    _capture(name="Tom Becker", company="Trivago", title="VP Finance",
             vertical="travel", when="2025-10-01", conf_like=EURO,
             discussed="Still friendly but said priorities shifted internally.",
             signals=[], sentiment=4, meeting=False, email="tom.becker@trivago.com")
    _capture(name="Tom Becker", company="Trivago", title="VP Finance",
             vertical="travel", when="2026-04-21", conf_like=PLS,
             discussed="Brief, cool exchange; 'not this year'.",
             signals=["lukewarm"], sentiment=2, meeting=False,
             email="tom.becker@trivago.com")
    _finalize(tom_id)

    # 6) Maria Garcia ×2 — REVIEW-NEEDED. Two real people, same name+company,
    #    different emails → resolver refuses to auto-merge, queues for a human.
    _capture(name="Maria Garcia", company="Stripe", title="Treasury Analyst",
             vertical="payments", when="2025-10-26", conf_like=M2020_USA,
             discussed="Junior treasury contact; gathering info.",
             signals=[], sentiment=3, meeting=False, email="maria.garcia@stripe.com")
    _capture(name="Maria Garcia", company="Stripe", title="Payments Ops Lead",
             vertical="payments", when="2026-04-21", conf_like=PLS,
             discussed="Different Maria at Stripe — payments ops, not treasury.",
             signals=[], sentiment=3, meeting=False, email="m.garcia@stripe.com")

    # The Grain Brain sits ON TOP of these real captures: fold the genuine
    # contacts/encounters (with the arc verdicts the engine just produced) into
    # the brain's relationship space. seed_db ran the brain seed BEFORE any
    # contacts existed, so we (re)sync the relationship space here now that the
    # real demo contacts are in place. Idempotent.
    from grain.brain.spaces import sync_relationship_space_from_db  # local import
    sync = sync_relationship_space_from_db()
    print(f"Brain relationship sync: {sync}")


def report() -> None:
    conn = db.get_conn()
    try:
        print("\n=== DEMO CONTACTS (engine-produced verdicts) ===")
        for r in conn.execute(
            "SELECT primary_name, primary_company, arc_verdict, arc_confidence, "
            "nudge_active, (SELECT COUNT(*) FROM encounters e WHERE e.contact_id=c.id) n "
            "FROM contacts c ORDER BY nudge_active DESC, arc_verdict"
        ).fetchall():
            nm = (r["primary_name"] or "?").encode("ascii", "replace").decode()
            flag = "🔔 NUDGE" if r["nudge_active"] else "   silent"
            print(f"  {flag}  {str(r['arc_verdict']):<11} {r['arc_confidence']}  "
                  f"{nm} @ {r['primary_company']} ({r['n']} enc)")
        orphans = conn.execute(
            "SELECT COUNT(*) FROM encounters WHERE contact_id IS NULL"
        ).fetchone()[0]
        print(f"  review-queue (unattached encounters): {orphans}")
    finally:
        conn.close()


def main(force: bool = False) -> int:
    db.init_db()
    # Idempotent: don't clobber real captures on every container restart.
    # Seed only when there are no contacts yet (or when explicitly forced).
    conn = db.get_conn()
    try:
        existing = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    finally:
        conn.close()
    force = force or "--force" in sys.argv
    if existing and not force:
        print(f"Demo already seeded ({existing} contacts) — skipping. "
              "Re-run with --force to rebuild.")
        return 0
    build()
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
