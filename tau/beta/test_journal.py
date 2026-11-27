"""Журнал правок и откат (фаза 2, 2.3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    CategoricalValue,
    Entity,
    EntityId,
    IStorageEngine,
    ScalarDefinition,
    build_core,
)

from nu.psi.contracts.beta import (
    Journal,
    JournalError,
    journal_path_for,
    make_entry,
    undo_last,
)


async def storage() -> IStorageEngine:
    engine = build_core().storage
    assert (
        await engine.add_axis(AxisDefinition(name="tags", kind=AxisKind.CATEGORICAL, multi=True))
        == []
    )
    assert await engine.add_scalar(ScalarDefinition(name="title")) == []
    return engine


def journal(tmp_path: Path) -> Journal:
    return Journal(journal_path_for(tmp_path))


async def test_journal_survives_roundtrip(tmp_path: Path) -> None:
    engine = await storage()
    target = journal(tmp_path)
    entity = Entity.model_validate(
        {"id": "n1", "kind": "note", "facets": {"tags": [{"kind": "categorical", "value": "a"}]}}
    )
    assert await engine.upsert(entity) == []
    after = await engine.get(EntityId("n1"))
    target.append([make_entry("put", None, after)])

    entries = target.entries()
    assert len(entries) == 1
    assert entries[0].kind == "put"
    assert entries[0].before is None and entries[0].after is not None


async def test_undo_restores_previous_snapshot(tmp_path: Path) -> None:
    engine = await storage()
    target = journal(tmp_path)
    entity = Entity.model_validate(
        {"id": "n1", "kind": "note", "facets": {"tags": [{"kind": "categorical", "value": "old"}]}}
    )
    assert await engine.upsert(entity) == []
    before = await engine.get(EntityId("n1"))

    changed = Entity.model_validate(
        {"id": "n1", "kind": "note", "facets": {"tags": [{"kind": "categorical", "value": "new"}]}}
    )
    assert await engine.upsert(changed) == []
    after = await engine.get(EntityId("n1"))
    target.append([make_entry("patch", before, after)])

    entry = await undo_last(engine, target)
    assert entry is not None
    restored = await engine.get(EntityId("n1"))
    assert restored is not None
    values = [v.value for v in restored.facets["tags"] if isinstance(v, CategoricalValue)]
    assert values == ["old"], "фасет вернулся к снимку «до»"
    assert target.last() is None, "откаченная запись удалена из журнала"


async def test_undo_of_creation_tombstones_object(tmp_path: Path) -> None:
    engine = await storage()
    target = journal(tmp_path)
    entity = Entity.model_validate({"id": "n1", "kind": "note"})
    assert await engine.upsert(entity) == []
    target.append([make_entry("put", None, await engine.get(EntityId("n1")))])

    assert await undo_last(engine, target) is not None
    assert await engine.get(EntityId("n1")) is None, "создание отменяется tombstone"
    listed = await engine.list_entities(include_deleted=True)
    assert listed and listed[0].deleted_at is not None


async def test_undo_reports_conflict_instead_of_overwriting(tmp_path: Path) -> None:
    engine = await storage()
    target = journal(tmp_path)
    entity = Entity.model_validate({"id": "n1", "kind": "note"})
    assert await engine.upsert(entity) == []
    before = await engine.get(EntityId("n1"))
    target.append([make_entry("patch", before, before)])

    # Кто-то изменил объект после записи в журнал.
    assert await engine.upsert(entity) == []
    assert await engine.upsert(entity) == []

    with pytest.raises(JournalError, match="cannot undo"):
        await undo_last(engine, target)
    stored = await engine.get(EntityId("n1"))
    assert stored is not None and stored.rev == 3, "чужая правка не затёрта"


async def test_undo_on_empty_journal_returns_none(tmp_path: Path) -> None:
    engine = await storage()
    assert await undo_last(engine, journal(tmp_path)) is None


async def test_journal_limit_keeps_recent(tmp_path: Path) -> None:
    engine = await storage()
    target = Journal(journal_path_for(tmp_path), limit=2)
    entity = Entity.model_validate({"id": "n1", "kind": "note"})
    assert await engine.upsert(entity) == []
    snapshot = await engine.get(EntityId("n1"))
    target.append([make_entry("put", None, snapshot) for _ in range(5)])
    assert len(target.entries()) == 2
