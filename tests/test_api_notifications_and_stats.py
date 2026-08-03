from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.app import build_app_state, create_app
from app.clock import ManualClock
from app.config import Settings
from app.storage.db import connect, migrate
from app.storage.repositories import EpisodeRecord, NotificationRecord, RuleRecord


def _client(tmp_path):
    settings = Settings(db_path=str(tmp_path / "t.db"), schema_path="app/storage/schema.sql")
    conn = connect(settings)
    migrate(conn, settings)
    clock = ManualClock(datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc))
    state = build_app_state(settings=settings, conn=conn, clock=clock)
    app = create_app(state)
    return TestClient(app), state


def _seed_notification(state):
    state.engine_deps.rules_repo.create(RuleRecord(
        id="r1", name="n", subject_type="queue", selector_json='{"kind":"all"}',
        conditions_json='[{"fact":"tickets_waiting","op":"gt","value":20}]',
        for_duration_sec=0, cooldown_sec=0, recipient_kind="author", recipient_target=None,
        template="{subject_id}", enabled=True, created_by="lead_sam",
        created_at="2026-05-26T09:00:00Z", version=1,
    ))
    state.engine_deps.episodes_repo.create(EpisodeRecord(
        id="ep1", rule_id="r1", subject_id="billing", state="open",
        first_true_at=None, opened_at="2026-05-26T09:36:00Z", closed_at=None,
        last_notified_at="2026-05-26T09:36:00Z", notify_seq=0, evaluations_suppressed=0, stale=False,
    ))
    state.engine_deps.notifications_repo.insert_if_absent(NotificationRecord(
        id="n1", episode_id="ep1", rule_id="r1", subject_id="billing", transition="opened",
        occurrence_seq=0, recipient_kind="author", recipient_target="lead_sam",
        body="billing has 22 tickets waiting", facts_snapshot_json='{"tickets_waiting": 22}',
        event_time="2026-05-26T09:36:00Z", created_at="2026-05-26T09:36:00Z",
    ))


def test_notifications_page_renders_grouped_by_recipient_with_why(tmp_path):
    client, state = _client(tmp_path)
    _seed_notification(state)
    resp = client.get("/notifications")
    assert resp.status_code == 200
    assert "lead_sam" in resp.text
    assert "tickets_waiting=22" in resp.text  # facts_snapshot rendered as "why"


def test_notifications_page_empty_state(tmp_path):
    client, _ = _client(tmp_path)
    resp = client.get("/notifications")
    assert resp.status_code == 200
    assert "No notifications yet" in resp.text


def test_notifications_page_and_json_render_suppression_summary(tmp_path):
    """Phase 6 item 3: episodes with suppressed evaluations surface a
    human-readable summary alongside the notification, both in the HTML
    page and the JSON endpoint."""
    client, state = _client(tmp_path)
    _seed_notification(state)
    import dataclasses

    episode = state.engine_deps.episodes_repo.get("ep1")
    state.engine_deps.episodes_repo.update(dataclasses.replace(episode, evaluations_suppressed=3))

    html = client.get("/notifications").text
    assert "condition held true across 3 further evaluations" in html

    body = client.get("/api/notifications").json()
    assert "condition held true across 3 further evaluations" in body[0]["suppression_summary"]


def test_suppression_summary_uses_latest_known_event_time_not_wall_clock(tmp_path):
    """Bug found generating the README demo: a still-open episode's
    suppression duration must be computed against the latest event time the
    system has actually observed (subject state's last_event_ts), never
    real wall-clock time -- PLAN.md Sec 1.6, 'all time is event time, never
    wall time'. Using the live clock directly produced a nonsensical
    multi-month duration ('over 98540 min') the moment the API was queried
    well after the historical replay demo's data."""
    from app.domain.subjects import QueueState

    settings = Settings(db_path=str(tmp_path / "t.db"), schema_path="app/storage/schema.sql")
    conn = connect(settings)
    migrate(conn, settings)
    far_future_clock = ManualClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
    state = build_app_state(settings=settings, conn=conn, clock=far_future_clock)
    client = TestClient(create_app(state))

    _seed_notification(state)
    state.engine_deps.queue_repo.put(QueueState(
        queue_id="billing", tickets_waiting=22,
        last_event_ts=datetime(2026, 5, 26, 10, 0, tzinfo=timezone.utc),
    ))
    import dataclasses

    episode = state.engine_deps.episodes_repo.get("ep1")
    state.engine_deps.episodes_repo.update(dataclasses.replace(episode, evaluations_suppressed=5))

    summary = client.get("/api/notifications").json()[0]["suppression_summary"]
    assert "over 24 min" in summary  # 09:36 -> billing's last_event_ts 10:00, not years


