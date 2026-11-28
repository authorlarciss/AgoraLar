"""Продукт: ядро + реестр плагинов + выбранное хранилище.

`Product` — единственное, что собирает `main`/CLI. Внутри нет доменной
логики: модель и проверки живут в ядре (`axislar_core`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from axislar_core.contracts import Core, IStorageEngine, build_core

from nu.psi.contracts.alpha import BUILTIN_STORE, Config
from nu.psi.contracts.beta import Journal, PluginRegistry, journal_path_for
from nu.psi.contracts.mu import KERNEL_CONTRACT_VERSION
from nu.psi.contracts.sigma import LoggingConfig, SigmaResolver


@dataclass(frozen=True, slots=True)
class Product:
    """Собранный продукт."""

    core: Core
    registry: PluginRegistry
    store: str
    #: Журнал правок рядом с данными; `None` — хранилище без файла (memory).
    journal: Journal | None = None

    def describe(self) -> str:
        plugins = ", ".join(manifest.name for manifest in self.registry.available) or "<нет>"
        return f"store={self.store}, kernel contract={KERNEL_CONTRACT_VERSION}, plugins: {plugins}"


def build_product(
    store: str | None = None,
    options: Mapping[str, Any] | None = None,
    registry: PluginRegistry | None = None,
    config: Config | None = None,
) -> Product:
    """Собрать продукт: логирование, реестр плагинов, ядро с хранилищем.

    Источники настроек: явные аргументы поверх `config`, `config` поверх
    значений по умолчанию. Без конфига и аргументов — эталонное in-memory
    хранилище ядра: это библиотечный вход для тестов и плагинов, файлы он не
    создаёт. Пользовательский путь (`agoralar`, без `--store` и без конфига)
    берёт `Config()`, а там дефолт — sqlite (`DEFAULT_STORE`).
    Имя плагина берётся из реестра; если его нет или он отклонён, поднимается
    `UnknownPluginError` с причиной.
    """
    _setup_logging()
    registry = registry or PluginRegistry(core_contract=KERNEL_CONTRACT_VERSION)
    settings = config or Config(store=BUILTIN_STORE)
    chosen = store or settings.store
    merged: dict[str, Any] = {**settings.options_for(chosen), **(options or {})}

    if chosen == BUILTIN_STORE:
        return Product(core=build_core(), registry=registry, store=BUILTIN_STORE)

    base = settings.data.parent if settings.data is not None else Path.cwd()
    journal = Journal(journal_path_for(base))

    engine = cast(IStorageEngine, registry.create(chosen, merged))
    if not isinstance(engine, IStorageEngine):
        raise TypeError(
            f"plugin {chosen!r} returned {type(engine).__name__}, expected IStorageEngine"
        )
    return Product(
        core=build_core(storage=engine), registry=registry, store=chosen, journal=journal
    )


def _setup_logging() -> None:
    SigmaResolver().setup_logging(LoggingConfig(level="INFO", format="text"))


__all__ = ["Product", "build_product"]
