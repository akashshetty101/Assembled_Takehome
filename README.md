# Intraday Notification System

A rule-driven notification system for contact center operations: team leads author rules
against live queue and agent state, and the same event stream lands in different
inboxes — a team lead watching an SLA, an agent watching their own adherence, an
ops channel watching for anything severe.

For the phase-by-phase build log (what was built, why, every bug found and how it was
verified), see [`DEVELOPMENT_LOG.md`](./DEVELOPMENT_LOG.md). This document is the
outward-facing summary: decisions, tradeoffs, and how to run it.

## 1. Who this is for

The **team lead is the rule author**. Every rule has a `created_by` and a `recipient`
that resolves one of three ways:

| recipient kind | resolves to | valid when |
|---|---|---|
| `author` | the rule's creator | always |
| `subject_agent` | the agent the episode is about | agent-scoped rules only |
| `channel` | a named channel (e.g. `#ops-alerts`) | target is set |

A team lead sets up "notify me when billing breaches SLA" (`author`) *and* "notify each
agent when they personally drift out of adherence" (`subject_agent`) *and* "post
anything severe to `#ops-alerts`" (`channel`) — three rules, one evaluator, three
different inboxes. Agents receive notifications as a **consequence of routing** a lead
configured, not through a separate product surface. There is no second UI for agents to
manage.

**Head of support was cut.** A "summary unless something's on fire" digest is a
*buffering channel*, not a new evaluator — the fact registry and rule shape already
support it; it just needs a `DigestChannel` that batches instead of sending immediately.
Not building it now was a scope call, not an architectural dead end.

## 2. Deliberate cuts, and why

| Cut | Reason |
|---|---|
| Forecast / volume-deviation rules | `volume_forecast_next_15m` is `null` in the real sample data at 10:00. An input you can't trust isn't a v1 feature — extending later is one new registered fact, no new machinery. |
| Head-of-support digest | Different cadence, different product; a channel that buffers, not a rule. |
| Ack / snooze | Needs notification-state mutation APIs; `cooldown_sec` already gets ~80% of the benefit (no repeated pings for a known issue) for a fraction of the surface area. |
| Boolean OR / nested condition trees | Flat AND covers all three spec examples and keeps the evaluator, the validation errors, and the eventual rule-builder UI all honest about what's expressible. |
| Rule-builder UI | Rules are JSON over a real, validated API (`POST /rules`, 422s with field-level detail). A half-finished form scores worse than a clean schema a future UI can sit on top of. |
| `PATCH /rules/{id}` | Only `POST`/`GET`/`disable` were built — PLAN's own scope-trim guidance. Disable was kept because it's the one endpoint that exercises the open-episode-on-rule-change policy (see below). |
| Real Slack/email/push delivery | Out of scope per the brief. Two `Channel` implementations ship: `ConsoleChannel` (chat-formatted: `[09:55:00] @lead_sam — ...`) and `InboxChannel` (persisted, queryable via the API/page). "Add Slack" is visibly a third implementation and isn't built — the brief explicitly de-prioritizes integrations. |
| `HeapScheduler`, full three-valued logic | Optional phases (8 and 10 in the plan), not started. See §5 and §6. |

## 3. The one-generic-rule argument

The brief describes three rule "types." They're one type — a flat, ANDed list of
`{fact, op, value}` conditions — over two fact sets (`queue`, `agent`), plus four
mechanisms that do the actual noise-control work: a stable episode key
(`(rule_id, subject_id)`, so six raw events about one incident become **one**
notification), `for_duration` (a blip that self-corrects in five seconds shouldn't
page anyone), `cooldown` (default `0` — notify once per episode, not once per
evaluation), and a resolution notice (the thing that lets a lead stop refreshing a
dashboard).

The three examples from the brief, as the actual rule JSON the system validates and
runs (from [`seeds/rules.json`](./seeds/rules.json)):

```json
{
  "name": "out of adherence > 10 min",
  "subject_type": "agent",
  "selector": {"kind": "all"},
  "conditions": [
    {"fact": "in_adherence_violation", "op": "eq", "value": true},
    {"fact": "adherence_violation_duration_sec", "op": "gt", "value": 600}
  ],
  "cooldown_sec": 600,
  "recipient": {"kind": "subject_agent"},
  "template": "{subject_id} has been out of adherence for {adherence_violation_duration_sec}s"
}
```

