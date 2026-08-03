import string
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from app.domain.facts import FactRegistry, FactSpec
from app.domain.subjects import SubjectType


class Operator(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NEQ = "neq"
    IN = "in_"


class FactRef(BaseModel):
    """A tagged reference to another fact, e.g. {"fact_ref": "sla_target_sec"}.
    Tagged rather than string-sniffed so a literal string value can never be
    mistaken for a reference (PLAN.md 1.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_ref: str


LiteralValue = Union[str, float, int, bool, list]


class Condition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fact: str
    op: Operator
    value: Union[FactRef, LiteralValue]


class AllSelector(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["all"] = "all"


class IdsSelector(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["ids"] = "ids"
    ids: list[str]


class QueueMembershipSelector(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["queue_membership"] = "queue_membership"
    queue_ids: list[str]


SubjectSelector = Annotated[
    Union[AllSelector, IdsSelector, QueueMembershipSelector],
    Field(discriminator="kind"),
]


class RecipientKind(str, Enum):
    AUTHOR = "author"
    SUBJECT_AGENT = "subject_agent"
    CHANNEL = "channel"


class Recipient(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RecipientKind
    target: str | None = None


def _value_matches_type(value: Any, value_type: str) -> bool:
    if value_type == "bool":
        return isinstance(value, bool)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "str":
        return isinstance(value, str)
    return False


class Rule(BaseModel):
    """One generic rule over two fact sets (PLAN.md 1.2) -- not three rule
    classes. The registry this validates against is injected via
    ValidationInfo.context, never imported (R1): domain/ must not depend on
    evaluation/, and a module-level registry import would reopen the exact
    import-order hazard evaluation/registry.py's eager build_registry()
    exists to close."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    subject_type: SubjectType
    selector: SubjectSelector
    conditions: list[Condition] = Field(min_length=1)
    for_duration_sec: int = Field(default=0, ge=0)
    cooldown_sec: int = Field(default=0, ge=0)
    recipient: Recipient
    template: str
    enabled: bool = True
    created_by: str

    @model_validator(mode="after")
    def _validate_against_registry(self, info: ValidationInfo) -> "Rule":
        context = info.context or {}
        registry: FactRegistry | None = context.get("registry")
        if registry is None:
            raise ValueError(
                "Rule validation requires a FactRegistry injected via "
                "context={'registry': ...} -- see evaluation/registry.build_registry(). "
                "domain/rules.py never imports one implicitly (R1)."
            )

        valid_names = registry.names_for(self.subject_type)
        declared_facts: set[str] = set()

        for condition in self.conditions:
            spec = registry.get(self.subject_type, condition.fact)
            if spec is None:
                raise ValueError(
                    f"unknown fact {condition.fact!r} for subject_type "
                    f"{self.subject_type.value!r}; valid facts: {sorted(valid_names)}"
                )
            declared_facts.add(condition.fact)

            if isinstance(condition.value, FactRef):
                self._validate_fact_ref(condition, spec, registry, valid_names, declared_facts)
            else:
                self._validate_operand_type(condition, spec)

        if self.recipient.kind == RecipientKind.SUBJECT_AGENT and self.subject_type != SubjectType.AGENT:
            raise ValueError("recipient.kind == 'subject_agent' requires subject_type == 'agent'")

        if self.recipient.kind == RecipientKind.CHANNEL and not self.recipient.target:
            raise ValueError("recipient.kind == 'channel' requires a non-empty target")

        self._validate_template(declared_facts)

        return self

    def _validate_fact_ref(
        self,
        condition: Condition,
        spec: FactSpec,
        registry: FactRegistry,
        valid_names: list[str],
        declared_facts: set[str],
    ) -> None:
        assert isinstance(condition.value, FactRef)
        if condition.op == Operator.IN:
            raise ValueError(
                f"condition on {condition.fact!r} uses op 'in_' with a fact_ref value "
                f"({condition.value.fact_ref!r}) -- fact_ref targets are always scalar, "
                "so 'in_' requires a literal list value instead"
            )
        ref_spec = registry.get(self.subject_type, condition.value.fact_ref)
        if ref_spec is None:
            raise ValueError(
                f"fact_ref {condition.value.fact_ref!r} does not exist for subject_type "
                f"{self.subject_type.value!r}; valid facts: {sorted(valid_names)}"
            )
        declared_facts.add(condition.value.fact_ref)
        if ref_spec.value_type != spec.value_type:
            raise ValueError(
                f"fact_ref {condition.value.fact_ref!r} has value_type {ref_spec.value_type!r}, "
                f"incompatible with {condition.fact!r}'s value_type {spec.value_type!r}"
            )

    def _validate_operand_type(self, condition: Condition, spec: FactSpec) -> None:
        if condition.op == Operator.IN:
            if not isinstance(condition.value, list):
                raise ValueError(
                    f"condition on {condition.fact!r} with op 'in_' requires a list value"
                )
            if not all(_value_matches_type(v, spec.value_type) for v in condition.value):
                raise ValueError(
                    f"condition on {condition.fact!r} has an 'in_' value with an element "
                    f"incompatible with value_type {spec.value_type!r}"
                )
            return
        if not _value_matches_type(condition.value, spec.value_type):
            raise ValueError(
                f"condition on {condition.fact!r} (value_type={spec.value_type!r}) has an "
                f"incompatible literal value {condition.value!r}"
            )

    def _validate_template(self, declared_facts: set[str]) -> None:
        """Template compile-check against the DECLARED fact set (this
        rule's own conditions + fact_ref targets, plus the always-available
        {subject_id}) -- stops a rule that would KeyError at delivery time,
        even if the referenced name is a real, registered fact elsewhere."""
        allowed = declared_facts | {"subject_id"}
        try:
            field_names = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(self.template)
                if field_name is not None
            }
        except ValueError as e:
            raise ValueError(f"template is not a valid format string: {e}") from e

        unknown = field_names - allowed
        if unknown:
            raise ValueError(
                f"template references fact(s) {sorted(unknown)} not present in this rule's "
                f"conditions or fact_ref targets: {sorted(allowed)}"
            )
