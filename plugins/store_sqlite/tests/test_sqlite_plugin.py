"""Плагин SQLite: контракт ядра, долговечность, манифест."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
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
    StorageSchema,
    run_reference_index_contract,
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


def _write_v1_store(path: Path, schema: StorageSchema, entities: list[Entity]) -> None:
    """Собрать файл формата 1 руками: без колонки `deleted` и без индексов."""
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, doc TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE artifacts ("
            "content_hash TEXT PRIMARY KEY, media_type TEXT NOT NULL, data BLOB NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [("version", "1"), ("schema", schema.model_dump_json())],
        )
        connection.executemany(
            "INSERT INTO entities (id, doc) VALUES (?, ?)",
            [(str(entity.id), entity.model_dump_json()) for entity in entities],
        )


def _schema_with_links() -> StorageSchema:
    return StorageSchema(
        axes=(
            AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL),
            AxisDefinition(name="links", kind=AxisKind.REFERENCE, multi=True, target="note"),
        )
    )


def _note_with_link(source: str, target: str) -> Entity:
    return Entity(
        id=EntityId(source),
        kind="note",
        facets={"links": (ReferenceValue(target=EntityId(target)),)},
    )


async def test_v1_store_is_migrated_on_traversal(tmp_path: Path) -> None:
    """Старый файл мигрируется: появляется `deleted`, индексы и версия 2."""
    path = tmp_path / "v1.sqlite3"
    _write_v1_store(
        path,
        _schema_with_links(),
        [_note_with_link("a", "b"), Entity(id=EntityId("b"), kind="note")],
    )

    engine = SqliteStore(path)
    links = await engine.references_to(EntityId("b"))

    assert links == (ReferenceLink(axis="links", entity_id=EntityId("a")),)
    with closing(sqlite3.connect(path)) as connection:
        version = connection.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
        columns = {row[1] for row in connection.execute("PRAGMA table_info(entities)")}
        indexed = connection.execute("SELECT COUNT(*) FROM ref_index").fetchone()
    assert version == ("2",)
    assert "deleted" in columns
    assert indexed == (1,)


async def test_incremental_flush_updates_only_changed_rows(tmp_path: Path) -> None:
    """Запись одного объекта не переписывает остальные и правит индексы."""
    engine = store(tmp_path)
    await engine.add_axis(AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL))
    await engine.bulk_upsert(
        [
            Entity.model_validate(
                {
                    "id": "e-1",
                    "kind": "note",
                    "facets": {"theme": [{"kind": "categorical", "value": "a"}]},
                }
            ),
            Entity.model_validate(
                {
                    "id": "e-2",
                    "kind": "note",
                    "facets": {"theme": [{"kind": "categorical", "value": "b"}]},
                }
            ),
        ]
    )
    await engine.upsert(
        Entity.model_validate(
            {
                "id": "e-1",
                "kind": "note",
                "facets": {"theme": [{"kind": "categorical", "value": "c"}]},
            }
        )
    )

    with closing(sqlite3.connect(tmp_path / "vault.sqlite3")) as connection:
        rows = connection.execute("SELECT COUNT(*) FROM entities").fetchone()
        facets = connection.execute(
            "SELECT axis, value, entity_id FROM facet_index ORDER BY value"
        ).fetchall()

    assert rows == (2,), "обновление не должно добавлять или терять строки"
    assert facets == [("theme", "b", "e-2"), ("theme", "c", "e-1")], "старое значение ушло"


async def test_traversal_sees_tombstones_only_when_asked(tmp_path: Path) -> None:
    engine = store(tmp_path)
    await engine.add_axis(
        AxisDefinition(name="links", kind=AxisKind.REFERENCE, multi=True, target="note")
    )
    await engine.bulk_upsert([_note_with_link("a", "b"), Entity(id=EntityId("b"), kind="note")])

    deleted = _note_with_link("a", "b").model_copy(
        update={"deleted_at": datetime(2026, 1, 1, tzinfo=UTC)}
    )
    assert await engine.upsert(deleted) == []

    assert await engine.references_to(EntityId("b")) == ()
    assert await engine.references_to(EntityId("b"), include_deleted=True) == (
        ReferenceLink(axis="links", entity_id=EntityId("a")),
    )


async def test_traversal_on_missing_file_creates_nothing(tmp_path: Path) -> None:
    engine = store(tmp_path)
    assert await engine.references_to(EntityId("nobody")) == ()
    assert not (tmp_path / "vault.sqlite3").exists(), "чтение не должно создавать файл"


async def test_passes_reference_contract(tmp_path: Path) -> None:
    """Семантика обхода ссылок проверяется контрактным набором ядра (ADR-0021)."""
    await run_reference_index_contract(lambda schema: SqliteStore(tmp_path / "ref.db", schema))


async def test_matches_reference_engine_on_random_queries(tmp_path: Path) -> None:
    """Дифференциальный тест: SQL-бэкенд против эталонного движка.

    Семантика запросов в SQL (особенно разреженность, мультизначность и
    `none_of` на отсутствующем значении) — самое опасное место бэкенда:
    ошибка здесь тихая. Поэтому случайные данные и случайные запросы
    сравниваются с `MemoryStorage` из ядра по `ids` и `total`.
    """
    import random

    from axislar_core.contracts import (
        Filter,
        Operator,
        Query,
        ScalarDefinition,
        build_core,
    )

    rng = random.Random(20261127)
    schema = StorageSchema(
        axes=(
            AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL, multi=True),
            AxisDefinition(name="year", kind=AxisKind.MEASURABLE),
            AxisDefinition(name="links", kind=AxisKind.REFERENCE, multi=True, target="note"),
        ),
        scalars=(ScalarDefinition(name="title"),),
    )
    themes = ("noir", "cyberpunk", "epic", "fantasy", "doc")

    entities: list[Entity] = []
    for index in range(120):
        facets: dict[str, object] = {}
        if rng.random() < 0.8:
            chosen = rng.sample(themes, k=rng.randint(1, 3))
            facets["theme"] = tuple({"kind": "categorical", "value": value} for value in chosen)
        if rng.random() < 0.6:
            facets["year"] = ({"kind": "measurable", "value": float(rng.randint(1990, 2024))},)
        if rng.random() < 0.3:
            target = f"e-{rng.randrange(120):03d}"
            facets["links"] = ({"kind": "reference", "target": target},)
        scalars = {"title": f"title-{rng.randrange(5)}"} if rng.random() < 0.5 else {}
        entity = Entity.model_validate(
            {"id": f"e-{index:03d}", "kind": "note", "facets": facets, "scalars": scalars}
        )
        if rng.random() < 0.1:
            entity = entity.model_copy(update={"deleted_at": datetime(2026, 1, 1, tzinfo=UTC)})
        entities.append(entity)

    store = SqliteStore(tmp_path / "diff.sqlite3", schema)
    reference = build_core().storage
    for axis in schema.axes:
        await reference.add_axis(axis)
    for scalar in schema.scalars:
        await reference.add_scalar(scalar)
    assert await store.bulk_upsert(entities) == []
    assert await reference.bulk_upsert(entities) == []

    categorical_ops = (
        Operator.CONTAINS,
        Operator.ANY_OF,
        Operator.NONE_OF,
        Operator.EXISTS,
        Operator.NOT_EXISTS,
    )
    numeric_ops = (
        Operator.EQ,
        Operator.GT,
        Operator.LT,
        Operator.GTE,
        Operator.LTE,
        Operator.BETWEEN,
    )

    non_empty = 0
    for _ in range(80):
        conditions: list[Filter] = []
        for _ in range(rng.randint(1, 3)):
            kind = rng.randrange(4)
            if kind == 0:
                op = rng.choice(categorical_ops)
                values: tuple[object, ...] = (
                    () if op in (Operator.EXISTS, Operator.NOT_EXISTS) else (rng.choice(themes),)
                )
                conditions.append(
                    Filter.model_validate({"axis": "theme", "op": op.value, "values": values})
                )
            elif kind == 1:
                op = rng.choice(numeric_ops)
                if op is Operator.BETWEEN:
                    low, high = sorted((rng.randint(1990, 2024), rng.randint(1990, 2024)))
                    values = (float(low), float(high))
                else:
                    values = (float(rng.randint(1990, 2024)),)
                conditions.append(
                    Filter.model_validate({"axis": "year", "op": op.value, "values": values})
                )
            elif kind == 2:
                op = rng.choice(
                    (Operator.CONTAINS, Operator.ANY_OF, Operator.NONE_OF, Operator.EXISTS)
                )
                values = () if op is Operator.EXISTS else (f"e-{rng.randrange(120):03d}",)
                conditions.append(
                    Filter.model_validate({"axis": "links", "op": op.value, "values": values})
                )
            else:
                conditions.append(
                    Filter.model_validate(
                        {
                            "axis": "title",
                            "op": Operator.SCALAR_EQ.value,
                            "values": (f"title-{rng.randrange(5)}",),
                        }
                    )
                )

        query = Query(
            all=tuple(conditions),
            limit=rng.choice((None, 1, 5, 20)),
            offset=rng.choice((0, 0, 3)),
        )
        expected = await reference.query(query)
        actual = await store.query(query)
        assert (actual.ids, actual.total) == (expected.ids, expected.total), (
            f"query mismatch for {query}: {actual} != {expected}"
        )
        if expected.ids:
            non_empty += 1

    assert non_empty > 30, f"тест бесполезен: непустых запросов всего {non_empty}"
