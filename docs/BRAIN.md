# The Grain Brain — an orchestrated memory loop for sales/BD

The per-feature AI (`docs/AI_STRATEGY.md`) answers *"where does a model help in
one screen?"*. The **Grain Brain** answers the altitude above that: *"how does an
AI-native sales tool turn the messy reality a rep lives in into memory the team
can act on — without overflowing?"*

It is one LangGraph `StateGraph`, five namespaced memory "spaces", and a hard
quality gate. Not one giant prompt. This doc is the doctrine, the
implementation, how to seed/feed/iterate it, and an honest scope line.

---

## 1. The doctrine — a four-stage loop

AI in sales/BD is not a chat box bolted onto a CRM. It's a loop that runs every
time reality produces something unstructured:

```
CAPTURE → FILTER → COMPRESS → SURFACE/RESEARCH
 reality   gate    memory       answer
```

**CAPTURE — unstructured reality in.** A rep mutters a voice memo, types a note,
or asks "find events we don't already cover." Reality is freeform; the first job
is to *take it as-is* rather than force a form.

**FILTER — the quality gate, before anything enters memory.** This is the most
important stage, and the one that's usually missing. The prior data layer in
this project *overflowed* precisely because nothing gated for fit: a scrape
dumped ~3× more "targets" than were real, including stale CFOs and competitor
names. The lesson is doctrinal — **memory is only as good as what you refuse to
write to it.** The gate asks three questions of every candidate: is it *real*
(has substance / a source), is it *ICP-fit* (not a competitor, not an off-ICP
vertical), and is it *new* (not a known duplicate)? Only `accept` survives.

**COMPRESS — into evolving memory, summaries not transcripts.** Accepted items
go into long-term memory, but a brain that keeps every raw transcript overflows
just as badly as an ungated scrape. So each memory space carries **one rolling
summary** that is re-compressed as items accumulate. Consumers read the summary,
not the pile. "Don't overflow the brain" is enforced in code, not hoped for.

**SURFACE / RESEARCH — over structured memory.** Questions are answered against
the compressed summaries. Discovery *reads memory first* (what do we already
cover, where are the gaps) so "find events we don't know about" becomes
gap-targeted and exclusion-aware instead of a blind web search.

### Why this shape, and not one mega-prompt

This is how AI-native BD tooling is actually built once it leaves the demo:

- **Orchestrated agent graph, not a monolith.** Each step is a small, testable
  node with its own fallback. You can trace which path an input took and fix one
  node without touching the rest. A single prompt that "does everything" is
  un-debuggable and silently drifts.
- **Namespaced long-term memory.** Real BD memory is heterogeneous — who we
  target, where we go, what works, where we're thin, who we know. Cramming that
  into one blob loses the structure that makes it queryable. Separate spaces with
  separate compression keep each one bounded *and* meaningful.
- **Human gates on consequential writes.** Sales judgment is high-stakes; the
  system proposes and the human disposes on anything that enters shared memory.
  The discovery path literally pauses for approval before writing.

---

## 2. The implementation

### One StateGraph, three subgraphs (`brain/graphs.py`, `brain/nodes.py`)

A single `StateGraph(BrainState)` (`brain/state.py` — a flat, JSON-serialisable
`TypedDict`) holds the whole loop. A **classifier node** routes each input via
conditional edges into the matching subgraph:

```
START → classify ─┬─(unstructured_capture)→ extract → resolve → arc →
                  │                          compress_capture → gate →
                  │                          memory_writer → END
                  ├─(discover_events)→ read_context → search → propose →
                  │                    approval_gate (interrupt) → gate →
                  │                    memory_writer → END
                  └─(query)→ query → END
```

`classify_node` picks one of `unstructured_capture | discover_events | query`.
It uses an LLM when `OPENROUTER_API_KEY` is present and a deterministic keyword
classifier otherwise — so the graph runs hermetically (no key, no network),
which is what the tests rely on. Every node appends its own name to
`state["trace"]`, so the API response shows exactly which path an input took.

**Capture chain** (`extract → resolve → arc → compress_capture → gate →
memory_writer`):
- `extract` structures the freeform note into a lead (reuses the same
  `llm.text_to_lead` path as live voice capture; deterministic regex fallback).
- `resolve` entity-resolves the person against existing contacts —
  *read-only*; the brain is an analysis layer and the live `voice.py` pipeline
  still owns encounter persistence.
- `arc` attaches a relationship-arc verdict (warming / flat / cooling), using the
  real arc classifier for resolved contacts and a single-touch heuristic for
  net-new ones.
