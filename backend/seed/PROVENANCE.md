# Seed data provenance

The seed is **real, publicly-sourced data** — not hand-typed fixtures. The brief
allowed "a sample conference database from publicly available information"; this
is that, built from real scraping + verification.

## `conferences.json` — 195 events (after dedupe + cross-year cleanup)
- Compiled from public event calendars (paytech.events, 10times.com) and
  official conference sites, plus curated anchors Grain actually attends
  (Money20/20, EuroFinance, AFP…). Spans all brief verticals: payments (65),
  travel (36), fintech (36), treasury (27), marketplace (18), SaaS (10),
  crypto/other (3).
- Fields: name, dates, city/country/region, format, themes (from agendas),
  estimated attendance, pass/booth cost, website, `vertical`, grounded
  `agenda_summary`, `audience_composition_json`, `source_url`.
- Conference facts were fact-checked against source pages (dates/locations
  corrected where the calendar was stale); every event carries a real
  `source_url` and a real `estimated_attendance` (0 missing).

### Dataset expansion (web-researched real events)
The original ~89-event base was expanded to **195** by researching real,
verifiable 2026 events across payments, fintech, treasury, travel and SaaS from
public sources (10times.com "expected visitors" figures + official sites). Each
added event is a real, recurring conference with a citable `source_url`.
**Honest caveat on the added events:** their `estimated_attendance` is the real
public/marketing figure, but the `audience_composition_json` (the
`cfo_treasury_finance_pct` that drives ~part of the score) is an **analyst
estimate** keyed to the event's audience type (treasury/CFO events 60–80%,
payments 25–40%, fintech 20–35%, travel 8–20%, SaaS/e-commerce 5–15%, crypto
3–8%), not a measured survey. A handful of clearly-annual events whose 2026
edition had already passed were rolled forward to their 2026 date and flagged
"(2026 dates approximate)" in the agenda summary. Nothing was fabricated — every
event exists.

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
- Result: **204 people, of which 50 carry `verified = 1`** (a ✓ in the UI) — these
  were confirmed against the live web. The rest are shown as **unverified leads**
  ("verify before you approach"). The list was further tightened by **dropping
  Grain's competitors (FX/cross-border firms), retail banks (the FX supply side,
  not buyers), generic megacorps with no cross-border-platform angle, and
  no-name conference-speaker noise** — leaving an ICP-true core (travel,
  payments/PSP, marketplace) where finance/treasury buyers and commercial
  champions are surfaced first and CEO/"influencer" names are demoted.
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
