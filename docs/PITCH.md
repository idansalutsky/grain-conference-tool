# Grain Conference Intelligence — the pitch

A web tool that helps Grain's sales team **decide** which conferences to attend,
**plan** team coverage, **capture** leads in the field, **recognise**
relationships that develop across events, and **act** — pushing
intelligence-enriched contacts to HubSpot. Built against the assignment brief.
Everything below is honest about what is live AI work versus seeded data.

---

## The one-paragraph version

A salesperson opens **Dashboard**: the active (or next) event, the top ICP-fit
targets at it, any warming nudges, recent captures, and the review queue. They
**decide** where to go (transparent 7-factor ICP-fit scoring over **195 real
conferences**, defensible factor-by-factor), **plan** coverage (month-by-month +
geo/temporal trip clusters + uncovered tier-A events, and a one-click handoff that
sends each rep their assigned-event links), **capture** a lead on the floor in
seconds (speak → in-browser transcription → LLM-structured lead, auto-attributed
to the event; and an end-of-event "wrap" that texts back ready-to-send drafts),
and watch the cross-conference engine **recognise** the person across events,
read the relationship arc (warming / flat / cooling / tire-kicker), and — when
it isn't sure — **ask** instead of guessing. Push to HubSpot carries the judgment
with the contact via `grain_*` properties.

> **Scope was deliberately tightened** — see `docs/SCOPE_DECISIONS.md`. One
> coherent loop (Decide → Plan → Capture → Recognise → Act), not "a bit of
> everything." The agent/memory layer is demoted to its own tab, not the spine.

---

## What's live AI vs seed data (the most important honesty here)

| Capability | Live or seed? | How it works |
|---|---|---|
| **195 conferences** | **Seed**, web-researched + cached | Real events (Money20/20, EuroFinance, AFP, Sibos, Phocuswright, Seamless, GTR…) with date, city, region, vertical, themes, format, attendance, audience composition. Deduped across files/years. The brief explicitly allowed a "sample conference database." |
| **Per-event "who to approach"** (204 people, 50 ✓) | **Seed, public sources** | Seeded from public speaker/sponsor/entry-point data; **✓-verified** ones are marked, the rest flagged **"verify before you approach."** Deliberately light — the honest production answer is a **Clay/Apollo enrichment waterfall**. This is the *weakest* layer and the pitch says so; the strength is what the engine does with a contact, not the list. See `seed/PROVENANCE.md`. |
| **Reps (sample GTM team)** | **Seed (fictional)** | Sample reps — deliberately *not* Grain's real staff. |
| **6 demo contacts** | **Seed (sample)** | *Fictional* contacts whose histories exercise every arc state, nudge branch, and edge case. Built by running sample encounters through the **real** resolver + arc + nudge — the verdicts are engine-produced, not hand-typed. |
| **Conference scoring + tiering** | **Deterministic** | 7-factor glass-box score with per-factor evidence. Zero LLM calls. |
| **Planning (coverage/clusters/gaps)** | **Deterministic** | Geo+temporal clustering with travel-saving estimate. No LLM. |
| **Team & coverage admin + rep-link handoff** | **No-code UI** | Add reps, create events, assign coverage; one click sends a rep a paste-ready message with their events + a one-tap Telegram bind. Satisfies "a non-developer can update this." |
| **Cross-conference resolution + arc + nudge + review queue** | Deterministic core (+ optional LLM arc judge) | The spine. Decisive keys (email/LinkedIn/phone) auto-merge; ambiguous name+company goes to a **human review queue**, never a silent merge. The LLM judge only *lifts confidence* when it agrees. |
| **Voice → structured lead** | **🟢 LIVE (LLM)** | Browser Web Speech transcribes (keyless), then one OpenRouter call structures the lead. Fallback: record audio → multimodal model. Text capture falls back to a **regex extractor with no key at all**. |
| **End-of-event wrap (Telegram)** | **🟢 LIVE** | Rep texts "done" → event roster + ready-to-send follow-up drafts (tire-kickers filtered) + live nudges. Reuses the follow-up drafter + nudge state. |
| **Conference discovery** | **🟢 LIVE (LLM)** | Grounded search; proposals with real source URLs; HIL approve → auto-scored into the list. |
| **Approach brief + follow-up draft** | **🟢 LIVE (LLM)** | Grounded trigger-news + synthesis, tied to Grain's value prop. |
| **Plan-my-prep agent** | **🟢 LIVE (LLM)** | Real tool-calling loop, streamed over SSE — selective pre-event prep. |
| **Quality gate + feedback learning (Intelligence tab)** | **🟢 LIVE (agent graph)** | A LangGraph loop with a hard **ICP gate** (rejects competitors / off-ICP / duplicates before they enter memory) and a feedback loop that re-tunes scoring weights from rep overrides, within guardrails. |
| **HubSpot push** | Dry-run by default; live with token | Real API (upsert by email); `grain_*` properties carry arc verdict, nudge, follow-up. Self-heals its custom properties on first push. |