```json
{
  "name": "billing queue > 20 tickets waiting",
  "subject_type": "queue",
  "selector": {"kind": "ids", "ids": ["billing"]},
  "conditions": [{"fact": "tickets_waiting", "op": "gt", "value": 20}],
  "for_duration_sec": 300,
  "recipient": {"kind": "author"},
  "template": "{subject_id} has {tickets_waiting} tickets waiting"
}
```

```json
{
  "name": "agent on a single call > 45 min",
  "subject_type": "agent",
  "selector": {"kind": "all"},
  "conditions": [
    {"fact": "current_state", "op": "eq", "value": "on_call"},
    {"fact": "current_state_duration_sec", "op": "gt", "value": 2700}
  ],
  "recipient": {"kind": "author"},
  "template": "{subject_id} has been on a single call for {current_state_duration_sec}s"
}
```

Same `Rule` model, same evaluator, same episode machine, same delivery path. The
"SLA is breached" rule (`seeds/rules.json`'s second entry) goes further and expresses
`longest_wait_sec > sla_target_sec` — a per-queue *fact* compared to another per-queue
*fact*, via a tagged `{"fact_ref": "sla_target_sec"}`, not a hardcoded threshold. No new
condition type was needed to make that work.

## 4. The scale story

Memory is **O(subjects), not O(events)**. The event log is not the evaluation
substrate — each subject (queue or agent) collapses to one current-state row, so the
live state driving every rule evaluation is bounded by *how many queues and agents
exist* (thousands, realistically, even at large scale), not by how many events have
ever been ingested. Millions of events/day is uninteresting on its own; what matters is
~10k agents' worth of state, updated in place. Per-tenant sharding by subject id is the
obvious next step if a single process's throughput became the bottleneck — nothing in
the rule/episode/evaluation model assumes a single shared process.

**Single-threaded v1**, by design: nothing evaluates the same `(rule_id, subject_id)`
pair concurrently. That's not just documentation — `episodes(rule_id, subject_id)` has
a partial unique index (`WHERE state IN ('pending', 'open')`) that makes a second
concurrent evaluator's insert fail, not just its outcome be *wrong*. Multi-writer
concurrency is a real project (sharding, or per-subject locking), and the invariant
that makes it safe to add later already has an enforced backstop today.

## 5. The scheduler story

Event-arrival evaluation alone is insufficient: `current_state_duration_sec > 2700`
can't be evaluated on event arrival, because nothing in the stream signals elapsed
time. In the real sample data, `a_11` goes on a call at 09:10 and stays on it until
10:20 with **zero intervening events** — a rule watching for "more than 45 minutes on
one call" needs something to notice at 09:55 even though nothing happened at 09:55.

The scheduler sits behind a small interface (`schedule`, `due`, `note_subject_changed`,
`note_rule_activated`) so a cheap implementation can ship first and a smarter one can
swap in later without touching the evaluator. **`ScanScheduler` ships**: on every tick
it recomputes the active set (subjects with an open/pending episode, plus subjects
matched by an enabled time-sensitive rule) from scratch and returns it — no
invalidation logic, because there's no cached state to invalidate. The optional
`HeapScheduler` (not built) would replace the O(active subjects) recompute-every-tick
with a min-heap on due-time — but critically, **the heap would only ever be a
scheduling *hint*, never a source of truth**: on pop it reloads current state and
re-derives facts from scratch, so a stale or duplicate heap entry is simply a wasted,
harmless re-evaluation, never a correctness risk. That's why `HeapScheduler` needs no
eager deletion and no tombstones, and why it's a pure performance upgrade, not a
behavior change — the exact same conformance tests would parameterize over both.

One non-obvious failure mode this design has to actively avoid: a rule *created* while
an agent is already 50 minutes into a call must still fire, even though nothing in the
event stream will trigger a fresh evaluation. `note_rule_activated` exists specifically
to force an immediate evaluation of every matching subject the moment a rule turns on.

`evaluate_rule(rule_id, subject_id, now, trigger, deps)` is **the same function** for
both event-triggered and tick-triggered evaluation. `trigger` is a logging label —
`del trigger` runs immediately after it's used only to bump a stats counter
(`evaluations_event` vs. `evaluations_due`, visible on `/api/stats`), and a dedicated
test asserts the two triggers produce byte-identical results from identical state.
There is structurally no `state` parameter at all — every call re-reads from the
repository, so it's impossible to accidentally evaluate against stale, caller-supplied
state.

## 6. Unknown-as-false: a stated limitation

v1 still treats an uncomputable fact as `false` at the point a condition is actually
evaluated (`evaluation/operators.py`) — full three-valued logic (`TRUE | FALSE |
UNKNOWN` with Kleene conjunction) is a real, scoped, droppable phase (Phase 10 in
`PLAN.md`) that has not been started. What **has** shipped is the cheaper, targeted fix
for the dangerous half of the asymmetry below: Phase 9, "resolve hardening"
(`episodes/machine.py`), which freezes an already-open episode instead of resolving it
when a fact it depends on goes missing.

The asymmetry still has to be stated plainly, because it isn't symmetric:

- **Opening** an episode on a missing fact fails **safe** — a missed alert, not a false
  one. Reachable in the real sample data: `evt_01HXYZ086` reports `a_23` with
  `in_violation: true` and `violation_started_at: null`; a ">10 min out of adherence"
  rule silently never fires for `a_23`, because `adherence_violation_duration_sec` is
  `MISSING` by design (`domain/facts.py`'s `MISSING` sentinel, distinct from `None` and
  `False` since Phase 2 specifically so this retrofit wouldn't touch every extractor
  later) whenever `violation_start_source == "unknown"` — never guessing a start time.
  This half is still unfixed and still v1's behavior: a missed alert, not a false one.
- **Resolving** an episode on a missing fact used to fail **dangerous**, and no longer
  does. If a fact driving an *already-open* episode goes missing, `advance()` no longer
  collapses it to `false` and emits a `resolved` notification that never actually
  happened; instead it freezes the episode (`stale=True`, `stale_since` recorded) and
  emits nothing. This is directly visible in the real sample data: `a_19`'s "out of
  adherence" episode reaches `evt_01HXYZ076` (`10:10:30`), where the violation
  genuinely clears — `in_adherence_violation` correctly reads `False` — but
  `adherence_violation_duration_sec` goes `MISSING` in the same instant, because the
  system can no longer distinguish "genuinely cleared" from "lost the ability to
  compute this" from that fact alone. Pre-Phase-9, this produced a false
  `"a_19 has been out of adherence for unknowns"` `RESOLVED` notification; today it
  instead leaves the episode `open` and `stale` for the rest of the replay, with no
  notification at all (see §8). A fresh `adherence_check` supplying a real
  `violation_started_at` would thaw it; the sample data doesn't provide one after this
  point.

`missing_facts` is already threaded through `EvaluationResult` and asserted on directly
in tests marked `TODO(3vl)` (Phase 2), so Phase 9's fix was an assertion flip on top of
that plumbing, not new machinery: freeze an episode as `stale` instead of resolving it
when a driving fact goes missing. That's shipped. The fuller Phase 10 (real
three-valued logic throughout the evaluator, closing the "opening" half of the
asymmetry too) has not been started.

