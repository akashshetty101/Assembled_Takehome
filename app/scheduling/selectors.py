import json


def resolve_selector_subject_ids(rule_record, queue_repo, agent_repo) -> list[str]:
    """Which subject ids a rule's selector currently resolves to. Shared,
    public (code-review HIGH fix): both scheduling/scan.py's due() and
    engine/engine.py's on_event() need this, and a private, underscore-
    prefixed helper imported across module boundaries is a coupling smell
    -- nothing would stop scan.py's maintainer from changing its behavior
    for scan-specific reasons and silently breaking engine.py's selector
    matching. Promoted here as the one shared definition."""
    selector = json.loads(rule_record.selector_json)
    kind = selector.get("kind")
    if rule_record.subject_type == "queue":
        if kind == "ids":
            return selector["ids"]
        return [s.queue_id for s in queue_repo.list()]
    if kind == "ids":
        return selector["ids"]
    if kind == "queue_membership":
        wanted = set(selector["queue_ids"])
        return [a.agent_id for a in agent_repo.list() if wanted & set(a.queue_ids)]
    return [a.agent_id for a in agent_repo.list()]
