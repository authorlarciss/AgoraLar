"""Пакетная правка объектов: `bulk_patch` (фаза 2 продукта).

Сценарий, ради которого это существует: «найти по фильтру 300 объектов и
присвоить им фасет». Один вызов, одна валидация, одна ревизия на объект —
вместо 300 запросов и 300 конфликтов (ADR-0024).

Операции описываются данными, а не кодом: ядро остаётся про модель, а не
про «кнопки интерфейса».
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from axislar_core.contracts import (
    CategoricalValue,
    DomainError,
    Entity,
    EntityId,
    ErrorCode,
    FacetOwner,
    FacetValue,
    IStorageEngine,
    MeasurableValue,
    ReferenceValue,
)


@dataclass(frozen=True, slots=True)
class FacetPatch:
    """Что сделать с одной осью.

    `mode`: `set` — заменить значения, `add` — добавить (без дублей),
    `unset` — убрать ось целиком.
    """

    axis: str
    mode: str
    values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"set", "add", "unset"}:
            raise ValueError(f"unknown patch mode {self.mode!r}; expected set/add/unset")
        if self.mode != "unset" and not self.values:
            raise ValueError(f"patch {self.mode!r} for {self.axis!r} requires values")


@dataclass(frozen=True, slots=True)
class BulkPatchResult:
    """Итог: какие объекты изменены, где конфликт и что пропущено."""

    changed: tuple[tuple[str, int], ...] = ()
    conflicts: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    errors: tuple[DomainError, ...] = ()


def apply_patches(
    entity: Entity, patches: Sequence[FacetPatch], *, force: bool = False
) -> tuple[Entity, tuple[str, ...]]:
    """Применить операции к объекту; вернуть объект и список пропущенных осей.

    Фасет источника (`owners=source`) по умолчанию не трогаем: `sync` вернёт
    своё значение и правка исчезнет. С `force` правка применяется, и владение
    переходит человеку (`owners=user`) — тогда синхронизация эту ось не
    перезапишет (ADR-0016).
    """
    facets = dict(entity.facets)
    owners = dict(entity.owners)
    skipped: list[str] = []
    for patch in patches:
        if owners.get(patch.axis) is FacetOwner.SOURCE and not force:
            skipped.append(patch.axis)
            continue
        if owners.get(patch.axis) is FacetOwner.SOURCE and force:
            owners[patch.axis] = FacetOwner.USER
        if patch.mode == "unset":
            facets.pop(patch.axis, None)
            owners.pop(patch.axis, None)
            continue
        values = tuple(_value(patch.axis, raw, facets.get(patch.axis, ())) for raw in patch.values)
        if patch.mode == "set":
            facets[patch.axis] = values
        else:
            current = list(facets.get(patch.axis, ()))
            for value in values:
                if value not in current:
                    current.append(value)
            facets[patch.axis] = tuple(current)
        if patch.axis not in facets:
            owners.pop(patch.axis, None)
    return entity.model_copy(update={"facets": facets, "owners": owners}), tuple(skipped)


async def bulk_patch(
    storage: IStorageEngine,
    ids: Sequence[EntityId],
    patches: Sequence[FacetPatch],
    *,
    expected_revs: Mapping[str, int] | None = None,
    force: bool = False,
) -> BulkPatchResult:
    """Применить операции к набору объектов.

    Конфликт по любому объекту не отменяет остальные: результат честно
    сообщает, что изменено, а что нет. Это отличается от атомарной пачки
    импорта (`bulk_upsert`) — там «всё или ничего» обязательно из-за ссылок.
    """
    changed: list[tuple[str, int]] = []
    conflicts: list[str] = []
    skipped: list[tuple[str, str]] = []
    errors: list[DomainError] = []
    for entity_id in ids:
        key = str(entity_id)
        entity = await storage.get(entity_id)
        if entity is None:
            errors.append(
                DomainError(
                    code=ErrorCode.CONFLICT,
                    detail="object does not exist",
                    entity_id=entity_id,
                )
            )
            continue
        patched, omitted = apply_patches(entity, patches, force=force)
        skipped.extend((key, axis) for axis in omitted)
        if patched == entity:
            # Менять нечего: все операции пропущены. Ревизия не растёт —
            # иначе «пропущено» выглядело бы как правка и ломало чужой If-Match.
            continue
        expected = expected_revs.get(key) if expected_revs else entity.rev
        found = await storage.upsert(patched, expected_rev=expected)
        if not found:
            saved = await storage.get(entity_id)
            if saved is None:  # pragma: no cover - запись прошла, объект есть
                raise RuntimeError(f"object {key!r} disappeared after upsert")
            changed.append((key, saved.rev))
            continue
        if any(error.code is ErrorCode.CONFLICT for error in found):
            conflicts.append(key)
        errors.extend(found)
    return BulkPatchResult(
        changed=tuple(changed),
        conflicts=tuple(conflicts),
        skipped=tuple(skipped),
        errors=tuple(errors),
    )


def _value(axis: str, raw: str, current: tuple[FacetValue, ...]) -> FacetValue:
    """Значение нужного типа: тип берём из текущих значений оси, иначе строка."""
    sample = current[0] if current else None
    if isinstance(sample, ReferenceValue):
        return ReferenceValue(target=EntityId(raw))
    if isinstance(sample, MeasurableValue):
        return MeasurableValue(value=float(raw))
    return CategoricalValue(value=raw)


__all__ = [
    "BulkPatchResult",
    "FacetPatch",
    "apply_patches",
    "bulk_patch",
]
