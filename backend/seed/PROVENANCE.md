# Seed data provenance

The seed is **real, publicly-sourced data** — not hand-typed fixtures. The brief
allowed "a sample conference database from publicly available information"; this
is that, built from real scraping + verification.

## `conferences.json` — 77 events (after dedupe)
- Compiled from public event calendars (paytech.events and conference org sites)
  plus curated anchors Grain actually attends (Money20/20, EuroFinance, AFP…).
- Fields: name, dates, city/country/region, format, themes (from agendas),
  estimated attendance, pass/booth cost, website.
- Conference facts were fact-checked against source pages (dates/locations
  corrected where the calendar was stale).

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
