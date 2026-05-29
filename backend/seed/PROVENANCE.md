# Seed data provenance

The seed is **real, publicly-sourced data** — not hand-typed fixtures. The brief
allowed "a sample conference database from publicly available information"; this
is that, built from real scraping + verification.

## `conferences.json` — 89 events (after dedupe + cross-year cleanup)
- Compiled from public event calendars (paytech.events and conference org sites)
  plus curated anchors Grain actually attends (Money20/20, EuroFinance, AFP…).
- Fields: name, dates, city/country/region, format, themes (from agendas),
  estimated attendance, pass/booth cost, website, `vertical`, grounded
  `agenda_summary`, measured `audience_composition_json`, `source_url`.
- Conference facts were fact-checked against source pages (dates/locations
  corrected where the calendar was stale).

### Vertical classification (`vertical` field)
Every event carries an explicit `vertical` (one of: payments, treasury, travel,
booking, marketplace, psp, cross_border_payments, crypto, saas, supply_chain,
fintech_other). This is read directly by the scorer's `vertical_concentration`
factor (25% of the score). The `_vertical_of_conference()` heuristic in
`seed_db.py` is the fallback for any event that ships *without* an explicit
vertical; it matches travel/booking/marketplace on the event NAME first, so a
travel-industry event whose agenda merely mentions "payments" (Phocuswright,
WiT / Web in Travel, World Travel Market) still classifies as **travel**, not
payments. Anchors are locked verbatim:
- **Money20/20 (all editions) → payments**
- **Phocuswright / WiT / World Travel Market / ITB / Skift / Arival / FTE /
  TravelTech / HEDNA → travel**
- **EuroFinance / AFP / Sibos / EBAday → treasury**

### Cross-year de-duplication (a 2026-planning tool should be clean)
The raw scrape contained the same recurring event in multiple years and from
multiple sources. For planning we keep the next upcoming (2026+) edition and
drop the older past-year copies. **Dropped** (each has a kept 2026+ edition):
`money20-20-usa-2025`, `money20-20-europe-2025`, `afp-2024`, `phocuswright-2024`,
`ifx-expo-international-2024`, `eurofinance-2024`, `eurofinance-2025`,
`paytech-finovatefall` (duplicate of `finovate-fall-2026`, same Sept-2026 NYC
event), and `disc-world-travel-market-london-wtm` (duplicate of
`world-travel-market-london-2026`). No events were *fabricated* to roll forward —
every kept edition already existed in the data. `dedupe_conferences()` in
`seed_db.py` also enforces this at seed time (groups by year-stripped name,
keeps the next upcoming edition) as a safety net against re-importing stale
calendar rows.

### Travel-tech anchors — analyst-estimated additions (NOT scraped)
To make the travel wedge (Grain's lead-gen wedge) properly represented, the
following well-known travel / travel-tech events were added as **analyst
estimates**, not scrapes: **ITB Berlin, ITB Asia, Skift Global Forum,
Phocuswright Europe, Future Travel Experience (FTE) Global, Arival 360, WiT
Europe, WiT Japan & North Asia, Travel Technology Europe (TravelTech Show)**
(ids prefixed `disc-`). For each: `vertical="travel"`, a grounded
`agenda_summary`, a realistic PUBLIC ballpark `estimated_attendance`, a
deliberately LOW `cfo_treasury_finance_pct` (12–15% — travel audiences are
operator/commercial-heavy, not finance-heavy) in `audience_composition_json`,
and the official `source_url`. **Treat attendance/audience-mix on these as
analyst ballparks, not measured figures.**

## `people.json` — ICP-fit targets, agent-verified
The per-event "who to approach" list, sourced from public speaker/sponsor/
entry-point scrapes — then **agent-verified**, because raw scraped + LLM-extracted
people are noisy (we measured it).

**Be honest about this — it's the most important caveat:**
- The raw scrape blended real public people with **fabricated / stale
  attributions** (e.g. a name correctly at a company but with the wrong title,
  or a plausible-but-invented CFO for a private company). When we verified a
  sample against the live web, **only ~⅓ were accurate.**
- So we ran an **agentic verification pass** (one agent per target → web search →
  *confirmed / wrong-role / left-company / not-found*, with corrections). We
  **dropped the fabrications and stale roles, corrected titles, attached
  verified LinkedIn URLs**, and tightened to **ICP-fit companies** (travel,
  payments/PSP/cross-border, marketplaces — not generic megacorps/banks/brokers).
- Result: **~347 people, of which 94 carry `verified = 1`** (a ✓ in the UI) — these
  were confirmed against the live web. The rest are shown as **unverified leads**
  ("verify before you approach").
- This verification is itself the **AI-judgment feature**: public attendee data
  goes stale fast, so the tool verifies a target before a rep acts. The same
  approach is packaged open-source as **OpenClay**; in production you'd add a paid
  waterfall (Clay / Apollo) for work emails + higher coverage.

## Reps
**Fictional sample reps** (Jordan Avery, Sofia Marsh, Lukas Berg, Mei Tan, Omar
Haddad) — deliberately *not* Grain's real employees. This is a demo.

## What is NOT scraped (and shouldn't be)
The **encounters / contacts** in `seed_demo.py` are fictional samples — you
cannot scrape "what someone said to a rep on the floor." They exist only to
demonstrate the cross-conference engine, and are clearly labelled as samples.
The intelligence reading them (matching, arc, nudge) is the real engine.
