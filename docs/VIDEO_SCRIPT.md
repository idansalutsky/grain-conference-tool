# Video walkthrough — ~7 minutes

Two of the five evaluation axes are **sales empathy** and **communication** — the
demo carries them. This script is timed for ~7 minutes. For a tight 6-minute cut,
drop the *Intelligence/brain* beat (6:00) — it's the one beat that's "engineering
credential" more than "salesperson value," so it's the safe thing to trim.

Everything below matches what's actually in the product and seed data (re-verified
end-to-end). Don't claim more than the screen shows.

---

## 0:00 – 0:20 — Open with the brief, not the tool

> "Grain doesn't sell to fintechs. Grain sells to **businesses with heavy
> cross-border volume** — PSPs, travel platforms, marketplaces — whose CFO or
> Treasurer is bleeding money on FX spread. So the right question isn't 'is this
> a fintech event'. It's '**will the right finance buyers be in the room?**' That
> reframing is the spine of the whole tool."

Show: the **Events** page, top of the list.

---

## 0:20 – 1:30 — DECIDE: scoring + the buyer-density wedge

> "Top of the list: **EuroFinance** and **AFP** — pure-treasury events — out-rank
> **Money20/20**, which has six times the attendees. And a 45,000-person
> **Dreamforce** sits all the way down in Tier C. Why? The scorer ranks on
> **buyer density, not raw size**: EuroFinance is ~75% finance/treasury;
> Dreamforce is huge but almost no treasury buyers. A 300-person room full of
> Treasurers beats a stadium of the wrong people. Money20/20 still lands Tier A —
> a call Grain's already made — just not at the very top."

Click into the top event → the **7-factor score breakdown**:

> "Each factor shows its raw 0–1 score, weight, and a plain-English evidence
> string — 'core vertical: treasury', 'reachable committee: 75% finance'. It's a
> **glass box**: the sales lead can argue with it factor by factor. And the
> weights are sliders in Settings, so they tune it — not me."

Click a **−2** on the score, type a reason, **Apply**:

> "I can override the score with a reason — it's logged and auditable, and the
> override survives a re-score. Over time those overrides even nudge the weights
> automatically, within guardrails."

Nuance to say out loud:

> "The whole scoring path is **deterministic — no LLM**. Defensibility beats
> sophistication here. The 7th factor, historical-yield, is opt-in at weight zero
> until we have a year of real outcome data."

On the event's **buying committee** list, lead with honesty:

> "It doesn't just say *go* — it suggests *who to approach*, seeded from public
> speaker and sponsor data. The ones marked **✓ are verified**; the rest are
> flagged **'verify before you approach'** — because public attendee data goes
> stale fast. I kept this layer deliberately light: in production this is a
> **Clay or Apollo enrichment waterfall**. The point of the tool isn't the
> contact list — it's everything the engine does *with* a contact once a rep
> actually meets them."

---

## 1:30 – 2:30 — PLAN: coverage, clusters, gaps, and the field handoff

Switch to **Calendar** (planning):

> "Coverage by month — tier-A in the strong colour. We're under-invested where
> only one event is on the calendar. **Trip clusters**: events in the same region
> within a three-week window become one swing — that's a deterministic rule, same
> geo, ≤21 days, so I can defend every grouping. And we flag tier-A events with
> **zero rep coverage** — that's not a recommendation, it's a question: why
> aren't we there?"

Open an event → **Coverage** block, then the **Team** tab:

> "This is 'who covers what' from the brief. On any event I assign reps, and each
> gets a **one-tap Telegram link** — redeem it once on the phone, and from then on
> every memo they send auto-tags to that event. No code: a non-developer adds
> reps, creates events, assigns coverage."

Show the **📤 Send links** button on a rep in Team:

> "And here's the manager's real workflow: plan the season, then **one click
> generates a paste-ready message** with that rep's assigned events and their
> connect link — drop it in WhatsApp, they tap once, they're capturing. The
> planner and the field rep, joined by one link."

---

## 2:30 – 3:45 — EXECUTE: field capture (the hero feature)

Switch to **Capture**:

> "This is what the rep holds on the show floor. One big mic button, or type if
> the hall's loud. Watch." 

