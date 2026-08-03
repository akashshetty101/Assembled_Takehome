import dataclasses
import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.domain.rules import Rule
from app.storage.db import transaction
from app.storage.repositories import RuleRecord

router = APIRouter()


def _rule_to_record(rule: Rule, rule_id: str, created_at: str, version: int) -> RuleRecord:
    return RuleRecord(
        id=rule_id, name=rule.name, subject_type=rule.subject_type.value,
        selector_json=rule.selector.model_dump_json(),
        conditions_json=json.dumps([c.model_dump(mode="json") for c in rule.conditions]),
        for_duration_sec=rule.for_duration_sec, cooldown_sec=rule.cooldown_sec,
        recipient_kind=rule.recipient.kind.value, recipient_target=rule.recipient.target,
        template=rule.template, enabled=rule.enabled, created_by=rule.created_by,
        created_at=created_at, version=version,
    )


def _record_to_dict(record: RuleRecord) -> dict:
    return {
        "id": record.id, "name": record.name, "subject_type": record.subject_type,
        "selector": json.loads(record.selector_json), "conditions": json.loads(record.conditions_json),
        "for_duration_sec": record.for_duration_sec, "cooldown_sec": record.cooldown_sec,
        "recipient": {"kind": record.recipient_kind, "target": record.recipient_target},
        "template": record.template, "enabled": record.enabled, "created_by": record.created_by,
        "created_at": record.created_at, "version": record.version,
    }


def _close_active_episodes_silently(app_state, rule_id: str) -> None:
    """R13: close silently on disable, no resolved notice -- the condition
    didn't recover, the rule went away; emitting 'recovered' would be a lie."""
    now = app_state.clock.now().isoformat()
    for episode_record in app_state.engine_deps.episodes_repo.list_active():
        if episode_record.rule_id != rule_id:
            continue
        closed = dataclasses.replace(episode_record, state="clear", closed_at=now)
        app_state.engine_deps.episodes_repo.update(closed)


@router.post("/rules", status_code=201)
async def create_rule(request: Request):
    app_state = request.app.state.app_state
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=400, content={"detail": f"invalid JSON body: {e}"})
    try:
        rule = Rule.model_validate(body, context={"registry": app_state.registry})
    except ValidationError as e:
        return JSONResponse(status_code=422, content={"detail": json.loads(e.json())})

    rule_id = str(uuid.uuid4())
    now = app_state.clock.now().isoformat()
    record = _rule_to_record(rule, rule_id, now, 1)
    with transaction(app_state.conn):
        app_state.engine_deps.rules_repo.create(record)
    # note_rule_activated: ScanScheduler recomputes live on every due() call,
    # so this is a documented no-op today -- HeapScheduler (Phase 8) is the
    # implementation this call matters for.
    app_state.scheduler.note_rule_activated(rule_id)
    return _record_to_dict(record)


@router.get("/rules")
def list_rules(request: Request):
    app_state = request.app.state.app_state
    return [_record_to_dict(r) for r in app_state.engine_deps.rules_repo.list()]


@router.get("/rules/{rule_id}")
def get_rule(rule_id: str, request: Request):
    app_state = request.app.state.app_state
    record = app_state.engine_deps.rules_repo.get(rule_id)
    if record is None:
        return JSONResponse(status_code=404, content={"detail": "rule not found"})
    return _record_to_dict(record)


@router.post("/rules/{rule_id}/disable")
def disable_rule(rule_id: str, request: Request):
    app_state = request.app.state.app_state
    record = app_state.engine_deps.rules_repo.get(rule_id)
    if record is None:
        return JSONResponse(status_code=404, content={"detail": "rule not found"})

    updated = dataclasses.replace(record, enabled=False, version=record.version + 1)
    with transaction(app_state.conn):
        app_state.engine_deps.rules_repo.update(updated)
        _close_active_episodes_silently(app_state, rule_id)
    return _record_to_dict(updated)
