"""Конфиг продукта: DTO (ADR-0011, P4 плана).

Конфиг знает только то, что нужно продукту, чтобы собрать ядро с нужным
хранилищем: активный бэкенд, путь к данным и параметры плагинов. Модель
данных конфиг не описывает — она в схеме хранилища (ядро).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Имя встроенного бэкенда ядра (без плагинов).
BUILTIN_STORE = "memory"


@dataclass(frozen=True, slots=True)
class Config:
    """Настройки продукта."""

    store: str = BUILTIN_STORE
    data: Path | None = None
    plugins: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def options_for(self, store: str) -> dict[str, Any]:
        """Параметры бэкенда: секция плагина + путь к данным.

        `data` — общий путь для файловых бэкендов; конкретная секция
        плагина его перекрывает.
        """
        options: dict[str, Any] = dict(self.plugins.get(store, {}))
        if self.data is not None and store != BUILTIN_STORE:
            options.setdefault("path", str(self.data))
        return options


__all__ = ["BUILTIN_STORE", "Config"]
