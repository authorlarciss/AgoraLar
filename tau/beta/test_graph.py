"""Субграф связей: раскрытие, направление, ghost-узлы и потолок (4.1)."""

from __future__ import annotations

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    Entity,
    EntityId,
    FacetValue,
    IStorageEngine,
    ReferenceValue,
    build_core,
)

from nu.psi.contracts.beta import subgraph


async def storage() -> IStorageEngine:
    engine = build_core().storage
    assert (
        await engine.add_axis(
            AxisDefinition(name="links", kind=AxisKind.REFERENCE, multi=True, target="note")
        )
        == []
    )
    assert (
        await engine.add_axis(AxisDefinition(name="tags", kind=AxisKind.CATEGORICAL, multi=True))
        == []
    )
    return engine


def entity(entity_id: str, *targets: str) -> Entity:
    facets: dict[str, tuple[FacetValue, ...]] = {}
    if targets:
        facets["links"] = tuple(ReferenceValue(target=EntityId(item)) for item in targets)
    return Entity(id=EntityId(entity_id), kind="note", facets=facets)


async def graph_engine() -> IStorageEngine:
    """Живые a-1 → b-2, c-3 → a-1; цель d-4 стала tombstone: это ghost."""
    from datetime import UTC, datetime

    engine = await storage()
    gone = entity("gone-9")
    tombstone = gone.model_copy(update={"deleted_at": datetime.now(UTC)})
    errors = await engine.bulk_upsert(
        [
            tombstone,
            entity("b-2"),
            entity("a-1", "b-2"),
            entity("c-3", "a-1"),
            entity("d-4", "gone-9"),
        ]
    )
    assert errors == []
    return engine


async def test_outgoing_depth_one() -> None:
    engine = await graph_engine()
    window = await subgraph(engine, [EntityId("a-1")], depth=1, direction="out")
    assert {node.id for node in window.nodes} == {"a-1", "b-2"}
    assert [(edge.source, edge.target, edge.axis) for edge in window.edges] == [
        ("a-1", "b-2", "links")
    ]
    assert window.truncated is False


async def test_both_directions_include_referrers() -> None:
    engine = await graph_engine()
    window = await subgraph(engine, [EntityId("a-1")], depth=1, direction="both")
    assert {node.id for node in window.nodes} == {"a-1", "b-2", "c-3"}


async def test_dangling_target_becomes_ghost() -> None:
    engine = await graph_engine()
    window = await subgraph(engine, [EntityId("d-4")], depth=1, direction="out")
    ghost = next(node for node in window.nodes if node.id == "gone-9")
    assert ghost.ghost is True and ghost.kind == "ghost"
    assert any(edge.dangling for edge in window.edges)


async def test_depth_zero_returns_only_roots() -> None:
    engine = await graph_engine()
    window = await subgraph(engine, [EntityId("a-1")], depth=0)
    assert [node.id for node in window.nodes] == ["a-1"]
    assert window.edges == ()


async def test_limit_marks_truncated() -> None:
    engine = await graph_engine()
    window = await subgraph(engine, [EntityId("a-1")], depth=2, direction="both", limit=2)
    assert window.total <= 2
    assert window.truncated is True


async def test_axis_filter_and_validation() -> None:
    engine = await graph_engine()
    window = await subgraph(engine, [EntityId("a-1")], depth=1, axes=["tags"])
    assert [node.id for node in window.nodes] == ["a-1"]

    with pytest.raises(ValueError):
        await subgraph(engine, [EntityId("a-1")], direction="sideways")
