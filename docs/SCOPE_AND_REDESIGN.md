# Scope & frontend redesign — re-anchored to the assignment

**Status:** IMPLEMENTED (2026-05-31). Supersedes `FRONTEND_REDESIGN.md`.
Capture page removed; Dashboard rebuilt around Close-now + Where-to-invest;
market/mentioned signals demoted under Intelligence. Trip clusters bounded
(≤21 days, ≤4 events). 224 tests pass; build green; 0 console errors live.
**Decisions locked:** primary user = **the team lead / manager** (who also reps in the field on a small team); approach = **ruthless cut to 4 tabs, the "brain" hidden**.

> **What "manager-first" changes vs. rep-first:** the Dashboard leads with *where the team
> stands* — coverage across the year, where we're under-invested, which high-ROI events have
> nobody on them, and which relationships across the team are warming and need action — before
> the single-rep "my next move" view. On a small team the manager also captures in the field,
> so the rep flows still exist; they're just not what the landing screen optimizes for. The
> brief's "demo from a salesperson's perspective" still holds — a player-coach, not a
> back-office admin.

---

## 0. Why this doc exists

The backend grew far past what the assignment asks for (a LangGraph "brain," hierarchical
rollups, research/wrap-up agents, calibration). None of it is bad — but the **frontend
sprawled to 14 pages and started reading like a developer console, not a salesperson's
tool.** That hurts the two axes the brief screens hardest for: *shipping instinct* ("did you
scope, or build half of everything?") and *sales empathy* ("built for a salesperson, or a
generic CRUD app?"). This doc re-scopes the **surface** to the assignment. It does **not**
delete backend work — the engine stays; we just stop exposing all of it at once.

---

## 1. The assignment, in one screen

**Build a conference-intelligence tool a non-technical salesperson can actually use.**
Time expectation in the brief: **4–6 hours.** That's the calibration — scrappy and pointed
beats sprawling and impressive.

**The journey IS the spec** (straight from the brief's "help the team"):

1. **Decide** which conferences to prioritize — ICP scoring + tiers
2. **Plan** coverage across the year + cluster trips
3. **Capture** leads in the field — fast, low friction
4. **Recognize** relationships across conferences — interpretation, not a count
5. **Act** — nudge / follow-up / push to HubSpot

Graded on: sales empathy · AI judgment · cross-conference intelligence · shipping instinct ·
communication.

**The user:** the **team lead / manager** of a small conference team — a player-coach who
both decides where to deploy the team AND captures in the field. They care first about
*team coverage and where we're under-invested*, then about their own next actions. (Demo is
still "from a salesperson's perspective" — a player-coach, not a back-office admin.)

---

## 2. The surface — 4 tabs, each a journey beat

```
Dashboard  ·  Conferences  ·  Relationships  ·  ⚙ Settings
```
Capture is **not a tab** — capture happens in **Telegram** (the thing in the rep's hand).
The web app is the **desk**: decide, plan, review, recognize, act.

| Tab | Beats | Replaces (current pages) |
|---|---|---|
| **Dashboard** | manager's "where does the team stand + what needs action" glance | Today (rebuilt) |
| **Conferences** | **Decide + Plan** | Conferences, ConferenceDetail, Planning, Discovery |
| **Relationships** | **Recognize + Act + HubSpot** | Contacts, ContactDetail, Companies, CompanyDetail, Nudges |
| **⚙ Settings** | configure + connect | Team, Settings, + Brain machinery as "How it works" |

### 2.1 Dashboard — the manager's command screen
Leads with the team's standing, in order (manager-first, then the player-coach's own actions):
1. **Coverage at a glance** — the year's high-tier events and where we're **under-invested**:
   "5 Tier-A events, 2 with nobody assigned." The single most manager-relevant signal —
   answers "where should we deploy?" (Decide + Plan in one strip).
2. **Relationships warming across the team** — accounts/contacts trending up that need action
   now, with the *reason* ("warming · 78% · 3 touches · no meeting yet") → draft follow-up /
   assign. Team-wide, not just mine.
3. **Next / active event** + one-tap "who to approach" (top targets w/ persona + why) + who's
   covering it — the player-coach's in-the-field view.
4. **From the field** (small) — what came in via Telegram today, anything needing review;
   a **"Connect Telegram"** card if not yet linked.

Drop from the landing screen: the raw chronological capture log as the headline; the ICP block.

### 2.2 Conferences — Decide + Plan
Sub-views (segmented control, not hidden):
- **List** — ranked + tiered, filterable (name/vertical/region/tier). Each row shows the
  *one reason* it's tiered (top score factor as a chip). The 7-factor breakdown + the live
  weight tuner live behind a **"Why this score / tune"** toggle (power feature, not default).
- **Plan** — year timeline + **coverage gaps** (each with "assign a rep") + **trip clusters**
  that are *actual trips* (see §4 fix). Managing reps/coverage happens here, in context.
- **Find new** — the research agent as a **button** ("Find events worth attending"), with a
  progress state and "last run / run again"; proposals show *why relevant* + predicted tier
  before approve.
- **Event detail** — keep the rich page (committee, score breakdown, coverage, follow-ups);
  make the committee actionable and the per-rep Telegram bind a copy/QR, not a paste blob.

### 2.3 Relationships — Recognize + Act (the cross-conference spine, the hardest-graded part)
**Account-first**, because that's how a rep thinks ("how's Booking?"), not row-first.
- **Accounts** (default) — sorted by heat (warming first): "Booking — 4 known, 1 warm, met
  across 3 events, last touch 12d." Drill-in → next-step block + the people under it.
- **People** — the contact list with arc filter + sort by recency/confidence; inline
  copy-email/LinkedIn; the **review queue** for ambiguous matches surfaced at top.
- **Follow-ups** — nudges, each showing its firing reason → draft email / accept / dismiss.
- **Contact detail** — encounter timeline, arc (with override + confidence shift), a
  **copyable + re-generatable** brief, and one-tap **push to HubSpot** (the `grain_*` intel).

### 2.4 ⚙ Settings — configure + connect (no "Admin" — there are no roles)
- **Telegram** — connect/bind, QR + deep link, "what you can send" (voice/photo/contact/
  `/fix` `/undo` `/next`). This is where the field channel is set up.
- **Integrations** — keys per provider + a **Test** button so you know a key works.
- **Reps** — the roster as data (assigning to events happens in Conferences→Plan).
- **ICP & scoring** — ICP shown here as the *engine config* (not a destination), + link to
  scoring calibration.
- **How it works** — the brain's live graph / run demo / spaces internals live HERE, one
  page, for the video and the curious. Out of the rep's daily path.

---

## 3. What gets cut or demoted (the proof of scoping)

| Thing | Fate | Why |
|---|---|---|
| **Capture** page | **Removed** | Capture is a field act → Telegram. A web capture page contradicts "in their hand on a busy floor." |
| **Brain** as a tab | **Removed** | Conclusions scatter into Dashboard/Conferences/Relationships; machinery → Settings "How it works." A node-graph as a top tab reads as a dev console. |
| **ICP** as a prime view | **Demoted to Settings** | ICP is the engine under scoring, surfaced as the *"why,"* not something you stare at. |
| **Discovery / research** | **A button** inside Conferences→Find new | It's an action, not a destination. |
| **14 pages** | **→ 4 tabs** | One surface per journey beat. |
| Brain rollups/spaces UI, activity feed, calibration UI, ask-the-brain Q&A | **Optional / phase-2** | Keep the engine; expose only if time allows, as scattered insights — never as the headline. |

Nothing backend is deleted. This is purely about what the **surface** presents.

---

## 4. Your specific complaints — fixes

1. **Clusters too huge/long** → tighten `planning.py`: a cluster must be a real trip —
   **same region, window ≤14 days, cap ~5 events**, shown concretely ("3 events · Singapore ·
   Oct 12–22 · ~$X saved"). Anything bigger isn't a trip, it's the calendar.
2. **Capture page shouldn't exist** → removed; capture = Telegram; web app reviews/fixes only.
3. **Dashboard shows small things** → rebuilt to lead with event + coverage + relationships
   to act on + under-covered high-tier events. The capture log stops being the headline.
4. **ICP too front-and-center** → moved into Settings as engine config; appears elsewhere only
   as the one-line "why this is Tier A."
5. **We lost the assignment / scope** → fixed by the 4-tab journey spine above; the cut list
   in §3 is the visible evidence of scoping.

---

## 5. Rollout order (when code starts)

1. **Conferences → Plan**: tighten clusters + make gaps/clusters actionable. (Fixes a real bug.)
2. **Remove Capture tab**; move Telegram-connect to Settings + a Dashboard card; keep a tiny
   demo-only "+ capture" modal **only if** we decide the live-URL reviewer lacks a Telegram webhook.
3. **Dashboard** rebuild (command screen).
4. **Relationships** (account-first; contacts/follow-ups actionable).
5. **Conferences → List/detail** polish ("why tiered" inline; committee actionable).
6. **Settings** consolidation (Telegram, Test-key, reps, ICP, "How it works") + collapse nav
   to 4 tabs. Route paths stay stable; `/capture`, `/brain`, etc. redirect.

---

## 6. The video story (because communication is graded)
"Grain's team runs on conferences. **Decide** — the tool scores every event by ICP fit, and
treasury-pure events out-rank giant Money20/20 because the audience is denser. **Plan** — here's
our year and the trips we can cluster. **Capture** — on the floor I just talk to the Telegram
bot; it structures the lead. **Recognize** — when I meet someone again, it knows, and tells me
if they're warming or just tire-kicking. **Act** — it drafts the follow-up and pushes the whole
story to HubSpot." Five beats, one rep, end-to-end. The brain is the engine behind "Recognize" —
shown once on "How it works," not worn on the sleeve.
```
