"""Субграф связей: раскрытие узла и окно окружения (фаза 4, 4.1).

Задача — отдать клиенту ровно столько графа, сколько он собирается показать:
BFS от корней на `depth` шагов, фильтр по осям, направление (`out`/`in`/`both`)
и потолок числа узлов. Если потолок упёрся, `truncated=True` — клиент обязан
сказать об этом, а не рисовать «весь» граф молча.

Битые ссылки не скрываются: цель, которой нет среди живых объектов, приходит
узлом `ghost=True`. Это не модель, а отображение (ADR-0025 ядра): висячие
ссылки существуют после `sync`, и граф о них не должен врать.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from axislar_core.contracts import (
    Entity,
    EntityId,
    IReferenceIndex,
    IStorageEngine,
    ReferenceValue,
)

from nu.beta.public.objects import TITLE_SCALAR

#: Направление обхода: что раскрываем от узла.
DIRECTIONS = ("out", "in", "both")

#: Потолок узлов в одном ответе: больше не рисуем без явного запроса.
DEFAULT_LIMIT = 200


@dataclass(frozen=True, slots=True)
class GraphNode:
    """Узел графа. `ghost` — ссылка на удалённый или отсутствующий объект."""

    id: str
    label: str
    kind: str
    ghost: bool = False


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """Ребро: источник ссылается на цель по оси `axis`."""

    source: str
    target: str
    axis: str
    dangling: bool = False


@dataclass(frozen=True, slots=True)
class Subgraph:
    """Окно графа: узлы, рёбра и честный признак обрезки."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    truncated: bool
    total: int


def _node(entity: Entity) -> GraphNode:
    title = entity.scalars.get(TITLE_SCALAR)
    return GraphNode(
        id=str(entity.id),
        label=str(title) if title else entity.kind,
        kind=entity.kind,
    )


def _ghost(entity_id: str) -> GraphNode:
    return GraphNode(id=entity_id, label=f"{entity_id[:8]}…", kind="ghost", ghost=True)


def _outgoing(entity: Entity, axes: Sequence[str] | None) -> Iterable[tuple[str, str]]:
    """Пары «ось, цель» из reference-фасетов объекта."""
    for axis, values in entity.facets.items():
        if axes is not None and axis not in axes:
            continue
        for value in values:
            if isinstance(value, ReferenceValue):
                yield axis, str(value.target)


async def subgraph(
    storage: IStorageEngine,
    roots: Sequence[EntityId],
    *,
    depth: int = 1,
    axes: Sequence[str] | None = None,
    direction: str = "both",
    limit: int = DEFAULT_LIMIT,
) -> Subgraph:
    """Окно графа вокруг `roots`.

    `depth=0` — только сами корни. Потолок `limit` считается по узлам: как
    только он достигнут, обход останавливается и `truncated=True`.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    if depth < 0:
        raise ValueError("depth must be >= 0")
    if limit <= 0:
        raise ValueError("limit must be > 0")

    live = {str(entity.id): entity for entity in await storage.list_entities()}
    index = storage if isinstance(storage, IReferenceIndex) else None

    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    frontier: list[str] = []

    for root in roots:
        key = str(root)
        if key in nodes:
            continue
        entity = live.get(key)
        nodes[key] = _node(entity) if entity is not None else _ghost(key)
        frontier.append(key)

    level = 0
    truncated = False
    while frontier and level < depth:
        level += 1
        following: list[str] = []
        for key in frontier:
            entity = live.get(key)
            if entity is None:
                continue
            if direction in {"out", "both"}:
                for axis, target in _outgoing(entity, axes):
                    edge = (key, target, axis)
                    if edge in edges:
                        continue
                    edges[edge] = GraphEdge(
                        source=key, target=target, axis=axis, dangling=target not in live
                    )
                    if target not in nodes:
                        if len(nodes) >= limit:
                            truncated = True
                            continue
                        nodes[target] = _node(live[target]) if target in live else _ghost(target)
                        following.append(target)
            if direction in {"in", "both"} and index is not None:
                for link in await index.references_to(EntityId(key), axes):
                    source = str(link.entity_id)
                    edge = (source, key, link.axis)
                    if edge in edges:
                        continue
                    edges[edge] = GraphEdge(
                        source=source, target=key, axis=link.axis, dangling=source not in live
                    )
                    if source not in nodes:
                        if len(nodes) >= limit:
                            truncated = True
                            continue
                        nodes[source] = _node(live[source]) if source in live else _ghost(source)
                        following.append(source)
        frontier = following

    return Subgraph(
        nodes=tuple(nodes.values()),
        edges=tuple(edges.values()),
        truncated=truncated,
        total=len(nodes),
    )


__all__ = [
    "DEFAULT_LIMIT",
    "DIRECTIONS",
    "GraphEdge",
    "GraphNode",
    "Subgraph",
    "subgraph",
]
