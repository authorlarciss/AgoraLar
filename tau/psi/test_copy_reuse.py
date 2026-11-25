"""Копия проекта: производные факты переиспользуются по хэшу (ADR-0017)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    CategoricalValue,
    Entity,
    FacetOwner,
    IStorageEngine,
    Origin,
    Provenance,
    SyncPolicy,
    apply_plan,
    plan_sync,
)
from axislar_core.contracts import (
    EntityId as Id,
)

from nu.psi import build_product
from nu.psi.contracts.alpha import Config

STAMP = datetime(2025, 11, 25, tzinfo=UTC)
ORIGINAL = "src-original"
COPY = "src-copy"


async def store(tmp_path: Path) -> IStorageEngine:
    product = build_product(config=Config(store="json", data=tmp_path / "vault.json"))
    storage = product.core.storage
    await storage.add_axis(AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL))
    await storage.add_axis(AxisDefinition(name="cluster", kind=AxisKind.CATEGORICAL))
    return storage


def video(
    source_id: str,
    key: str,
    *,
    content_hash: str = "h1",
    cluster: str | None = None,
) -> Entity:
    facets: dict[str, object] = {
        "theme": (CategoricalValue(value="noir"),),
    }
    owners: dict[str, FacetOwner] = {"theme": FacetOwner.SOURCE}
    derived: dict[str, Provenance] = {}
    if cluster is not None:
        facets["cluster"] = (CategoricalValue(value=cluster),)
        owners["cluster"] = FacetOwner.ANALYSIS
        derived["cluster"] = Provenance(plugin="cluster", version="1", at=STAMP)

    return Entity(
        id=Id(f"{source_id}-{key}"),
        kind="video",
        facets=facets,  # type: ignore[arg-type]
        owners=owners,
        derived_by=derived,
        origin=Origin(source_id=source_id, key=key, content_hash=content_hash, hash_algo="partial"),
    )


async def test_copy_reuses_analysis_without_rerun(tmp_path: Path) -> None:
    storage = await store(tmp_path)

    # Оригинал уже проанализирован: cluster проставлен анализом.
    original = video(ORIGINAL, "a.mp4", cluster="c-7")
    assert await storage.upsert(original) == []

    # Копия: другой источник, тот же хэш, анализа ещё не было.
    copied = video(COPY, "a.mp4")
    plan = plan_sync(
        [copied], await storage.list_entities(include_deleted=True), SyncPolicy(source_id=COPY)
    )
    assert [action.kind.value for action in plan.actions] == ["create"]

    action = plan.actions[0]
    facet_values = action.entity.facets["cluster"][0]
    assert getattr(facet_values, "value", None) == "c-7", "анализ пришёл из донора"
    assert action.entity.owners["cluster"] is FacetOwner.ANALYSIS
    assert "cluster" in action.entity.derived_by

    assert await apply_plan(storage, plan) == 1
    stored = await storage.get(Id(str(action.entity.id)))
    assert stored is not None
    assert "cluster" in stored.facets


async def test_copy_without_known_hash_does_not_reuse(tmp_path: Path) -> None:
    storage = await store(tmp_path)
    await storage.upsert(video(ORIGINAL, "a.mp4", content_hash="h1", cluster="c-1"))

    copied = video(COPY, "a.mp4", content_hash="h-new")
    plan = plan_sync(
        [copied], await storage.list_entities(include_deleted=True), SyncPolicy(source_id=COPY)
    )
    assert "cluster" not in plan.actions[0].entity.facets
