# Frontend redesign spec — "simple but smart"

**Status:** PLAN (no code yet). Awaiting approval.
**Why:** The backend matured a lot (18 routers, ~95 endpoints, Events Brain, research /
wrap-up agents, calibration, L1 rollups, account-level arc). The frontend was built in
the first cycle and never caught up — it shows data, not judgment; buries Telegram; has a
thin dashboard; and only lets a rep fix 5 fields after a capture. This spec rebuilds the
surface so the backend's intelligence shows through, while staying simple for a
non-technical salesperson.

---

## 1. Guiding principles

1. **Show judgment, not just data.** Every list row earns its place with a *reason*
   (why this target, why this tier, why warming) pulled from data already in the API.
2. **Account-first, not row-first.** The cross-conference spine is the product. People
   roll up into accounts; accounts carry temperature.
3. **Capture is the floor surface, and it IS the Telegram surface.** One page = "how you
   get a lead in", web + Telegram together, with full post-detection editing.
4. **The Brain's *conclusions* belong on the rep's pages; its *machinery* hides behind a
   drawer.** Reps see "Booking is warming across 3 events"; engineers click "how it works".
5. **Every screen ends in an action**, never a dead-end read.

---

## 2. Tab structure

### Current (muddled)
`Dashboard · Events(+Discovery) · Calendar · Capture · People(Contacts+Companies+Nudges) · Admin(Team+Settings) · Intelligence`

Problems: "Events" mixes browse vs. discover; "People" is a grab-bag of 3 different jobs;
"Calendar" is a vague name for planning; "Capture" is isolated from events + Telegram;
"Intelligence" is a developer sandbox most reps never open.

### Proposed — RECOMMENDED: 5 tabs, no "Capture" tab, no "Admin" framing

```
Today | Events | People | Intelligence | ⚙ Settings
```

**Two reframes drive this:**

1. **Capture is NOT a web page — it's a field act, and the field device is Telegram.** A
   rep at a busy booth does not open a browser and fill a form; what's "in their hand" is a
   phone → the Telegram bot (voice memo, badge photo, shared contact, `/fix`, `/undo`,
   `/next`). The web app is the **desk**: decide, plan, *review what came in from the field*,
   recognise relationships, act. So "Capture" stops being a peer tab and splits into the
   things it really is (setup + review — see below).

2. **No auth / no roles** — whoever opens the demo sees everything, so an "Admin" tab
   (implying a gate) is the wrong frame. Config is just Settings, a small gear at the end.

| Tab | Job (one sentence) | Folds in |
|---|---|---|
| **Today** | "What do I do right now?" — command center + *what came in from the field* | dashboard + review/fix captures + "Connect Telegram" card |
| **Events** | "Which events, who covers them, what's new?" | Browse + Plan(+reps/coverage) + Discover (sub-tabs) |
| **People** | "The relationships I'm building" — account-first | Companies + Contacts + Follow-ups (sub-tabs) |
| **Intelligence** | "What the market/brain is telling us" — plain language | Brain conclusions, reframed (machinery in a drawer) |
| **⚙ Settings** | "Configure the tool" | Telegram connect/QR + reps roster + integrations + thresholds + ICP |

