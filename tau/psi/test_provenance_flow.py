"""Сквозной провенанс: sync не затирает ручные фасеты (ADR-0018)."""

from __future__ import annotations

from pathlib import Path

from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    CategoricalValue,
    Entity,
    EntityId,
    FacetOwner,
    IStorageEngine,
    Origin,
    SyncPolicy,
    apply_plan,
    plan_sync,
)

from nu.psi import build_product
from nu.psi.contracts.alpha import Config


async def store(tmp_path: Path) -> IStorageEngine:
    config = Config(store="json", data=tmp_path / "vault.json")
    product = build_product(config=config)
    storage = product.core.storage
    await storage.add_axis(AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL))
    await storage.add_axis(AxisDefinition(name="note", kind=AxisKind.CATEGORICAL))
    return storage


def entity(**facets: str) -> Entity:
    return Entity(
        id=EntityId("4d86277f-18e5-50b7-8f7d-bfd1abd0f664"),
        kind="video",
        facets={axis: (CategoricalValue(value=value),) for axis, value in facets.items()},
        owners=dict.fromkeys(facets, FacetOwner.SOURCE),
        origin=Origin(source_id="src", key="a.mp4", content_hash="h1", hash_algo="partial"),
    )


async def test_user_facet_survives_resync(tmp_path: Path) -> None:
    storage = await store(tmp_path)

    scanned = entity(theme="noir")
    plan = plan_sync([scanned], [], SyncPolicy(source_id="src"))
    assert await apply_plan(storage, plan) == 1

    stored = await storage.get(EntityId(str(scanned.id)))
    assert stored is not None
    manual = stored.model_copy(
        update={
            "facets": {**stored.facets, "note": (CategoricalValue(value="руками"),)},
            "owners": {**stored.owners, "note": FacetOwner.USER},
        }
    )
    assert await storage.upsert(manual) == []

    changed = entity(theme="cyberpunk")
    plan = plan_sync([changed], [manual], SyncPolicy(source_id="src"))
    assert [action.kind.value for action in plan.actions] == ["update"]
    assert await apply_plan(storage, plan) == 1

    final = await storage.get(EntityId(str(scanned.id)))
    assert final is not None
    values = {axis: getattr(item[0], "value", None) for axis, item in final.facets.items()}
    assert values["theme"] == "cyberpunk", "фасет источника обновился"
    assert values["note"] == "руками", "ручной фасет выжил"
    assert final.owners["note"] is FacetOwner.USER


async def test_user_only_change_does_not_rewrite(tmp_path: Path) -> None:
    storage = await store(tmp_path)
    scanned = entity(theme="noir")
    await apply_plan(storage, plan_sync([scanned], [], SyncPolicy(source_id="src")))

    stored = await storage.get(EntityId(str(scanned.id)))
    assert stored is not None
    manual = stored.model_copy(
        update={
            "facets": {**stored.facets, "note": (CategoricalValue(value="пометка"),)},
            "owners": {**stored.owners, "note": FacetOwner.USER},
        }
    )
    await storage.upsert(manual)

    plan = plan_sync([scanned], [manual], SyncPolicy(source_id="src"))
    assert plan.actions == (), "правка пользователя не считается изменением источника"
