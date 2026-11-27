"""Шаблоны схемы для `agoralar init` (ROADMAP §1).

Шаблон — это данные, а не код: набор осей и скаляров плюс стартовые секции
`[plugins.<имя>]` для `agoralar.toml`. Шаблон не создаёт объектов: демо и
примеры — отдельный стенд (`examples/demo`).

Почему в коде, а не в TOML-файлах пакета: шаблон обязан ехать в колесе
(иначе `pip install agoralar` его не увидит), а package-data в сборочном
переименовании `nu → agoralar` — лишняя точка отказа. Пользовательские
шаблоны — отдельная задача, когда появится спрос.

Оси неизменяемы (DOMAIN A4): `init` умеет только добавлять недостающие.
Повторный запуск с `--force` не переименовывает и не удаляет ничего —
пропускает существующие оси и сообщает об этом.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from axislar_core.contracts import AxisDefinition, AxisKind, ScalarDefinition


class UnknownTemplateError(ValueError):
    """Шаблона с таким именем нет."""


@dataclass(frozen=True, slots=True)
class Template:
    """Готовый набор схемы и стартового конфига."""

    name: str
    summary: str
    store: str
    axes: tuple[AxisDefinition, ...]
    scalars: tuple[ScalarDefinition, ...]
    plugins: Mapping[str, Mapping[str, Any]]

    def describe(self) -> str:
        return f"{self.name}: {self.summary}"


#: Домашний видеоархив: дерево `collection/section/title/файл`, имя файла —
#: автор и метка времени, код выпуска в названии каталога (VTA/VTG/...).
VIDEO_ARCHIVE = Template(
    name="video-archive",
    summary="домашний видеоархив: коллекции, разделы, выпуски, файлы",
    store="sqlite",
    axes=(
        AxisDefinition(name="collection", kind=AxisKind.CATEGORICAL, description="коллекция"),
        AxisDefinition(name="section", kind=AxisKind.CATEGORICAL, description="раздел"),
        AxisDefinition(name="title", kind=AxisKind.CATEGORICAL, description="выпуск/название"),
        AxisDefinition(name="code", kind=AxisKind.CATEGORICAL, description="код выпуска (VTA)"),
        AxisDefinition(name="author", kind=AxisKind.CATEGORICAL, description="автор из имени"),
        AxisDefinition(name="taken_at", kind=AxisKind.MEASURABLE, description="снято, сек"),
        AxisDefinition(name="format", kind=AxisKind.CATEGORICAL, description="расширение"),
        AxisDefinition(
            name="related",
            kind=AxisKind.REFERENCE,
            multi=True,
            target="video",
            description="связанные выпуски",
        ),
    ),
    scalars=(
        ScalarDefinition(name="path", searchable=True),
        ScalarDefinition(name="filename", searchable=True),
    ),
    plugins={
        "sqlite": {"path": "vault.sqlite3"},
        "filesystem": {
            "root": "library",
            "source_id": "library",
            "levels": ["collection", "section", "title"],
        },
    },
)

#: Заметки в духе Obsidian: папка и теги как оси, `[[wiki-links]]` — как
#: reference-ось, дата создания из frontmatter. Источник — плагин
#: `markdown` (ROADMAP §4.1); связи сразу видны в `agoralar links`.
OBSIDIAN_VAULT = Template(
    name="obsidian-vault",
    summary="заметки Obsidian: папки, теги, wiki-ссылки, дата создания",
    store="sqlite",
    axes=(
        AxisDefinition(name="folder", kind=AxisKind.CATEGORICAL, description="папка заметки"),
        AxisDefinition(name="tags", kind=AxisKind.CATEGORICAL, multi=True, description="теги"),
        AxisDefinition(name="created", kind=AxisKind.MEASURABLE, description="дата создания, сек"),
        AxisDefinition(
            name="links",
            kind=AxisKind.REFERENCE,
            multi=True,
            target="note",
            description="wiki-ссылки",
        ),
    ),
    scalars=(
        ScalarDefinition(name="title", searchable=True),
        ScalarDefinition(name="path", searchable=True),
        ScalarDefinition(name="filename", searchable=True),
    ),
    plugins={
        "sqlite": {"path": "vault.sqlite3"},
        "markdown": {"root": "vault", "source_id": "vault"},
    },
)

#: Все встроенные шаблоны по имени.
BUILTIN_TEMPLATES: Mapping[str, Template] = {
    template.name: template for template in (VIDEO_ARCHIVE, OBSIDIAN_VAULT)
}


def available_templates() -> tuple[Template, ...]:
    """Встроенные шаблоны в стабильном порядке."""
    return tuple(BUILTIN_TEMPLATES[name] for name in sorted(BUILTIN_TEMPLATES))


def load_template(name: str) -> Template:
    """Шаблон по имени; неизвестное имя — `UnknownTemplateError` со списком."""
    template = BUILTIN_TEMPLATES.get(name)
    if template is None:
        known = ", ".join(sorted(BUILTIN_TEMPLATES)) or "<нет>"
        raise UnknownTemplateError(f"unknown template {name!r}; available: {known}")
    return template


__all__ = [
    "BUILTIN_TEMPLATES",
    "OBSIDIAN_VAULT",
    "VIDEO_ARCHIVE",
    "Template",
    "UnknownTemplateError",
    "available_templates",
    "load_template",
]