## 7. AI tool usage and verification method

This was built with **Claude Code** end-to-end: implementation, tests, and this
writeup. Practically everything — every module, every test file — was AI-generated
against a detailed upfront spec (`PLAN.md`, itself produced collaboratively before
implementation started) and a strict phase-by-phase TDD workflow: tests written first,
confirmed failing (RED), minimal implementation, confirmed passing (GREEN). I did not
hand-write algorithmic code in this repo; my role was specifying the plan, reviewing
every phase's diff, deciding what to build vs. cut, and — critically — **independently
verifying** rather than trusting green tests alone. That verification is the part worth
being specific about, because "the tests pass" turned out not to be a sufficient
correctness signal on its own, more than once:

- **Every phase** was reviewed by a separate code-review pass (a second AI instance,
  scoped to just that phase's diff, prompted to look for correctness/security/
  transaction-safety issues) before moving on — not a rubber stamp. Real CRITICAL/HIGH
  findings were caught and fixed this way, including: repositories self-committing
  instead of using the shared transaction boundary (Phase 0); episode and notification
  writes committing in separate, non-atomic transactions, risking a permanently-lost
  notification on a crash between them (Phase 4); a template-validation truthiness bug
  (`if field_name` instead of `is not None`) that let `{}`-style auto-numbering
  placeholders through rule validation and crash notification rendering later
  (Phase 5); a `POST /rules` 500 on malformed JSON instead of a client error (Phase 5).
