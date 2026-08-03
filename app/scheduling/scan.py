"""
Scan Scheduler.

This module implements the `ScanScheduler`, a simple yet robust scheduler that identifies 
which subjects are due for time-sensitive rule evaluations (e.g., duration checks) at each tick.

Design Strategy:
- Active Set Scanning: Rather than scanning all subjects in the system, it scans only the
  "active set" of subjects:
    1. Any subject that currently has a PENDING or OPEN episode.
    2. Any subject matching an enabled rule that depends on time-derived facts (e.g., call duration).
- Simple & Robust: It contains zero invalidation logic or cached state, avoiding cache 
  invalidation bugs. It computes the active set fresh on every tick.
- Scale Boundaries: Runs at O(active subjects) per tick. For larger scales, a HeapScheduler 
  (Phase 8) can swap in using the exact same interface, where a min-heap on due-times acts
  as an evaluation hint with lazy invalidation.
"""

import json
from datetime import datetime

from app.scheduling.due_time import TIME_DERIVED_FACTS
from app.scheduling.selectors import resolve_selector_subject_ids


def _is_time_sensitive(rule_record) -> bool:
    conditions = json.loads(rule_record.conditions_json)
    return bool({c["fact"] for c in conditions} & TIME_DERIVED_FACTS)


class ScanScheduler:
    """Ignores due_at entirely; zero invalidation logic (PLAN.md 1.5). The
    active set = subjects with a pending/open episode + subjects matched by
    an enabled time-sensitive rule, recomputed fresh on every due() call."""

    def __init__(self, episodes_repo, rules_repo, queue_repo, agent_repo) -> None:
        self._episodes_repo = episodes_repo
        self._rules_repo = rules_repo
        self._queue_repo = queue_repo
        self._agent_repo = agent_repo

    def schedule(self, due_at: datetime, rule_id: str, subject_id: str) -> None:
        # ScanScheduler computes the active set dynamically on every tick.
        # Thus, explicit scheduling is a no-op: we do not maintain a queue of future tasks.
        pass

    def due(self, now: datetime) -> list[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        # 1. Include all subjects with an active (PENDING or OPEN) episode.
        # We must re-evaluate these to check if they have elapsed their duration,
        # need a reminder, or should resolve/clear.
        for ep in self._episodes_repo.list_active():
            pairs.add((ep.rule_id, ep.subject_id))

        # 2. Include all subjects matching enabled, time-sensitive rules.
        # This catches subjects whose state hasn't triggered an episode yet, but might
        # cross a duration threshold purely due to the passage of time without new events.
        for rule_record in self._rules_repo.list(enabled_only=True):
            if not _is_time_sensitive(rule_record):
                continue
            for subject_id in resolve_selector_subject_ids(rule_record, self._queue_repo, self._agent_repo):
                pairs.add((rule_record.id, subject_id))

        # Return unique rule_id and subject_id pairs for the engine to evaluate.
        return list(pairs)

    def note_subject_changed(self, subject_id: str) -> None:
        # Since we rebuild the active set dynamically on each tick, we do not
        # need to respond to state changes to invalidate scheduling caches.
        pass

    def note_rule_activated(self, rule_id: str) -> None:
        # Since rule evaluation queries enabled rules fresh, rule activation
        # does not need to trigger cache invalidation or rescheduling.
        pass

