"""Статистика хранилища: заполненность осей, распределения, артефакты.

Считается по объектам и схеме, без привязки к способу показа. Потребители:
HTML-отчёт (`agoralar visualize`) и HTTP-маршрут `/api/v1/stats` для UI.
Рендеринг живёт в `gamma`: здесь только данные (ADR-0019 — доменная логика
в `beta`, представление отдельно).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Any

from axislar_core.contracts import (
    ArtifactRef,
    AxisKind,
    CategoricalValue,
    Entity,
    MeasurableValue,
    ReferenceValue,
    StorageSchema,
)

#: Сколько значений показывать в распределении оси.
TOP_VALUES = 10


@dataclass(frozen=True, slots=True)
class AxisStats:
    """Сводка по одной оси."""

    name: str
    kind: str
    multi: bool
    filled: int
    total: int
    distinct: int
    top: tuple[tuple[str, int], ...] = ()
    minimum: float | None = None
    middle: float | None = None
    maximum: float | None = None

    @property
    def share(self) -> float:
        return (self.filled / self.total * 100.0) if self.total else 0.0


@dataclass(frozen=True, slots=True)
class ScalarStats:
    """Сколько объектов заполнили скаляр."""

    name: str
    filled: int
    total: int

    @property
    def share(self) -> float:
        return (self.filled / self.total * 100.0) if self.total else 0.0


@dataclass(frozen=True, slots=True)
class StoreStats:
    """Всё, что нужно для отчёта."""

    store: str
    total: int
    deleted: int
    artifacts: int
    artifact_bytes: int
    axes: tuple[AxisStats, ...]
    scalars: tuple[ScalarStats, ...]

    def as_dict(self) -> dict[str, Any]:
        """Данные отчёта как JSON-совместимый словарь (для HTTP и отчёта)."""
        return {
            "store": self.store,
            "objects": {
                "total": self.total,
                "visible": self.visible,
                "deleted": self.deleted,
            },
            "artifacts": {"count": self.artifacts, "bytes": self.artifact_bytes},
            "axes": [
                {
                    "name": axis.name,
                    "kind": axis.kind,
                    "multi": axis.multi,
                    "filled": axis.filled,
                    "share": round(axis.share, 1),
                    "distinct": axis.distinct,
                    "top": list(axis.top),
                    "min": axis.minimum,
                    "median": axis.middle,
                    "max": axis.maximum,
                }
                for axis in self.axes
            ],
            "scalars": [
                {"name": scalar.name, "filled": scalar.filled, "share": round(scalar.share, 1)}
                for scalar in self.scalars
            ],
        }

    @property
    def visible(self) -> int:
        return self.total - self.deleted

    @property
    def empty_axes(self) -> tuple[str, ...]:
        return tuple(axis.name for axis in self.axes if axis.filled == 0)


def store_stats(
    store: str,
    schema: StorageSchema,
    entities: tuple[Entity, ...],
    artifacts: tuple[ArtifactRef, ...],
) -> StoreStats:
    """Посчитать сводку по объектам и артефактам."""
    visible = tuple(entity for entity in entities if entity.deleted_at is None)
    total = len(entities)
    axes = tuple(_axis_stats(axis, visible, total) for axis in schema.axes)
    scalars = tuple(
        ScalarStats(
            name=scalar.name,
            filled=sum(1 for entity in visible if entity.scalars.get(scalar.name)),
            total=len(visible),
        )
        for scalar in schema.scalars
    )
    return StoreStats(
        store=store,
        total=total,
        deleted=total - len(visible),
        artifacts=len(artifacts),
        artifact_bytes=sum(reference.size for reference in artifacts),
        axes=axes,
        scalars=scalars,
    )


def _axis_stats(axis: Any, entities: tuple[Entity, ...], total: int) -> AxisStats:
    values: list[Any] = []
    filled = 0
    for entity in entities:
        present = entity.facets.get(axis.name)
        if present:
            filled += 1
            values.extend(present)

    kind = axis.kind.value if isinstance(axis.kind, AxisKind) else str(axis.kind)
    if axis.kind is AxisKind.MEASURABLE:
        numbers = [value.value for value in values if isinstance(value, MeasurableValue)]
        stats = AxisStats(
            name=axis.name,
            kind=kind,
            multi=axis.multi,
            filled=filled,
            total=total,
            distinct=len(set(numbers)),
            minimum=min(numbers) if numbers else None,
            middle=float(median(numbers)) if numbers else None,
            maximum=max(numbers) if numbers else None,
        )
        return stats

    counter = _labels(values)
    return AxisStats(
        name=axis.name,
        kind=kind,
        multi=axis.multi,
        filled=filled,
        total=total,
        distinct=len(counter),
        top=tuple(counter.most_common(TOP_VALUES)),
    )


def _labels(values: list[Any]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for value in values:
        if isinstance(value, CategoricalValue):
            counter[value.value] += 1
        elif isinstance(value, ReferenceValue):
            counter[str(value.target)] += 1
        elif isinstance(value, MeasurableValue):
            counter[repr(value.value)] += 1
    return counter
