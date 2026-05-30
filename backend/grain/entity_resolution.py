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
    # Common nicknames that diverge sharply from the legal first name — without
    # these, a "nickname + job change" (e.g. Bill Turner @ Stripe → William
    # Turner @ Adyen) folds to a low name score and is wrongly REJECTED, silently
    # splitting one warming relationship into two contacts and breaking the arc.
    "bill": "william", "will": "william", "willy": "william", "william": "william",
    "rick": "richard", "rich": "richard", "dick": "richard", "richard": "richard",
    "tony": "anthony", "anthony": "anthony",
    "dave": "david", "david": "david",
    "ed": "edward", "eddie": "edward", "ted": "edward", "edward": "edward",
    "greg": "gregory", "gregory": "gregory",
    "ron": "ronald", "ronald": "ronald",
    "don": "donald", "donald": "donald",
    "pat": "patrick", "patrick": "patrick",
    "sam": "samuel", "sammy": "samuel", "samuel": "samuel",
    "jen": "jennifer", "jenny": "jennifer", "jennifer": "jennifer",
    "sue": "susan", "susie": "susan", "susan": "susan",
    "kim": "kimberly", "kimberly": "kimberly",
    "becky": "rebecca", "rebecca": "rebecca",
    "meg": "margaret", "maggie": "margaret", "peggy": "margaret", "margaret": "margaret",
    "fran": "frances", "frances": "frances", "francis": "francis",
    "charlie": "charles", "chuck": "charles", "charles": "charles",
    "ray": "raymond", "raymond": "raymond",
    "gabe": "gabriel", "gabriel": "gabriel",
    "nate": "nathan", "nathan": "nathan",
    "vinny": "vincent", "vince": "vincent", "vincent": "vincent",
}


def _fold(s: str) -> str:
    """NFKD-fold to ASCII-lower, strip punctuation. Handles José → jose,
    Müller → muller, Søren → soren."""
    if not s:
        return ""
    # German umlauts transliterate to digraphs (ü→ue, ö→oe, ä→ae), NOT to bare
    # vowels — do this BEFORE NFKD strips the combining diaeresis, so
    # "Müller" → "mueller" (matching the explicit "Mueller" spelling).
    s = (s.replace("ü", "ue").replace("Ü", "Ue")
          .replace("ö", "oe").replace("Ö", "Oe")
          .replace("ä", "ae").replace("Ä", "Ae")
          .replace("ß", "ss"))
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


def _name_similarity_strict(a: str, b: str) -> float:
    """High-precision name agreement: token_SORT (order- and completeness-
    sensitive) so a SUBSET/SUPERSET name ('Async Test Person' ⊃ 'Test Person')
    does NOT score 1.0 the way token_set does. Used as the gate for the
    cross-company "job change" branch, where a false positive would wrongly link
    two different people. Nicknames/transliteration still fold via _canon_first."""
    a_toks = _name_tokens(a)
    b_toks = _name_tokens(b)
    if not a_toks or not b_toks:
        return 0.0
    a_can = [_canon_first(a_toks[0])] + a_toks[1:]
    b_can = [_canon_first(b_toks[0])] + b_toks[1:]
    raw = fuzz.token_sort_ratio(" ".join(a_toks), " ".join(b_toks)) / 100.0
    can = fuzz.token_sort_ratio(" ".join(a_can), " ".join(b_can)) / 100.0
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


