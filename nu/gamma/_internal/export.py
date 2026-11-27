"""Экспорт представлений: дерево симлинков по значениям осей (ROADMAP §3).

Смысл — «порядок без риска»: оригиналы не перемещаются и не изменяются,
новое дерево состоит только из ссылок. Поэтому по умолчанию включён
`--dry-run`: сначала показываем план, применяем по `--apply`.

Решения, зафиксированные в ROADMAP:

- мультизначные оси: `--multi first` (по умолчанию, чтобы не раздувать
  дерево), `all` (объект попадает в каждое значение) или `join` (значения
  склеиваются в один сегмент пути);
- отсутствие значения — норма, попадает в bucket `_none`;
- коллизии имён разрешаются суффиксом `-<id[:8]>` детерминированно;
- в непустой каталог без `--force` не пишем; существующие ссылки не
  удаляем, а сообщаем о конфликте;
- `--hardlink` для файловых систем без симлинков (Windows, права).
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from axislar_core.contracts import (
    AxisKind,
    CategoricalValue,
    Entity,
    MeasurableValue,
    ReferenceValue,
)

#: Значения `--multi`.
MULTI_MODES = ("first", "all", "join")

#: Bucket для объектов без значения по оси.
NONE_BUCKET = "_none"

#: Сколько путей показывать в плане.
SAMPLE = 10


class ExportError(ValueError):
    """Экспорт невозможен: не задан корень источника, каталог занят и т.п."""


@dataclass(frozen=True, slots=True)
class Link:
    """Одна ссылка плана: куда положить и на что она указывает."""

    relative: str
    target: Path
    entity_id: str


@dataclass(frozen=True, slots=True)
class ExportPlan:
    """План экспорта: что будет создано и что пропущено."""

    root: Path
    group_by: tuple[str, ...]
    links: tuple[Link, ...]
    skipped_no_path: tuple[str, ...] = ()
    missing_files: tuple[str, ...] = ()
    collisions: tuple[str, ...] = ()


@dataclass(slots=True)
class ExportReport:
    """Что реально сделано при `--apply`."""

    created: int = 0
    existing: int = 0
    conflicts: list[str] = field(default_factory=list)


def plan_export(
    entities: Iterable[Entity],
    group_by: Sequence[str],
    *,
    output_root: Path,
    source_root: Path | None,
    multi: str = "first",
    name_scalar: str = "filename",
) -> ExportPlan:
    """Построить план дерева симлинков (ничего не пишет на диск)."""
    if not group_by:
        raise ExportError("нужен хотя бы один сегмент: --group-by <ось>[,<ось>]")
    if multi not in MULTI_MODES:
        raise ExportError(f"unknown --multi {multi!r}, ожидалось {', '.join(MULTI_MODES)}")
    files_root = Path(source_root) if source_root is not None else None

    links: list[Link] = []
    skipped: list[str] = []
    missing: list[str] = []
    collisions: list[str] = []
    used: set[str] = set()

    for entity in entities:
        if entity.deleted_at is not None:
            continue
        key = entity.scalars.get("path")
        if not key:
            skipped.append(str(entity.id))
            continue
        if files_root is None:
            raise ExportError(
                "не задан корень источника: укажите --source-root или "
                "корень в секции [plugins.<источник>] конфига"
            )
        target = (files_root / key).resolve()
        if not target.exists():
            missing.append(key)
            continue

        name = entity.scalars.get(name_scalar) or Path(key).name
        for parts in _buckets(entity, group_by, multi):
            relative = "/".join([*parts, name])
            if relative in used:
                name_with_id = _with_id(name, str(entity.id))
                relative = "/".join([*parts, name_with_id])
                collisions.append(relative)
            used.add(relative)
            links.append(Link(relative=relative, target=target, entity_id=str(entity.id)))

    return ExportPlan(
        root=Path(output_root),
        group_by=tuple(group_by),
        links=tuple(links),
        skipped_no_path=tuple(skipped),
        missing_files=tuple(missing),
        collisions=tuple(collisions),
    )


def _buckets(entity: Entity, group_by: Sequence[str], multi: str) -> list[list[str]]:
    """Значения осей для пути: по одному списку сегментов на каждую ось."""
    per_axis: list[list[str]] = []
    for axis in group_by:
        values = entity.facets.get(axis, ())
        labels = [_label(value) for value in values]
        if not labels:
            per_axis.append([NONE_BUCKET])
        elif len(labels) == 1 or multi == "first":
            per_axis.append([labels[0]])
        elif multi == "join":
            per_axis.append(["+".join(sorted(labels))])
        else:  # all
            per_axis.append(sorted(dict.fromkeys(labels)))
    return [list(combination) for combination in itertools.product(*per_axis)]


def _label(value: object) -> str:
    if isinstance(value, CategoricalValue):
        return value.value
    if isinstance(value, ReferenceValue):
        return str(value.target)
    if isinstance(value, MeasurableValue):
        return f"{value.value:g}"
    return str(value)  # pragma: no cover - защита от нового типа значения


def _with_id(name: str, entity_id: str) -> str:
    path = Path(name)
    return f"{path.stem}-{entity_id[:8]}{path.suffix}"


def apply_export(plan: ExportPlan, *, force: bool = False, hardlink: bool = False) -> ExportReport:
    """Создать дерево ссылок. Оригиналы не трогаются, чужие ссылки не удаляются."""
    unknown = _unknown_entries(plan)
    if unknown and not force:
        sample = ", ".join(unknown[:3])
        raise ExportError(
            f"в {plan.root} есть файлы, которых нет в плане ({len(unknown)}): {sample}. "
            "Это чужое содержимое; используйте --force, чтобы добавить ссылки рядом"
        )

    report = ExportReport()
    for link in plan.links:
        destination = plan.root / link.relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            if _points_to(destination, link.target):
                report.existing += 1
                continue
            report.conflicts.append(link.relative)
            continue
        try:
            if hardlink:
                os.link(link.target, destination)
            else:
                os.symlink(link.target, destination)
        except OSError as error:
            raise ExportError(
                f"не удалось создать ссылку {destination}: {error}. "
                "На файловых системах без симлинков используйте --hardlink"
            ) from error
        report.created += 1
    return report


def _unknown_entries(plan: ExportPlan) -> list[str]:
    """Файлы в каталоге плана, которых план не создавал: чужое содержимое."""
    if not plan.root.is_dir():
        return []
    planned = {link.relative for link in plan.links}
    unknown: list[str] = []
    for path in sorted(plan.root.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(plan.root).as_posix()
        if relative not in planned:
            unknown.append(relative)
    return unknown


def _points_to(destination: Path, target: Path) -> bool:
    if destination.is_symlink():
        return destination.resolve() == target
    try:
        return destination.samefile(target)
    except OSError:  # pragma: no cover - цель исчезла
        return False


def render_plan(plan: ExportPlan) -> str:
    """Человекочитаемый план: что будет создано и что пропущено."""
    lines = [
        f"export symlinks: {len(plan.links)} ссылок в {plan.root}",
        f"  группировка: {', '.join(plan.group_by)}",
    ]
    for link in plan.links[:SAMPLE]:
        lines.append(f"  {link.relative} -> {link.target}")
    if len(plan.links) > SAMPLE:
        lines.append(f"  … и ещё {len(plan.links) - SAMPLE}")
    if plan.collisions:
        lines.append(f"  коллизии имён разрешены суффиксом id: {len(plan.collisions)}")
    if plan.skipped_no_path:
        lines.append(f"  пропущено без скаляра path: {len(plan.skipped_no_path)}")
    if plan.missing_files:
        lines.append(f"  файлов нет на диске: {len(plan.missing_files)}")
    return "\n".join(lines)


def axis_kinds_note() -> str:
    """Подсказка про типы осей (используется в --help и сообщениях)."""
    return f"подходят любые оси, кроме {AxisKind.REFERENCE.value} без цели"


__all__ = [
    "MULTI_MODES",
    "NONE_BUCKET",
    "ExportError",
    "ExportPlan",
    "ExportReport",
    "Link",
    "apply_export",
    "axis_kinds_note",
    "plan_export",
    "render_plan",
]
