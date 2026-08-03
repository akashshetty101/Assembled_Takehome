"""
Event Ingestion Pipeline.

This module coordinates the processing of raw, incoming contact center events
(queue snapshots, agent state transitions, adherence checks). It sits at the entry point 
of the system and projects event data onto a compact database schema.

Key Pipeline Stages (Enforced Order):
1. Parse & Validate: Ensures schema conformances via Pydantic event models.
2. Deduplicate: Rejects redundant events by ID. Runs before watermark checking (R3) 
   so late duplicate events are counted as duplicates, not late drops.
3. Watermark Validation: Discards late events to prevent state regression (R5), 
   while logging them to SQLite for debugging/auditing.
4. Transactional Projection: Commits state changes to Agent/Queue storage and updates 
   watermarks within a single transaction to maintain atomic consistency.
5. Change Callbacks: Invokes engine evaluation hooks on successful ingest, using a 
   decoupled callback to keep ingest free from engine dependency (R1).
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from pydantic import ValidationError

from app.domain.events import AdherenceCheckEvent, parse_event
from app.domain.subjects import SubjectRef, SubjectType, subject_ref_for_event
from app.ingest.counters import Counters
from app.projection.agent import apply_adherence, apply_state_change, state_disagreement
from app.projection.queue import apply_snapshot
from app.storage.db import transaction
from app.storage.repositories import (
    AgentStateRepository,
    EventRecord,
    EventsRepository,
    QueueStateRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    accepted: bool
    changed_subject: SubjectRef | None = None


def _field_diff(old: dict, new: dict) -> dict:
    keys = set(old) | set(new)
    return {k: (old.get(k), new.get(k)) for k in keys if old.get(k) != new.get(k)}


def ingest_event(
    raw: dict,
    *,
    conn: sqlite3.Connection,
    events_repo: EventsRepository,
    counters: Counters,
    queue_repo: QueueStateRepository,
    agent_repo: AgentStateRepository,
    received_seq: int,
    on_change: Callable[[SubjectRef, datetime], None] | None = None,
) -> IngestOutcome:
    """The ordered gauntlet (PLAN.md Phase 1 item 5). Order is a decision:
    dedup MUST precede the watermark check (R3) -- a duplicate that is also
    late must be counted as a duplicate, not a late_drop. Late events are
    still appended to the events log (R5) even though their projection is
    skipped. Ties (event.ts == watermark) are accepted, not late; same-
    subject ties resolve by arrival order because that is simply the order
    this function is called in -- no separate tie-breaking logic exists.

    Every counter increment is inside its own transaction() block on the
    path it returns from: a caplog-visible early return with an uncommitted
    increment would silently lose that count the moment a reader on a
    different connection looks (or the moment an unrelated later write on
    this same connection rolls back) -- a real bug caught by code review.
    Logging always happens AFTER the transaction that backs it commits
    (persist-then-log), mirroring Phase 6's persist-then-send rule.

    `on_change` wires the engine's on_event in via a callback, not an
    import (Phase 4 item 9) -- ingest must not depend on the engine.
    Called only on the normal (projected) path, after the transaction
    commits, never for late/duplicate/invalid events."""
    try:
        event = parse_event(raw)
    except ValidationError:
        with transaction(conn):
            counters.increment("validation_failures")
        return IngestOutcome(accepted=False)

    canonical_payload = json.dumps(raw, sort_keys=True)

    # Deduplication by event_id -- must precede the watermark check (R3).
    existing = events_repo.get(event.event_id)
    if existing is not None:
        mismatch = existing.payload_json != canonical_payload
        with transaction(conn):
            counters.increment("duplicates")
            if mismatch:
                counters.increment("duplicate_payload_mismatch")
        if mismatch:
            diff = _field_diff(json.loads(existing.payload_json), raw)
            logger.warning(
                "duplicate event %s has a different payload than the stored one: %s",
                event.event_id, diff,
            )
        return IngestOutcome(accepted=False)

    ref = subject_ref_for_event(event)
    prior_queue = queue_repo.get(ref.subject_id) if ref.subject_type == SubjectType.QUEUE else None
    prior_agent = agent_repo.get(ref.subject_id) if ref.subject_type == SubjectType.AGENT else None
    watermark = prior_queue.last_event_ts if prior_queue else (
        prior_agent.last_event_ts if prior_agent else None
    )

    # Watermark: late events are still logged (R5) but skip projection.
    # late_drops and events_accepted are disjoint buckets (Phase 6's counter
    # reconciliation: events_accepted + duplicates + late_drops +
    # validation_failures == lines_read) -- a late event increments
    # late_drops only, even though it IS written to the events table.
    if watermark is not None and event.ts < watermark:
        with transaction(conn):
            counters.increment("late_drops")
            events_repo.append(
                EventRecord(event.event_id, event.ts.isoformat(), event.type, canonical_payload, received_seq)
            )
        return IngestOutcome(accepted=True, changed_subject=None)

    disagreement = isinstance(event, AdherenceCheckEvent) and state_disagreement(prior_agent, event)

    # Project + advance watermark + append event + counters, in one
    # transaction (R4). state_disagreement's increment lives in the same
    # block as the projection write it describes, not before it -- no
    # counter increment anywhere in this function is committed separately
    # from the return path it belongs to.
    with transaction(conn):
        counters.increment("events_accepted")
        if disagreement:
            counters.increment("state_disagreement")
        events_repo.append(
            EventRecord(event.event_id, event.ts.isoformat(), event.type, canonical_payload, received_seq)
        )
        if ref.subject_type == SubjectType.QUEUE:
            queue_repo.put(apply_snapshot(prior_queue, event))
        elif isinstance(event, AdherenceCheckEvent):
            agent_repo.put(apply_adherence(prior_agent, event))
        else:
            agent_repo.put(apply_state_change(prior_agent, event))

    if disagreement:
        logger.warning(
            "state disagreement for agent %s: adherence actual_state=%r but current_state=%r",
            event.agent_id, event.actual_state,
            prior_agent.current_state if prior_agent else None,
        )

    if on_change is not None:
        on_change(ref, event.ts)

    return IngestOutcome(accepted=True, changed_subject=ref)
