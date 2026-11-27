"""Use-cases чтения: списки, карточки и связи (для CLI, HTTP и UI).

Здесь нет ни разбора транспорта, ни рендеринга: только операции над
хранилищем через контракты ядра. ADR-0019: клиенты (CLI, HTTP, UI) тонкие,
логика одна.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from axislar_core.contracts import (
    Entity,
    EntityId,
    IReferenceIndex,
    IStorageEngine,
    Query,
    ReferenceValue,
)

#: Скаляр, который считается «заголовком» для показа.
TITLE_SCALAR = "title"


@dataclass(frozen=True, slots=True)
class ObjectRow:
    """Строка списка: минимум, нужный для таблицы."""

    id: str
    kind: str
    title: str | None


@dataclass(frozen=True, slots=True)
class ObjectList:
    """Страница списка объектов."""

    rows: tuple[ObjectRow, ...]
    total: int
    offset: int
    limit: int | None


@dataclass(frozen=True, slots=True)
class LinkRow:
    """Одно ребро: входящее или исходящее."""

    axis: str
    entity_id: str
    title: str | None
    direction: str


async def list_objects(
    storage: IStorageEngine,
    query: Query,
    *,
    offset: int = 0,
    limit: int = 50,
    title_scalar: str = TITLE_SCALAR,
) -> ObjectList:
    """Страница объектов по запросу: `ids` из `query`, строки — отдельным чтением."""
    page = query.model_copy(update={"offset": offset, "limit": limit})
    result = await storage.query(page)
    entities = await storage.fetch_many(result.ids)
    rows = tuple(
        ObjectRow(id=str(entity.id), kind=entity.kind, title=entity.scalars.get(title_scalar))
        for entity in entities
    )
    return ObjectList(rows=rows, total=result.total, offset=result.offset, limit=result.limit)


async def entity_links(
    storage: IStorageEngine,
    entity_id: EntityId,
    *,
    include_deleted: bool = False,
    title_scalar: str = TITLE_SCALAR,
) -> tuple[LinkRow, ...]:
    """Входящие и исходящие связи объекта.

    Исходящие читаются из фасетов самого объекта, входящие — через
    `IReferenceIndex`. Бэкенд без порта отдаёт только исходящие: «не умею» —
    состояние, а не ошибка (ADR-0021).
    """
    rows: list[LinkRow] = []
    entity = await storage.get(entity_id)
    if entity is not None:
        outgoing = [
            (axis, str(value.target))
            for axis, values in entity.facets.items()
            for value in values
            if isinstance(value, ReferenceValue)
        ]
        titles = await _titles(storage, [target for _, target in outgoing], title_scalar)
        rows.extend(
            LinkRow(axis=axis, entity_id=target, title=titles.get(target), direction="out")
            for axis, target in outgoing
        )

    if isinstance(storage, IReferenceIndex):
        incoming = await storage.references_to(entity_id, None, include_deleted=include_deleted)
        sources = [str(link.entity_id) for link in incoming]
        titles = await _titles(storage, sources, title_scalar)
        rows.extend(
            LinkRow(
                axis=link.axis,
                entity_id=str(link.entity_id),
                title=titles.get(str(link.entity_id)),
                direction="in",
            )
            for link in incoming
        )
    return tuple(rows)


async def _titles(
    storage: IStorageEngine, ids: Sequence[str], title_scalar: str
) -> dict[str, str | None]:
    if not ids:
        return {}
    entities = await storage.fetch_many(tuple(EntityId(entity_id) for entity_id in ids))
    return {str(entity.id): entity.scalars.get(title_scalar) for entity in entities}


def row_of(entity: Entity, title_scalar: str = TITLE_SCALAR) -> ObjectRow:
    """Строка списка из уже загруженного объекта."""
    return ObjectRow(id=str(entity.id), kind=entity.kind, title=entity.scalars.get(title_scalar))


__all__ = [
    "TITLE_SCALAR",
    "LinkRow",
    "ObjectList",
    "ObjectRow",
    "entity_links",
    "list_objects",
    "row_of",
]
