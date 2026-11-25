"""Продукт: ядро + реестр плагинов + выбранное хранилище.

`Product` — единственное, что собирает `main`/CLI. Внутри нет доменной
логики: модель и проверки живут в ядре (`axislar_core`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from axislar_core.contracts import CONTRACT_VERSION, Core, IStorageEngine, build_core

from nu.psi.contracts.beta import PluginRegistry
from nu.psi.contracts.sigma import LoggingConfig, SigmaResolver

#: Имя встроенного бэкенда — его даёт само ядро, без плагинов.
BUILTIN_STORE = "memory"


@dataclass(frozen=True, slots=True)
class Product:
    """Собранный продукт."""

    core: Core
    registry: PluginRegistry
    store: str

    def describe(self) -> str:
        plugins = ", ".join(manifest.name for manifest in self.registry.available) or "<нет>"
        return f"store={self.store}, kernel contract={CONTRACT_VERSION}, plugins: {plugins}"


def build_product(
    store: str | None = None,
    options: Mapping[str, Any] | None = None,
    registry: PluginRegistry | None = None,
) -> Product:
    """Собрать продукт: логирование, реестр плагинов, ядро с хранилищем.

    `store=None` — эталонное in-memory хранилище ядра (без плагинов).
    Имя плагина берётся из реестра; если его нет или он отклонён,
    поднимается `UnknownPluginError` с причиной.
    """
    _setup_logging()
    registry = registry or PluginRegistry(core_contract=CONTRACT_VERSION)

    if store in (None, BUILTIN_STORE):
        return Product(core=build_core(), registry=registry, store=BUILTIN_STORE)

    engine = cast(IStorageEngine, registry.create(store, options))
    if not isinstance(engine, IStorageEngine):
        raise TypeError(
            f"plugin {store!r} returned {type(engine).__name__}, expected IStorageEngine"
        )
    return Product(core=build_core(storage=engine), registry=registry, store=store)


def _setup_logging() -> None:
    SigmaResolver().setup_logging(LoggingConfig(level="INFO", format="text"))


__all__ = ["BUILTIN_STORE", "Product", "build_product"]
