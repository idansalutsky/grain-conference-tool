# Video walkthrough — 5-7 minutes

Two of five evaluation axes are sales empathy and communication. The
demo carries them. The script below is timed for **6 minutes**.

---

## 0:00 — 0:20 — Open with the brief, not the tool

> "Grain doesn't sell to fintechs. Grain sells to **businesses with heavy
> cross-border transaction volume** — PSPs, travel platforms, marketplaces —
> whose CFO or Treasurer is paying for FX spread leakage. So the right
> question isn't 'is this a fintech event'. It's 'will the right finance
> buyers be in the room?' That re-framing is the spine of the tool."

Show: the Conferences page, top of the list.

---

## 0:20 — 1:30 — DECIDE: scoring + the buyer-density wedge

> "Money20/20 USA is Tier A at the top — and Grain already knows it goes there,
> so the tool *agrees with a decision they've made*. The interesting part is
> what sits right beside it: treasury-pure events like EuroFinance and AFP score
> Tier A **over bigger, generic fintech shows** — because the score weights
> finance/treasury buyer density, not raw attendance. That's the Grain wedge."

Click into the top event. Show the 7-factor score breakdown:

> "Each factor shows raw 0-1 score, weight, and an evidence string. The top
> event gets 1.00 on vertical concentration, evidence 'core vertical: payments'.
> **Glass-box — the sales lead can argue with it factor by factor.** And the
> weights are sliders in Settings, so they tune it, not me."

Important nuance to mention:
> "The scoring path is deterministic. No LLM. Defensibility > sophistication.
> The 7th factor — historical_yield — is opt-in at weight 0.0 until we have
> Year-1 data; once we do, it becomes the dominant factor and the others
> become priors."

Scroll to the event's target list (the strongest moment — real people):
> "And it doesn't just say *go* — it says *who to find*. This is real scraped
> data: at Money20/20 Europe the tool surfaces the CFOs of Klarna, Stripe,
> Revolut, Wise, Mollie. At a travel event like Phocuswright it surfaces the
> CFOs of Booking Holdings, Trip.com, Hilton, Hyatt — exactly Grain's heavy-FX
> travel buyers. Each is scored and tagged by buying role: BUYER, the
> ENTRY_POINT who staffs the booth, the CHAMPION. The rep walks in with a
> hit-list, not a badge scanner."

---

## 1:30 — 2:30 — PLAN: coverage + clusters + gaps

Switch to Planning page.

> "Coverage by month — green is tier-A. We're under-invested in March, where
> only Phocuswright is on the calendar."

Scroll down:

> "Trip clusters: events in the same region within a 3-week window become one
> swing, with an estimated flight saving vs. flying separately. The detection
> is deterministic — same geo cluster, ≤ 21 days apart — so I can defend every
> grouping." (Read the actual top swing + saving off the screen.)

Then gaps:

> "And we flag tier-A events with zero rep encounters. That's not a
> 'recommendation' — it's a question. Why aren't we covering this?"

Open an event → the Coverage block (and the Team tab):

> "This is the 'who covers what' from the brief. On any event I assign reps —
> here Marc, Diana and Ben are on Money20/20 Europe — and each gets a one-tap
> Telegram link. The rep redeems it once on their phone, and from then on every
> voice memo they send auto-tags to this event. The Team tab is the no-code
> admin: a non-developer adds reps, creates events, and assigns coverage — no
> code, which is exactly what the brief asked for."

---

## 2:30 — 4:00 — EXECUTE: field capture (the hero feature)

Switch to Capture page.

> "This is what the rep has in their hand on the show floor. One big mic
> button. Or text, if they're in a noisy hall. Watch."

Click "Tap to record" and speak naturally (use a *net-new* name so it doesn't
merge into a seeded contact):

> "Just met Priya Nair, Head of Treasury at Razorpay. Running treasury for
> their cross-border merchant payouts — mentioned real FX spread leakage on
> multi-currency settlement. Wants to talk next week."

Stop. It transcribes in-browser, then the AI structures it in a couple seconds.
The result card shows name / title / company / vertical / sentiment /
soft_signals / meeting_requested — and, because it's a first encounter, "new
contact created".

> "Two things matter here. One: speed — I tapped once, talked, and got clean
> structured CRM data, no form. Two: if I'd met this person before, the card
> would immediately show what we already know — the prior arc and any nudge —
> so the rep recognises a returning contact *on the floor*, not back at the
> hotel."

