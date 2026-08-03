import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.domain.episodes import Episode, EpisodeState, Transition
from app.domain.facts import FactRegistry
from app.domain.notifications import Notification
from app.domain.rules import Rule
from app.domain.subjects import SubjectRef, SubjectType
from app.episodes.machine import advance
from app.evaluation.evaluator import evaluate
from app.ingest.counters import Counters
from app.routing.channels import Channel
from app.routing.dispatcher import dispatch_persist, dispatch_send
from app.routing.recipients import resolve_recipient
from app.routing.renderer import render
from app.scheduling.selectors import resolve_selector_subject_ids
from app.storage.db import transaction
from app.storage.repositories import (
    AgentStateRepository,
    EpisodeRecord,
    EpisodesRepository,
    NotificationsRepository,
    QueueStateRepository,
    RulesRepository,
)


@dataclass(frozen=True, slots=True)
class EngineDeps:
    conn: sqlite3.Connection
    rules_repo: RulesRepository
    episodes_repo: EpisodesRepository
    queue_repo: QueueStateRepository
    agent_repo: AgentStateRepository
    registry: FactRegistry
    channels: list[Channel]
    notifications_repo: NotificationsRepository
    counters: Counters | None = None


def _rule_from_record(record, registry: FactRegistry) -> Rule:
    return Rule.model_validate(
        {
            "name": record.name,
            "subject_type": record.subject_type,
            "selector": json.loads(record.selector_json),
            "conditions": json.loads(record.conditions_json),
            "for_duration_sec": record.for_duration_sec,
            "cooldown_sec": record.cooldown_sec,
            "recipient": {"kind": record.recipient_kind, "target": record.recipient_target},
            "template": record.template,
            "enabled": record.enabled,
            "created_by": record.created_by,
        },
        context={"registry": registry},
    )


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _episode_from_record(record: EpisodeRecord) -> Episode:
    return Episode(
        id=record.id, rule_id=record.rule_id, subject_id=record.subject_id,
        state=EpisodeState(record.state),
        first_true_at=_parse_ts(record.first_true_at),
        opened_at=_parse_ts(record.opened_at),
        closed_at=_parse_ts(record.closed_at),
        last_notified_at=_parse_ts(record.last_notified_at),
        notify_seq=record.notify_seq, evaluations_suppressed=record.evaluations_suppressed,
        stale=record.stale,
    )


def _episode_to_record(episode: Episode) -> EpisodeRecord:
    return EpisodeRecord(
        id=episode.id, rule_id=episode.rule_id, subject_id=episode.subject_id,
        state=episode.state.value,
        first_true_at=episode.first_true_at.isoformat() if episode.first_true_at else None,
        opened_at=episode.opened_at.isoformat() if episode.opened_at else None,
        closed_at=episode.closed_at.isoformat() if episode.closed_at else None,
        last_notified_at=episode.last_notified_at.isoformat() if episode.last_notified_at else None,
        notify_seq=episode.notify_seq, evaluations_suppressed=episode.evaluations_suppressed,
        stale=episode.stale,
    )


def evaluate_rule(
    rule_id: str,
    subject_id: str,
    now: datetime,
    trigger: str,
    deps: EngineDeps,
) -> list[Transition]:
    """THE single function both triggers (event arrival, tick) call.
    `trigger` is a logging/stats label ONLY -- assert this with a test, and
    it must never appear in a conditional. Always reloads subject state
    fresh; never trusts what the caller passed (a subject may have changed
    between when a tick was scheduled and when it fires)."""
    if deps.counters is not None:
        # Own transaction, committed immediately: every return path below (including
        # the early returns) must not lose this count, matching ingest/pipeline.py's
        # per-increment transaction discipline (code-review HIGH fix).
        with transaction(deps.conn):
            deps.counters.increment("evaluations_event" if trigger == "event" else "evaluations_due")
    del trigger  # logging/stats label only; not read anywhere in this function's logic

    rule_record = deps.rules_repo.get(rule_id)
    if rule_record is None or not rule_record.enabled:
        return []
    rule = _rule_from_record(rule_record, deps.registry)

    if rule.subject_type == SubjectType.QUEUE:
        state = deps.queue_repo.get(subject_id)
    else:
        state = deps.agent_repo.get(subject_id)
    if state is None:
        return []

    result = evaluate(rule, state, now, deps.registry)

    prior_record = deps.episodes_repo.get_active(rule_id, subject_id)
    prior_episode = _episode_from_record(prior_record) if prior_record is not None else None
    new_id = str(uuid.uuid4())
    new_episode, transitions = advance(
        prior_episode, result.matched, now, rule,
        rule_id=rule_id, subject_id=subject_id, new_id=new_id,
    )

    # Episode state change AND every notification it produces are one
    # atomic unit (code-review HIGH fix): a crash between committing the
    # episode and committing its notification would otherwise permanently
    # lose the notification, since advance() has no way to know a
    # already-OPEN episode's opening notification never landed. Channel
    # sends happen strictly AFTER this transaction commits -- never
    # interleaved with it (persist-then-send).
    pending_sends: list[Notification] = []
    with transaction(deps.conn):
        if prior_record is None:
            if new_episode.state != EpisodeState.CLEAR:
                deps.episodes_repo.create(_episode_to_record(new_episode))
        else:
            deps.episodes_repo.update(_episode_to_record(new_episode))

        for transition in transitions:
            recipient = resolve_recipient(rule, subject_id)
            body = render(rule.template, subject_id, result.facts_snapshot)
            occurrence_seq = new_episode.notify_seq if transition == Transition.REMINDER else 0
            notification = Notification(
                id=str(uuid.uuid4()), episode_id=new_episode.id, rule_id=rule_id, subject_id=subject_id,
                transition=transition, occurrence_seq=occurrence_seq,
                recipient_kind=recipient.kind, recipient_target=recipient.target,
                body=body, facts_snapshot=result.facts_snapshot, event_time=now, created_at=now,
            )
            # Gated on rowcount (R9/R10): a duplicate (episode_id, transition,
            # occurrence_seq) -- e.g. a crash-recovery replay re-deriving an
            # already-persisted reminder -- must not reach Channel.send() again,
            # even though advance() correctly recomputed the same transition.
            if dispatch_persist(notification, deps.notifications_repo):
                pending_sends.append(notification)

    for notification in pending_sends:
        dispatch_send(notification, deps.channels)

    return transitions


def on_event(changed_subject: SubjectRef, now: datetime, deps: EngineDeps) -> list[Transition]:
    """Match enabled rules by subject_type + selector, evaluate each."""
    transitions: list[Transition] = []
    for rule_record in deps.rules_repo.list(enabled_only=True):
        if rule_record.subject_type != changed_subject.subject_type.value:
            continue
        candidates = resolve_selector_subject_ids(rule_record, deps.queue_repo, deps.agent_repo)
        if changed_subject.subject_id not in candidates:
            continue
        transitions += evaluate_rule(rule_record.id, changed_subject.subject_id, now, "event", deps)
    return transitions


def on_tick(now: datetime, deps: EngineDeps, scheduler) -> list[Transition]:
    transitions: list[Transition] = []
    for rule_id, subject_id in scheduler.due(now):
        transitions += evaluate_rule(rule_id, subject_id, now, "due", deps)
    return transitions
