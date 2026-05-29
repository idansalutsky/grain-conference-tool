# Grain Conference Intelligence — the pitch

A web tool that helps Grain's sales team **decide** which conferences to attend,
**plan** team coverage, **capture** leads in the field, **recognise**
relationships that develop across events, and **act** — including a push of
intelligence-enriched contacts to HubSpot. Built against the assignment brief.
Everything below is honest about what is live AI work versus seeded data.

---

## The one-paragraph version

A salesperson opens the app on **Today**: the active (or next) event, the top 3
ICP-fit targets at it, any active warming nudges, their recent captures, and the
review queue. They **decide** where to go (transparent 7-factor ICP-fit scoring
on 78 real conferences, defensible factor-by-factor), **plan** coverage
(month-by-month + geo/temporal trip clusters + uncovered tier-A events),
**capture** a lead on the floor in seconds (speak → in-browser transcription →
LLM-structured lead, auto-attributed to the event), and watch the
cross-conference engine **recognise** the person across events and read the
relationship arc (warming / flat / cooling / tire-kicker) with a calibrated
nudge that stays *silent on weak signal* — and tells you why. They can argue
with any AI decision (scoring weights, persona, score, brief, arc, nudge gate);
every override is logged. Push to HubSpot carries the judgment with the contact
via `grain_*` properties.

> **Scope was deliberately tightened** — see `docs/SCOPE_DECISIONS.md`. The
> product is one coherent loop (Decide → Plan → Capture → Recognise → Act), not
> "a bit of everything." Account/prospect management was demoted to a
> drill-down, not a nav destination.

---

## What's live AI vs seed data (the most important honesty here)

| Capability | Live or seed? | How it works |
|---|---|---|
| **77 conferences** | **Seed**, scraped-then-cached | Public event data (Money20/20, EuroFinance, Sibos, iFX EXPO, Seamless, Phocuswright…) with date, city, vertical, themes, format, attendance, cost. Deduped (3 same-event-same-year copies merged). Brief explicitly allowed a "sample conference database." |
| **861 real scraped people** (676 conference-linked, 26 events) | **Seed** (Apify + Sonar) | Real speakers/sponsors/entry-points at ICP companies, classified into 6 personas by title. This powers the per-event "who to approach" list — e.g. Money20/20 Europe surfaces the CFOs of Klarna, Stripe, Revolut, Wise, Mollie; Phocuswright surfaces CFOs of Booking Holdings, Trip.com, TripAdvisor, Hilton, Hyatt. Real names, real heavy-FX companies. |
| **Reps = real Grain GTM team** | **Seed** | Chris Day (VP, North America), Marc Padrosa (VP Sales), Eugene Lin (Head of Sales, ex-Expedia), Diana Mihaylova, Ben Strugo — scraped from public LinkedIn. |
| **6 demo contacts** | **Seed (sample)** | *Fictional* contacts whose histories exercise every arc state + nudge branch + edge case. **Not real people, not scraped.** Built by running 16 sample encounters through the *real* resolver + arc + nudge — the verdicts are engine-produced, not hand-typed. |
| **Conference scoring + tiering** | **Deterministic** | 7-factor glass-box score with per-factor evidence. Runs with zero LLM calls. |
| **Planning (coverage/clusters/gaps)** | **Deterministic** | Geo+temporal clustering with travel-saving estimate. No LLM. |
| **Entity resolution + arc + nudge** | Deterministic core (+ optional LLM arc judge) | The cross-conference spine. Runs from seeded data; the LLM judge only *lifts confidence* when it agrees. |
| **Voice → structured lead** | **🟢 LIVE (LLM)** | Browser Web Speech API transcribes in-browser (keyless), then one OpenRouter call structures the lead. Fallback: record audio → Gemini multimodal. |
| **Conference discovery** | **🟢 LIVE (LLM)** | Perplexity Sonar grounded search; returns proposals with real source URLs; HIL approve → auto-scored into the list. |
| **Approach brief + follow-up draft** | **🟢 LIVE (LLM)** | Sonar for grounded trigger-news + Gemini synthesis, tied to Grain's value prop. |
| **Plan-my-prep agent** | **🟢 LIVE (LLM)** | Real tool-calling loop (selects targets, reuses/generates briefs, flags competitors), streamed over SSE. The one agentic feature — justified because pre-event prep needs *selective* judgment. |
| **HubSpot push** | Dry-run by default; live with token | Real API (upsert by email); 7 `grain_*` properties carry the arc verdict, nudge, follow-up. |
| **Telegram capture** | Bot + per-rep binding built; optional | Off the critical path (needs a public webhook URL). |

**Demo-safety:** scraping and LLM calls never sit in the demo's critical path.
Everything on Decide / Plan / Recognise runs from cached, deterministic data. Only
*new* capture / discovery / brief generation hits the model.

---

## Why the scoring is defensible (the factor weights)

Weighted toward Grain's *real* ICP — heavy-FX, finance/treasury buyer — not raw
audience size. Full derivation in `docs/SCORING.md`.

| Factor | Weight | Why |
|---|---|---|
| Vertical concentration | 0.25 | Does the event centre travel/payments/cross-border/marketplaces? |
| Buyer density | 0.25 | Do CFO/treasury/payments leaders actually attend? (the Grain-specific lever) |
| FX-exposure proxy | 0.20 | Themes carrying cross-border / multi-currency / settlement signal |
| Reachability | 0.10 | Format/size — can a rep actually get meetings? |
| Geo + cost-efficiency | 0.10 | Fit-per-dollar; a cheap high-fit regional event can out-rank a mega-pass |
| Competitive validation | 0.10 | Competitors present = market validation |
| Historical yield | 0.00 (opt-in) | Last year's captured-lead quality re-weights next year — the feedback loop |

**The worked example for the video:** the tool *agrees with a decision Grain
already made* (Money20/20 USA → Tier A, 88) **and** surfaces treasury-heavy
events (EuroFinance, AFP) as Tier A over bigger generic shows — defend the
heavy-FX-buyer wedge that makes that non-obvious call correct.

---

## How the five evaluation axes are served (they interlock via one ICP)

- **Sales empathy** — the field layer: 1-tap voice capture, the approach brief, the per-event hit-list, a nudge that stays quiet on weak signal. Built around the rep's day, not a CRUD form.
- **AI judgment** — AI only where synthesis/reasoning is the right tool: voice→lead, the arc read, the approach brief, conference discovery, and the selective prep agent. Each justified in `docs/AI_STRATEGY.md`.
- **Cross-conference intelligence** — resolution + interpretation (warming vs tire-kicker) + a calibrated nudge. `docs/CROSS_CONFERENCE.md` covers the edge cases: name variants, transliteration, job changes, missing data, name collisions.
- **Shipping instinct** — one event source, ICP as a single config, scrape-to-cache demo-safety, the heavy/ToS-fragile work explicitly deferred. `docs/SCOPE_DECISIONS.md`.
- **Communication** — this doc + the scope record + the video defend the weights, the matching edge cases, and where AI helped vs got in the way.

---

## Cost

Demo-grade: a few dollars of OpenRouter credit covers it (Gemini Flash
extraction ~fractions of a cent per call; Sonar discovery a few cents). No GPU,
no self-hosted Whisper, no standing infrastructure — one SQLite file, BYO keys.