def test_suppression_summary_only_shown_on_latest_notification_in_episode(tmp_path):
    """Bug found reviewing the PDF export: the episode row's suppression
    state is live/mutable, so attaching it to every historical notification
    of that episode made an OPENED notification (event_time 09:36) display
    a REMINDER's later 'notified once at 10:00' timestamp -- a time after
    the OPENED notification itself. Only the most recent notification for
    an episode should carry the live summary; earlier ones must show None
    rather than a copy of a later notification's state."""
    import dataclasses

    client, state = _client(tmp_path)
    _seed_notification(state)  # ep1's 'opened' notification n1 @ 09:36

    state.engine_deps.notifications_repo.insert_if_absent(NotificationRecord(
        id="n2", episode_id="ep1", rule_id="r1", subject_id="billing", transition="reminder",
        occurrence_seq=1, recipient_kind="author", recipient_target="lead_sam",
        body="billing has 25 tickets waiting", facts_snapshot_json='{"tickets_waiting": 25}',
        event_time="2026-05-26T10:00:00Z", created_at="2026-05-26T10:00:00Z",
    ))
    episode = state.engine_deps.episodes_repo.get("ep1")
    state.engine_deps.episodes_repo.update(dataclasses.replace(
        episode, last_notified_at="2026-05-26T10:00:00Z", evaluations_suppressed=3,
    ))

    body = client.get("/api/notifications").json()
    by_id = {n["id"]: n for n in body}
    assert by_id["n1"]["suppression_summary"] is None
    assert by_id["n2"]["suppression_summary"] is not None
    assert "notified once at 2026-05-26T10:00:00Z" in by_id["n2"]["suppression_summary"]


def test_stale_only_shown_on_latest_notification_in_episode(tmp_path):
    """Same bug class as the suppression-summary fix above, for the `stale`
    flag: episode.stale is live/current state, so stamping it onto every
    historical notification of the episode made an OPENED/REMINDER sent
    BEFORE the episode ever went stale (a driving fact went MISSING later)
    retroactively render 'stale — awaiting data', which never described
    those notifications at the time they were sent."""
    import dataclasses

    client, state = _client(tmp_path)
    _seed_notification(state)  # ep1's 'opened' notification n1, episode not stale

    state.engine_deps.notifications_repo.insert_if_absent(NotificationRecord(
        id="n2", episode_id="ep1", rule_id="r1", subject_id="billing", transition="reminder",
        occurrence_seq=1, recipient_kind="author", recipient_target="lead_sam",
        body="billing has 25 tickets waiting", facts_snapshot_json='{"tickets_waiting": 25}',
        event_time="2026-05-26T10:00:00Z", created_at="2026-05-26T10:00:00Z",
    ))
    episode = state.engine_deps.episodes_repo.get("ep1")
    state.engine_deps.episodes_repo.update(dataclasses.replace(episode, stale=True))

    body = client.get("/api/notifications").json()
    by_id = {n["id"]: n for n in body}
    assert by_id["n1"]["stale"] is False
    assert by_id["n2"]["stale"] is True


def test_api_notifications_json_and_filters(tmp_path):
    client, state = _client(tmp_path)
    _seed_notification(state)
    resp = client.get("/api/notifications")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["facts_snapshot"]["tickets_waiting"] == 22

    assert len(client.get("/api/notifications?recipient=lead_sam").json()) == 1
    assert len(client.get("/api/notifications?recipient=nobody").json()) == 0


def test_api_stats_reports_counters_and_subjects_tracked(tmp_path):
    client, state = _client(tmp_path)
    state.counters.increment("duplicates", 3)
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["duplicates"] == 3
    assert "subjects_tracked" in body