Tap record, speak naturally (use a **net-new** name so it doesn't merge):

> "Just met Priya Nair, Head of Treasury at Razorpay — running their cross-border
> merchant payouts, real FX spread leakage on multi-currency settlement, wants to
> talk next week."

Stop. It transcribes in-browser, then the AI structures it in a couple seconds —
name / title / company / vertical / sentiment / signals / meeting-requested, and
"new contact created."

> "Two things matter: **speed** — one tap, talk, clean structured data, no form.
> And if I'd met this person before, the card would instantly show what we already
> know — the prior arc and any nudge — so the rep recognises a returning contact
> *on the floor*, not back at the hotel."

Then show the **end-of-event wrap** (Telegram, or describe it):

> "And when the event's over, the rep just texts the bot **'done'**. Back comes a
> wrap-up: everyone they captured here, **ready-to-send follow-up drafts** for the
> ones worth chasing — tire-kickers filtered out — and any live nudges. The
> drafts already reference the event and what was actually discussed. That's the
> difference between a tool a rep tolerates and one they'd actually use."

---

## 3:45 – 5:15 — RECOGNIZE: cross-conference matching (the hardest ask)

Switch to **People / Contacts**. Open **Daniel Roth** (tire-kicker):

> "Daniel's shown up four times across a year — Money20/20, EuroFinance, Payments
> Leaders — never asked for a meeting, mostly lukewarm. The engine calls him a
> **tire-kicker** and the nudge stays **silent on purpose**. Don't burn a rep's
> calendar on a polite listener."

> "And the verdict isn't a count — a deterministic rule runs first: four touches,
> ~320-day span, zero meetings, lukewarm. An LLM judge can only *raise* confidence
> when it agrees; when it disagrees, we keep the deterministic call. Honest about
> where AI helps and where it doesn't."

Open **Lena Novak** (warming, nudge firing):

> "Lena's the opposite — and the hard edge case. She first showed up at Acme Pay,
> and by a later event she'd moved to **Head of Treasury at Trip.com** — an ICP
> travel customer. Different company, but the resolver kept her as **one person**
> and the nudge fires *and* flags the job change into a buyer role. A promotion
> into treasury at an ICP account is a buying signal, not noise."

Now the **Review Queue** (top of Contacts) — the strongest correctness story:

> "Here's what I'm most proud of: the engine knows **when it isn't sure**. Two
> people at Booking — '**Sarah Chen**' and '**Sarah Cohen**' — similar name, same
> company, but different emails. A naive matcher merges them and you've corrupted
> two relationships. This one **refuses to auto-merge** and asks me: same person,
> or different? Same with a transliteration — '**Patrick Janet**' vs '**Patrick
> Janý**'. Decisive keys — email, LinkedIn, phone — auto-merge; a name guess never
> does. **I'd rather ask than be wrong.**"

(Edge cases to mention if asked: nicknames fold — Bill ↔ William — but a nickname
*plus* a company change goes to review, not a silent split; a bare first name
never auto-merges; umlauts and rebrands are handled.)

---

## 5:15 – 5:40 — ACT: push to HubSpot (the judgment travels)

On Lena's contact, click **Push to HubSpot**:

> "The push carries the **intelligence**, not just name and email —
> `grain_arc_verdict`, `grain_arc_summary`, `grain_nudge_text`,
> `grain_followup_draft` as custom properties. No token? It shows the exact
> dry-run payload. With one, it upserts by email. Either way the judgment **lands
> in the CRM the team already lives in — it doesn't die in our tool.**"

---

## 5:40 – 6:00 — AI feature spotlight: conference discovery

Switch to **Events → ✨ Find new** (Discovery). Region = LATAM, **Discover**:

> "The brief's example AI feature — finding events we don't know about. Grounded
> search returns real conferences with **clickable source URLs**. Approve one → it
> joins the list, auto-scored against the same 7 factors. **AI surfaces, the human
> approves, the deterministic scorer validates.** Every step auditable."

---

## 6:00 – 6:40 — Intelligence: the quality gate behind the features (optional beat)

Switch to the **Intelligence** tab (the brain).

> "Everything so far is per-feature AI. This is the layer that keeps team memory
> from filling up with junk. The single best thing it does: **a quality gate**.
> Watch — I drop in 'met the CFO of [a competitor] at Money20/20.' The gate
> **rejects it** — off-ICP, a competitor, not worth remembering. Memory is only
> as good as what you refuse to write to it."

> "It also gets smarter from corrections: when reps override scores, the weights
> re-tune within guardrails. Under the hood it's a small agent graph with durable
> checkpoints — there's a 'how it works' section if you want the internals — but
> the value a sales lead cares about is just this: **it remembers the right
> things, and learns from the team.**"

(Don't over-narrate the memory-tier internals — the gate and the learning loop are
the demoable value.)

---

## 6:40 – 7:00 — What I'd build next + close

> "Another week, three things:
> 1. **Year-1 outcome data → the historical-yield factor goes live** — the only
>    truly closed loop; it needs a quarter of real usage.
> 2. **Live trip re-planning** — when a high-value lead surfaces mid-event, the
>    planner should react, not just run once.
> 3. **A coaching layer on the silent nudge** — not just 'why we stayed quiet',
>    but 'here's what would change the verdict.'"

Close:

> "One spine: ICP-as-config feeds scoring, planning, capture, the arc, the nudge,
> and HubSpot. Honest about where AI helps. Defensible at every layer. And scrappy
> enough that a salesperson would actually open it on a show floor."

---

## Deploy note (if they ask "how do we host it?")

> "One command. It's a single Docker image that serves the API and the app at one
> URL — push to GitHub, point Render at it, done. Boots with **zero API keys** in
> demo mode; keys are configurable in-app, never hardcoded. 213 tests, one SQLite
> file, no standing infrastructure."

---

## Defensive answers for Q&A

| Q | A |
|---|---|
| Why SQLite, not Postgres? | The brief says "a non-developer can host it." SQLite = one file, no infra. ~1-hour swap to Postgres if it scales. |
| Two real Sarah/Maria's at the same company? | The resolver **refuses to auto-merge** on a name+company guess when emails differ — it routes to the Review Queue. Decisive keys (email/LinkedIn/phone) are the only things that auto-merge. Covered live, and in `test_entity_resolution.py`. |
| Nickname *and* a job change at once? | Nicknames fold (Bill→William), but a nickname + a company change is deliberately sent to **review**, never silently split — that combo is exactly where a false merge or false split hurts most. |
| Why so few "AI features," and only one LLM in the matcher? | AI is used only where synthesis is the right tool — voice→lead, the arc judge, discovery, the brief, the prep agent. The scoring and matching cores are deterministic so they're defensible. AI-stuffing was a deliberate non-goal. |
| Where's the people data from / how real is it? | Public speaker/sponsor sources, kept deliberately light and flagged ✓-verified vs not. The honest production answer is a Clay/Apollo enrichment waterfall — the contact list is the *weakest* layer and I'd rather say so than oversell it. The strength is the engine. |
| What runs without API keys? | Everything on Decide / Plan / Recognise (deterministic + seeded). Text capture even falls back to a regex extractor keyless. Only *new* voice/photo/discovery/brief generation needs a key. |
