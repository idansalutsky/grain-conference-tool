"""Cross-conference entity resolution.

When a rep captures someone at a conference, this module decides:
  - is this a NEW contact?
  - or is it the same person we already have (auto_merge)?
  - or is it ambiguous (review_needed — surface to a human)?

Handles the hard cases:
  - Name variants (Sarah ↔ Sara, Mike ↔ Michael)
  - Latin transliteration (Yossi ↔ Joseph, Müller ↔ Mueller, José ↔ Jose)
  - Job changes (same email, new company)
  - Email gained mid-history (new contact for the same person)
  - Company rebrand (Currencycloud ↔ Visa Cross Border Solutions)
  - Name collisions (two real Maria Garcias at Booking.com) → never auto-merge

Every decision is logged with the factor breakdown so the UI can show "why".
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

from . import db


# ---------------------------------------------------------------------------
# Normalisation + nicknames
# ---------------------------------------------------------------------------
NICKNAMES = {
    "sara": "sarah", "sarah": "sarah",
    "mike": "michael", "michael": "michael", "mick": "michael",
    "bob": "robert", "rob": "robert", "robert": "robert",
    "liz": "elizabeth", "beth": "elizabeth", "elizabeth": "elizabeth",
    "tom": "thomas", "thomas": "thomas",
    "dan": "daniel", "danny": "daniel", "daniel": "daniel",
    "chris": "christopher", "christopher": "christopher",
    "andy": "andrew", "drew": "andrew", "andrew": "andrew",
    "alex": "alexander", "alexander": "alexander",
    "ben": "benjamin", "benjamin": "benjamin",
    "joe": "joseph", "yossi": "joseph", "joseph": "joseph",
    "jose": "joseph",
    "jim": "james", "james": "james",
    "matt": "matthew", "matthew": "matthew",
    "nick": "nicholas", "nicholas": "nicholas",
    "kate": "katherine", "kathy": "katherine", "katie": "katherine", "katherine": "katherine",
    "steve": "steven", "stephen": "steven", "steven": "steven",
}


def _fold(s: str) -> str:
    """NFKD-fold to ASCII-lower, strip punctuation. Handles José → jose,
    Müller → muller, Søren → soren."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_only = ascii_only.replace("ø", "o").replace("Ø", "o").replace("ß", "ss")
    return re.sub(r"[^a-z0-9 ]+", " ", ascii_only.lower()).strip()


def _name_tokens(full: str) -> list[str]:
    return [t for t in _fold(full).split() if t]


def _canon_first(first: str) -> str:
    f = _fold(first)
    return NICKNAMES.get(f, f)


def _name_similarity(a: str, b: str) -> float:
    a_toks = _name_tokens(a)
    b_toks = _name_tokens(b)
    if not a_toks or not b_toks:
        return 0.0
    def canon(toks):
        if not toks:
            return toks
        return [_canon_first(toks[0])] + toks[1:]
    a_can = canon(a_toks)
    b_can = canon(b_toks)
    raw = fuzz.token_set_ratio(" ".join(a_toks), " ".join(b_toks)) / 100.0
    can = fuzz.token_set_ratio(" ".join(a_can), " ".join(b_can)) / 100.0
    return max(raw, can)


_COMPANY_REBRANDS = {
    "currencycloud": "currencycloud",
    "visa cross border solutions": "currencycloud",
    "wise transferwise": "wise", "wise business": "wise", "wise": "wise",
    "first data": "fiserv", "fiserv": "fiserv",
    "worldpay": "worldpay", "fis worldpay": "worldpay",
    "booking holdings": "booking", "booking com": "booking", "booking": "booking",
    "expedia": "expedia", "expedia group": "expedia",
}


def _company_normalize(c: Optional[str]) -> str:
    if not c:
        return ""
    folded = _fold(c)
    folded = re.sub(
        r"\b(inc|incorporated|llc|ltd|limited|gmbh|sa|sas|spa|plc|group|"
        r"holdings|co|corp|corporation|technologies|tech)\b", "", folded,
    ).strip()
    folded = re.sub(r"\s+", " ", folded)
    return _COMPANY_REBRANDS.get(folded, folded)


def _company_similarity(a: Optional[str], b: Optional[str]) -> float:
    na, nb = _company_normalize(a), _company_normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return fuzz.token_set_ratio(na, nb) / 100.0