- `compress_capture` distils everything to **one salient insight** plus a
  salience weight — the compression happens before memory, not after.

**Discovery chain** (`read_context → search → propose → approval_gate →
gate → memory_writer`):
- `read_context` pulls the ICP summary, the gaps, and the known-event signature
  set (from the `events` space *and* the live `conferences` table).
- `search` finds events **targeting the gaps**, excluding what's known. Every
  proposal passes a **recency guard** (`datetime.date.today()`): an event we can
  prove already happened is dropped, so discovery never resurfaces a past-dated
  conference. Without a search key the deterministic fallback emits a single,
  clearly-labelled placeholder asking the operator to configure a key (the gate
  routes it to review).
- `propose` assembles candidates with stable proposal ids.
- `approval_gate` is the **human-in-the-loop interrupt** (see below).

**Query** is a single node that answers over the space summaries.

### Human-in-the-loop interrupt (`approval_gate_node`)

The discovery path *pauses* at `approval_gate` via LangGraph's `interrupt()`.
First time through it raises a `GraphInterrupt` whose payload (the proposals)
surfaces to the API as `status: "awaiting_approval"`. On resume with
`Command(resume={"approvals": [...]})`, `interrupt()` *returns* the human's
decisions and the node continues into the gate. Nothing reaches memory until a
human has approved.

### The gate — the filter (`gate_node`)

Applied to **both** capture insights and discovered events, producing
`accept | review | reject` with a reason per candidate:

1. **REAL** — discovered events need a `source_url`/citation (no source →
   `review`); captures need at least an insight or a company.
2. **ICP-FIT** — reject competitor-branded events and contacts who work for a
   Grain competitor; off-ICP verticals fall to `review`. Competitors and target
   verticals are read from the single `IcpConfig.default()` — the same ICP object
   the rest of the product scores against.
3. **NEW** — reject events whose name-signature is already known (dedupe).

On the discovery path the gate also honours the human's call: an explicit reject
always wins; an explicit approve lifts a borderline `review` to `accept`.

### The memory writer — only `accept` survives (`memory_writer_node`)

Iterates the gate decisions and writes **only** accepted items to spaces, then
re-summarises every touched space. Discovered events → `events`; captures →
`relationship`; a strong warming + meeting-requested capture also logs a win to
`playbook`. Rejected/review items never enter long-term memory.

### Compression that keeps memory bounded (`brain/spaces.py`)

A space is a namespace of items **plus one rolling summary**. Storage is two
SQLite tables (`brain_memory`, `brain_space_summary`). `write_item` upserts by
`(space, item_key)`. Two mechanisms keep a space bounded:

- **Bounded raw store (pruning).** The raw `brain_memory` rows for a space are
  hard-capped at `_MAX_ITEMS_PER_SPACE` (50). When a write pushes a space over
  the cap, the lowest-salience / oldest rows beyond the cap are pruned (keep
  top-N by salience then recency). The summary alone is no longer the only thing
  bounded — the raw store can't grow unbounded either.
- **Throttled re-summarise.** Re-compression fires when the count crosses an
  early Fibonacci threshold (`{1,2,3,5,8,13,21,34,55}` — frequent early so a
  small space always has a fresh summary) *or* every `_RESUMMARY_EVERY_N` (8)
  new items once large — **not on literally every new key.** A busy capture
  stream therefore does not pay an LLM re-summary on every single contact. (A
  consequence: a space's recorded `item_count` is a snapshot from the last
  re-summary and may trail the live raw count slightly — by design.)

Compression is LLM-prose (150–250 words, most-salient-first) when a key is
present, and a bounded deterministic top-N join otherwise. Either way the
summary is bounded by construction — *that*, plus pruning, is what stops the
brain overflowing.

### The five spaces

