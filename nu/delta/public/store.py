"""Встроенные бэкенды продукта: манифесты плагинов.

Реализация живёт в `_internal/`, доступ к ней — только через
`DeltaResolver` (паттерн resolver, ADR-0002 BaseLar).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from axislar_core.contracts import IStorageEngine

from nu.delta.public.resolver import DEFAULT_STORE_PATH, JSON_STORE, DeltaResolver
from nu.psi.contracts.alpha import PluginKind, PluginManifest


def make_json_store(options: Mapping[str, Any]) -> IStorageEngine:
    """Фабрика плагина: путь из настроек или значение по умолчанию."""
    raw_path = options.get("path")
    return DeltaResolver().make_json_store(Path(str(raw_path)) if raw_path else None)


def builtin_plugins() -> tuple[PluginManifest, ...]:
    """Встроенные бэкенды: не требуют установки отдельным пакетом."""
    return (
        PluginManifest(
            name=JSON_STORE,
            kind=PluginKind.STORAGE,
            requires_contract=">=2,<3",
            factory=make_json_store,
        ),
    )


__all__ = ["DEFAULT_STORE_PATH", "JSON_STORE", "builtin_plugins", "make_json_store"]
