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

#: Имя встроенного бэкенда ядра (без плагинов): эталон для тестов и демо.
BUILTIN_STORE = "memory"

#: Бэкенд по умолчанию для продукта. SQLite, а не json: инкрементальная
#: запись, индексы по фасетам и ссылкам, открытие на порядок быстрее
#: (замеры в `scripts/bench.py`). `json` остаётся в комплекте — он удобен для
#: переносимого файла, но архив по умолчанию живёт в sqlite.
DEFAULT_STORE = "sqlite"


@dataclass(frozen=True, slots=True)
class Config:
    """Настройки продукта."""

    store: str = DEFAULT_STORE
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

    def plugin_options_for(self, name: str) -> dict[str, Any]:
        """Параметры плагина: только его секция, без `data`.

        `data` — путь хранилища, а не источника/анализатора; подставлять
        его в чужие настройки значило бы угадывать. Плагин получает ровно
        то, что записано в `[plugins.<name>]`.
        """
        return dict(self.plugins.get(name, {}))

    def source_options_for(self, source: str) -> dict[str, Any]:
        """То же, что `plugin_options_for` (имя сохранено ради совместимости)."""
        return self.plugin_options_for(source)


__all__ = ["BUILTIN_STORE", "Config"]