| Space | Holds | Seeded from | Fed by |
|---|---|---|---|
| `icp` | verticals, buyers, competitors, FX-exposure signals | `icp.py` (the brief's ICP) | seed + **human ICP/persona/arc overrides & prospect approve-reject** (`feedback:*`) |
| `events` | conference distribution + known-event signatures (dedupe set) | the `conferences` table | accepted discovery proposals + **human score overrides & discovery approve/reject** (`feedback:*`) |
| `gaps` | thin verticals / thin regions (where to go) | computed from the conference distribution | (recomputed from events) |
| `playbook` | what works in outreach | a starter heuristic + warming captured contacts | strong warming captures (brain path + real field captures) + **nudge accept/dismiss** (`feedback:*`) |
| `relationship` | salient, compressed insights about specific people/accounts | **synced from the real `contacts`/`encounters` table** (arc-verdict captures), one compressed insight per contact | accepted brain-path captures + `ingest_encounter` / `sync_relationship_space_from_db` |

### Infra & observability

- **SQLite checkpointer.** The graph is compiled with a `SqliteSaver` over the
  app's *existing* SQLite DB file. The paused discovery run is durably persisted
  under its `thread_id` and can be resumed in a separate request/thread.
  **No new infrastructure** — same one file the rest of the app uses, which keeps
  the deploy simple (the same "non-developer can host" constraint the whole
  product honours).
- **LangSmith tracing** is supported and **env-gated**: set
  `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_API_KEY` and every graph run is
  traced. No keys in code; absent the env vars it's a no-op.

### The API + Brain UI (`api/routers/brain.py`)

```
GET  /api/brain/spaces        list the 5 spaces (summary + counts)
POST /api/brain/sync          rebuild relationship/playbook from real captures
GET  /api/brain/space/{name}  items + summary + provenance for one space
POST /api/brain/run           run the graph (capture / discover / query)
POST /api/brain/resume        resume an interrupted discovery run with approvals
GET  /api/brain/graph         static node/edge description (for visualization)
```

Every endpoint is robust without an LLM key (deterministic fallbacks throughout).

---

## 3. How to seed, feed, and iterate

**Seeding** is idempotent (`spaces.seed_brain_spaces()`, called from
`seed_db.main()` — re-runnable because writes upsert):
- `icp` ← the brief's ICP (`icp.py`): verticals, target titles, competitors, FX
  signals.
- `events` ← summarised distribution of the `conferences` table, *plus* the set
  of known-event name-signatures used for dedupe.
- `gaps` ← **computed** from that distribution: verticals/regions with ≤1–2
  events are flagged as under-covered. The gaps aren't guessed; they fall out of
  the real conference data.
- `playbook` ← a minimal starter heuristic (plus a "what works" signal for every
  warming captured contact picked up by the relationship sync).
- `relationship` ← **synced from the real `contacts`/`encounters` table.**
  `seed_brain_spaces()` calls `sync_relationship_space_from_db()`, which reads
  the genuine captured contacts (with the arc verdicts the engine produced from
  real encounter history) and folds **one compressed insight per contact**
  (provenance `capture:field`) into the space. This is the fix that makes the
  brain sit *on top of* the real capture pipeline rather than beside it: after a
  full seed the relationship space shows the actual demo people (e.g. the
  warming Sarah Cohen / Mike Schmidt, the tire-kicker Daniel Roth, the cooling
  Tom Becker) — not just brain-path test items. The sync is idempotent (it
  clears and rebuilds its own `capture:field` rows). `POST /api/brain/sync`
  refreshes it on demand; `ingest_encounter()` folds a single contact.

**How a real capture reaches the brain.** Two complementary paths feed
`relationship`:
- **brain path** — running text through `POST /api/brain/run` (classify →
  extract → resolve → arc → compress → gate → memory_writer), provenance
  `capture:brain`. This is the live, single-input demo of the loop.
- **field path** — `sync_relationship_space_from_db()` / `ingest_encounter()`
  reflect the contacts the *real* voice/text/badge capture pipeline already
  persisted (the `contacts`/`encounters` tables), provenance `capture:field`.
  This is what keeps the brain coherent with the rest of the product.

**Feeding / iteration signal — the human-action learning loop is LIVE:**
- discovery **approve/reject** at the interrupt directly decides what enters the
  `events` space (and reject always wins at the gate) — **LIVE**;
- real field captures flowing into the relationship/playbook spaces via the sync
  — **LIVE**;
- **every human decision logged to the `feedback` table is now folded into the
  brain as one compressed knowledge item** — **LIVE.** `db.log_feedback()` calls
  `spaces.ingest_feedback()` (best-effort, lazy-imported, wrapped so feedback
  logging never fails on a brain error). Each write carries provenance
  `feedback:<decision_kind>` and a salience reflecting signal strength, and rides
  the same `write_item` prune + throttled-resummarize machinery as every other
  space — so the spaces stay bounded. The brain CONSUMES the feedback table now;
  it no longer just audits it. The exact mapping:

| Human decision (`decision_kind`) | Source | Space | Compressed knowledge written |
|---|---|---|---|
| `conference_score_adjust` | conferences router (`scoring.set_score_override`) | `events` | "Rep adjusted event X up/down to score N (tier T) — model said M. Reps value this event more/less." |
| `conference_discovery_approved` | `discovery.approve_proposal` | `events` | "Reps WANT <vertical>/<region> events — find more like '<name>'." |
| `conference_discovery_rejected` | `discovery.reject_proposal` | `events` | "Reps SKIPPED <proposal> — down-rank similar discoveries." |
| `nudge_accept` | nudges router | `playbook` | "Reps ACT on nudges — keep surfacing this situation." |
| `nudge_dismiss` | nudges router | `playbook` | "Reps IGNORE '<situation>' nudges — tune down." |
| `rep_match_confirmed` | `review_queue.confirm_match` | `relationship` | "Rep CONFIRMED encounter is the same person across events — merge validated." |
| `rep_match_rejected` | `review_queue.reject_match` | `relationship` | "Rep SPLIT encounter into a new contact — the model's match was wrong." |
| `people_score_override` / `arc_override` | people / contacts routers | `icp` | "Rep marked person X as <persona/score/arc> — better fit than the title classifier." |
| `prospect_discovery_approved` | `prospect_discovery.approve` | `icp` | "Rep marked company X as ICP-fit (tier T)." |
| `prospect_discovery_rejected` | `prospect_discovery.reject` | `icp` | "Rep marked company X as NOT ICP-fit." |

  Audit-only kinds the brain does **not** learn from (the auto `entity_resolution`
  verdicts, `brief_rate`, `parameter_update`, `person_added/deleted`, `rep_added`,
  the discovery *proposal* logs) are explicitly ignored (`ingest_feedback` returns
  `None`). What's compressed is honest: one short salient line + structured fields
  per decision, **not** the raw before/after blob — the rolling summary then
  re-compresses the space as these accumulate.

**Gap-targeted, exclusion-aware discovery.** Because `read_context` loads ICP +
gaps + the known-event set *before* searching, the "find events you don't know
about" feature stops being a blind query. `search` aims at thin verticals/regions
and the gate rejects anything already known — discovery becomes *targeted* and
*deduped* rather than noisy.

---

## 4. Honest scope

Under-claim rather than over-claim. This was deliberately scoped to **one
coherent slice** — a working capture→filter→compress→surface loop — rather than a
half-built "AI platform."

**LIVE now:**
- the LangGraph `StateGraph` with classifier routing and the three subgraphs;
- the five seeded, compressing memory spaces (two SQLite tables);
- **the brain reflects the real capture pipeline:** the `relationship` (and
  `playbook`) spaces are synced from the genuine `contacts`/`encounters` table
  (arc-verdict captures) via `sync_relationship_space_from_db()` /
  `ingest_encounter()` (provenance `capture:field`), exposed at
  `POST /api/brain/sync` and run from `seed_brain_spaces()`. The brain is no
  longer a parallel universe to the field captures;
- **the human-action learning loop is closed:** every `db.log_feedback()`
  decision is folded into the matching space as a compressed `feedback:<kind>`
  item via `spaces.ingest_feedback()` — score overrides & discovery approve/reject
  → `events`, nudge accept/dismiss → `playbook`, rep-match confirm/reject →
  `relationship`, persona/arc/ICP overrides & prospect approve-reject → `icp`. The
  spaces visibly get smarter as reps use the tool (see §3 for the full mapping);
- the **capture** path (extract → resolve → arc → compress) end-to-end;
- the **discovery** path (read_context → search → propose) end-to-end, with a
  **recency guard** so it never proposes a past-dated event;
- the **gate** (real / ICP-fit / new) on both paths;
- **bounded memory:** rolling-summary compression with *throttled* re-summarise
  **and** a hard-capped + pruned raw item store (not just a bounded summary);
- **HIL** approve/reject via LangGraph `interrupt()` + durable SQLite-checkpointed
  resume;
- the **API** (`/api/brain/*`) and the **Brain UI** that drives it;
- hermetic deterministic fallbacks on every LLM node;
- LangSmith tracing via env.

**ROADMAP (named, not stubbed):**
- the human-action learning loop is now **closed** (see §3): `db.log_feedback()`
  → `spaces.ingest_feedback()` folds every human decision into the matching space
  as a compressed `feedback:<kind>` item. What remains is *weight* learning —
  turning the accumulated `feedback:*` items into adjusted scoring weights (e.g.
  the historical-yield factor that sits at weight 0 in scoring). Today the
  knowledge lands in the spaces and surfaces in the rolling summaries; it does
  not yet auto-tune the numeric scorer;
- more capture sources into the graph (e.g. the Telegram channel, async batch);
- semantic retrieval over the spaces (today retrieval is summary + keyword;
  embeddings would let `query` and `read_context` pull the most relevant items,
  not just the rolling summary).

The line is deliberate: a clean, honest slice that demonstrates the doctrine
beats a broad surface where half the wires are loose.
