# Scoring methodology — 7 factors

The brief says "rank conferences by ICP fit" and "defend it in your video".
Here's the defence.

## Why 7 factors, not 1

A single overall score is undemandable. A breakdown is.

The whole point of building this tool over a spreadsheet is that a salesperson
can look at *why* Money20/20 USA scores 87.8 — not just that it does. Each
factor surfaces in the UI with the raw 0..1 sub-score, the weight, the
contribution to the total, and a one-sentence evidence string.

## The 7 factors + default weights

| Factor | Weight | What it measures | Why it's here |
|---|---|---|---|
| `vertical_concentration` | 0.25 | Is the event vertical in Grain's ICP? | The wedge — Grain sells to payments, treasury, travel, marketplaces. Generic fintech doesn't cut it. |
| `buyer_density` | 0.25 | Likelihood that CFOs / Treasurers / Heads of Finance attend | The single most direct correlate of pipeline. |
| `fx_exposure_proxy` | 0.20 | Themes carrying FX-relevant signal | "Cross-border", "settlement", "multi-currency" — these are the words a buyer who needs Grain uses. |
| `reachability` | 0.10 | Format + size — can a rep actually meet people? | A 50-person closed roundtable scores worse than a 5000-attendee expo. Webinars score lowest. |
| `geo_cost_efficiency` | 0.10 | Region weighted by typical travel cost | A $7K NYC trip needs higher ROI than a $1.5K Singapore trip. |
| `competitive_validation` | 0.10 | Competitor presence (signal, not deterrent) | If Currencycloud and Wise both attend, that's a market signal — not a reason to skip. |
| `historical_yield` | 0.00 (opt-in) | Boost from prior meetings/deals | Off by default until we have data. Once a tenant has run 6+ months, raise this. |

Weights are sliders in the Settings UI. Sum to 1.0 by default (excluding
`historical_yield`).

## What this surfaces — examples from the seed data

| Conference | Tier | Score | Top contributors |
|---|---|---|---|
| Money20/20 USA 2026 | A | 87.8 | vertical_concentration 1.00, fx_exposure 0.95, buyer_density 0.90 |
| EuroFinance International Treasury Management 2026 | A | 87.2 | buyer_density 0.95 (treasury-pure), fx_exposure 0.95 |
| Phocuswright 2026 | A | 76 | vertical=travel direct hit, fx_exposure on cross-border bookings |
| ITB Berlin 2026 | B | 62 | reachability 0.90 (huge), but vertical=travel is one step removed |
| WiT Singapore | B | 58 | travel + APAC + small format |

## What the model **deliberately doesn't** do

- It doesn't auto-pick which events to attend. It surfaces the score; humans decide.
- It doesn't penalise low scores — a tier-C event might still be worth attending for relationship reasons. The score is one input, not the answer.
- It doesn't try to predict pipeline directly — that requires Year-1 of usage data. Once we have that, `historical_yield` becomes the dominant factor and the others become priors.

## How to defend a tweak

Every parameter change is logged to the `feedback` table with before/after
values + reason + decided_by. After 90 days of usage you have an audit trail
that says: "On 2026-08-12, the team raised vertical_concentration from 0.25
to 0.30 because Money20/20 wasn't ranking high enough relative to its actual
pipeline contribution."

That's the closed-loop story.

## What's deliberately simple

- All scoring is single-threaded SQLite read + Python compute. No
  pre-aggregation, no caching. Re-scoring all conferences takes ~300ms.
- No LLM in the scoring path. Defensibility > sophistication.
- The factor evidence strings are template-driven, not LLM-generated. They
  always say the same thing for the same input.
