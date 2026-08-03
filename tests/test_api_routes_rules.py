import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.app import build_app_state, create_app
from app.clock import ManualClock
from app.config import Settings
from app.storage.db import connect, migrate
from app.storage.repositories import EpisodeRecord


def _client(tmp_path):
    settings = Settings(db_path=str(tmp_path / "t.db"), schema_path="app/storage/schema.sql")
    conn = connect(settings)
    migrate(conn, settings)
    clock = ManualClock(datetime(2026, 5, 26, 9, 0, tzinfo=timezone.utc))
    state = build_app_state(settings=settings, conn=conn, clock=clock)
    app = create_app(state)
    return TestClient(app), state


VALID_RULE = {
    "name": "billing queue > 20 tickets waiting",
    "subject_type": "queue",
    "selector": {"kind": "ids", "ids": ["billing"]},
    "conditions": [{"fact": "tickets_waiting", "op": "gt", "value": 20}],
    "for_duration_sec": 300, "cooldown_sec": 0,
    "recipient": {"kind": "author"}, "template": "{subject_id} has {tickets_waiting} tickets waiting",
    "enabled": True, "created_by": "lead_sam",
}


def test_post_rules_creates_and_returns_201(tmp_path):
    client, _ = _client(tmp_path)
    resp = client.post("/rules", json=VALID_RULE)
    assert resp.status_code == 201
    assert "id" in resp.json()


def test_post_rules_invalid_rule_returns_422_with_field_detail(tmp_path):
    client, _ = _client(tmp_path)
    bad = {**VALID_RULE, "conditions": [{"fact": "current_state", "op": "gt", "value": "on_call"}]}
    resp = client.post("/rules", json=bad)
    assert resp.status_code == 422
    assert "unknown fact" in json.dumps(resp.json())


def test_post_rules_malformed_json_body_returns_400_not_500(tmp_path):
    """Code-review MEDIUM: request.json() raising json.JSONDecodeError was
    unhandled, returning an unstructured 500 instead of a client error --
    on a public, unauthenticated endpoint, malformed input is expected
    traffic, not a server fault."""
    client, _ = _client(tmp_path)
    resp = client.post(
        "/rules", content=b"{not valid json", headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


def test_get_rules_lists_created_rules(tmp_path):
    client, _ = _client(tmp_path)
    client.post("/rules", json=VALID_RULE)
    resp = client.get("/rules")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_rule_by_id(tmp_path):
    client, _ = _client(tmp_path)
    created = client.post("/rules", json=VALID_RULE).json()
    resp = client.get(f"/rules/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == VALID_RULE["name"]


def test_get_rule_missing_returns_404(tmp_path):
    client, _ = _client(tmp_path)
    resp = client.get("/rules/does-not-exist")
    assert resp.status_code == 404


def test_disable_rule_closes_open_episode_silently_no_resolved_notice(tmp_path):
    """R13: disable closes any open/pending episode WITHOUT a resolved
    notice -- the condition didn't recover, the rule went away."""
    client, state = _client(tmp_path)
    created = client.post("/rules", json=VALID_RULE).json()
    rule_id = created["id"]

    state.engine_deps.episodes_repo.create(EpisodeRecord(
        id="ep1", rule_id=rule_id, subject_id="billing", state="open",
        first_true_at=None, opened_at="2026-05-26T09:00:00Z", closed_at=None,
        last_notified_at="2026-05-26T09:00:00Z", notify_seq=0, evaluations_suppressed=0, stale=False,
    ))

    resp = client.post(f"/rules/{rule_id}/disable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    episode = state.engine_deps.episodes_repo.get("ep1")
    assert episode.state == "clear"
    notifications = state.engine_deps.notifications_repo.list(rule_id=rule_id)
    assert all(n.transition != "resolved" for n in notifications)


def test_disable_rule_missing_returns_404(tmp_path):
    client, _ = _client(tmp_path)
    resp = client.post("/rules/does-not-exist/disable")
    assert resp.status_code == 404


def test_disable_rule_bumps_version(tmp_path):
    """R13: rule mutation mid-episode is decided and documented -- version
    bumps so the change is observable."""
    client, state = _client(tmp_path)
    created = client.post("/rules", json=VALID_RULE).json()
    client.post(f"/rules/{created['id']}/disable")
    record = state.engine_deps.rules_repo.get(created["id"])
    assert record.version == 2
    assert record.enabled is False
