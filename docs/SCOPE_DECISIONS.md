# Scope decisions — what's in, what's out, and why

The brief rewards **shipping instinct**: "Did you scope smartly and get something
working end-to-end, or build half of everything?" This is the explicit record of
what we chose to build, what we deliberately left out, and the reasoning — so
none of it reads as accidental.

## The organising principle

One salesperson, one year, one spine. Every screen maps to a moment in the rep's
day — **Decide → Plan → Capture → Recognise → Act** — and everything hangs off a
single contact identity that persists across events. We resisted building "a bit
of everything" in favour of one coherent loop that works end-to-end.

A single **ICP definition** (`backend/grain/icp.py`) is the source of truth that
ties the criteria together: it scores events, ranks the people to approach,
shapes the approach brief, and grounds the relationship read. That's the answer
to "the five criteria are connected" — they all read from one object.

## In scope — and finished, not stubbed

| Capability | Brief requirement | Status |
|---|---|---|
| Conference list + filter + 7-factor ICP score + A/B/C tiers | #1, #2 | Done — 78 real events, glass-box scoring with per-factor evidence |
| Planning: yearly coverage, gaps, geo+temporal trip clusters | #3 | Done — deterministic clustering with travel-saving estimate |
| Field capture: 1-tap voice (browser transcription) + text | #4 | Done — speed-first, structured lead in seconds |
| Cross-conference resolution + arc interpretation + calibrated nudge | #5 | Done — the hardest part; see `CROSS_CONFERENCE.md` |
| AI features, each justified | #6 | voice→lead, arc summariser, conference discovery, approach-brief, selective prep agent |
| HubSpot push carrying `grain_*` intelligence (+ dry-run) | #7 | Done — the judgment travels with the contact |
| Team & coverage admin — add reps, create events, assign "who covers what" | Business problem + "non-dev can update" | Done — each assignment has a one-tap per-rep Telegram bind |
| BYO keys, one-file SQLite, Vercel+Render deploy | Constraints | Done — nothing hardcoded |

## Deliberately cut or demoted (the discipline)

- **Standalone "Companies" prospecting tab — removed from the product.** A
  find-new-accounts / approve-prospects surface drifted toward a second CRM and
  overlapped the conference-discovery feature. It added navigation weight against
  the "simple enough for a non-technical salesperson" constraint. We kept the
  company **drill-down** (the FX-exposure context behind a target you click) and
  removed the prospecting flow from the rep's path.

- **Proactive "brain insights" + semantic search — removed.** Both worked, but
  they pushed the tool toward feeling like a *platform* rather than a rep's tool,
  and "simplicity" is an explicit constraint. The insights were abstract where
  the rep wants concrete next actions (which the nudges and prep agent already
  give); the search box was rarely the way a rep navigates 8 screens. Cutting
  them end-to-end (frontend + backend + schema) kept the surface honest and the
  repo clean — every screen now maps to a moment in the rep's day.

- **Live LinkedIn scraping for enrichment — out.** ToS-fragile, needs proxies,
  and breaks in a live demo. The approach brief is generated from the role +
  company + vertical we already hold, grounded with web search. LinkedIn
  enrichment is a clean future add, not core plumbing.

- **Telegram bot capture — built but optional.** The per-rep binding + webhook
  exist and are wired; it's off the critical path because it needs a public URL.
  Web capture is the demoed channel.

## Demo-safety (a deliberate engineering choice)

Scraping and LLM calls never sit in the demo's critical path. The conference DB
is scraped-then-cached; scoring, planning, entity-resolution, arc and nudge are
deterministic and run from seeded data with **zero live LLM calls**. Only *new*
capture / discovery / brief generation hits the model. A flaky network can't
break the walkthrough.

## What we'd build next (named, not half-built)

- Historical-yield feedback loop fully wired (the scoring factor exists at weight
  0): last year's captured-lead quality re-weights next year's event scores.
- LinkedIn / news enrichment on the approach brief, async and cached.
- Multi-language voice and an offline-queued PWA for dead conference Wi-Fi.
- Coverage **optimiser**: given reps + budget + scores, propose the event/rep
  allocation, batched into trips.
