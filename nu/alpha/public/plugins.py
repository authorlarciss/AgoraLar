"""DTO продукта: вид плагина и его манифест (ADR-0011)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PluginKind(StrEnum):
    """Что именно поставляет плагин."""

    STORAGE = "storage"
    SOURCE = "source"
    ANALYZER = "analyzer"


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """То, что возвращает точка входа плагина.

    `factory` получает настройки из конфига продукта и возвращает готовый
    объект: для `storage` — реализацию `IStorageEngine` из ядра, для
    `source` — `IEntitySource`, для `analyzer` — объект с `accepts`/
    `analyze` (см. `beta.IAnalyzer`).
    """

    name: str
    kind: PluginKind
    requires_contract: str
    factory: Callable[[Mapping[str, Any]], Any] = field(repr=False)

    def describe(self) -> str:
        return f"{self.name} ({self.kind.value}, contract {self.requires_contract})"


@dataclass(frozen=True, slots=True)
class RejectedPlugin:
    """Плагин, который найден, но не может быть использован."""

    name: str
    kind: str
    reason: str

    def describe(self) -> str:
        return f"{self.name} ({self.kind}): {self.reason}"


__all__ = ["PluginKind", "PluginManifest", "RejectedPlugin"]
