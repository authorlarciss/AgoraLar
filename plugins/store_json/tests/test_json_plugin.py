"""Плагин JSON: контракт ядра, долговечность, манифест."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    Entity,
    EntityId,
    run_storage_contract,
)

from agora_store_json import JsonStore, JsonStoreError, create, plugin


def store(tmp_path: Path) -> JsonStore:
    return JsonStore(tmp_path / "vault.json")


async def test_passes_kernel_contract(tmp_path: Path) -> None:
    await run_storage_contract(lambda schema: JsonStore(tmp_path / "v.json", schema))


async def test_data_survives_reopen(tmp_path: Path) -> None:
    engine = store(tmp_path)
    await engine.add_axis(AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL))
    await engine.upsert(
        Entity.model_validate(
            {
                "id": "e-1",
                "kind": "edit",
                "facets": {"theme": [{"kind": "categorical", "value": "noir"}]},
            }
        )
    )

    reopened = store(tmp_path)
    assert await reopened.count() == 1
    assert (await reopened.get(EntityId("e-1"))) is not None


async def test_broken_json_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(JsonStoreError, match="broken JSON"):
        await JsonStore(path).count()


async def test_unsupported_version_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vault.json"
    path.write_text(json.dumps({"version": 99, "schema": {}, "entities": []}), encoding="utf-8")
    with pytest.raises(JsonStoreError, match="unsupported store version"):
        await JsonStore(path).count()


def test_manifest_is_plain_data() -> None:
    manifest = plugin()
    assert manifest["requires_contract"] == ">=3,<4"
    assert callable(manifest["factory"])


def test_create_uses_options_path(tmp_path: Path) -> None:
    engine = create({"path": str(tmp_path / "custom.json")})
    assert isinstance(engine, JsonStore)