def _email_match(a: Optional[str], b: Optional[str]) -> float:
    if not a or not b:
        return 0.0
    return 1.0 if a.strip().lower() == b.strip().lower() else 0.0


def _linkedin_match(a: Optional[str], b: Optional[str]) -> float:
    if not a or not b:
        return 0.0
    return 1.0 if _fold(a).replace(" ", "-") == _fold(b).replace(" ", "-") else 0.0


def _phone_digits(p: Optional[str]) -> str:
    """Last 10 digits — tolerates +country codes, spaces, dashes, parens."""
    if not p:
        return ""
    digits = re.sub(r"\D+", "", p)
    return digits[-10:] if len(digits) >= 10 else digits


def _phone_match(a: Optional[str], b: Optional[str]) -> float:
    da, db_ = _phone_digits(a), _phone_digits(b)
    if not da or not db_ or len(da) < 7:
        return 0.0
    return 1.0 if da == db_ else 0.0


# ---------------------------------------------------------------------------
# Scoring + decision
# ---------------------------------------------------------------------------
@dataclass
class MatchCandidate:
    contact_id: str
    confidence: float
    factors: dict
    decision_hint: str  # auto_merge / review_needed / reject


def _factor_breakdown(enc: dict, contact_row: dict) -> dict:
    return {
        "email_match": _email_match(enc.get("email"), contact_row.get("primary_email")),
        "linkedin_match": _linkedin_match(enc.get("linkedin"), contact_row.get("linkedin_handle")),
        "phone_match": _phone_match(enc.get("phone"), contact_row.get("phone")),
        "name_similarity": _name_similarity(enc.get("name") or "", contact_row.get("primary_name") or ""),
        "company_similarity": _company_similarity(enc.get("company"), contact_row.get("primary_company")),
    }


def _score_factors(f: dict, *, both_emails_present: bool = False) -> float:
    """Compose factors into 0..1 confidence with transparent rules."""
    email = f["email_match"]
    li = f["linkedin_match"]
    phone = f.get("phone_match", 0.0)
    name = f["name_similarity"]
    comp = f["company_similarity"]

    if email == 1.0 and name >= 0.6:
        return 1.0
    if email == 1.0 and name < 0.6:
        # Email re-use across two real people (rare); cap.
        return 0.6 + 0.4 * name
    if li == 1.0:
        return 0.95
    # Phone is a strong identity key (a shared contact card), but a shared
    # office/switchboard number could collide — require a name nod too.
    if phone == 1.0 and name >= 0.5:
        return 0.93
    if phone == 1.0:
        return 0.75  # phone-only → review band, not auto-merge

    # Two real people, same name + same company + different emails →
    # cap at review_needed band.
    if both_emails_present and email == 0.0:
        if comp >= 0.95 and name >= 0.85:
            return 0.75

    if comp >= 0.95 and name >= 0.85:
        return 0.86 + 0.1 * (name - 0.85)  # 0.86..0.91
    if comp >= 0.95 and name >= 0.7:
        return 0.7 + 0.5 * (name - 0.7)    # 0.7..0.78
    if comp >= 0.95 and name >= 0.55:
        return 0.55 + 0.4 * (name - 0.55)  # 0.55..0.66
    if name >= 0.9 and comp < 0.5:
        return 0.4 + 0.2 * (name - 0.9)    # different company → review at best
    return min(0.6, 0.6 * name + 0.4 * comp)


def _decide(confidence: float, auto: float, review: float) -> str:
    if confidence >= auto:
        return "auto_merge"
    if confidence >= review:
        return "review_needed"
    return "reject"


def _live_thresholds() -> tuple[float, float]:
    auto = db.get_setting("entity_resolution.auto_merge_threshold")
    review = db.get_setting("entity_resolution.review_threshold")
    return (
        float(auto) if auto else 0.85,
        float(review) if review else 0.65,
    )


def _all_contacts() -> list[dict]:
    conn = db.get_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM contacts").fetchall()]
    finally:
        conn.close()


