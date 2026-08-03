"""
Domain Rule Engine Models & Validation.

This module defines the schema and validation logic for notification rules configured
by end-users (e.g., Team Leads). Rather than using multiple rule classes, this system
employs a single generic `Rule` model that can be evaluated against different subjects
(Agents or Queues) by looking up facts in the Fact Registry.

Key Features:
- Rule Structure: Conjunction of Conditions (flat AND), for_duration_sec, cooldown_sec,
  and a template string for the notification body.
- Selector System: Supports filtering rule evaluations to all subjects, specific IDs,
  or queue memberships (e.g., "billing").
- Clean Validation: Utilizes Pydantic's `model_validator` to validate rule conditions
  against the `FactRegistry`. The registry is dynamically injected via validation
  context to strictly prevent domain circular dependencies.
- Compile-time checking: Prevents template failures during dispatch by validating
  placeholders at definition time against declared rule facts.
"""

import string
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from app.domain.facts import FactRegistry, FactSpec
from app.domain.subjects import SubjectType


# Supported operators for comparing a fact against a literal or reference value.
class Operator(str, Enum):
    GT = "gt"     # Greater than (e.g., tickets_waiting > 20)
    GTE = "gte"   # Greater than or equal to
    LT = "lt"     # Less than
    LTE = "lte"   # Less than or equal to
    EQ = "eq"     # Equal to (e.g., current_state == "on_call")
    NEQ = "neq"   # Not equal to
    IN = "in_"    # Membership (e.g., current_state in ["on_break", "in_meeting"])


class FactRef(BaseModel):
    """A tagged reference to another fact, e.g. {"fact_ref": "sla_target_sec"}.
    Tagged rather than string-sniffed so a literal string value can never be
    mistaken for a reference (PLAN.md 1.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_ref: str


# Valid literal value types that rules can check against.
LiteralValue = Union[str, float, int, bool, list]


class Condition(BaseModel):
    """Represents a single query clause (e.g. 'longest_wait_sec > 120' or 'longest_wait_sec > {fact_ref: sla_target_sec}')."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    fact: str                            # The left-hand fact name (e.g., "longest_wait_sec")
    op: Operator                         # The comparison operator (e.g., Operator.GT)
    value: Union[FactRef, LiteralValue]  # The right-hand operand (either a dynamic fact reference or a literal value)


# -- Subject Selectors --
# Selectors filter which agents or queues a rule is evaluated against.

class AllSelector(BaseModel):
    """Matches all subjects of the rule's subject type."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["all"] = "all"


class IdsSelector(BaseModel):
    """Matches a specific hardcoded list of subject IDs (e.g., agents ["a_11", "a_19"])."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["ids"] = "ids"
    ids: list[str]


class QueueMembershipSelector(BaseModel):
    """Matches agents who are currently members of specific queues (e.g., ["billing"]). Only valid when subject_type == agent."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["queue_membership"] = "queue_membership"
    queue_ids: list[str]


# Discriminated union for subject selectors, keyed on the "kind" field.
SubjectSelector = Annotated[
    Union[AllSelector, IdsSelector, QueueMembershipSelector],
    Field(discriminator="kind"),
]


# -- Notification Recipients --
# Defines who should receive notifications when a rule fires.

class RecipientKind(str, Enum):
    AUTHOR = "author"               # The team lead who created the rule (notified at rule.created_by)
    SUBJECT_AGENT = "subject_agent" # The agent that triggered the alert (only valid for subject_type == agent)
    CHANNEL = "channel"             # A specific alert channel (e.g., Console or InboxChannel)


class Recipient(BaseModel):
    """Configures the notification destination."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RecipientKind
    target: str | None = None       # Non-empty target is required if kind is CHANNEL


def _value_matches_type(value: Any, value_type: str) -> bool:
    """Strict helper for type checking operand values against the registered fact types.
    Ensures boolean values are not mistakenly accepted as numeric (number) values."""
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
        # Retrieve the injected fact registry from the Pydantic validation context.
        # This dynamically breaks direct compilation imports from domain -> evaluation.
        context = info.context or {}
        registry: FactRegistry | None = context.get("registry")
        if registry is None:
            raise ValueError(
                "Rule validation requires a FactRegistry injected via "
                "context={'registry': ...} -- see evaluation/registry.build_registry(). "
                "domain/rules.py never imports one implicitly (R1)."
            )

        # Collect valid fact names for this rule's subject type (Queue or Agent).
        valid_names = registry.names_for(self.subject_type)
        declared_facts: set[str] = set()

        # Validate each condition clause one-by-one.
        for condition in self.conditions:
            # 1. Fact Existence: Ensure the fact name is registered for the specified subject type.
            spec = registry.get(self.subject_type, condition.fact)
            if spec is None:
                raise ValueError(
                    f"unknown fact {condition.fact!r} for subject_type "
                    f"{self.subject_type.value!r}; valid facts: {sorted(valid_names)}"
                )
            declared_facts.add(condition.fact)

            # 2. Type Checking: Depending on operand type (dynamic reference vs. literal value), validate structure.
            if isinstance(condition.value, FactRef):
                self._validate_fact_ref(condition, spec, registry, valid_names, declared_facts)
            else:
                self._validate_operand_type(condition, spec)

        # 3. Routing Constraint: 'subject_agent' as a recipient only makes sense if evaluating agents.
        if self.recipient.kind == RecipientKind.SUBJECT_AGENT and self.subject_type != SubjectType.AGENT:
            raise ValueError("recipient.kind == 'subject_agent' requires subject_type == 'agent'")

        # 4. Target Constraint: CHANNEL routing requires a valid channel target (e.g. channel ID or name).
        if self.recipient.kind == RecipientKind.CHANNEL and not self.recipient.target:
            raise ValueError("recipient.kind == 'channel' requires a non-empty target")

        # 5. Compile-time template check: Ensure template fields map strictly to declared rule facts.
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
        """Validates a condition comparing a fact to another dynamic fact reference on the same subject."""
        assert isinstance(condition.value, FactRef)
        
        # 'in_' expects a literal list of options, not a singular dynamic scalar reference.
        if condition.op == Operator.IN:
            raise ValueError(
                f"condition on {condition.fact!r} uses op 'in_' with a fact_ref value "
                f"({condition.value.fact_ref!r}) -- fact_ref targets are always scalar, "
                "so 'in_' requires a literal list value instead"
            )
            
        # Ensure the referenced fact exists in the registry for this subject.
        ref_spec = registry.get(self.subject_type, condition.value.fact_ref)
        if ref_spec is None:
            raise ValueError(
                f"fact_ref {condition.value.fact_ref!r} does not exist for subject_type "
                f"{self.subject_type.value!r}; valid facts: {sorted(valid_names)}"
            )
            
        # Track the referenced fact so it can be dynamically injected during template rendering.
        declared_facts.add(condition.value.fact_ref)
        
        # Ensure data types match (e.g., cannot compare a string fact to a numeric fact).
        if ref_spec.value_type != spec.value_type:
            raise ValueError(
                f"fact_ref {condition.value.fact_ref!r} has value_type {ref_spec.value_type!r}, "
                f"incompatible with {condition.fact!r}'s value_type {spec.value_type!r}"
            )

    def _validate_operand_type(self, condition: Condition, spec: FactSpec) -> None:
        """Validates that a literal operand value is type-compatible with the fact's registered type."""
        # For membership checking, verify the operand is a list containing only elements of the correct type.
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
            
        # For standard scalar comparisons, check literal value matches expected type.
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
            # Parse placeholders out of the formatting string template.
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