- **Running the actual system, not just the test suite**, caught bugs coverage alone
  didn't: `cli.replay` piped to a file and read back in isolation (rather than trusted
  from terminal scrollback across several similar debug runs) surfaced a real R10 clock
  purity violation — the replay driver was letting known out-of-order lines in the
  sample data move the engine's clock *backward*, corrupting every subsequent
  tick's `now`. Running `cli.seed` → `cli.replay` → querying the live API by hand (not
  only `pytest`) is what caught the `/notifications` page passing raw DB records where
  the template expected parsed JSON (would have crashed on first real use, since no
  existing test exercised that specific endpoint yet), and separately caught a required
  spec item (`evaluations_event`/`evaluations_due` stats) that had been silently
  dropped (`del trigger` with nothing reading it first).
- **A "replay twice should be identical" claim was verified, found wanting, and fixed
  twice** — this is the clearest example of not trusting a green test suite at face
  value. The first fix for cross-call idempotency (an episode-derived clock floor)
  passed its own test but was independently checked with a small reproduction script
  querying `evaluations_suppressed` directly before and after a second replay pass —
  which showed a real double-count (240 → 359) the notification-level test hadn't
  caught, because a suppressed evaluation produces no notification and therefore no
  row to check for duplication. The actual fix (a persisted tick watermark) was then
  itself sent through a second, narrowly-scoped review specifically because the first
  fix's own bug made me distrust "it passed the test it was built for" — that second
  review caught a further CRITICAL regression (`--no-eval` runs silently poisoning the
  watermark for a later evaluation-enabled run) before it shipped.
- **Writing this README's own demo output** (§8 below) caught a third, separate bug:
  a still-open episode's suppression summary was diffing an event-time timestamp
  against the live wall clock, producing a nonsense duration ("over 98540 min")
  whenever the API was queried well after the historical sample data's timestamps —
  a direct, if easy-to-miss-in-a-unit-test, violation of this project's own "all time
  is event time, never wall time" principle. Fixed by deriving `as_of` from the latest
  real subject data instead of `SystemClock`.

Every test in the suite (333 tests, 98%+ coverage) was written before its
implementation and run to confirm failure before the implementation existed; none were
retrofitted after the fact to match whatever the code happened to do. Where AI-written
tests didn't actually prove what their own docstring claimed (a boundary test that
accidentally tested the wrong side of a `>` vs `>=` split; a suppression test whose
`ManualClock` was pinned close enough to real dates that it couldn't have caught the
wall-clock bug above), those gaps were caught during review and closed with a sharper
test, not silently left. The full technical narrative — every flagged module's design
rationale tied to the specific risk it answers, every bug with its root cause and fix —
is in [`DEVELOPMENT_LOG.md`](./DEVELOPMENT_LOG.md), written phase-by-phase as the build
happened rather than reconstructed afterward.

## 8. Run instructions

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run the test suite (333 tests, 80%+ coverage gate enforced)
pytest

# Seed the 5 example rules (through the same validation POST /rules uses)
python -m app.cli.seed

# Replay the real 90-minute sample file through the engine, printing every
# notification to the console as it fires, plus a final counter table
python -m app.cli.replay Sample_Data/events.jsonl --speed 0   # --speed 0 = no artificial delay

# Serve the API + /notifications page (against the same var/app.db the above wrote to)
uvicorn "app.api.app:create_app" --factory --reload
# then open http://127.0.0.1:8000/notifications
```

### A paste of `/notifications`, from a real run of the sample data

Console output from `python -m app.cli.seed` + `python -m app.cli.replay Sample_Data/events.jsonl --speed 0`:

```
Seeded 5 rules from seeds/rules.json
state disagreement for agent a_23: adherence actual_state='in_meeting' but current_state='on_call'
duplicate event evt_01HXYZ050 has a different payload than the stored one: {'ts': ('2026-05-26T09:36:00Z', '2026-05-26T09:36:30Z')}
[09:30:00] #ops-alerts — billing has 18 tickets waiting
[09:35:00] @lead_sam — billing is breaching SLA: 130 > 120
[09:45:05] @a_19 — a_19 has been out of adherence for 605.0s
[09:55:05] @a_19 — a_19 has been out of adherence for 1205.0s
[09:55:05] @lead_sam — a_11 has been on a single call for 2705.0s
[10:00:00] #ops-alerts — billing has 17 tickets waiting
[10:00:00] #ops-alerts — billing has 14 tickets waiting
[10:05:00] @lead_sam — tier_2 is breaching SLA: 320 > 300
[10:05:05] @a_19 — a_19 has been out of adherence for 1805.0s
[10:10:05] @lead_sam — a_07 has been on a single call for 2705.0s
[10:10:05] @a_88 — a_88 has been out of adherence for 605.0s
[10:15:00] @lead_sam — billing is breaching SLA: 90 > 120
[10:15:00] @lead_sam — tier_2 is breaching SLA: 180 > 300
[10:15:05] @lead_sam — a_31 has been on a single call for 2705.0s
[10:20:00] @lead_sam — a_11 has been on a single call for 0.0s
[10:20:05] @a_88 — a_88 has been out of adherence for 1205.0s
[10:25:00] @lead_sam — a_31 has been on a single call for 0.0s

