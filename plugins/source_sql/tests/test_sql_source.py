"""SQL-источник: контракт синхронизации, раскладка колонок, разреженность."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    CategoricalValue,
    EntityId,
    MeasurableValue,
    ReferenceValue,
    ScalarDefinition,
    StorageSchema,
    entity_id_for,
    run_source_sync_contract,
)

from agora_source_sql import (
    KEY_SEPARATOR,
    SqlSource,
    SqlSourceError,
    create,
    plugin,
)

SCHEMA = StorageSchema(
    axes=(
        AxisDefinition(name="author", kind=AxisKind.CATEGORICAL),
        AxisDefinition(name="genre", kind=AxisKind.CATEGORICAL, multi=True),
        AxisDefinition(name="year", kind=AxisKind.MEASURABLE),
        AxisDefinition(name="editor", kind=AxisKind.REFERENCE, target="person"),
    ),
    scalars=(ScalarDefinition(name="title"),),
)

ROWS = (
    ("e-1", "Alpha", "ann", "noir, epic", 1999.0, "p-1", "n1"),
    ("e-2", "Beta", None, None, None, None, "n2"),
    ("e-3", "Gamma", "bob", "noir", 2004.0, "p-2", "n3"),
)


def categorical(value: object) -> CategoricalValue:
    """Сузить фасет до категориального значения в тестах."""
    assert isinstance(value, CategoricalValue)
    return value


def make_db(tmp_path: Path, rows: tuple[tuple[object, ...], ...] = ROWS) -> Path:
    path = tmp_path / "export.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE edits (id TEXT PRIMARY KEY, title TEXT, author TEXT, genre TEXT,"
        " year REAL, editor TEXT, notes TEXT)"
    )
    connection.executemany("INSERT INTO edits VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()
    return path


def source(path: Path, **options: object) -> SqlSource:
    settings: dict[str, object] = {
        "path": str(path),
        "table": "edits",
        "source_id": "db-edits",
        "kind": "edit",
        "axes": ["author", "genre"],
        "multi": {"genre": ","},
        "measurable": ["year"],
        "reference": {"editor": "person"},
        "scalars": ["title"],
    }
    settings.update(options)
    return create(settings)


async def test_source_passes_sync_contract(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    await run_source_sync_contract(lambda: source(path), SCHEMA)


async def test_columns_become_facets_and_scalars(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    entities = [entity async for entity in source(path).scan()]
    assert len(entities) == 3

    first = next(entity for entity in entities if entity.scalars["title"] == "Alpha")
    assert first.kind == "edit"
    assert [categorical(value).value for value in first.facets["author"]] == ["ann"]
    assert [categorical(value).value for value in first.facets["genre"]] == ["noir", "epic"]
    year = first.facets["year"][0]
    assert isinstance(year, MeasurableValue) and year.value == pytest.approx(1999.0)
    editor = first.facets["editor"][0]
    assert isinstance(editor, ReferenceValue)
    assert editor.target == EntityId("p-1")
    assert {owner.value for owner in first.owners.values()} == {"source"}
    assert set(first.owners) == set(first.facets)


async def test_null_means_absent_not_empty(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    entities = [entity async for entity in source(path).scan()]
    entity = next(entity for entity in entities if entity.scalars["title"] == "Beta")
    assert entity.facets == {}, "NULL не создаёт фасет: объект разреженный"
    assert entity.scalars == {"title": "Beta"}, "скаляр с NULL не пишется"


async def test_id_follows_kernel_policy(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    entity = await anext(source(path).scan())
    assert entity.origin is not None
    assert entity.id == entity_id_for("db-edits", entity.origin.key)
    assert KEY_SEPARATOR in entity.origin.key or entity.origin.key == "e-1"


async def hashes(path: Path, **options: object) -> dict[str, str]:
    """Хэши объектов источника по их естественному ключу."""
    result: dict[str, str] = {}
    async for entity in source(path, **options).scan():
        assert entity.origin is not None
        result[entity.origin.key] = entity.origin.content_hash or ""
    return result


async def test_hash_ignores_unmapped_columns(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    before = await hashes(path)

    connection = sqlite3.connect(path)
    connection.execute("UPDATE edits SET notes = 'changed' WHERE id = 'e-1'")
    connection.commit()
    connection.close()

    after = await hashes(path)
    assert before == after, "неиспользуемая колонка не влияет на объект"

    connection = sqlite3.connect(path)
    connection.execute("UPDATE edits SET title = 'Alpha 2' WHERE id = 'e-1'")
    connection.commit()
    connection.close()

    updated = await hashes(path)
    assert updated["e-1"] != before["e-1"], "изменение сопоставленной колонки видно"


async def test_empty_key_row_is_skipped_with_warning(tmp_path: Path) -> None:
    rows = (
        ("e-1", "Alpha", None, None, None, None, "n1"),
        (None, "Broken", None, None, None, None, "x"),
    )
    path = make_db(tmp_path, rows)
    src = source(path)
    entities = [entity async for entity in src.scan()]
    assert len(entities) == 1
    assert any("ключевая колонка" in warning for warning in src.warnings)


async def test_non_numeric_measurable_is_reported_and_skipped(tmp_path: Path) -> None:
    rows = (("e-1", "Alpha", None, None, "soon", None, "n1"),)
    path = make_db(tmp_path, rows)
    src = source(path)
    entity = await anext(src.scan())
    assert "year" not in entity.facets
    assert any("не число" in warning for warning in src.warnings)


async def test_query_option_is_used_instead_of_table(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    src = source(path, table=None, query="SELECT * FROM edits WHERE id = 'e-2'")
    entities = [entity async for entity in src.scan()]
    assert [entity.scalars["title"] for entity in entities] == ["Beta"]
    assert "QUERY" in src.describe().upper()


async def test_missing_column_is_reported(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    src = source(path, axes=["author", "nope"], multi={})
    with pytest.raises(SqlSourceError, match="nope"):
        _ = [entity async for entity in src.scan()]


async def test_missing_file_is_reported(tmp_path: Path) -> None:
    src = source(tmp_path / "absent.db")
    with pytest.raises(SqlSourceError, match="cannot open sqlite"):
        _ = [entity async for entity in src.scan()]


async def test_batch_size_does_not_change_result(tmp_path: Path) -> None:
    path = make_db(tmp_path)
    big = [entity async for entity in source(path).scan()]
    small = [entity async for entity in source(path, batch=1).scan()]
    assert [entity.id for entity in big] == [entity.id for entity in small]


def test_plugin_manifest() -> None:
    manifest = plugin()
    assert manifest["requires_contract"] == ">=4,<5"
    assert callable(manifest["factory"])


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"path": "/tmp/x.db"},
        {"path": "/tmp/x.db", "source_id": "s"},
        {"path": "/tmp/x.db", "source_id": "s", "table": "t", "query": "SELECT 1"},
        {"path": "/tmp/x.db", "source_id": "s", "table": "t", "axes": ["a"], "scalars": ["a"]},
        {"path": "/tmp/x.db", "source_id": "s", "table": "t", "multi": {"a": ","}},
        {"path": "/tmp/x.db", "source_id": "s", "table": "t", "axes": {"a": 1}},
        {"path": "/tmp/x.db", "source_id": "s", "table": "t", "batch": 0},
    ],
)
def test_create_rejects_bad_options(options: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        create(options)