**Demo-safety:** scraping and LLM calls never sit in the demo's critical path.
Decide / Plan / Recognise run from cached, deterministic data. Only *new*
capture / discovery / brief generation hits a model.

---

## Why the scoring is defensible (the factor weights)

Weighted toward Grain's *real* ICP — heavy-FX, finance/treasury buyer — not raw
audience size. Full derivation in `docs/SCORING.md`.

| Factor | Weight | Why |
|---|---|---|
| `vertical_concentration` | 0.25 | Does the event centre travel / payments / cross-border / marketplaces? |
| `buyer_reachability` | 0.25 | Can a rep reach Grain's **buying committee**? Persona-weighted share of the **measured audience composition** (e.g. EuroFinance ≈ 75% finance/treasury), not a keyword guess. The Grain-specific lever. |
| `fx_exposure_proxy` | 0.20 | Themes carrying cross-border / multi-currency / settlement signal. |
| `reachability` | 0.10 | Format/size — can a rep actually get meetings? |
| `geo_cost_efficiency` | 0.10 | Fit-per-dollar; a cheap high-fit regional event can out-rank a mega-pass. |
| `icp_strategic_fit` | 0.10 | Strategic-wedge centrality blended with ICP-shaped audience density. |
| `historical_yield` | 0.00 (opt-in) | Last year's captured-lead quality re-weights next year — the feedback loop, off until there's usage data. |

Tiers: **A ≥ 78, B ≥ 58, else C** → live **A=33 / B=137 / C=25**.

**The worked example for the video:** EuroFinance (~92) and AFP (~87.5) top the
list; Money20/20 USA (~85) is Tier A but not #1; a 45,000-person **Dreamforce
lands in C**. Defend the heavy-FX-buyer wedge — **buyer density beats raw size** —
that makes that non-obvious ranking correct.

---

## How the five evaluation axes are served (they interlock via one ICP)

- **Sales empathy** — the field layer: one-tap voice capture, the end-of-event wrap with ready drafts, the per-event hit-list, a nudge that stays quiet on weak signal. Built around the rep's day, not a CRUD form.
- **AI judgment** — AI only where synthesis/reasoning is the right tool: voice→lead, the arc read, the brief, discovery, the prep agent, the memory gate. Each justified in `docs/AI_STRATEGY.md`.
- **Cross-conference intelligence** — resolution + interpretation (warming vs tire-kicker) + a calibrated nudge + a **review queue when it isn't sure**. `docs/CROSS_CONFERENCE.md` covers the edge cases: name variants, nicknames, transliteration, job changes, missing data, name collisions.
- **Shipping instinct** — one event source, ICP as a single config, scrape-to-cache demo-safety, the heavy/ToS-fragile work explicitly deferred, **213 tests**, a single-image one-URL deploy. `docs/SCOPE_DECISIONS.md`.
- **Communication** — this doc + the scope record + the video defend the weights, the matching edge cases, and where AI helped vs got in the way.

---

## Deploy / cost

Single Docker image serves the API **and** the app at one URL — push to GitHub,
point Render at the `render.yaml` Blueprint, done. Boots **keyless** in demo mode;
integration keys are configurable in-app, never hardcoded. Demo-grade cost: a few
dollars of model credit (cheap extraction calls, a few cents per grounded
discovery). No GPU, no standing infrastructure — one SQLite file, BYO keys.
