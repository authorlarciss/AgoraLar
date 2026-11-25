"""Состояние синхронизации: история прогонов по источникам.

Хранится рядом с данными (`.agoralar/sync.json` по умолчанию) и нужна
только для отчёта: что изменилось с прошлого раза. Модель от этого файла
не зависит — потеря состояния означает лишь потерю истории.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Версия формата файла состояния.
STATE_VERSION = 1


class SyncStateError(RuntimeError):
    """Файл состояния повреждён или несовместим."""


@dataclass(frozen=True, slots=True)
class SyncState:
    """Итог одного прогона по источнику."""

    source_id: str
    last_run_at: datetime
    scanned: int
    counts: dict[str, int] = field(default_factory=dict)
    conflicts: int = 0

    def describe(self) -> str:
        plan = ", ".join(f"{kind}={count}" for kind, count in sorted(self.counts.items()))
        stamp = self.last_run_at.isoformat(timespec="seconds")
        return f"{stamp} scanned={self.scanned} plan[{plan or 'нет изменений'}] conflicts={self.conflicts}"


def state_path_for(base: Path) -> Path:
    """Путь файла состояния по умолчанию."""
    return base / ".agoralar" / "sync.json"


def load_state(path: Path) -> dict[str, SyncState]:
    """Прочитать историю; отсутствие файла — пустая история."""
    if not path.is_file():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SyncStateError(f"broken sync state {path}: {error}") from error

    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        found = raw.get("version") if isinstance(raw, dict) else None
        raise SyncStateError(
            f"unsupported sync state version in {path}: {found!r} (expected {STATE_VERSION})"
        )

    states: dict[str, SyncState] = {}
    for source_id, payload in raw.get("sources", {}).items():
        try:
            states[str(source_id)] = SyncState(
                source_id=str(source_id),
                last_run_at=datetime.fromisoformat(payload["last_run_at"]),
                scanned=int(payload["scanned"]),
                counts={str(k): int(v) for k, v in payload.get("counts", {}).items()},
                conflicts=int(payload.get("conflicts", 0)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SyncStateError(f"broken sync state {path}: {error}") from error
    return states


def save_state(path: Path, states: dict[str, SyncState]) -> None:
    """Записать историю (атомарно)."""
    payload: dict[str, Any] = {
        "version": STATE_VERSION,
        "sources": {
            source_id: {
                "last_run_at": state.last_run_at.isoformat(),
                "scanned": state.scanned,
                "counts": state.counts,
                "conflicts": state.conflicts,
            }
            for source_id, state in sorted(states.items())
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def record(states: dict[str, SyncState], state: SyncState) -> dict[str, SyncState]:
    """Вернуть новую историю с обновлённой записью источника."""
    updated = dict(states)
    updated[state.source_id] = state
    return updated


def now() -> datetime:
    """Текущее время (UTC) — отдельной функцией, чтобы тесты могли подменить."""
    return datetime.now(UTC)


__all__ = [
    "STATE_VERSION",
    "SyncState",
    "SyncStateError",
    "load_state",
    "now",
    "record",
    "save_state",
    "state_path_for",
]
