from datetime import datetime
from typing import Any, Callable, Literal

from app.domain.subjects import SubjectType


class _Missing:
    """Sentinel distinct from None and False. A fact is MISSING when it
    cannot be computed from available data (e.g.
    adherence_violation_duration_sec when violation_start_source ==
    'unknown') -- distinct from a fact whose real value happens to be
    None/False. v1 collapses MISSING to false at evaluation time
    (evaluation/operators.py), but the sentinel is introduced now so
    Phase 10's three-valued-logic upgrade is an assertion flip on top of
    tests already asserting `missing_facts`, not new plumbing (R2)."""

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        raise TypeError("MISSING has no truthiness -- check `is MISSING` explicitly")


MISSING = _Missing()

ValueType = Literal["number", "bool", "str"]


class FactSpec:
    __slots__ = ("name", "subject_type", "value_type", "extractor")

    def __init__(
        self,
        name: str,
        subject_type: SubjectType,
        value_type: ValueType,
        extractor: Callable[[Any, datetime], Any],
    ) -> None:
        self.name = name
        self.subject_type = subject_type
        self.value_type = value_type
        self.extractor = extractor


class FactRegistry:
    def __init__(self, specs: list[FactSpec]) -> None:
        self._by_subject: dict[SubjectType, dict[str, FactSpec]] = {}
        for spec in specs:
            bucket = self._by_subject.setdefault(spec.subject_type, {})
            if spec.name in bucket:
                raise ValueError(
                    f"duplicate fact {spec.name!r} registered twice for subject_type "
                    f"{spec.subject_type.value!r}"
                )
            bucket[spec.name] = spec

    def get(self, subject_type: SubjectType, name: str) -> FactSpec | None:
        return self._by_subject.get(subject_type, {}).get(name)

    def names_for(self, subject_type: SubjectType) -> list[str]:
        return list(self._by_subject.get(subject_type, {}).keys())