--- ingest counters ---
events_accepted                94
duplicates                     1
duplicate_payload_mismatch     1
late_drops                     1
validation_failures            0
state_disagreement             1
evaluations_event              201
evaluations_due                18234
subjects_tracked               11
```

`/notifications`, grouped by recipient (the product argument — this is the default
view, not a toggle), each row showing why with real numbers and, where suppression
happened, how long the condition held quietly before or after the notification:

```
-- #ops-alerts --
  [OPENED  ] billing has 18 tickets waiting
             why: tickets_waiting=18
             notified once at 2026-05-26T10:00:00+00:00, condition held true across 363 further evaluations over 0 min, suppressed.
  [REMINDER] billing has 17 tickets waiting
             why: tickets_waiting=17
  [RESOLVED] billing has 14 tickets waiting
             why: tickets_waiting=14

-- lead_sam --
  [OPENED  ] billing is breaching SLA: 130 > 120
             why: longest_wait_sec=130, sla_target_sec=120
             notified once at 2026-05-26T09:35:00+00:00, condition held true across 544 further evaluations over 40 min, suppressed.
  [OPENED  ] a_11 has been on a single call for 2705.0s
             why: current_state=on_call, current_state_duration_sec=2705.0
  [OPENED  ] tier_2 is breaching SLA: 320 > 300
             why: longest_wait_sec=320, sla_target_sec=300
  [OPENED  ] a_07 has been on a single call for 2705.0s
             why: current_state=on_call, current_state_duration_sec=2705.0
  [RESOLVED] billing is breaching SLA: 90 > 120
             why: longest_wait_sec=90, sla_target_sec=120
  [RESOLVED] tier_2 is breaching SLA: 180 > 300
             why: longest_wait_sec=180, sla_target_sec=300
  [OPENED  ] a_31 has been on a single call for 2705.0s
             why: current_state=on_call, current_state_duration_sec=2705.0
  [RESOLVED] a_11 has been on a single call for 0.0s
             why: current_state=available, current_state_duration_sec=0.0
  [RESOLVED] a_31 has been on a single call for 0.0s
             why: current_state=available, current_state_duration_sec=0.0

-- a_19 --
  [OPENED  ] a_19 has been out of adherence for 605.0s
             why: in_adherence_violation=True, adherence_violation_duration_sec=605.0
  [REMINDER] a_19 has been out of adherence for 1205.0s
             why: in_adherence_violation=True, adherence_violation_duration_sec=1205.0
  [REMINDER] a_19 has been out of adherence for 1805.0s
             why: in_adherence_violation=True, adherence_violation_duration_sec=1805.0
             stale — awaiting data

-- a_88 --
  [OPENED  ] a_88 has been out of adherence for 605.0s
             why: in_adherence_violation=True, adherence_violation_duration_sec=605.0
             notified once at 2026-05-26T10:20:05+00:00, condition held true across 240 further evaluations over 10 min, suppressed.
  [REMINDER] a_88 has been out of adherence for 1205.0s
             why: in_adherence_violation=True, adherence_violation_duration_sec=1205.0
```

Note the `tier_2` SLA rule and the `billing` SLA rule are the **same rule
definition** — one produced zero notifications early on and one `opened` + one
`resolved` later; the other produced its own independent `opened`/`resolved` pair.
Same evaluator, different outcomes, driven entirely by data. And `a_19`'s episode never
reaches `RESOLVED` at all: its adherence violation genuinely clears at `10:10:30`, but
`adherence_violation_duration_sec` goes `MISSING` in that same instant, so — per §6's
Phase 9 fix — the episode freezes as `stale` (`stale — awaiting data`, above) instead
of emitting a false `resolved`. It stays open and stale for the rest of this replay;
nothing in the sample data supplies a fresh `violation_started_at` to thaw it.
