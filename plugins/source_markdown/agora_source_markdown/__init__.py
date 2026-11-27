"""Заметки Markdown как источник объектов AxisLar (ROADMAP §4.1).

Что читается:

- **frontmatter** — простой YAML-подобный блок между `---`: скаляры и
  списки (`tags: [a, b]` или блок `- item`). Вложенные таблицы не
  поддерживаются: источник сообщает о проблеме, а не угадывает;
- **#tags** в теле заметки (кодовые блоки игнорируются);
- **[[wiki-links]]** — в том числе с якорем `[[note#part]]` и псевдонимом
  `[[note|alias]]`.

Ссылка превращается в фасет по reference-оси: значение — `id` целевой
заметки. Цель ищется по имени файла (как в Obsidian) и по пути без
расширения. Ссылка на несуществующую заметку **не создаёт** теневой
объект: она попадает в `warnings` и отбрасывается (ADR-0016 — источник
сообщает, а не решает молча).

Дерево **не изменяется**: источник только читает. `id` следует политике
ядра (`entity_id_for(source_id, key)`), `content_hash` — полный sha256
файла, чтобы `sync` умел отличать правку от переименования.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from axislar_core.contracts import (
    CategoricalValue,
    Entity,
    FacetOwner,
    FacetValue,
    IEntitySource,
    MeasurableValue,
    Origin,
    ReferenceValue,
    entity_id_for,
)

#: Контракт ядра, с которым совместим плагин.
REQUIRES_CONTRACT = ">=5,<6"

#: Каталоги, которые не являются заметками.
SKIP_DIRS = frozenset({".git", ".obsidian", ".trash", ".idea", ".vscode", "node_modules"})

#: Расширение заметок.
SUFFIX = ".md"

#: `#tag`, `#tag/subtag`; не внутри слова и не `#` перед пробелом.
_TAG_RE = re.compile(r"(?<![\w/#])#([\w][\w/-]*)", re.UNICODE)

#: `[[target]]`, `[[target#anchor]]`, `[[target|alias]]`.
_WIKI_RE = re.compile(r"\[\[([^\]|#]*)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")

#: Кодовые блоки: внутри них теги и ссылки не считаются.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

#: Поля frontmatter, из которых берётся дата создания.
_DATE_KEYS = ("created", "date", "updated")


@dataclass(slots=True)
class MarkdownSource(IEntitySource):
    """Источник поверх дерева Markdown-заметок."""

    root: Path
    source_id: str
    folder_axis: str = "folder"
    tags_axis: str = "tags"
    links_axis: str = "links"
    created_axis: str = "created"
    kind: str = "note"
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return f"markdown:{self.root} (source_id={self.source_id})"

    async def scan(self) -> AsyncIterator[Entity]:
        self.warnings = []
        files = _files(self.root)
        by_name = _links_index(files)
        for key, path in files:
            entity = self._build(path, key, by_name)
            if entity is not None:
                yield entity

    # ------------------------------------------------------------- построение

    def _build(self, path: Path, key: str, by_name: Mapping[str, str]) -> Entity | None:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            self.warnings.append(f"{key}: не прочитан ({error})")
            return None

        parsed, body = _split_frontmatter(text.replace("\r\n", "\n"))
        front: dict[str, Any] = parsed or {}
        if parsed is None:
            self.warnings.append(f"{key}: frontmatter не закрыт, прочитан как тело")

        cleaned = _FENCE_RE.sub("", body)
        facets: dict[str, tuple[FacetValue, ...]] = {}

        folder = Path(key).parent.as_posix()
        if folder not in {"", "."}:
            facets[self.folder_axis] = (CategoricalValue(value=folder),)

        tags = _tags(front, cleaned)
        if tags:
            facets[self.tags_axis] = tuple(CategoricalValue(value=value) for value in tags)

        links, missing = _links(cleaned, by_name)
        if links:
            facets[self.links_axis] = tuple(
                ReferenceValue(target=entity_id_for(self.source_id, target)) for target in links
            )
        for target in missing:
            self.warnings.append(f"{key}: ссылка на несуществующую заметку [[{target}]]")

        created = _created(front)
        if created is not None:
            facets[self.created_axis] = (MeasurableValue(value=created, unit="s"),)

        scalars = {
            "title": _title(front, path),
            "path": key,
            "filename": path.name,
        }
        return Entity(
            id=entity_id_for(self.source_id, key),
            kind=self.kind,
            scalars=scalars,
            facets=facets,
            owners={axis: FacetOwner.SOURCE for axis in facets},
            origin=Origin(
                source_id=self.source_id,
                key=key,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                hash_algo="sha256",
            ),
        )


# ------------------------------------------------------------------- утилиты


def _files(root: Path) -> list[tuple[str, Path]]:
    """Заметки дерева: относительный путь (posix) и путь на диске."""
    if not root.is_dir():
        return []
    found: list[tuple[str, Path]] = []
    for path in sorted(root.rglob(f"*{SUFFIX}")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in relative.parts):
            continue
        found.append((relative.as_posix(), path))
    return found


def _links_index(files: Iterable[tuple[str, Path]]) -> dict[str, str]:
    """Карта «имя заметки → относительный путь» для разрешения wiki-ссылок.

    Ключи — имя файла без расширения и путь без расширения, в нижнем
    регистре: `[[Daily Notes]]` должен находить `notes/Daily Notes.md`.
    При совпадении имён в разных папках остаётся первый по порядку, а
    источник об этом не молчит — предупреждение добавляется при разборе.
    """
    index: dict[str, str] = {}
    for key, path in files:
        for candidate in (path.stem, key[: -len(SUFFIX)]):
            index.setdefault(candidate.lower(), key)
    return index


def _split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Отделить frontmatter от тела. `None` в первом значении — блок не закрыт."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    block = text[len("---\n") : end]
    body_start = end + len("\n---")
    if body_start < len(text) and text[body_start] == "\n":
        body_start += 1
    return _parse_frontmatter(block), text[body_start:]


def _parse_frontmatter(block: str) -> dict[str, Any]:
    """Минимальный разбор: скаляры, inline-списки и блочные списки."""
    result: dict[str, Any] = {}
    pending: str | None = None
    for raw in block.split("\n"):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- ") and pending is not None:
            value = _clean(line.lstrip()[2:])
            current = result.setdefault(pending, [])
            if isinstance(current, list):
                current.append(value)
            continue
        key, separator, rest = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        rest = rest.strip()
        pending = key
        if not rest:
            result[key] = []
            continue
        if rest.startswith("[") and rest.endswith("]"):
            result[key] = [_clean(item) for item in rest[1:-1].split(",") if item.strip()]
        else:
            result[key] = _clean(rest)
    return result


def _clean(value: str) -> str:
    return value.strip().strip("\"'").strip()


def _tags(front: Mapping[str, Any], body: str) -> list[str]:
    """Теги из frontmatter и из тела: без дубликатов, порядок появления."""
    values: list[str] = []
    for key in ("tags", "tag"):
        raw = front.get(key)
        if isinstance(raw, str):
            values.extend(piece.strip() for piece in raw.replace(",", " ").split())
        elif isinstance(raw, list):
            values.extend(str(piece).strip() for piece in raw)
    values.extend(match.group(1) for match in _TAG_RE.finditer(body))
    seen: dict[str, None] = {}
    for value in values:
        if value:
            seen.setdefault(value.lstrip("#"), None)
    return list(seen)


def _links(body: str, by_name: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """Разрешённые цели (пути) и имена, которых нет в дереве."""
    resolved: list[str] = []
    missing: list[str] = []
    seen: dict[str, None] = {}
    for match in _WIKI_RE.finditer(body):
        target = match.group(1).strip()
        if not target:
            continue
        key = target.lstrip("/")
        candidate = by_name.get(key.lower()) or by_name.get(
            key[: -len(SUFFIX)].lower() if key.lower().endswith(SUFFIX) else key.lower()
        )
        if candidate is None:
            if target not in seen:
                seen[target] = None
                missing.append(target)
            continue
        if candidate not in resolved:
            resolved.append(candidate)
    return resolved, missing


def _title(front: Mapping[str, Any], path: Path) -> str:
    value = front.get("title")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return path.stem


def _created(front: Mapping[str, Any]) -> float | None:
    for key in _DATE_KEYS:
        raw = front.get(key)
        if not isinstance(raw, str):
            continue
        parsed = _parse_date(raw)
        if parsed is not None:
            return parsed
    return None


def _parse_date(raw: str) -> float | None:
    value = raw.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def create(options: Mapping[str, Any]) -> MarkdownSource:
    """Фабрика из настроек плагина `[plugins.markdown]`."""
    root = options.get("root")
    if not root:
        raise ValueError("plugin markdown: option 'root' is required")
    source_id = str(options.get("source_id") or root)
    return MarkdownSource(
        root=Path(str(root)),
        source_id=source_id,
        folder_axis=str(options.get("folder_axis", "folder")),
        tags_axis=str(options.get("tags_axis", "tags")),
        links_axis=str(options.get("links_axis", "links")),
        created_axis=str(options.get("created_axis", "created")),
        kind=str(options.get("kind", "note")),
    )


def plugin() -> dict[str, Any]:
    """Манифест плагина обычными данными (без DTO продукта)."""
    return {"requires_contract": REQUIRES_CONTRACT, "factory": create}


__all__ = [
    "REQUIRES_CONTRACT",
    "SKIP_DIRS",
    "MarkdownSource",
    "create",
    "plugin",
]
