"""Сборка продукта: ядро из AxisLar + реестр плагинов."""

from __future__ import annotations

from typing import Any

import pytest
from axislar_core.contracts import IStorageEngine, StorageSchema, build_core

from nu.psi import Product, build_product
from nu.psi.contracts.alpha import PluginKind, PluginManifest
from nu.psi.contracts.beta import ENTRY_GROUPS, PluginRegistry, UnknownPluginError

CONTRACT = "2"


class FakePoint:
    def __init__(self, name: str, group: str, loaded: Any) -> None:
        self._name = name
        self._group = group
        self._loaded = loaded

    @property
    def name(self) -> str:
        return self._name

    @property
    def group(self) -> str:
        return self._group

    def load(self) -> Any:
        return self._loaded


def test_build_product_defaults_to_kernel_memory() -> None:
    product = build_product(registry=PluginRegistry([], core_contract=CONTRACT))
    assert isinstance(product, Product)
    assert product.store == "memory"
    assert isinstance(product.core.storage, IStorageEngine)
    assert product.registry.available == ()


def test_build_product_uses_plugin_engine() -> None:
    def factory(_options: Any) -> IStorageEngine:
        # Ядро даёт эталонный движок через build_core(); отдельный бэкенд
        # в тесте не нужен — проверяем путь «плагин подставил хранилище».
        return build_core().storage

    manifest = PluginManifest(
        name="custom", kind=PluginKind.STORAGE, requires_contract=">=2,<3", factory=factory
    )
    point = FakePoint("custom", ENTRY_GROUPS[PluginKind.STORAGE], manifest)
    product = build_product("custom", registry=PluginRegistry([point], core_contract=CONTRACT))

    assert product.store == "custom"
    assert product.core.storage is not None


def test_build_product_rejects_bad_engine_type() -> None:
    manifest = PluginManifest(
        name="broken", kind=PluginKind.STORAGE, requires_contract=">=2,<3", factory=lambda _o: 42
    )
    point = FakePoint("broken", ENTRY_GROUPS[PluginKind.STORAGE], manifest)
    with pytest.raises(TypeError, match="expected IStorageEngine"):
        build_product("broken", registry=PluginRegistry([point], core_contract=CONTRACT))


def test_build_product_unknown_store() -> None:
    with pytest.raises(UnknownPluginError):
        build_product("json", registry=PluginRegistry([], core_contract=CONTRACT))


async def test_product_core_is_usable() -> None:
    product = build_product(registry=PluginRegistry([], core_contract=CONTRACT))
    assert await product.core.count() == 0
    assert (await product.core.schema()) == StorageSchema()


def test_default_product_exposes_builtin_json_store() -> None:
    product = build_product()
    names = [manifest.name for manifest in product.registry.available]
    assert "json" in names


def test_product_describe_lists_store_and_plugins() -> None:
    product = build_product()
    text = product.describe()
    assert "store=memory" in text
    assert "json" in text


def test_bootstrap_exposes_build_product() -> None:
    from nu.psi.bootstrap import build_product as bootstrap_build_product

    product = bootstrap_build_product()
    assert product.store == "memory"
