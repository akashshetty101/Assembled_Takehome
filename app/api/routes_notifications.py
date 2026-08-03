import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.routing.renderer import render_suppression_summary

router = APIRouter()


def _notification_to_dict(n, suppression_summary: str | None = None, stale: bool = False) -> dict:
    return {
        "id": n.id, "episode_id": n.episode_id, "rule_id": n.rule_id, "subject_id": n.subject_id,
        "transition": n.transition, "occurrence_seq": n.occurrence_seq,
        "recipient_kind": n.recipient_kind, "recipient_target": n.recipient_target,
        "body": n.body, "facts_snapshot": json.loads(n.facts_snapshot_json),
        "event_time": n.event_time, "created_at": n.created_at,
        "suppression_summary": suppression_summary,
        "stale": stale,
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


def _latest_notification_ids(notifications) -> set[str]:
    """The episode row is a single mutable record -- last_notified_at and
    evaluations_suppressed only ever describe its CURRENT state. Rendering
    that state onto every historical notification for the episode (bug
    found reviewing the PDF export) made an OPENED notification display a
    later REMINDER's 'notified once at ...' timestamp, sometimes dated
    AFTER the OPENED notification itself. Only the most recent notification
    per episode may legitimately show the live summary. Repo's `list()` is
    ORDER BY created_at, so the last occurrence of each episode_id wins."""
    latest: dict[str, str] = {}
    for n in notifications:
        latest[n.episode_id] = n.id
    return set(latest.values())


def _to_dict_with_suppression(app_state, n, as_of, is_latest: bool) -> dict:
    """PLAN.md Phase 6 item 3: per-episode suppression summary, looked up
    once per notification -- the episode row already carries
    evaluations_suppressed/last_notified_at, no separate aggregation
    needed. Only rendered for the episode's latest notification (see
    `_latest_notification_ids`); earlier notifications in the same episode
    get no summary rather than a copy of a later one's state. Phase 9: the
    same lookup carries `stale` -- an episode frozen awaiting data (a
    driving fact went MISSING) renders "stale — awaiting data" instead of a
    false RESOLVED, since no such notification is ever emitted for it. Also
    gated on `is_latest`: `stale` is live episode state too, so an
    OPENED/REMINDER sent before the episode ever went stale must not
    retroactively render as stale."""
    episode = app_state.engine_deps.episodes_repo.get(n.episode_id)
    summary = render_suppression_summary(episode, as_of) if episode and is_latest else None
    stale = episode.stale if episode and is_latest else False
    return _notification_to_dict(n, suppression_summary=summary, stale=stale)


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
    latest_ids = _latest_notification_ids(notifications)
    grouped: dict[str, list] = {}
    for n in notifications:
        grouped.setdefault(n.recipient_target, []).append(
            _to_dict_with_suppression(app_state, n, as_of, n.id in latest_ids)
        )
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
    latest_ids = _latest_notification_ids(notifications)
    return [_to_dict_with_suppression(app_state, n, as_of, n.id in latest_ids) for n in notifications]
