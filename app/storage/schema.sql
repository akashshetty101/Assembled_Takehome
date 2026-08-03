-- Full DDL, applied once by app.storage.db's idempotent migration runner.
-- All timestamps are stored as ISO-8601 UTC text (event time, never wall time).

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,          -- PK gives dedup at the storage layer
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_seq INTEGER NOT NULL
);

-- One row per queue (subject). last_event_ts is the per-subject watermark.
CREATE TABLE IF NOT EXISTS queue_state (
    queue_id TEXT PRIMARY KEY,
    tickets_waiting INTEGER,
    longest_wait_sec INTEGER,
    sla_target_sec INTEGER,
    agents_available INTEGER,
    agents_on_call INTEGER,
    volume_last_15m INTEGER,
    volume_forecast_next_15m INTEGER,
    last_event_ts TEXT NOT NULL
);

-- One row per agent (subject). last_event_ts is the per-subject watermark,
-- shared across agent_state_change and adherence_check events for that agent.
CREATE TABLE IF NOT EXISTS agent_state (
    agent_id TEXT PRIMARY KEY,
    current_state TEXT,
    state_entered_at TEXT,
    queue_ids_json TEXT NOT NULL DEFAULT '[]',
    violation_active INTEGER NOT NULL DEFAULT 0,
    violation_started_at TEXT,
    violation_start_source TEXT NOT NULL DEFAULT 'unknown'
        CHECK (violation_start_source IN ('event', 'observed', 'unknown')),
    last_event_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('queue', 'agent')),
    selector_json TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    for_duration_sec INTEGER NOT NULL DEFAULT 0 CHECK (for_duration_sec >= 0),
    cooldown_sec INTEGER NOT NULL DEFAULT 0 CHECK (cooldown_sec >= 0),
    recipient_kind TEXT NOT NULL CHECK (recipient_kind IN ('author', 'subject_agent', 'channel')),
    recipient_target TEXT
        CHECK (recipient_kind != 'channel' OR (recipient_target IS NOT NULL AND recipient_target != '')),
    template TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES rules(id),
    subject_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('clear', 'pending', 'open')),
    first_true_at TEXT,
    opened_at TEXT,
    closed_at TEXT,
    last_notified_at TEXT,
    notify_seq INTEGER NOT NULL DEFAULT 0,
    evaluations_suppressed INTEGER NOT NULL DEFAULT 0,
    stale INTEGER NOT NULL DEFAULT 0
);

-- At most one pending/open episode per (rule, subject) at a time. This is the
-- enforceable half of the single-threaded-evaluation invariant (R12): even if
-- two triggers raced, only one could win the insert.
CREATE UNIQUE INDEX IF NOT EXISTS ux_episode_open
    ON episodes(rule_id, subject_id)
    WHERE state IN ('pending', 'open');

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(id),
    rule_id TEXT NOT NULL REFERENCES rules(id),
    subject_id TEXT NOT NULL,
    transition TEXT NOT NULL CHECK (transition IN ('opened', 'reminder', 'resolved')),
    occurrence_seq INTEGER NOT NULL,
    recipient_kind TEXT NOT NULL,
    recipient_target TEXT NOT NULL,
    body TEXT NOT NULL,
    facts_snapshot_json TEXT NOT NULL,
    event_time TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- R9: NOT (episode_id, transition) -- cooldown reminders share a transition
-- value for one episode, so occurrence_seq (== episode.notify_seq at send
-- time) is the third column that makes each reminder distinct.
CREATE UNIQUE INDEX IF NOT EXISTS ux_notif_idem
    ON notifications(episode_id, transition, occurrence_seq);

-- Support Phase 5's ?rule_id=/?subject_id= notification filters and the
-- Phase 4 scheduler's active-subject scan without a full table scan.
CREATE INDEX IF NOT EXISTS ix_notifications_rule_id ON notifications(rule_id);
CREATE INDEX IF NOT EXISTS ix_notifications_subject_id ON notifications(subject_id);
CREATE INDEX IF NOT EXISTS ix_episodes_subject_id ON episodes(subject_id);

CREATE TABLE IF NOT EXISTS ingest_counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

-- Single-row watermark: how far ANY prior cli.replay run's ManualClock has
-- ticked in this DB. Phase 6: without this, a second replay() call resets
-- its clock to the event file's first timestamp and re-walks ticks through
-- time a prior call already fully evaluated -- episode-derived proxies
-- (last_notified_at etc.) can't recover this exactly for episodes that kept
-- accumulating suppressed evaluations after their last notification, which
-- silently double-counts evaluations_suppressed on a second full replay.
CREATE TABLE IF NOT EXISTS replay_watermark (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ticked_through TEXT NOT NULL
);
