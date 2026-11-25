"""Плагин SQLite: контракт ядра, долговечность, манифест."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    Entity,
    run_storage_contract,
)

from agora_store_sqlite import SqliteStore, SqliteStoreError, create, plugin


def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "vault.sqlite3")


async def test_passes_kernel_contract(tmp_path: Path) -> None:
    await run_storage_contract(lambda schema: SqliteStore(tmp_path / "v.sqlite3", schema))


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


async def test_broken_database_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vault.sqlite3"
    path.write_bytes(b"not a database at all")
    with pytest.raises(SqliteStoreError, match="broken database"):
        await SqliteStore(path).count()


async def test_unsupported_version_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "vault.sqlite3"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, doc TEXT NOT NULL)")
        connection.execute("INSERT INTO meta VALUES ('version', '99')")
        connection.commit()
    with pytest.raises(SqliteStoreError, match="unsupported store version"):
        await SqliteStore(path).count()


def test_manifest_is_plain_data() -> None:
    manifest = plugin()
    assert manifest["requires_contract"] == ">=2,<3"
    assert callable(manifest["factory"])


def test_create_uses_options_path(tmp_path: Path) -> None:
    assert isinstance(create({"path": str(tmp_path / "x.sqlite3")}), SqliteStore)
