"""SQLite-бэкенд: контракт ядра, долговечность, отказы."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    Entity,
    EntityId,
    StorageSchema,
    run_storage_contract,
)

from nu.delta._internal.sqlite_store import SqliteStore
from nu.psi.contracts.delta import SQLITE_STORE, SqliteStoreError, builtin_plugins


def store(tmp_path: Path) -> SqliteStore:
    return SqliteStore(tmp_path / "vault.sqlite3")


async def test_sqlite_passes_kernel_contract(tmp_path: Path) -> None:
    """Второй бэкенд обязан проходить контрактный набор ядра целиком."""
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
    assert (await reopened.get_schema()).axis("theme") is not None
    entity = await reopened.get(EntityId("e-1"))
    assert entity is not None and entity.facets


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


async def test_broken_rows_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "vault.sqlite3"
    schema = StorageSchema()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, doc TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO meta VALUES ('version', '1'), ('schema', ?)",
            (schema.model_dump_json(),),
        )
        connection.execute("INSERT INTO entities VALUES ('x', '{\"id\": 1}')")
        connection.commit()
    with pytest.raises(SqliteStoreError, match="broken store"):
        await SqliteStore(path).count()


def test_sqlite_plugin_manifest() -> None:
    manifests = {manifest.name: manifest for manifest in builtin_plugins()}
    assert SQLITE_STORE in manifests
    assert manifests[SQLITE_STORE].requires_contract == ">=2,<3"
