"""Встроенный JSON-бэкенд: персистентность + семантика ядра."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    CategoricalValue,
    Entity,
    EntityId,
    ErrorCode,
    Filter,
    MeasurableValue,
    Operator,
    Query,
    ReferenceValue,
    ScalarDefinition,
    StorageSchema,
)

from nu.delta._internal.json_store import JsonStore
from nu.psi.contracts.delta import JSON_STORE, JsonStoreError, builtin_plugins


def store(tmp_path: Path) -> JsonStore:
    return JsonStore(tmp_path / "vault.json")


async def seeded(tmp_path: Path) -> JsonStore:
    engine = store(tmp_path)
    assert (
        await engine.add_axis(AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL, multi=True))
        == []
    )
    assert await engine.add_axis(AxisDefinition(name="year", kind=AxisKind.MEASURABLE)) == []
    assert await engine.add_scalar(ScalarDefinition(name="title", searchable=True)) == []
    entities = (
        Entity(
            id=EntityId("a-1"),
            kind="edit",
            scalars={"title": "Alpha"},
            facets={
                "theme": (CategoricalValue(value="cyberpunk"), CategoricalValue(value="noir")),
                "year": (MeasurableValue(value=2024.0),),
            },
        ),
        Entity(id=EntityId("b-2"), kind="edit", scalars={"title": "Beta"}),
    )
    assert await engine.bulk_upsert(entities) == []
    return engine


def ids(result: object) -> list[str]:
    return [str(entity_id) for entity_id in result.ids]  # type: ignore[attr-defined]


async def test_empty_store_starts_empty(tmp_path: Path) -> None:
    engine = store(tmp_path)
    assert await engine.count() == 0
    assert await engine.get_schema() == StorageSchema()


async def test_data_and_schema_survive_reload(tmp_path: Path) -> None:
    await seeded(tmp_path)

    reopened = store(tmp_path)
    schema = await reopened.get_schema()
    assert schema.axis("theme") is not None
    assert schema.scalar("title") is not None
    assert await reopened.count() == 2

    entity = await reopened.get(EntityId("a-1"))
    assert entity is not None
    assert len(entity.facets["theme"]) == 2


async def test_missing_value_semantics_after_reload(tmp_path: Path) -> None:
    await seeded(tmp_path)
    engine = store(tmp_path)

    none_of = await engine.query(
        Query(all=(Filter(axis="theme", op=Operator.NONE_OF, values=("noir",)),))
    )
    assert ids(none_of) == ["b-2"]

    exists = await engine.query(Query(all=(Filter(axis="theme", op=Operator.EXISTS),)))
    assert ids(exists) == ["a-1"]

    numeric_missing = await engine.query(
        Query(all=(Filter(axis="year", op=Operator.GTE, values=(1900,)),))
    )
    assert ids(numeric_missing) == ["a-1"]


async def test_scalar_query_after_reload(tmp_path: Path) -> None:
    await seeded(tmp_path)
    engine = store(tmp_path)
    result = await engine.query(
        Query(all=(Filter(axis="title", op=Operator.SCALAR_EQ, values=("Beta",)),))
    )
    assert ids(result) == ["b-2"]


async def test_dangling_reference_is_rejected(tmp_path: Path) -> None:
    engine = store(tmp_path)
    await engine.add_axis(AxisDefinition(name="author", kind=AxisKind.REFERENCE, target="person"))
    errors = await engine.upsert(
        Entity(
            id=EntityId("x"),
            kind="edit",
            facets={"author": (ReferenceValue(target=EntityId("missing")),)},
        )
    )
    assert {error.code for error in errors} == {ErrorCode.DANGLING_REFERENCE}
    assert await engine.count() == 0


async def test_bulk_upsert_persists_valid_entities(tmp_path: Path) -> None:
    engine = store(tmp_path)
    await engine.add_axis(AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL))

    errors = await engine.bulk_upsert(
        (
            Entity(
                id=EntityId("ok"),
                kind="edit",
                facets={"theme": (CategoricalValue(value="noir"),)},
            ),
            Entity(id=EntityId("bad"), kind="edit", scalars={"unknown": "x"}),
        )
    )
    assert len(errors) == 1
    assert await store(tmp_path).count() == 1


async def test_write_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    engine = await seeded(tmp_path)
    assert (tmp_path / "vault.json").is_file()
    assert list(tmp_path.glob("*.tmp")) == []

    payload = json.loads((tmp_path / "vault.json").read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert len(payload["entities"]) == 2
    assert await engine.count() == 2


async def test_broken_json_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(JsonStoreError, match="broken JSON"):
        await JsonStore(path).count()


async def test_unsupported_version_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    path.write_text(json.dumps({"version": 99, "schema": {}, "entities": []}), encoding="utf-8")
    with pytest.raises(JsonStoreError, match="unsupported store version"):
        await JsonStore(path).count()


async def test_capabilities_match_kernel_reference(tmp_path: Path) -> None:
    engine = store(tmp_path)
    capabilities = engine.capabilities()
    assert capabilities, "JSON-бэкенд обязан объявлять возможности"
    assert engine.capabilities() == build_core_capabilities()


def build_core_capabilities() -> frozenset[object]:
    from axislar_core.contracts import build_core

    return build_core().capabilities()


def test_builtin_plugin_manifest_is_compatible() -> None:
    manifests = {manifest.name: manifest for manifest in builtin_plugins()}
    assert JSON_STORE in manifests
    assert all(manifest.requires_contract == ">=2,<3" for manifest in manifests.values())


def test_builtin_factory_uses_default_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from nu.psi.contracts.delta import make_json_store

    monkeypatch.chdir(tmp_path)
    engine = make_json_store({})
    assert isinstance(engine, JsonStore)


async def test_corrupted_entity_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    path.write_text(
        json.dumps({"version": 1, "schema": {}, "entities": [{"id": "x"}]}), encoding="utf-8"
    )
    with pytest.raises(JsonStoreError, match="broken store"):
        await JsonStore(path).count()


async def test_entities_violating_schema_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "schema": {
                    "axes": [{"name": "theme", "kind": "categorical"}],
                    "scalars": [],
                },
                "entities": [
                    {
                        "id": "x",
                        "kind": "edit",
                        "facets": {"colour": [{"kind": "categorical", "value": "red"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(JsonStoreError, match="unknown_axis"):
        await JsonStore(path).count()


async def test_failed_mutations_do_not_touch_file(tmp_path: Path) -> None:
    engine = store(tmp_path)
    await engine.add_axis(AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL))
    before = (tmp_path / "vault.json").read_bytes()

    assert await engine.add_axis(AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL))
    assert await engine.add_scalar(ScalarDefinition(name="title")) == []
    duplicated = await engine.add_scalar(ScalarDefinition(name="title"))
    assert duplicated
    assert await engine.upsert(Entity(id=EntityId("bad"), kind="edit", scalars={"nope": "x"}))
    assert (tmp_path / "vault.json").read_bytes() != before  # add_scalar успел записать


async def test_bulk_upsert_all_invalid_does_not_create_file(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    engine = JsonStore(path)
    await engine.add_axis(AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL))
    before = path.read_bytes()

    errors = await engine.bulk_upsert(
        (Entity(id=EntityId("bad"), kind="edit", scalars={"nope": "x"}),)
    )
    assert errors
    assert path.read_bytes() == before
    assert await engine.count() == 0
