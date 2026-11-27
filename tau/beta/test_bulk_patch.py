"""`bulk_patch`: массовая правка фасетов через ревизии (фаза 2)."""

from __future__ import annotations

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    CategoricalValue,
    Entity,
    EntityId,
    FacetOwner,
    ReferenceValue,
    ScalarDefinition,
    build_core,
)

from nu.psi.contracts.beta import FacetPatch, apply_patches, bulk_patch


async def storage() -> object:
    engine = build_core().storage
    assert (
        await engine.add_axis(AxisDefinition(name="tags", kind=AxisKind.CATEGORICAL, multi=True))
        == []
    )
    assert await engine.add_axis(AxisDefinition(name="year", kind=AxisKind.MEASURABLE)) == []
    assert (
        await engine.add_axis(
            AxisDefinition(name="links", kind=AxisKind.REFERENCE, multi=True, target="note")
        )
        == []
    )
    assert await engine.add_scalar(ScalarDefinition(name="title")) == []
    assert (
        await engine.bulk_upsert(
            [
                Entity.model_validate({"id": "n1", "kind": "note", "scalars": {"title": "Первый"}}),
                Entity.model_validate({"id": "n2", "kind": "note"}),
            ]
        )
        == []
    )
    return engine


def test_apply_patches_adds_and_dedupes() -> None:
    entity = Entity.model_validate(
        {
            "id": "n1",
            "kind": "note",
            "facets": {"tags": [{"kind": "categorical", "value": "noir"}]},
        }
    )
    patched, skipped = apply_patches(
        entity, [FacetPatch(axis="tags", mode="add", values=("noir", "epic"))]
    )
    assert skipped == ()
    values = [
        value.value for value in patched.facets["tags"] if isinstance(value, CategoricalValue)
    ]
    assert values == ["noir", "epic"], "дубликат не добавляется"


def test_apply_patches_keeps_source_owned_facets() -> None:
    entity = Entity.model_validate(
        {
            "id": "n1",
            "kind": "note",
            "facets": {"tags": [{"kind": "categorical", "value": "from-source"}]},
            "owners": {"tags": "source"},
        }
    )
    patched, skipped = apply_patches(
        entity, [FacetPatch(axis="tags", mode="set", values=("manual",))]
    )
    assert skipped == ("tags",), "пропуск сообщается, а не молчит"
    assert patched.facets["tags"] == entity.facets["tags"], "фасет источника не перезаписывается"
    assert patched.owners["tags"] is FacetOwner.SOURCE

    forced, skipped_forced = apply_patches(
        entity, [FacetPatch(axis="tags", mode="set", values=("manual",))], force=True
    )
    assert skipped_forced == ()
    assert forced.owners["tags"] is FacetOwner.USER, "владение переходит человеку"


async def test_bulk_patch_sets_facet_for_many_objects() -> None:
    engine = await storage()
    result = await bulk_patch(
        engine,  # type: ignore[arg-type]
        [EntityId("n1"), EntityId("n2")],
        [FacetPatch(axis="tags", mode="add", values=("noir",))],
    )

    assert [key for key, _ in result.changed] == ["n1", "n2"]
    assert result.conflicts == ()
    for entity_id in ("n1", "n2"):
        stored = await engine.get(EntityId(entity_id))  # type: ignore[attr-defined]
        assert stored is not None
        assert [value.value for value in stored.facets["tags"]] == ["noir"]
        assert stored.rev == 2, "одна ревизия на объект"


async def test_bulk_patch_reports_conflicts_per_object() -> None:
    engine = await storage()
    # Ожидаем у n1 ревизию 5, которой нет: конфликт только у этого объекта.
    result = await bulk_patch(
        engine,  # type: ignore[arg-type]
        [EntityId("n1"), EntityId("n2")],
        [FacetPatch(axis="tags", mode="set", values=("noir",))],
        expected_revs={"n1": 5},
    )

    assert result.conflicts == ("n1",)
    assert [key for key, _ in result.changed] == ["n2"], "остальные объекты изменены"
    stored = await engine.get(EntityId("n1"))  # type: ignore[attr-defined]
    assert stored is not None and "tags" not in stored.facets


async def test_bulk_patch_unset_and_typed_values() -> None:
    engine = await storage()
    await engine.upsert(  # type: ignore[attr-defined]
        Entity.model_validate(
            {
                "id": "n1",
                "kind": "note",
                "facets": {
                    "tags": [{"kind": "categorical", "value": "noir"}],
                    "links": [{"kind": "reference", "target": "n2"}],
                },
                "owners": {"tags": "user", "links": "user"},
            }
        )
    )
    result = await bulk_patch(
        engine,  # type: ignore[arg-type]
        [EntityId("n1")],
        [FacetPatch(axis="tags", mode="unset")],
    )
    stored = await engine.get(EntityId("n1"))  # type: ignore[attr-defined]
    assert stored is not None
    assert "tags" not in stored.facets
    assert result.changed and result.changed[0][0] == "n1"


async def test_bulk_patch_reports_missing_object() -> None:
    engine = await storage()
    result = await bulk_patch(
        engine,  # type: ignore[arg-type]
        [EntityId("ghost")],
        [FacetPatch(axis="tags", mode="add", values=("x",))],
    )
    assert result.changed == ()
    assert [error.entity_id for error in result.errors] == [EntityId("ghost")]


def test_facet_patch_validates_mode_and_values() -> None:
    with pytest.raises(ValueError, match="unknown patch mode"):
        FacetPatch(axis="tags", mode="replace", values=("x",))
    with pytest.raises(ValueError, match="requires values"):
        FacetPatch(axis="tags", mode="set")


def test_reference_values_get_reference_type() -> None:
    entity = Entity.model_validate(
        {
            "id": "n1",
            "kind": "note",
            "facets": {"links": [{"kind": "reference", "target": "n2"}]},
            "owners": {"links": "user"},
        }
    )
    patched, _ = apply_patches(entity, [FacetPatch(axis="links", mode="add", values=("n3",))])
    targets = [
        str(value.target) for value in patched.facets["links"] if isinstance(value, ReferenceValue)
    ]
    assert targets == ["n2", "n3"]
