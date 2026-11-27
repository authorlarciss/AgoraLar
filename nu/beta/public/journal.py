"""Журнал изменений и откат (фаза 2, пункт 2.3).

Пишем снимки объектов до и после правки, а не диффы: снимок нельзя
«неправильно применить», он не зависит от порядка операций и переживает
смену формата фасетов. Цена — размер файла, поэтому журнал ограничен
числом записей.

Что journalируется: правки человека — `put`, `patch`, откат. Синхронизация
источника не журналируется: её результат воспроизводим из источника
(`sync` и есть откат), а журнал из десятков тысяч записей мешал бы искать
свою правку.

Откат:
- была правка (снимок «до» есть) — восстанавливаем его с проверкой ревизии;
- было создание (снимка «до» нет) — ставим tombstone через
  `tombstone_entity` ядра: если на созданный объект уже кто-то ссылается,
  откат отказывает (`REFERENCE_IN_USE`), а не оставляет висячую ссылку
  (ADR-0025 ядра).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from axislar_core.contracts import Entity, EntityId, IStorageEngine, tombstone_entity

#: Сколько записей журнала хранить. Дальше — обрезаем при записи.
DEFAULT_LIMIT = 200


class JournalError(RuntimeError):
    """Журнал повреждён или откат невозможен."""


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """Одна запись: снимок объекта до и после изменения."""

    at: datetime
    kind: str
    entity_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None

    def as_json(self) -> str:
        return json.dumps(
            {
                "at": self.at.isoformat(),
                "kind": self.kind,
                "entity_id": self.entity_id,
                "before": self.before,
                "after": self.after,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, line: str) -> JournalEntry:
        try:
            payload = json.loads(line)
            at = datetime.fromisoformat(str(payload["at"]))
        except (KeyError, TypeError, ValueError) as error:
            raise JournalError(f"broken journal entry: {error}") from error
        return cls(
            at=at,
            kind=str(payload.get("kind", "update")),
            entity_id=str(payload.get("entity_id", "")),
            before=payload.get("before"),
            after=payload.get("after"),
        )


def journal_path_for(base: Path) -> Path:
    """Журнал лежит рядом с состоянием продукта."""
    return base / ".agoralar" / "journal.jsonl"


class Journal:
    """Append-only файл журнала с ограничением длины."""

    def __init__(self, path: Path, limit: int = DEFAULT_LIMIT) -> None:
        self.path = Path(path)
        self.limit = limit

    def entries(self) -> list[JournalEntry]:
        if not self.path.is_file():
            return []
        result: list[JournalEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                result.append(JournalEntry.from_json(line))
        return result

    def append(self, entries: Iterable[JournalEntry]) -> None:
        fresh = list(entries)
        if not fresh:
            return
        existing = self.entries()
        combined = [*existing, *fresh][-self.limit :]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            "".join(entry.as_json() + "\n" for entry in combined), encoding="utf-8"
        )
        temporary.replace(self.path)

    def last(self) -> JournalEntry | None:
        entries = self.entries()
        return entries[-1] if entries else None

    def pop_last(self) -> JournalEntry | None:
        entries = self.entries()
        if not entries:
            return None
        entry = entries.pop()
        self.path.write_text("".join(item.as_json() + "\n" for item in entries), encoding="utf-8")
        return entry


def snapshot(entity: Entity | None) -> dict[str, Any] | None:
    return None if entity is None else json.loads(entity.model_dump_json())


def make_entry(kind: str, before: Entity | None, after: Entity | None) -> JournalEntry:
    present = after if after is not None else before
    entity_id = str(present.id) if present is not None else ""
    return JournalEntry(
        at=datetime.now(UTC),
        kind=kind,
        entity_id=entity_id,
        before=snapshot(before),
        after=snapshot(after),
    )


async def undo_last(storage: IStorageEngine, journal: Journal) -> JournalEntry | None:
    """Откатить последнюю журнальную правку.

    Возвращает отменённую запись или `None`, если журнал пуст. Конфликт
    ревизий не игнорируется: если объект успели изменить после записи,
    откат вернёт `CONFLICT` — молча затирать чужие правки нельзя.
    """
    entry = journal.last()
    if entry is None:
        return None

    expected = _recorded_rev(entry)
    if entry.before is None:
        current = await storage.get(EntityId(entry.entity_id))
        if current is not None and current.deleted_at is None:
            errors = await tombstone_entity(storage, current.id, expected_rev=expected)
            if errors:
                raise JournalError(
                    f"cannot undo creation: {errors[0]} — снимите ссылки или откажитесь от отката"
                )
        journal.pop_last()
        return entry

    restore = Entity.model_validate(entry.before)
    errors = await storage.upsert(restore, expected_rev=expected)
    if errors:
        raise JournalError(f"cannot undo: {errors[0]} — объект меняли после этой записи журнала")
    journal.pop_last()
    return entry


def _recorded_rev(entry: JournalEntry) -> int:
    """Ревизия, которую запись журнала оставила после себя.

    Откат не должен затирать чужие правки: если объект с тех пор менялся,
    `expected_rev` не совпадёт и бэкенд вернёт `CONFLICT` — это и нужно.
    """
    payload = entry.after or entry.before or {}
    rev = payload.get("rev")
    if not isinstance(rev, int):
        raise JournalError(f"journal entry for {entry.entity_id!r} has no revision")
    return rev


__all__ = [
    "DEFAULT_LIMIT",
    "Journal",
    "JournalEntry",
    "JournalError",
    "journal_path_for",
    "make_entry",
    "snapshot",
    "undo_last",
]
