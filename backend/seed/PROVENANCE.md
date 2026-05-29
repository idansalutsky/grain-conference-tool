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

## `people.json` — 861 real people (676 linked to 26 events)
Three public, high-signal sources, classified into the 6-persona buying
committee by title:
- **Speakers** — scraped from public conference agendas/speaker pages.
- **Sponsors / exhibitors** — public company lists.
- **Entry-points** — BD / partnerships / sales contacts at the heavy-FX ICP
  companies (Booking, Adyen, Amadeus, Maersk, Hotelbeds, Expedia, Klarna,
  Mollie, Wise, Stripe, …), found via grounded web search (Perplexity Sonar)
  and LinkedIn profile search (Apify `harvestapi/linkedin-profile-search`).
- Each person carries an `icp_score` and persona so the per-event "who to
  approach" list ranks by buying influence.

## Reps
Real Grain GTM team members scraped from public LinkedIn (VP North America,
VP Sales, Head of Sales, Director Fintech, VP Business Development).

## What is NOT scraped (and shouldn't be)
The **encounters / contacts** in `seed_demo.py` are fictional samples — you
cannot scrape "what someone said to a rep on the floor." They exist only to
demonstrate the cross-conference engine, and are clearly labelled as samples.
The intelligence reading them (matching, arc, nudge) is the real engine.