---

## 4:00 — 5:00 — RECOGNIZE: cross-conference matching + arc

Switch to Contacts. Open **Daniel Roth** (tire_kicker).

> "Daniel's shown up four times across a year of conferences — Money20/20
> Europe, EuroFinance, Money20/20 USA, Payments Leaders — never asked for a
> meeting, mostly lukewarm. The engine called him a *tire-kicker*. He's
> listening, not buying. Don't burn a rep's calendar on him — and notice the
> nudge stays **silent** here on purpose."

> "The verdict isn't a count. A deterministic rule ran first — 4 encounters,
> ~320-day span, zero meetings, lukewarm signals — and that's the ground truth.
> An LLM judge can only *lift confidence* when it agrees; when it disagrees we
> keep the deterministic call. Honest about where AI helps and where it doesn't."

Open **Lena Novak** (warming, nudge firing).

> "Lena is the opposite, and she's the hard edge case. She showed up as Finance
> Manager at Acme Pay — email and company both changed by the third encounter,
> she's now **Head of Treasury at Wise**. Different email, different company —
> the only thing that stayed constant is her LinkedIn, and that's how the
> resolver kept her as one person. The nudge fires *and* flags the job change
> into an ICP buyer role. That promotion is a buying signal."

(Also open **Sarah Cohen** to show a clean name-variant merge: captured as
"Sarah" and "Sara", merged into one warming contact with a firing nudge.)

---

## 5:00 — 5:20 — Push to HubSpot (the judgment travels with the contact)

On Lena's (or Sarah's) contact page, click "Push to HubSpot".

> "The push doesn't just carry name and email. It carries the intelligence —
> grain_arc_verdict, grain_arc_summary, grain_nudge_text, grain_followup_draft —
> as custom HubSpot properties. Without a token it shows the exact dry-run
> payload; with one, it upserts by email. Either way the judgment travels with
> the contact. **It doesn't die in our tool.**"

## 5:20 — 5:50 — AI feature spotlight: conference discovery

Switch to the Discovery tab. Pick region = EU. Click "Discover new events".

> "This is the brief's example AI feature suggestion — finding events we
> don't know about. Perplexity Sonar returns real conferences with real
> citations. **You can click the source URL on every proposal.** Approve
> one → it joins the main list, auto-scored against the 7 factors. **The
> AI surfaces; the human approves; the deterministic scorer validates.**
> Three layers — and the rep can audit every step."

(I'll show this discovering 3-4 events in ~5 seconds.)

## 5:50 — 6:00 — Pre-event prep + what I'd build next

> "If I had another week:
> 1. **Year-1 outcome data → real historical_yield**. The 7th factor is the
>    only true closed loop; we need a quarter of usage to make it meaningful.
> 2. **Auto-rebalance the trip plan when a high-value lead surfaces mid-event** —
>    the planner runs once today; it should react to live captures.
> 3. **Coaching reasons in the silent nudge**. Today we tell the rep "why
>    suppressed". Next: surface "and here's what would change the verdict" —
>    a coaching layer, not just an explanation."

Close:

> "One spine: ICP-as-config feeds scoring, planning, capture interpretation,
> arc, nudge, brief, and HubSpot. Honest where AI helps. Defensible at every
> layer. Scrappy enough that a salesperson would actually use it."

---

## Defensive answers for Q&A

| Q | A |
|---|---|
| Why SQLite, not Postgres? | The brief says "non-developer can host". SQLite = single file, no infra. 1-hour swap to Postgres if it scales. |
| Why one LLM AI feature (arc judge) vs many? | Many AI features risks looking like AI-stuffing. The arc judge solves the *specific* hardest ask in the brief: "is this a warming relationship or a tire-kicker?". The rest are deterministic. |
| What's the deterministic fallback under every LLM call? | Brief synthesis has a `_fallback_brief`. Arc judge falls back to the deterministic classifier on LLM failure. Voice extraction has no fallback — but the web UI lets the rep type instead. |
| How did you decide the scoring weights? | Defaults are priors from the ICP wedge (vertical + buyer + FX = 0.70). They're sliders. Year-1 usage validates; historical_yield then takes over. |
| What about two real Maria Garcias at Booking.com? | The resolver caps confidence at 0.75 when both sides have different emails — drops to "review_needed". Tested in `test_entity_resolution.py::test_name_collision_different_emails_review_only`. |
