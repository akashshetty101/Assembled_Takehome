import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.routing.renderer import render_suppression_summary

router = APIRouter()


def _notification_to_dict(n, suppression_summary: str | None = None) -> dict:
    return {
        "id": n.id, "episode_id": n.episode_id, "rule_id": n.rule_id, "subject_id": n.subject_id,
        "transition": n.transition, "occurrence_seq": n.occurrence_seq,
        "recipient_kind": n.recipient_kind, "recipient_target": n.recipient_target,
        "body": n.body, "facts_snapshot": json.loads(n.facts_snapshot_json),
        "event_time": n.event_time, "created_at": n.created_at,
        "suppression_summary": suppression_summary,
    }


def _latest_known_event_time(app_state):
    """Event time, not wall time (PLAN.md Sec 1.6): a still-open episode's
    suppression 'as of' must be the latest point the system has actually
    observed data for. Using the live clock (SystemClock in normal
    operation) would compute a nonsensical duration any time the API is
    queried well after the underlying data was ingested -- e.g. querying
    long after a historical replay demo -- since last_notified_at is an
    event-time timestamp from the data, not from whenever this request
    happens to run."""
    timestamps = [s.last_event_ts for s in app_state.engine_deps.queue_repo.list()]
    timestamps += [s.last_event_ts for s in app_state.engine_deps.agent_repo.list()]
    return max(timestamps) if timestamps else app_state.clock.now()


def _to_dict_with_suppression(app_state, n, as_of) -> dict:
    """PLAN.md Phase 6 item 3: per-episode suppression summary, looked up
    once per notification -- the episode row already carries
    evaluations_suppressed/last_notified_at, no separate aggregation
    needed."""
    episode = app_state.engine_deps.episodes_repo.get(n.episode_id)
    summary = render_suppression_summary(episode, as_of) if episode else None
    return _notification_to_dict(n, suppression_summary=summary)


@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(
    request: Request, recipient: str | None = None, rule_id: str | None = None, subject_id: str | None = None,
):
    """Grouped by recipient by default -- that grouping IS the product
    argument (PLAN.md 1.8), not a toggle."""
    app_state = request.app.state.app_state
    notifications = app_state.engine_deps.notifications_repo.list(
        recipient_target=recipient, rule_id=rule_id, subject_id=subject_id,
    )
    as_of = _latest_known_event_time(app_state)
    grouped: dict[str, list] = {}
    for n in notifications:
        grouped.setdefault(n.recipient_target, []).append(_to_dict_with_suppression(app_state, n, as_of))
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "notifications.html",
        {"grouped": grouped, "counters": app_state.counters.snapshot()},
    )


@router.get("/api/notifications")
def notifications_json(
    request: Request, recipient: str | None = None, rule_id: str | None = None, subject_id: str | None = None,
):
    app_state = request.app.state.app_state
    notifications = app_state.engine_deps.notifications_repo.list(
        recipient_target=recipient, rule_id=rule_id, subject_id=subject_id,
    )
    as_of = _latest_known_event_time(app_state)
    return [_to_dict_with_suppression(app_state, n, as_of) for n in notifications]
