"""Entity resolution — name variants, transliteration, rebrand, collisions."""
from __future__ import annotations

from grain.entity_resolution import (
    _name_similarity, _company_similarity, _factor_breakdown,
    _score_factors, resolve_encounter,
)


def test_sara_matches_sarah():
    assert _name_similarity("Sara Cohen", "Sarah Cohen") >= 0.9


def test_mike_matches_michael():
    assert _name_similarity("Mike Patel", "Michael Patel") >= 0.9


def test_yossi_matches_joseph():
    assert _name_similarity("Yossi Klein", "Joseph Klein") >= 0.9


def test_jose_matches_jose_unicode():
    assert _name_similarity("José Garcia", "Jose Garcia") >= 0.99


def test_muller_matches_mueller():
    assert _name_similarity("Müller", "Mueller") >= 0.9


def test_currencycloud_matches_visa_cross_border():
    assert _company_similarity("Currencycloud", "Visa Cross Border Solutions") == 1.0


def test_booking_holdings_matches_booking():
    assert _company_similarity("Booking Holdings", "Booking.com") == 1.0


def test_job_change_same_email_auto_merges():
    enc = {"name": "Maria Garcia", "company": "Acme", "email": "m@example.com"}
    contact = {"primary_name": "Maria Garcia", "primary_company": "OldCo",
               "primary_email": "m@example.com", "linkedin_handle": None}
    f = _factor_breakdown(enc, contact)
    conf = _score_factors(f)
    assert conf >= 0.85, conf  # email + name → auto


def test_name_collision_different_emails_review_only():
    """Two real Maria Garcia at Booking.com → never auto-merge."""
    enc = {"name": "Maria Garcia", "company": "Booking", "email": "maria@booking.com"}
    contact = {"primary_name": "Maria Garcia", "primary_company": "Booking",
               "primary_email": "maria.g@booking.com", "linkedin_handle": None}
    f = _factor_breakdown(enc, contact)
    conf = _score_factors(f, both_emails_present=True)
    assert conf < 0.85, conf  # NOT auto_merge
    assert conf >= 0.65, conf  # but surfaces for review


def test_nickname_plus_job_change_reaches_review_not_reject():
    """Bill Turner @ Stripe → William Turner @ Adyen (nickname + job change):
    must REACH review so the cross-conference arc survives, never split silently."""
    out = resolve_encounter(
        {"name": "Bill Turner", "company": "Stripe"},
        candidates=[{"id": "c1", "primary_name": "William Turner",
                     "primary_company": "Adyen", "primary_email": None,
                     "linkedin_handle": None}],
    )
    assert out is not None, "nickname+job-change was rejected → arc broken"
    assert out.decision_hint == "review_needed", out.decision_hint
    assert 0.65 <= out.confidence < 0.85, out.confidence


def test_single_first_name_same_company_does_not_auto_merge():
    """A bare first name ('John') + same company must NOT silently auto-merge
    into 'John Smith' — there could be several Johns at Revolut."""
    f = _factor_breakdown(
        {"name": "John", "company": "Revolut"},
        {"primary_name": "John Smith", "primary_company": "Revolut",
         "primary_email": None, "linkedin_handle": None},
    )
    conf = _score_factors(f)
    assert conf < 0.85, conf          # not auto
    assert conf >= 0.65, conf          # but surfaced for review


def test_single_first_name_with_decisive_email_still_auto_merges():
    """The single-token guard must NOT block a decisive key: 'John' with a
    matching email is still provably the same person → auto."""
    f = _factor_breakdown(
        {"name": "John", "company": "Revolut", "email": "j@revolut.com"},
        {"primary_name": "John Smith", "primary_company": "Revolut",
         "primary_email": "j@revolut.com", "linkedin_handle": None},
    )
    assert _score_factors(f) >= 0.85


def test_resolve_empty_pool_returns_none():
    out = resolve_encounter({"name": "X"}, candidates=[])
    assert out is None


def test_resolve_matches_when_pool_has_candidate():
    pool = [{"id": "c1", "primary_name": "Sarah Cohen",
             "primary_company": "Booking", "primary_email": None,
             "linkedin_handle": None}]
    out = resolve_encounter({"name": "Sara Cohen", "company": "Booking"},
                            candidates=pool)
    assert out is not None
    assert out.contact_id == "c1"
    assert out.confidence >= 0.85
    assert out.decision_hint == "auto_merge"
