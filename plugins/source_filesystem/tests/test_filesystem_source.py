"""FS-источник: контракт синхронизации, фасеты, рассогласования."""

from __future__ import annotations

from pathlib import Path

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    ScalarDefinition,
    StorageSchema,
    run_source_sync_contract,
)

from agora_source_filesystem import (
    FilesystemSource,
    create,
    parse_filename,
    partial_hash,
    plugin,
)

SCHEMA = StorageSchema(
    axes=(
        AxisDefinition(name="owner", kind=AxisKind.CATEGORICAL),
        AxisDefinition(name="media", kind=AxisKind.CATEGORICAL),
        AxisDefinition(name="shelf", kind=AxisKind.CATEGORICAL),
        AxisDefinition(name="section", kind=AxisKind.CATEGORICAL),
        AxisDefinition(name="title", kind=AxisKind.CATEGORICAL),
        AxisDefinition(name="code", kind=AxisKind.CATEGORICAL),
        AxisDefinition(name="author", kind=AxisKind.CATEGORICAL),
        AxisDefinition(name="taken_at", kind=AxisKind.MEASURABLE),
        AxisDefinition(name="format", kind=AxisKind.CATEGORICAL),
    ),
    scalars=(ScalarDefinition(name="path"), ScalarDefinition(name="filename")),
)


def tree(tmp_path: Path, name: str = "gutsu__2025-01-29-00-26-24_1738099584068.mp4") -> Path:
    folder = tmp_path / "Owner" / "Video" / "Treat" / "Anime" / "VTA Berserk"
    folder.mkdir(parents=True)
    (folder / name).write_bytes(b"video-bytes")
    return tmp_path


def source(root: Path, **options: object) -> FilesystemSource:
    return create({"root": str(root), "source_id": "owner-media", **options})


async def test_source_passes_sync_contract(tmp_path: Path) -> None:
    tree(tmp_path)
    await run_source_sync_contract(lambda: source(tmp_path), SCHEMA)


async def test_axes_and_filename_facets(tmp_path: Path) -> None:
    tree(tmp_path)
    entities = [entity async for entity in source(tmp_path).scan()]
    assert len(entities) == 1
    entity = entities[0]

    values = {name: getattr(facet[0], "value", None) for name, facet in entity.facets.items()}
    assert values["owner"] == "Owner"
    assert values["media"] == "Video"
    assert values["shelf"] == "Treat"
    assert values["section"] == "Anime"
    assert values["title"] == "Berserk", "код должен уйти из title"
    assert values["code"] == "VTA"
    assert values["author"] == "gutsu"
    assert values["format"] == "mp4"
    assert values["taken_at"] == pytest.approx(1738099584.068)

    assert entity.scalars["filename"].startswith("gutsu__")
    assert entity.scalars["path"].endswith(".mp4")
    assert entity.origin is not None and entity.origin.content_hash


async def test_code_mismatch_is_reported(tmp_path: Path) -> None:
    tree(tmp_path)
    src = source(tmp_path, codes={"VTA": "games"})
    _ = [entity async for entity in src.scan()]
    assert any("код VTA" in warning and "games" in warning for warning in src.warnings)


async def test_missing_code_is_reported(tmp_path: Path) -> None:
    folder = tmp_path / "Owner" / "Video" / "Treat" / "Anime" / "Berserk"
    folder.mkdir(parents=True)
    (folder / "a_1738099584068.mp4").write_bytes(b"x")
    src = source(tmp_path)
    entities = [entity async for entity in src.scan()]
    assert len(entities) == 1
    assert any("нет кода" in warning for warning in src.warnings)


async def test_unexpected_depth_is_skipped(tmp_path: Path) -> None:
    folder = tmp_path / "Owner" / "Video"
    folder.mkdir(parents=True)
    (folder / "x_1738099584068.mp4").write_bytes(b"x")
    src = source(tmp_path)
    assert [entity async for entity in src.scan()] == []
    assert any("уровней каталогов" in warning for warning in src.warnings)


async def test_sparse_author_when_filename_has_no_author(tmp_path: Path) -> None:
    tree(tmp_path, name="plain-clip.mp4")
    entity = await anext(source(tmp_path).scan())
    assert "author" not in entity.facets, "нет автора — нет фасета, а не пустое значение"
    assert "taken_at" not in entity.facets


async def test_unknown_suffix_is_ignored(tmp_path: Path) -> None:
    tree(tmp_path)
    (tmp_path / "Owner" / "Video" / "Treat" / "Anime" / "VTA Berserk" / "notes.txt").write_text("x")
    assert len([entity async for entity in source(tmp_path).scan()]) == 1


async def test_hash_changes_with_content(tmp_path: Path) -> None:
    tree(tmp_path)
    first = await anext(source(tmp_path).scan())
    path = next((tmp_path / "Owner").rglob("*.mp4"))
    path.write_bytes(b"other-bytes")
    second = await anext(source(tmp_path).scan())

    assert first.id == second.id, "id зависит от пути, а не содержимого"
    assert first.origin is not None and second.origin is not None
    assert first.origin.content_hash != second.origin.content_hash


def test_parse_filename_variants() -> None:
    author, taken = parse_filename("gutsu__2025-01-29-00-26-24_1738099584068")
    assert author == "gutsu"
    assert taken == pytest.approx(1738099584.068)

    author, taken = parse_filename("Snaptik.app_6995811764617202945")
    assert author == "Snaptik.app"
    assert taken == pytest.approx(6995811764.617)

    assert parse_filename("plain-name") == (None, None)


def test_partial_hash_detects_content_change(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"a" * 10 + b"b" * 10)
    before = partial_hash(path)
    path.write_bytes(b"a" * 10 + b"c" * 10)
    assert partial_hash(path) != before


def test_plugin_manifest() -> None:
    manifest = plugin()
    assert manifest["requires_contract"] == ">=3,<4"
    assert callable(manifest["factory"])


@pytest.mark.parametrize("options", [{}, {"root": "/tmp"}])
def test_create_requires_root_and_source_id(options: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        create(options)
