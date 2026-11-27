"""Источник Markdown: контракты ядра, frontmatter, теги, wiki-ссылки."""

from __future__ import annotations

from pathlib import Path

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    CategoricalValue,
    Entity,
    MeasurableValue,
    ReferenceValue,
    ScalarDefinition,
    StorageSchema,
    entity_id_for,
    run_source_contract,
    run_source_sync_contract,
)

from agora_source_markdown import MarkdownSource, create, plugin

SCHEMA = StorageSchema(
    axes=(
        AxisDefinition(name="folder", kind=AxisKind.CATEGORICAL),
        AxisDefinition(name="tags", kind=AxisKind.CATEGORICAL, multi=True),
        AxisDefinition(name="created", kind=AxisKind.MEASURABLE),
        AxisDefinition(name="links", kind=AxisKind.REFERENCE, multi=True, target="note"),
    ),
    scalars=(
        ScalarDefinition(name="title", searchable=True),
        ScalarDefinition(name="path"),
        ScalarDefinition(name="filename"),
    ),
)


def categorical(entity: Entity, axis: str) -> list[str]:
    return [value.value for value in entity.facets[axis] if isinstance(value, CategoricalValue)]


def measurable(entity: Entity, axis: str) -> float:
    values = [value.value for value in entity.facets[axis] if isinstance(value, MeasurableValue)]
    assert values, f"axis {axis!r} has no measurable value"
    return values[0]


def references(entity: Entity, axis: str) -> set[str]:
    return {str(value.target) for value in entity.facets[axis] if isinstance(value, ReferenceValue)}


def make_vault(root: Path) -> Path:
    (root / "notes").mkdir(parents=True)
    (root / ".obsidian").mkdir()
    (root / "index.md").write_text(
        "---\n"
        'title: "Главная"\n'
        "tags: [genre, noir]\n"
        "created: 2024-05-01\n"
        "---\n"
        "# Добро пожаловать\n"
        "Смотри [[Daily Note]] и [[notes/Today|сегодня]].\n"
        "#inline\n",
        encoding="utf-8",
    )
    (root / "notes" / "Daily Note.md").write_text(
        "Заметка дня. Назад: [[index]].\n", encoding="utf-8"
    )
    (root / "notes" / "Today.md").write_text("Сегодня.\n", encoding="utf-8")
    (root / "notes" / "orphan.md").write_text(
        "Ссылка в пустоту: [[missing-note]].\n", encoding="utf-8"
    )
    (root / "notes" / "code.md").write_text(
        "```\n[[not-a-link]] #not-a-tag\n```\nнастоящий #tag\n", encoding="utf-8"
    )
    (root / ".obsidian" / "ignored.md").write_text("служебное\n", encoding="utf-8")
    return root


def source(root: Path) -> MarkdownSource:
    return MarkdownSource(root=root, source_id="vault")


async def test_passes_kernel_contracts(tmp_path: Path) -> None:
    make_vault(tmp_path)
    await run_source_contract(lambda: source(tmp_path), SCHEMA)
    await run_source_sync_contract(lambda: source(tmp_path), SCHEMA)


async def test_frontmatter_tags_and_body_tags(tmp_path: Path) -> None:
    make_vault(tmp_path)
    entities = {str(entity.id): entity for entity in [e async for e in source(tmp_path).scan()]}
    index = entities[str(entity_id_for("vault", "index.md"))]

    assert categorical(index, "tags") == ["genre", "noir", "inline"], (
        "frontmatter + тело, без дубликатов"
    )
    assert index.scalars["title"] == "Главная"
    assert measurable(index, "created") == pytest.approx(1714521600.0)


async def test_wiki_links_resolve_by_name_and_path(tmp_path: Path) -> None:
    make_vault(tmp_path)
    entities = {str(entity.id): entity for entity in [e async for e in source(tmp_path).scan()]}
    index = entities[str(entity_id_for("vault", "index.md"))]

    assert references(index, "links") == {
        str(entity_id_for("vault", "notes/Daily Note.md")),
        str(entity_id_for("vault", "notes/Today.md")),
    }


async def test_missing_link_is_a_warning_not_an_object(tmp_path: Path) -> None:
    make_vault(tmp_path)
    engine = source(tmp_path)
    entities = [entity async for entity in engine.scan()]

    assert len(entities) == 5, "служебный каталог .obsidian не читается"
    assert any("missing-note" in warning for warning in engine.warnings)
    orphan = next(e for e in entities if e.scalars["filename"] == "orphan.md")
    assert "links" not in orphan.facets, "битая ссылка не превращается в фасет"


async def test_code_fences_are_ignored(tmp_path: Path) -> None:
    make_vault(tmp_path)
    engine = source(tmp_path)
    entities = {entity.scalars["filename"]: entity async for entity in engine.scan()}

    code = entities["code.md"]
    assert categorical(code, "tags") == ["tag"]
    assert "links" not in code.facets


async def test_unclosed_frontmatter_is_reported(tmp_path: Path) -> None:
    (tmp_path / "broken.md").write_text("---\ntitle: x\nбез конца\n", encoding="utf-8")
    engine = source(tmp_path)
    entities = [entity async for entity in engine.scan()]

    assert len(entities) == 1
    assert any("frontmatter" in warning for warning in engine.warnings)


async def test_origin_and_ids_follow_kernel_policy(tmp_path: Path) -> None:
    make_vault(tmp_path)
    entity = None
    async for candidate in source(tmp_path).scan():
        if candidate.scalars["filename"] == "index.md":
            entity = candidate
            break
    assert entity is not None

    assert entity.origin is not None
    assert str(entity.id) == str(entity_id_for("vault", "index.md"))
    assert entity.origin.content_hash and len(entity.origin.content_hash) == 64
    assert entity.origin.hash_algo == "sha256"
    assert entity.kind == "note"


def test_create_requires_root() -> None:
    with pytest.raises(ValueError, match="root"):
        create({})


def test_manifest_is_plain_data() -> None:
    manifest = plugin()
    assert manifest["requires_contract"] == ">=5,<6"
    assert callable(manifest["factory"])
