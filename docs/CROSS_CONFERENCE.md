# Cross-conference intelligence

The brief was specific:

> When the same person is encountered at multiple conferences, the tool should
> recognize it and surface the pattern. The goal: help the rep judge whether a
> repeat contact is a warming relationship worth closing, or a polite
> tire-kicker who's been listening for a year and never buying.

That's three jobs:

1. **Recognize** — entity resolution
2. **Interpret** — arc classifier
3. **Act** — calibrated nudge

This doc walks each one.

## 1. Recognize — entity resolution (`backend/grain/entity_resolution.py`)

A new encounter arrives. We compute 4 factors against every existing contact:

| Factor | What it sees | Example |
|---|---|---|
| `email_match` | exact match (case-folded) | `s@booking.com` ↔ `S@Booking.com` = 1.0 |
| `linkedin_match` | exact (slugged) | `/in/sarah-cohen` ↔ `/in/Sarah-Cohen` = 1.0 |
| `name_similarity` | nicknames + Unicode-fold + token_set_ratio | "Sara Cohen" ↔ "Sarah Cohen" = 0.95 |
| `company_similarity` | normalize + known-rebrand map + fuzzy | "Visa Cross Border Solutions" ↔ "Currencycloud" = 1.0 |

The composite confidence is computed by transparent rules, NOT by an opaque model:

```
email == 1.0 AND name ≥ 0.6   → 1.0   (job change, same email)
linkedin == 1.0                → 0.95  (same person, no email)
company == 1.0 AND name ≥ 0.85 → ~0.86-0.91 (likely same person at same co)
company == 1.0 AND name ≥ 0.70 → ~0.70-0.78 (review)
name ≥ 0.9 AND company < 0.5   → ~0.40-0.50 (same name, different co — review)
both emails set, both differ + same name+co → 0.75 (two real people, NEVER auto)
```

Three decision bands:
- `confidence ≥ auto_merge_threshold` (default 0.85) → **auto_merge**
- `confidence ≥ review_threshold` (default 0.65) → **review_needed** (HIL)
- otherwise → **reject** (create a new contact)

Both thresholds are sliders in Settings. Every decision is logged with the
factor breakdown so the merge UI can show "why".

### Edge cases handled

| Case | Approach |
|---|---|
| Name variants (Sara ↔ Sarah, Mike ↔ Michael, Yossi ↔ Joseph) | Canonical-first-name dictionary lookup before fuzzy |
| Unicode / accents (José ↔ Jose, Müller ↔ Mueller, Søren ↔ Soren) | NFKD-fold to ASCII + strip combining marks |
| Job change | email or linkedin match overrides company mismatch |
| Email gained mid-history | name+company match without email still merges if conf high enough |
| Company rebrand | `_COMPANY_REBRANDS` table maps known cases (Currencycloud ↔ Visa Cross Border Solutions, First Data ↔ Fiserv, Booking Holdings ↔ Booking.com) |
| Name collision (two real Maria Garcias at Booking) | `both_emails_present=True AND email_match=0.0` caps confidence at 0.75 — drops to review band, never auto-merges |

### Where it's tested

`tests/test_entity_resolution.py` — 10 tests covering every edge case above.

## 2. Interpret — arc classifier (`backend/grain/arc.py`)

Given an encounter history for one contact, decide:

- **warming** — clear progression, positive sentiment trend, ≥ 1 meeting requested
- **flat** — 1-2 encounters, no clear directional signal
- **cooling** — negative sentiment trend, was warmer earlier
- **tire_kicker** — 3+ encounters across a long window (>180 days) with no meeting + ≥ 2 lukewarm signals

**Deterministic-first design**:
1. Extract feature vector: `n_encounters`, `span_days`, `avg_sentiment`,
   `sentiment_trend`, `meeting_requests`, `pain_signals`, `lukewarm_signals`
2. Apply the deterministic rules above → first verdict + confidence
3. **Then** ask an LLM judge for a higher-fidelity opinion
4. If they agree → boost confidence
5. If they disagree → trust the deterministic call when its confidence is
   high; otherwise believe the LLM but cap the confidence

The LLM is the judge, not the oracle. We never ship a verdict the
deterministic classifier strongly contradicts.

### Why this design

Outsourcing the entire interpretation to an LLM would be opaque (we couldn't
defend "why warming?") and unreliable (LLM outputs drift with model upgrades).
Deterministic-first means the floor is always defensible; LLM-as-judge means
the ceiling can still be high.

### Where it's tested

`tests/test_arc.py` — 5 tests covering zero / one / tire_kicker / warming /
cooling.

## 3. Act — calibrated nudge (`backend/grain/nudge.py`)

A nudge fires only when:

```
arc == 'warming'
  AND arc_confidence ≥ 0.70 (slider)
  AND last_touch ≤ 90 days (slider)
  AND no meeting requested yet
  AND ≥ 2 encounters of history
```

Bypass: `arc == 'warming' AND job_change_to_ICP_role` → fires even if older.

**When the nudge does NOT fire we record WHY**. The `why_suppressed` array is
returned to the rep as part of the capture result and surfaced in the contact
detail UI. That's the "too subtle ≠ invisible" answer — the rep can see
exactly why the system stayed quiet:

> Silent — arc is 'flat', not 'warming'; only 1 encounter — need ≥ 2

That's audit-grade silence. The rep can argue with it (override the arc, or
tune the threshold).

### Where it's tested

`tests/test_nudge.py` — 4 tests covering the fires / suppressed paths.

## The chain end-to-end

```
Field capture (voice or text)
    ↓
Structured lead (Gemini multimodal)
    ↓
Persist encounter
    ↓
Entity resolution (4-factor composite confidence)
    ↓
Decision: auto_merge / review / new contact
    ↓
Arc classifier (deterministic + LLM judge)
    ↓
Nudge gate (4 hard rules + bypass)
    ↓
HubSpot push with grain_arc_verdict + grain_nudge_text custom props
```

A single voice memo on the floor walks all 7 steps, returns the result to the
rep in ~3-5 seconds, and lands intelligence-enriched in HubSpot.
