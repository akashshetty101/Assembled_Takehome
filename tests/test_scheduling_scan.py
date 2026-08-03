from datetime import datetime, timezone

from app.scheduling.scan import ScanScheduler
from app.storage.sqlite.episodes import SqliteEpisodesRepository
from app.storage.sqlite.rules import SqliteRulesRepository
from app.storage.sqlite.subject_state import SqliteAgentStateRepository, SqliteQueueStateRepository
from app.storage.repositories import EpisodeRecord, RuleRecord

NOW = datetime(2026, 5, 26, 9, 20, tzinfo=timezone.utc)


def _scheduler(db_conn) -> ScanScheduler:
    return ScanScheduler(
        episodes_repo=SqliteEpisodesRepository(db_conn),
        rules_repo=SqliteRulesRepository(db_conn),
        queue_repo=SqliteQueueStateRepository(db_conn),
        agent_repo=SqliteAgentStateRepository(db_conn),
    )


def _rule_record(rule_id, subject_type, conditions_json, **overrides):
    data = dict(
        id=rule_id, name="n", subject_type=subject_type, selector_json='{"kind":"all"}',
        conditions_json=conditions_json, for_duration_sec=0, cooldown_sec=0,
        recipient_kind="author", recipient_target=None, template="t", enabled=True,
        created_by="lead_sam", created_at="2026-05-26T09:00:00Z", version=1,
    )
    data.update(overrides)
    return RuleRecord(**data)


def test_subject_with_active_episode_is_in_the_due_set(db_conn):
    SqliteRulesRepository(db_conn).create(
        _rule_record("r1", "queue", '[{"fact":"tickets_waiting","op":"gt","value":20}]')
    )
    SqliteEpisodesRepository(db_conn).create(
        EpisodeRecord("ep1", "r1", "billing", "pending", "2026-05-26T09:15:00Z",
                       None, None, None, 0, 0, False)
    )
    scheduler = _scheduler(db_conn)
    assert ("r1", "billing") in scheduler.due(NOW)


def test_subject_matched_by_time_sensitive_rule_with_no_episode_is_in_due_set(db_conn):
    """The a_11 scenario: no episode exists yet (zero intervening events),
    but the rule is time-sensitive (references current_state_duration_sec)
    and applies to every agent -- must still be scheduled."""
    from app.domain.subjects import AgentState

    SqliteRulesRepository(db_conn).create(
        _rule_record(
            "r1", "agent",
            '[{"fact":"current_state","op":"eq","value":"on_call"},'
            '{"fact":"current_state_duration_sec","op":"gt","value":2700}]',
        )
    )
    SqliteAgentStateRepository(db_conn).put(
        AgentState(
            agent_id="a_11", current_state="on_call",
            state_entered_at=datetime(2026, 5, 26, 9, 10, tzinfo=timezone.utc), last_event_ts=NOW,
        )
    )
    scheduler = _scheduler(db_conn)
    assert ("r1", "a_11") in scheduler.due(NOW)


def test_subject_with_no_time_sensitive_rule_and_no_episode_is_never_returned(db_conn):
    SqliteRulesRepository(db_conn).create(
        _rule_record("r1", "queue", '[{"fact":"tickets_waiting","op":"gt","value":20}]')
    )
    scheduler = _scheduler(db_conn)
    assert scheduler.due(NOW) == []


def test_queue_staleness_snapshot_age_sec_rule_is_scheduled(db_conn):
    """R14 conformance case."""
    from app.domain.subjects import QueueState

    SqliteRulesRepository(db_conn).create(
        _rule_record("r1", "queue", '[{"fact":"snapshot_age_sec","op":"gt","value":120}]')
    )
    SqliteQueueStateRepository(db_conn).put(QueueState(queue_id="billing", last_event_ts=NOW))
    scheduler = _scheduler(db_conn)
    assert ("r1", "billing") in scheduler.due(NOW)


def test_disabled_rule_is_never_scheduled(db_conn):
    from app.domain.subjects import AgentState

    SqliteRulesRepository(db_conn).create(
        _rule_record(
            "r1", "agent",
            '[{"fact":"current_state_duration_sec","op":"gt","value":2700}]',
            enabled=False,
        )
    )
    SqliteAgentStateRepository(db_conn).put(
        AgentState(agent_id="a_11", state_entered_at=NOW, last_event_ts=NOW)
    )
    scheduler = _scheduler(db_conn)
    assert scheduler.due(NOW) == []


def test_ids_selector_resolves_for_queue_and_agent_rules(db_conn):
    from app.domain.subjects import AgentState

    SqliteRulesRepository(db_conn).create(
        _rule_record("r1", "queue", '[{"fact":"snapshot_age_sec","op":"gt","value":60}]',
                      selector_json='{"kind":"ids","ids":["billing"]}')
    )
    SqliteRulesRepository(db_conn).create(
        _rule_record("r2", "agent", '[{"fact":"current_state_duration_sec","op":"gt","value":60}]',
                      selector_json='{"kind":"ids","ids":["a_11"]}')
    )
    from app.domain.subjects import QueueState

    SqliteQueueStateRepository(db_conn).put(QueueState(queue_id="billing", last_event_ts=NOW))
    SqliteAgentStateRepository(db_conn).put(AgentState(agent_id="a_11", state_entered_at=NOW, last_event_ts=NOW))
    scheduler = _scheduler(db_conn)
    due = scheduler.due(NOW)
    assert ("r1", "billing") in due
    assert ("r2", "a_11") in due


def test_queue_membership_selector_resolves_agents_by_queue(db_conn):
    """'any of my agents' -- the selector kind queue_membership exists
    specifically to express this."""
    from app.domain.subjects import AgentState

    SqliteRulesRepository(db_conn).create(
        _rule_record(
            "r1", "agent", '[{"fact":"current_state_duration_sec","op":"gt","value":60}]',
            selector_json='{"kind":"queue_membership","queue_ids":["billing"]}',
        )
    )
    SqliteAgentStateRepository(db_conn).put(
        AgentState(agent_id="a_19", queue_ids=["billing"], state_entered_at=NOW, last_event_ts=NOW)
    )
    SqliteAgentStateRepository(db_conn).put(
        AgentState(agent_id="a_23", queue_ids=["tier_2"], state_entered_at=NOW, last_event_ts=NOW)
    )
    scheduler = _scheduler(db_conn)
    due = scheduler.due(NOW)
    assert ("r1", "a_19") in due
    assert ("r1", "a_23") not in due


def test_schedule_and_note_hooks_do_not_raise(db_conn):
    """ScanScheduler ignores due_at and does zero invalidation -- these are
    no-ops by design, not missing functionality."""
    scheduler = _scheduler(db_conn)
    scheduler.schedule(NOW, "r1", "billing")
    scheduler.note_subject_changed("billing")
    scheduler.note_rule_activated("r1")
