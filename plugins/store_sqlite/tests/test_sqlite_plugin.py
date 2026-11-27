"""Плагин SQLite: контракт ядра, долговечность, манифест."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    Capability,
    Entity,
    EntityId,
    IReferenceIndex,
    ReferenceLink,
    ReferenceValue,
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
    assert manifest["requires_contract"] == ">=5,<6"
    assert callable(manifest["factory"])


def test_create_uses_options_path(tmp_path: Path) -> None:
    assert isinstance(create({"path": str(tmp_path / "x.sqlite3")}), SqliteStore)


async def test_artifacts_survive_reopen(tmp_path: Path) -> None:
    from axislar_core.contracts import ArtifactRef, EntityId, make_artifact

    path = tmp_path / "vault.sqlite3"
    artifact = make_artifact(b"preview-bytes", "image/png")
    engine = SqliteStore(path)
    assert await engine.put_artifact(artifact) == []

    entity = Entity(
        id=EntityId("e-1"),
        kind="edit",
        artifacts={
            "preview": ArtifactRef(
                content_hash=artifact.content_hash,
                media_type=artifact.media_type,
                size=artifact.size,
            )
        },
    )
    assert await engine.upsert(entity) == []

    reopened = SqliteStore(path)
    assert await reopened.get_artifact(artifact.content_hash) == artifact
    fetched = await reopened.get(EntityId("e-1"))
    assert fetched is not None and fetched.artifacts == entity.artifacts


async def test_artifact_bytes_are_binary_in_sqlite(tmp_path: Path) -> None:
    from axislar_core.contracts import make_artifact

    path = tmp_path / "vault.sqlite3"
    artifact = make_artifact(bytes(range(256)), "application/octet-stream")
    await SqliteStore(path).put_artifact(artifact)

    with closing(sqlite3.connect(path)) as connection, connection:
        row = connection.execute("SELECT data FROM artifacts").fetchone()
    assert row is not None and bytes(row[0]) == artifact.data


async def test_reference_traversal_survives_reopen(tmp_path: Path) -> None:
    """Обратный индекс ядра виден через стор и живёт после переоткрытия."""
    engine = store(tmp_path)
    await engine.add_axis(
        AxisDefinition(name="links", kind=AxisKind.REFERENCE, multi=True, target="note")
    )
    await engine.bulk_upsert(
        [
            Entity(
                id=EntityId("a"),
                kind="note",
                facets={"links": (ReferenceValue(target=EntityId("b")),)},
            ),
            Entity(id=EntityId("b"), kind="note"),
        ]
    )

    assert isinstance(engine, IReferenceIndex)
    assert Capability.TRAVERSE_REFERENCES in engine.capabilities()
    expected = (ReferenceLink(axis="links", entity_id=EntityId("a")),)
    assert await engine.references_to(EntityId("b")) == expected

    reopened = store(tmp_path)
    assert await reopened.references_to(EntityId("b")) == expected
