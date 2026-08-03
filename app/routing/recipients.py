from dataclasses import dataclass

from app.domain.rules import RecipientKind, Rule


@dataclass(frozen=True, slots=True)
class RecipientRef:
    kind: str
    target: str


def resolve_recipient(rule: Rule, subject_id: str) -> RecipientRef:
    """Pure, no I/O (PLAN.md 1.1). author -> rule.created_by; subject_agent
    -> the subject itself (Rule's own validator already guarantees
    subject_type == agent whenever this kind is used); channel ->
    rule.recipient.target (guaranteed non-empty by the same validator)."""
    if rule.recipient.kind == RecipientKind.AUTHOR:
        return RecipientRef(kind="author", target=rule.created_by)
    if rule.recipient.kind == RecipientKind.SUBJECT_AGENT:
        return RecipientRef(kind="subject_agent", target=subject_id)
    assert rule.recipient.target is not None
    return RecipientRef(kind="channel", target=rule.recipient.target)