def resolve_encounter(
    encounter: dict,
    *,
    candidates: Optional[list[dict]] = None,
    auto_merge_threshold: Optional[float] = None,
    review_threshold: Optional[float] = None,
) -> Optional[MatchCandidate]:
    """Find the best matching canonical Contact for this encounter.

    Returns None when contact pool is empty. Returns a MatchCandidate with
    decision_hint='reject' when no candidate clears the review threshold —
    the caller will create a new contact in that case.
    """
    if auto_merge_threshold is None or review_threshold is None:
        am, rv = _live_thresholds()
        if auto_merge_threshold is None:
            auto_merge_threshold = am
        if review_threshold is None:
            review_threshold = rv

    pool = candidates if candidates is not None else _all_contacts()
    if not pool:
        return None

    best: Optional[MatchCandidate] = None
    enc_email = encounter.get("email")
    for c in pool:
        factors = _factor_breakdown(encounter, c)
        both_emails = bool(enc_email) and bool(c.get("primary_email"))
        conf = _score_factors(factors, both_emails_present=both_emails)
        if best is None or conf > best.confidence:
            best = MatchCandidate(
                contact_id=c["id"],
                confidence=round(conf, 4),
                factors={k: round(v, 4) if isinstance(v, float) else v
                         for k, v in factors.items()},
                decision_hint=_decide(conf, auto_merge_threshold, review_threshold),
            )
    return best


# ---------------------------------------------------------------------------
# DB-level operations
# ---------------------------------------------------------------------------
def create_contact_from_encounter(enc_struct: dict) -> str:
    cid = str(uuid.uuid4())
    name = enc_struct.get("name") or "Unknown"
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO contacts (id, primary_name, primary_email, primary_company, "
            "primary_title, linkedin_handle, phone, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, name, enc_struct.get("email"), enc_struct.get("company"),
             enc_struct.get("role") or enc_struct.get("title"),
             enc_struct.get("linkedin"), enc_struct.get("phone"),
             db.now_iso(), db.now_iso()),
        )
    finally:
        conn.close()
    return cid


def attach_encounter_to_contact(encounter_id: str, contact_id: str) -> None:
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE encounters SET contact_id = ? WHERE id = ?",
            (contact_id, encounter_id),
        )
    finally:
        conn.close()


def resolve_and_attach(encounter_id: str) -> dict:
    """Resolve one encounter to a contact (create if no match).

    Returns {decision, contact_id, candidate?} for audit.
    """
    conn = db.get_conn()
    try:
        enc = conn.execute(
            "SELECT id, structured_json FROM encounters WHERE id = ?",
            (encounter_id,),
        ).fetchone()
        if not enc:
            raise ValueError(f"encounter {encounter_id} not found")
        struct = json.loads(enc["structured_json"] or "{}")
    finally:
        conn.close()

    candidate = resolve_encounter(struct)
    if candidate is None or candidate.decision_hint == "reject":
        new_cid = create_contact_from_encounter(struct)
        attach_encounter_to_contact(encounter_id, new_cid)
        db.log_feedback(
            decision_kind="entity_resolution",
            target_kind="encounter",
            target_id=encounter_id,
            after={"contact_id": new_cid, "decision": "created_new",
                   "best_candidate": candidate.__dict__ if candidate else None},
            reason="no candidate above review threshold",
            decided_by="entity_resolver",
        )
        return {"decision": "created_new", "contact_id": new_cid}

    if candidate.decision_hint == "auto_merge":
        attach_encounter_to_contact(encounter_id, candidate.contact_id)
        db.log_feedback(
            decision_kind="entity_resolution",
            target_kind="encounter",
            target_id=encounter_id,
            after={"contact_id": candidate.contact_id, "decision": "auto_merged",
                   "confidence": candidate.confidence, "factors": candidate.factors},
            reason=f"confidence {candidate.confidence:.2f}",
            decided_by="entity_resolver",
        )
        return {"decision": "auto_merged", "contact_id": candidate.contact_id,
                "candidate": candidate.__dict__}

    # review_needed: leave encounter unattached; surface for human merge.
    db.log_feedback(
        decision_kind="entity_resolution",
        target_kind="encounter",
        target_id=encounter_id,
        after={"contact_id": candidate.contact_id, "decision": "review_needed",
               "confidence": candidate.confidence, "factors": candidate.factors},
        reason=f"confidence {candidate.confidence:.2f} in review band",
        decided_by="entity_resolver",
    )
    return {"decision": "review_needed", "contact_id": candidate.contact_id,
            "candidate": candidate.__dict__}
