"""ICP — Grain's Ideal Customer Profile, as configuration.

ICP isn't a thing salespeople should memorize. It's a thing they should TUNE.
This module exposes the entire profile as data: company verticals, target
titles, buying-personas with role patterns, and competitors. The scoring,
people-discovery, and brief modules ALL read from this. Edit the ICP, and the
whole tool re-prioritizes — no code changes needed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Default ICP (Grain Finance)
# ---------------------------------------------------------------------------
DEFAULT_ICP = {
    "version": "2026-05",
    "company_level": {
        "verticals": [
            "fintech_other", "payments", "psp", "cross_border_payments",
            "travel", "booking", "marketplace", "treasury",
            "crypto", "supply_chain",
        ],
        "company_size_range_employees": [100, 5000],
        "fx_exposure_signals": [
            "cross-border", "multi-currency", "international expansion",
            "FX hedging", "treasury", "settlement", "payouts in",
            "foreign exchange",
        ],
    },
    "person_level": {
        "target_titles": [
            "CFO", "Chief Financial Officer",
            "Treasurer", "VP Treasury", "Head of Treasury",
            "VP Finance", "Head of Finance", "Director of Finance",
            "Head of Payments", "VP Payments",
            "Head of FX", "Head of Foreign Exchange",
            "Head of Treasury Operations",
            "Director Treasury", "Group Treasurer",
        ],
        "champion_titles": [
            "CPO", "Chief Product Officer", "VP Product",
            "Head of Product", "Head of Platform",
        ],
    },
    "competitors": [
        "Currencycloud", "Wise Business", "Convera", "OFX", "Ebury",
        "AirWallex", "Kantox", "GPS Capital Markets", "Argentex",
    ],
    "anchor_events_known_attended": [
        "Money20/20 USA", "Money20/20 Europe", "Phocuswright",
        "EuroFinance International Treasury Management", "AFP Annual Conference",
        "Fintech Meetup", "iFX EXPO International",
    ],
    # The buying-committee model. Each persona gets a weight that the
    # scoring layer composes into per-company influence.
    "personas": {
        "BUYER": {
            "weight": 1.0,
            "title_patterns": [
                "cfo", "chief financial officer",
                "treasurer", "group treasurer",
                "vp treasury", "vice president treasury", "head of treasury",
                "vp finance", "head of finance", "director of finance",
                "head of fx", "head of foreign exchange",
                "head of payments", "vp payments",
                "head of settlement",
            ],
        },
        "CHAMPION": {
            "weight": 0.75,
            "title_patterns": [
                "cpo", "chief product officer", "vp product", "head of product",
                "head of platform", "head of fintech", "chief commercial officer",
            ],
        },
        "PAIN_OWNER": {
            "weight": 0.70,
            "title_patterns": [
                "head of international", "head of latam", "head of emea",
                "head of apac", "head of cross-border",
                "regional president", "regional vice president",
                "managing director",
            ],
        },
        "GATEKEEPER": {
            "weight": 0.60,
            "title_patterns": [
                "cto", "chief technology officer", "cio",
                "vp engineering", "head of engineering",
                "head of risk", "chief risk", "chief compliance",
            ],
        },
        "ENTRY_POINT": {
            # The sales/BD person at a target company who attends conferences
            # and opens doors to the BUYER. They don't sign the deal but
            # they're who the Grain rep meets first.
            "weight": 0.65,
            "title_patterns": [
                "vp sales", "vp of sales", "head of sales",
                "cro", "chief revenue officer",
                "head of partnerships", "vp partnerships",
                "head of business development", "vp business development",
                "head of strategic partnerships",
                "head of fintech partnerships",
                "global accounts", "strategic accounts",
                "chief business officer", "cbo",
            ],
        },
        "INFLUENCER": {
            # CEOs / founders / board — they influence but rarely staff booths.
            "weight": 0.40,
            "title_patterns": [
                "ceo", "chief executive officer", "founder", "co-founder",
                "president", "chairman",
                "managing partner", "general partner",
                "advisor", "board member",
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# Typed wrapper
# ---------------------------------------------------------------------------
@dataclass
class IcpConfig:
    version: str
    company_level: dict
    person_level: dict
    competitors: list[str]
    anchor_events_known_attended: list[str]
    personas: dict

    @classmethod
    def default(cls) -> "IcpConfig":
        d = DEFAULT_ICP
        return cls(
            version=d["version"],
            company_level=d["company_level"],
            person_level=d["person_level"],
            competitors=d["competitors"],
            anchor_events_known_attended=d["anchor_events_known_attended"],
            personas=d["personas"],
        )

    def classify_persona(self, title: Optional[str]) -> tuple[Optional[str], float, str]:
        """Title → (persona_kind, persona_weight, matched_pattern).

        Returns (None, 0, 'missing_title') if no title.
        Returns the HIGHEST-WEIGHT matching persona (so BUYER beats ENTRY_POINT
        on "CFO of Sales" type oddities — though that's not realistic).
        """
        if not title:
            return None, 0.0, "missing_title"
        t = title.lower()
        # Normalize abbreviations
        t = t.replace("sr.", "senior").replace("vice-president", "vice president")
        t = t.replace("vp.", "vp").replace("dir.", "director")
        ordered = sorted(
            self.personas.items(),
            key=lambda kv: -kv[1].get("weight", 0),
        )
        for kind, spec in ordered:
            for pat in spec.get("title_patterns", []):
                if pat.lower() in t:
                    return kind, float(spec.get("weight", 0.0)), pat
        return None, 0.0, "no_persona_match"