> **Where "Capture" went:**
> - **Doing the capture** = Telegram, in the field. Not in the web app at all (that's the point).
> - **Connecting Telegram** (bind / QR / deep link / "what you can send") = setup → lives in
>   **Settings**, plus a one-time **"Connect Telegram to capture in the field"** card on Today.
> - **Reviewing & fixing captures** (today's captures, the review queue, full post-detection
>   editing) = back at the desk → woven into **Today** ("what came in from the field") and
>   **People** (the contact/account it resolved to).
>
> **Where reps/coverage go:** assigning reps to events is a *planning* act → inside
> **Events → Plan**. The reps list as data + Telegram links sit under the gear.

> **One open tension — the demo / live URL.** Telegram needs a public webhook, which a
> reviewer clicking the live URL may not have wired. Today the web capture *is* the demoed
> channel. So we keep a **lightweight web-capture fallback** — but as a secondary
> **"+ Capture a lead" quick-action (modal)** launched from Today, explicitly framed as
> "desk/demo entry; in the field, use Telegram" — NOT a primary nav tab. (See §3.3.)
> *Decision needed from you: keep this fallback modal, or drop web capture entirely and make
> the demo show Telegram?*

> Why keep Intelligence (vs. a 5-tab fold-in): you invested heavily in the Events Brain,
> research agent, rollups, and activity feed. Reframed into plain language it's a genuine
> differentiator ("warming accounts, coverage gaps, events your buyers mentioned, what we
> learned this week"). Burying it wastes real work. But it must read as *insights*, not as
> node-traces and graph diagrams — those move into a collapsed "How the brain works" drawer.

> Alternative (5 tabs): drop Intelligence as a tab, surface its conclusions inside Today +
> People, and keep the live-demo graph only under Admin. Choose this if you want maximum
> simplicity over showcasing the brain.

Sub-tabs render as a clear segmented control at the top of each grouped tab (the existing
`SubTabs` component), so the second level is always visible once you're in a tab.

---

## 3. Per-tab spec (what each screen shows + the endpoints behind it)

### 3.1 TODAY — the command center
**Replaces:** the current flat status page.
**Endpoint:** `GET /api/today/{rep_id}` (already returns more than the page shows) +
`GET /api/brain/rollups?scope=account&sort=priority` for account heat +
`GET /api/brain/activity?limit=8` for "what changed".

Sections, top to bottom:
1. **Event hero** — active/next event, tier, days-until, *one-line "why this is Tier A"*
   from `score_breakdown` (top contributing factor), big **Capture** CTA.
2. **Your next moves** (was "Follow-ups") — nudges, each with the *reason* it fired
   ("warming · 78% · 3 touches · last 14d ago · no meeting yet") + one-tap **Draft
   follow-up** / **Dismiss**. Pulls `arc_confidence`, encounter count, recency that the
   API already returns.
3. **Top targets for this event** — grouped **brief ready / needs brief**, each with
   persona + ICP-fit %; "needs brief" rows get a one-tap **Prep brief** (`POST
   /api/briefs/prep`). Uses `targets[].has_brief / persona_weight / icp_score / verified`.
4. **Account heat strip** — 3–5 warming accounts from account rollups ("Booking — 4 known,
   1 warm, met across 3 events"), linking to the People→account view. *New signal, already
   computed.*
5. **Needs your attention** — pending discovery approvals + matches needing review (keep;
   it's the one good part).
6. **What changed** (optional, small) — last few brain activity items in plain language.

Drop: the raw 20-row "recent captures" log (moves to Capture as "today's captures").

### 3.2 EVENTS — browse · plan · discover
**Sub-tabs:** Browse · Plan · Discover.

**Browse** (`GET /api/conferences`): ranked list, but each row shows *why it's tiered*
inline (top 2 score factors as chips) + an **"I cover this"** marker from
`GET /api/coverage`. Keep the live scoring tuner but move it behind a "Tune ranking"
toggle (it's a power feature, not the default view). Keep "Add event".

**Plan** (was Calendar — `GET /api/planning/coverage|clusters|gaps`): the year timeline +
clusters + gaps, but each **gap** gets an **Assign a rep** CTA and each **cluster** gets a
**Plan this trip** action (assigns reps to the bundled events). Turns a report into a tool.

**Discover** (`POST /api/discovery/conferences`, `/pending`, `/mentioned`): proposals with
the agent's *why_relevant* and a **predicted tier** before approval; "events your buyers
mentioned" filtered to untracked + mentioned-by-2+. Show progress while the agent runs.

**Event detail** (the existing rich page): keep Coverage, score-breakdown, buying
committee, agent runner, follow-ups. Fixes: (a) make the **buying committee actionable** —
add-person-manually, "met / not met" badge, last-encounter date; (b) **one-click copy / QR**
for the per-rep Telegram bind instead of manual paste; (c) surface the event's **brain
rollup** ("what happened here": arc mix, worth-returning verdict) at the top.

### 3.3 CAPTURE — not a tab; it's Telegram (field) + desk review
Capture is **not a page**. It is the field act done on Telegram, plus the desk-side review of
what came back. It shows up in three places, none of them a "Capture" nav tab:

**(a) Connect Telegram — in Settings + a Today card.** `POST /api/telegram/issue-token`.
"Send voice memos / badge photos to **@GrainSales_bot** from your phone." A **QR code +
deep link + one-tap copy**, plus a short "what you can send" list (voice, photo, shared
contact, `/fix`, `/undo`, `/next`). Per-rep bind. This is the only place the rep "sets up
capture" — once done, capturing happens entirely in Telegram, in the field.

**(b) Review what came in — on Today.** A **"From the field today"** section: the live
capture list, each row showing who/where + resolution state (new / auto-merged / **needs
review**) so the rep, back at the desk, can fix anything fast. The review queue surfaces here.

**(c) Fix a capture — on the contact/encounter (reached from Today or People).** The
**CaptureResultCard / encounter editor** with **full post-detection editing** (see §4; the
gap you flagged). This is where missing fields get filled and arc gets corrected — at the
desk, not on the floor.

**(d) Demo fallback only — "+ Capture a lead" quick-action.** Because the live-URL reviewer
may not have a public Telegram webhook, keep a **lightweight modal** (voice / badge / text)
launched from Today, labelled "desk entry — in the field, use Telegram." Secondary, not a
tab. *(Pending your call on whether to keep it at all.)*

### 3.4 PEOPLE — account-first relationships
**Sub-tabs:** Companies · Contacts · Follow-ups.

**Companies** (default — `GET /api/companies` + account rollups): accounts sorted by
**heat** (warming first), each row: name · vertical · tier · "4 known, 1 warm" · last-touch.
This is the cross-conference spine made visible. Drill-in (`GET /api/companies/{id}`) gets a
**next-step block** ("3 warm contacts, no meeting booked → draft follow-ups") and makes the
people/contacts lists actionable.

**Contacts** (`GET /api/contacts`): keep the arc filter, but add sub-sort by recency /
confidence / account tier, show ICP-fit + encounter count + last-touch per row, and
inline **copy email / LinkedIn / open** actions. Surface the **review queue** prominently
at the top when matches are pending.

**Follow-ups** (was Nudges — `GET /api/nudges`): each nudge shows its *firing reason* and
offers **Draft email** (`/api/followups/contact/{id}`) + **Accept/Dismiss**, not just text.

**Contact detail:** make the brief **copyable + re-generatable with guidance + rate inline**;
add a **gap line** ("last contact 4 months ago"); link the contact's company to the account
view; let arc override show the confidence shift without a manual refresh.

### 3.5 INTELLIGENCE — plain-language insights (reframed Brain)
**Endpoints:** `GET /api/brain/spaces`, `/rollups`, `/activity`, `POST /api/discovery/conferences`
(research), and the `query` path of `POST /api/brain/run` (for the ask box).

**Default view = insights, not internals.** No "paste a capture and watch nodes" box.
- **Warming accounts** (from relationship rollups) — "who to close".
- **Coverage gaps** (from segment rollups) — "travel: 36 events, 0 worked".
- **Events your buyers mentioned** + **discovered events worth attending**.
- **Playbook notes** — "what's working" learned from accepted nudges.
- **What we learned this week** — activity feed in plain language.

**Research is a button, not a form.** A single **"Find new events"** action (optionally
scoped by region) kicks the discovery/research agent, with a progress state while it runs
and **"last researched 3h ago · run again"** when idle. (If a scheduled/cron research run
gets wired later, this becomes **"next research in Xh"** with a manual override button.)
The point: the rep clicks one thing, the agent goes and works — no text to compose.

**The ONLY text box is an AI-native "Ask the brain"** (optional, additive) — a single
natural-language question → a grounded answer that **cites the rollups/accounts/events it
used** (via the `query` path). E.g. "Which warming accounts can I reach at a Q4 event?" →
"Booking.com and Adyen are warming; both attend Money20/20 EU (Tier A, Oct)." This is the
AI-native interface you want — a question-answer, not a node trace.

Collapsed drawer **"How the brain works"** keeps the live capture/discovery/query demo and
the graph diagram — for the video and the curious, out of the rep's way by default.

### 3.6 ⚙ SETTINGS — configure the tool (no "Admin")
One page (gear in nav), since there are no roles to gate. Sections:
- **Reps** (`/api/reps`, `/api/reps/{id}/event-links`): the roster as data + per-rep
  capture counts. The *act* of assigning reps to events lives in Events→Plan; this is just
  add/edit/remove the people. Telegram **"Send links"** becomes copy/QR, not a `<pre>` block.
- **Integrations** (`/api/settings/integrations`): keys per provider + a **Test key**
  button (reuse the `/hubspot/status` pattern) so the user knows a key works before relying on it.
- **Thresholds** (`/api/settings`): nudge/matching sliders with plain-language tooltips.
- **ICP** (read-only view of what the tool scores against) + a link to scoring calibration
  (`/api/brain/calibration`).

---

## 4. The post-detection editing gap (your specific worry)

Today `CaptureResultCard` only edits **name, company, title, email, phone**. Backend
`PUT /api/encounters/{id}` already accepts **email, phone, linkedin, vertical,
what_discussed, sentiment, meeting_requested** too. The card just doesn't expose them.

**Fix — full edit affordance on the card:**
- Add inputs/controls for **LinkedIn, vertical, what-was-said (textarea), sentiment
  (1–5 picker), meeting-requested (toggle)**, and **soft-signals** (toggle chips).
- Add an **arc override** right on the card ("Actually cooling — they're shopping Wise")
  via `POST /api/contacts/{id}/arc/override`, so the rep doesn't have to leave the page.
- Add **"add another input to this person"** (badge photo after a voice memo) so a second
  capture stitches into the same encounter instead of starting over.
- Keep it fast: collapsed by default, one tap to expand, mobile-first.

This is mostly *exposing fields the API already takes* — small code, big rep-empathy win.

---

## 5. Rollout order (when code starts)

1. **Today** (command center) — landing page becomes the battle plan + "from the field
   today" review + "Connect Telegram" card. The home of the capture-review flow.
2. **CaptureResultCard / encounter editor — full post-detection editing** (§4) + the
   Telegram connect/QR in Settings. (The "capture" work, minus the dead web tab.)
3. **People** (account-first + actionable contacts/follow-ups) — surfaces the spine.
4. **Events** (inline "why tiered" + actionable Plan/Discover; reps/coverage moved in).
5. **Intelligence** reframe (insights + "Find new events" button + AI-native ask box;
   machinery into the drawer) + **Settings** consolidation (drop "Admin", gear in nav,
   Telegram copy/QR, test-key buttons).
6. **Remove the "Capture" nav tab** and regroup nav (5 tabs) last, after the review/fix
   flows have a home on Today/People (route paths stay stable; `/capture` can redirect or
   become the demo modal route).

Each step ships independently and is verifiable in the browser. No backend changes required
for most of it (it's surfacing existing endpoints); the only likely additions are tiny
(e.g. a per-row last-touch field if not already returned).
```