def _title_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Fuzzy title agreement, 0..1. Returns -1.0 when EITHER side is missing
    (unknown — not evidence of divergence; the collision guard treats unknown
    as 'no signal', never as 'diverging')."""
    na, nb = _fold(a or ""), _fold(b or "")
    if not na or not nb:
        return -1.0
    if na == nb:
        return 1.0
    return fuzz.token_set_ratio(na, nb) / 100.0


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
        "name_similarity_strict": _name_similarity_strict(
            enc.get("name") or "", contact_row.get("primary_name") or ""),
        "company_similarity": _company_similarity(enc.get("company"), contact_row.get("primary_company")),
        "title_similarity": _title_similarity(
            enc.get("title") or enc.get("role"), contact_row.get("primary_title")),
        # Fewest name tokens on either side: a bare first name ("John") has 1.
        # token_set_ratio scores "John" ⊆ "John Smith" as a perfect 1.0, so
        # without this guard a single first name + same company silently
        # auto-merges into whichever full name it happens to be a prefix of —
        # collapsing two different "John"s at the same employer.
        "min_name_tokens": min(
            len(_name_tokens(enc.get("name") or "")),
            len(_name_tokens(contact_row.get("primary_name") or "")),
        ),
    }


def _score_factors(f: dict, *, both_emails_present: bool = False) -> float:
    """Compose factors into 0..1 confidence with transparent rules.

    Identity model (the core of cross-conference tracking):
      - email / linkedin / phone+name are DECISIVE keys → auto-merge band.
        A decisive key means "provably the same person" even across a company
        change, so a job change with a matching email auto-merges.
      - name + company are SUGGESTIVE only → review band, never a silent
        auto-merge of two real people who happen to share a name + employer.

    Two opposing failure modes this balances:
      P0-2  same person, NEW company (job change), no decisive key →
            must still REACH review (≥ review threshold), not be rejected as a
            duplicate-creating "different person".
      P0-3  two DIFFERENT real people, same name + same company, no decisive
            key, diverging titles → must NOT auto-merge; cap into review.
    """
    email = f["email_match"]
    li = f["linkedin_match"]
    phone = f.get("phone_match", 0.0)
    name = f["name_similarity"]
    name_strict = f.get("name_similarity_strict", name)
    comp = f["company_similarity"]
    title = f.get("title_similarity", -1.0)  # -1.0 == unknown (no signal)

    # --- DECISIVE keys: provable identity, auto-merge even across a job change.
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

    # --- No decisive key beyond here. Detect a likely name COLLISION (two real
    # people): strong name + same company but the OTHER signals diverge. We never
    # let this auto-merge — cap it into the review band so a human decides.
    title_diverges = title >= 0.0 and title < 0.55      # known and clearly different
    emails_diverge = both_emails_present and email == 0.0
    collision_risk = (comp >= 0.95 and name >= 0.85
                      and (title_diverges or emails_diverge))
    if collision_risk:
        return 0.78  # review band, below auto-merge — route to human

    # --- Same person, NEW company (job change) with no decisive key. A perfect
    # name match across conferences with a different employer is most likely a
    # job change, so it must reach REVIEW (not be rejected into a duplicate).
    # Gate on the STRICT name match: a subset/superset name ("Async Test Person"
    # ⊃ "Test Person") is NOT a confident same-person and stays out of this band.
    if name >= 0.85 and name_strict >= 0.9 and comp < 0.5:
        # Strong title agreement (e.g. "CFO" → "CFO") nudges it up; an unknown
        # title leaves it mid-review. Range ~0.66..0.80.
        base = 0.66 + 0.2 * (name - 0.85) / 0.15        # 0.66..0.80 over name
        if title >= 0.85:
            base = max(base, 0.80)
        return min(0.80, base)

    # --- Single first name (no surname) on either side is too thin to AUTO-merge
    # on name+company alone: "John" @ Revolut could be any of several Johns. With
    # no decisive key (handled above), cap a bare-first-name match into review so
    # a human confirms rather than silently collapsing two people.
    single_token_name = f.get("min_name_tokens", 2) < 2
    if single_token_name and email != 1.0 and li != 1.0 and phone != 1.0:
        if comp >= 0.95 and name >= 0.85:
            return 0.78  # review band, not auto-merge
        # otherwise fall through to the gentler bands below

    # --- Same company, varying name strength (no divergence detected).
    if comp >= 0.95 and name >= 0.85:
        return 0.86 + 0.1 * (name - 0.85)  # 0.86..0.91 → auto-merge
    if comp >= 0.95 and name >= 0.7:
        return 0.7 + 0.5 * (name - 0.7)    # 0.7..0.78 → review
    if comp >= 0.95 and name >= 0.55:
        return 0.55 + 0.4 * (name - 0.55)  # 0.55..0.66
    # Moderate name match, different/unknown company → review at best.
    if name >= 0.9 and comp < 0.5:
        return 0.4 + 0.2 * (name - 0.9)
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
