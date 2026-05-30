# Scoring methodology — 7 factors

The brief says "rank conferences by ICP fit" and "defend it in your video".
Here's the defence.

## Why 7 factors, not 1

A single overall score is undemandable. A breakdown is.

The whole point of building this tool over a spreadsheet is that a salesperson
can look at *why* an event scores what it does — not just that it does. Each
factor surfaces in the UI with the raw 0..1 sub-score, the weight, the
contribution to the total, and a one-sentence evidence string. Sales can argue
with the model, not just the output.

## The 7 factors + default weights

| Factor | Weight | What it measures | Why it's here |
|---|---|---|---|
| `vertical_concentration` | 0.25 | Is the event's vertical in Grain's ICP? | The wedge — Grain sells into travel / booking / marketplaces (lead wedge), payments / PSP / cross-border (rails), and treasury (direct buyer). The strong-wedge verticals score 1.0; other in-ICP verticals 0.85; everything else falls back to an FX-theme scan. |
| `buyer_reachability` | 0.25 | Can a rep actually reach Grain's **buying committee** here? | Not raw CFO count — the **persona-weighted** share of the audience that maps to *any* reachable committee persona (finance/treasury = BUYER, weighted highest; commercial/sales = ENTRY_POINT; product/eng = CHAMPION). Prefers the **measured, scraped audience composition** where we have it (e.g. EuroFinance is finance-dense); else a name/theme fallback. |
| `fx_exposure_proxy` | 0.20 | Themes carrying FX-relevant signal | "Cross-border", "settlement", "multi-currency", "remittance" — the words a buyer who needs Grain uses. |
| `reachability` | 0.10 | Format + size — can a rep work the room? | An open expo / trade-show floor scores highest; a curated exec roundtable scores mid (small but high-quality); a webinar scores lowest (no floor). |
| `geo_cost_efficiency` | 0.10 | Region weighted by typical travel cost | A pricier NA/EU trip needs higher ROI than a cheaper APAC/LATAM one. |
| `icp_strategic_fit` | 0.10 | Strategic-wedge centrality blended with ICP-company density | How central the event's vertical is to Grain's GTM (travel/marketplace lead wedge > payments rails > treasury > generic), blended (70/30) with the measured non-"other" share of the audience. Every event gets a distinct value. |
| `historical_yield` | 0.00 (dormant) | Boost from prior meetings/deals | Off until a tenant has run ~6+ months and accumulated usage data. Once we have it, this becomes the dominant factor and the others become priors. |

Weights are sliders in the Settings UI. The six active factors sum to 1.0;
`historical_yield` is opt-in at 0.00.

> **Renamed in 2026-05.** Two earlier factors were structurally broken and have
> been replaced — the names `buyer_density` and `competitive_validation` no
> longer exist:
> - `buyer_density` → **`buyer_reachability`**. The old factor keyed almost
>   entirely on finance/treasury %, which is right for a treasury-pure event but
>   **buried Grain's stated lead wedge** (travel / marketplaces), where the door
>   opens through commercial / product people, not the CFO. The new factor scores
>   the whole reachable buying committee, finance weighted highest.
> - `competitive_validation` → **`icp_strategic_fit`**. The old factor scanned
>   event metadata for competitor company names that **never appear there** — it
>   fired on 0/195 events, a constant that differentiated nothing. The
>   replacement is a real, per-event differentiating signal.
> A tenant who had already tuned the old setting keys keeps that value under the
> new factor name (see `_RENAMED_WEIGHTS` in `scoring.py`).

### How `vertical_concentration` reads the event's vertical

The factor keys off the conference's `vertical` field. Every seeded event has an
explicit `vertical` (see `seed/PROVENANCE.md`), so this factor is grounded in a
curated label, not a guess. The strong-wedge verticals — **travel, booking,
marketplace, treasury, payments, psp, cross_border_payments** — score 1.0;
other in-ICP verticals score 0.85; everything else falls back to an FX-theme
scan. Classification is **name-first for the travel wedge**: a travel-industry
event whose agenda also mentions "payments" (Phocuswright, WiT, World Travel
Market) is scored as `travel`, not mis-tagged `payments`. Money20/20 stays
`payments`; EuroFinance / AFP / Sibos / EBAday stay `treasury`.

## Tiers

```
A  if total ≥ 78
B  if total ≥ 58
else C
```

Tiering is deliberately selective: **A** is the elite ~top tier (the genuinely
finance-dense rooms worth a booth), **C** is the clearly off-ICP tail
(crypto-retail, consumer fairs, generic SaaS). Travel/marketplace events land
as strong, visible **B** — honest, since a ~12%-finance travel expo shouldn't
outrank a ~70%-finance treasury event.

**Current live distribution (195 events): A = 33, B = 137, C = 25.**

## What this surfaces — examples from the seed data

| Conference | Tier | Score | Why |
|---|---|---|---|
| EuroFinance Intl Treasury Management | A | ~92 (top) | measured finance/treasury-dense committee + strong FX themes |
| AFP Annual Conference | A | ~87.5 | treasury-pure, measured high buyer density |
| Money20/20 USA | A | ~85.3 | vertical 1.00 + strong FX themes — but lower measured finance-density than the treasury-pure shows |

**The defining moment:** treasury-pure events (EuroFinance, AFP) out-rank the
giant Money20/20 *because* their measured finance/treasury committee is denser —
even though Money20/20 has many times the attendees. **Buyer density beats raw
size.** A 300-person, 70%-treasury room outranks a 45,000-person generic SaaS
event like Dreamforce, which lands in **C**. That's the heavy-FX-buyer wedge
beating headcount, grounded in real audience data, not a hunch.

## What's analyst-estimated vs measured

Be honest about provenance:

- **Measured:** the `vertical`, `format`, `region`, `estimated_attendance`, and
  theme fields are curated/scraped per event (see `seed/PROVENANCE.md`).
- **Analyst-estimated:** the **audience composition percentages**
  (finance/treasury %, commercial %, product/eng %, "other" %) are analyst
  estimates where a measured breakdown wasn't published. `buyer_reachability`
  and `icp_strategic_fit` prefer these numbers when present and **fall back to
  name/theme signals when they're absent** — so a missing composition degrades
  gracefully rather than guessing a precise number it doesn't have.

## What the model **deliberately doesn't** do

- It doesn't auto-pick which events to attend. It surfaces the score; humans decide.
- It doesn't penalise low scores — a tier-C event might still be worth attending for relationship reasons. The score is one input, not the answer.
- It doesn't try to predict pipeline directly — that requires Year-1 of usage data. Once we have that, `historical_yield` becomes the dominant factor and the others become priors.

## How to defend a tweak

Every parameter change is logged to the `feedback` table with before/after
values + reason + decided_by. After 90 days of usage you have an audit trail
that says: "On 2026-08-12, the team raised `vertical_concentration` from 0.25
to 0.30 because Money20/20 wasn't ranking high enough relative to its actual
pipeline contribution."

The tool can also turn accumulated rep score-overrides into a **bounded,
renormalised nudge** of the six active weights (see `learn_scoring_weights` in
`scoring.py`) — gated, guardrailed, and reversible. That's the closed-loop story.

## What's deliberately simple

- All scoring is single-threaded SQLite read + Python compute. No
  pre-aggregation, no caching. Re-scoring all conferences takes ~300ms.
- No LLM in the scoring path. Defensibility > sophistication.
- The factor evidence strings are template-driven, not LLM-generated. They
  always say the same thing for the same input.
