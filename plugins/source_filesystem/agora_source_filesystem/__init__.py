"""Файловое дерево как источник объектов AxisLar.

Путь и имя файла — единственные входные данные. Дерево **не
изменяется**: источник только читает.

Раскладка по осям задаётся конфигом `levels` (имена осей по уровням
каталогов); последний уровень трактуется как `title`, из которого
выделяется код (`VTA Cyberpunk` → `code=VTA`, `title=Cyberpunk`).
Код сверяется с осью `section` по карте `codes` — рассогласование
попадает в `warnings`, но объект всё равно импортируется (ADR-0016:
адаптер сообщает, а не решает молча).

Имя файла разбирается набором шаблонов: автор и метка времени, где они
есть; иначе объект остаётся разреженным (без `author`/`taken_at`).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
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
    entity_id_for,
)

#: Контракт ядра, с которым совместим плагин.
REQUIRES_CONTRACT = ">=5,<6"

#: Оси по умолчанию: пять уровней каталогов.
DEFAULT_LEVELS = ("owner", "media", "shelf", "section", "title")

#: Расширения, которые считаются объектами.
DEFAULT_SUFFIXES = (".mp4", ".webm", ".mov", ".mkv", ".jpg", ".jpeg", ".png")

#: Размер частичного хэша (голова и хвост файла).
HASH_CHUNK = 64 * 1024

_CODE_RE = re.compile(r"^(?P<code>[A-Z]{2,4})\s+(?P<title>.+)$")

_NAME_PATTERNS = (
    re.compile(
        r"^(?P<author>.+?)__(?P<date>\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})_(?P<epoch>\d{9,})$"
    ),
    re.compile(
        r"^(?P<author>.+?)_(?P<date>\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})_(?P<epoch>\d{9,})$"
    ),
    re.compile(r"^(?P<author>.+?)_(?P<epoch>\d{9,})$"),
)


@dataclass(slots=True)
class FilesystemSource(IEntitySource):
    """Источник поверх каталога."""

    root: Path
    source_id: str
    levels: tuple[str, ...] = DEFAULT_LEVELS
    codes: Mapping[str, str] = field(default_factory=dict)
    suffixes: tuple[str, ...] = DEFAULT_SUFFIXES
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return f"filesystem:{self.root} (source_id={self.source_id})"

    async def scan(self) -> AsyncIterator[Entity]:
        self.warnings = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in self.suffixes:
                continue
            entity = self._build(path)
            if entity is not None:
                yield entity

    # ------------------------------------------------------------- построение

    def _build(self, path: Path) -> Entity | None:
        relative = path.relative_to(self.root)
        key = relative.as_posix()
        parts = relative.parts[:-1]

        if len(parts) != len(self.levels):
            self.warnings.append(
                f"{key}: ожидалось {len(self.levels)} уровней каталогов, найдено {len(parts)}"
            )
            return None

        facets: dict[str, tuple[FacetValue, ...]] = {}
        for level, value in zip(self.levels, parts, strict=True):
            facets[level] = (CategoricalValue(value=value),)

        title = facets["title"][0]
        assert isinstance(title, CategoricalValue)
        code_match = _CODE_RE.match(title.value)
        if code_match:
            code = code_match.group("code")
            facets["title"] = (CategoricalValue(value=code_match.group("title").strip()),)
            facets["code"] = (CategoricalValue(value=code),)
            expected = self.codes.get(code)
            section = facets.get("section", ())
            first_section = section[0] if section else None
            actual = first_section.value if isinstance(first_section, CategoricalValue) else None
            if expected is not None and actual is not None and expected.lower() != actual.lower():
                self.warnings.append(
                    f"{key}: код {code} соответствует section={expected!r}, "
                    f"а каталог даёт {actual!r}"
                )
        else:
            self.warnings.append(f"{key}: в названии каталога нет кода (VTA/VTG/...)")

        author, taken_at = parse_filename(path.stem)
        if author:
            facets["author"] = (CategoricalValue(value=author),)
        if taken_at is not None:
            facets["taken_at"] = (MeasurableValue(value=taken_at, unit="s"),)
        facets["format"] = (CategoricalValue(value=path.suffix.lower().lstrip(".")),)

        return Entity(
            id=entity_id_for(self.source_id, key),
            kind="video" if path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"} else "image",
            scalars={"path": key, "filename": path.name},
            facets=facets,
            owners={axis: FacetOwner.SOURCE for axis in facets},
            origin=Origin(
                source_id=self.source_id,
                key=key,
                content_hash=partial_hash(path),
                hash_algo=f"partial-sha256-{HASH_CHUNK}",
            ),
        )


# ------------------------------------------------------------------- утилиты


#: Масштаб метки времени в зависимости от разрядности (мс/мкс/нс).
_EPOCH_SCALE = {10: 1.0, 13: 1e3, 16: 1e6, 19: 1e9}


def epoch_to_seconds(epoch: str) -> float | None:
    """Привести метку времени из имени файла к секундам.

    Разрядность варьируется: 10 знаков — секунды, 13 — миллисекунды,
    16 — микросекунды, 19 — наносекунды.
    """
    scale = _EPOCH_SCALE.get(len(epoch))
    if scale is None:
        return None
    return int(epoch) / scale


def parse_filename(stem: str) -> tuple[str | None, float | None]:
    """Автор и метка времени (в секундах) из имени файла."""
    for pattern in _NAME_PATTERNS:
        match = pattern.match(stem)
        if match is None:
            continue
        author = match.group("author").strip(" _.-") or None
        epoch = match.groupdict().get("epoch")
        taken_at = epoch_to_seconds(epoch) if epoch else None
        return author, taken_at
    return None, None


def partial_hash(path: Path, chunk: int = HASH_CHUNK) -> str:
    """Хэш содержимого: размер + голова + хвост (быстро для больших файлов)."""
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(chunk))
        if size > chunk:
            handle.seek(max(chunk, size - chunk))
            digest.update(handle.read(chunk))
    return digest.hexdigest()


def create(options: Mapping[str, Any]) -> FilesystemSource:
    """Фабрика из настроек плагина."""
    root = options.get("root")
    source_id = options.get("source_id")
    if not root:
        raise ValueError("filesystem source requires 'root'")
    if not source_id:
        raise ValueError("filesystem source requires 'source_id'")

    levels = options.get("levels")
    codes = options.get("codes")
    suffixes = options.get("suffixes")
    return FilesystemSource(
        root=Path(str(root)),
        source_id=str(source_id),
        levels=tuple(str(level) for level in levels) if levels else DEFAULT_LEVELS,
        codes={str(key): str(value) for key, value in (codes or {}).items()},
        suffixes=tuple(str(item) for item in suffixes) if suffixes else DEFAULT_SUFFIXES,
    )


def plugin() -> dict[str, Any]:
    """Манифест плагина обычными данными (ADR-0013)."""
    return {"requires_contract": REQUIRES_CONTRACT, "factory": create}


__all__ = [
    "DEFAULT_LEVELS",
    "DEFAULT_SUFFIXES",
    "REQUIRES_CONTRACT",
    "FilesystemSource",
    "create",
    "epoch_to_seconds",
    "parse_filename",
    "partial_hash",
    "plugin",
]
