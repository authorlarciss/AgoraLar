"""Реестр плагинов: обнаружение через entry points и проверка контракта.

Реестр не импортирует внутренности плагинов: он читает метаданные точек
входа, проверяет `requires_contract` против версии контракта ядра и только
по запросу вызывает фабрику (ADR-0011).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Protocol

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from nu.psi.contracts.alpha import PluginKind, PluginManifest, RejectedPlugin
from nu.psi.contracts.mu import CORE_CONTRACT_RANGE

#: Группы entry points по видам плагинов.
ENTRY_GROUPS: dict[PluginKind, str] = {
    PluginKind.STORAGE: "agoralar.storage",
    PluginKind.SOURCE: "agoralar.source",
}


class EntryPointLike(Protocol):
    """Минимум, который нужен от `importlib.metadata.EntryPoint`."""

    @property
    def name(self) -> str: ...

    @property
    def group(self) -> str: ...

    def load(self) -> Any: ...


class UnknownPluginError(LookupError):
    """Плагин с таким именем не найден или отклонён."""


@dataclass(frozen=True, slots=True)
class RegistryState:
    """Результат обнаружения: что приняли и что отклонили."""

    available: tuple[PluginManifest, ...]
    rejected: tuple[RejectedPlugin, ...]


def discover(
    entry_points_iter: Iterable[EntryPointLike] | None = None,
    *,
    core_contract: str,
    product_range: str = CORE_CONTRACT_RANGE,
) -> RegistryState:
    """Найти плагины и проверить совместимость с контрактом ядра."""
    points = list(entry_points_iter) if entry_points_iter is not None else _installed_points()

    available: list[PluginManifest] = []
    rejected: list[RejectedPlugin] = []
    seen: set[str] = set()

    for point in points:
        kind = _kind_for_group(point.group)
        if kind is None:
            continue

        if point.name in seen:
            rejected.append(RejectedPlugin(point.name, point.group, "duplicate plugin name"))
            continue
        seen.add(point.name)

        try:
            loaded = point.load()
        except Exception as error:  # плагин не должен ронять продукт
            rejected.append(RejectedPlugin(point.name, kind.value, f"import failed: {error!r}"))
            continue

        manifest = _as_manifest(loaded, point.name, kind)
        if isinstance(manifest, RejectedPlugin):
            rejected.append(manifest)
            continue

        reason = _compatibility_error(manifest.requires_contract, core_contract, product_range)
        if reason is not None:
            rejected.append(RejectedPlugin(point.name, kind.value, reason))
            continue

        available.append(manifest)

    available.sort(key=lambda manifest: (manifest.kind.value, manifest.name))
    rejected.sort(key=lambda item: (item.kind, item.name))
    return RegistryState(tuple(available), tuple(rejected))


def _installed_points() -> list[EntryPoint]:
    found: list[EntryPoint] = []
    for group in ENTRY_GROUPS.values():
        found.extend(entry_points(group=group))
    return found


def _kind_for_group(group: str) -> PluginKind | None:
    for kind, name in ENTRY_GROUPS.items():
        if name == group:
            return kind
    return None


def _as_manifest(loaded: Any, point_name: str, kind: PluginKind) -> PluginManifest | RejectedPlugin:
    if isinstance(loaded, PluginManifest):
        return (
            loaded
            if loaded.name
            else PluginManifest(point_name, kind, loaded.requires_contract, loaded.factory)
        )
    if callable(loaded):
        try:
            manifest = loaded()
        except Exception as error:  # фабрика манифеста может упасть
            return RejectedPlugin(point_name, kind.value, f"manifest factory failed: {error!r}")
        if isinstance(manifest, PluginManifest):
            return manifest
    return RejectedPlugin(
        point_name, kind.value, "entry point must return PluginManifest or a callable producing it"
    )


def _compatibility_error(
    requires_contract: str, core_contract: str, product_range: str
) -> str | None:
    try:
        specifier = SpecifierSet(requires_contract)
    except InvalidSpecifier:
        return f"invalid requires_contract {requires_contract!r}"

    version = core_contract
    if version not in specifier:
        return f"requires contract {requires_contract}, kernel provides {core_contract}"

    if version not in SpecifierSet(product_range):
        return f"kernel contract {core_contract} is outside product range {product_range}"

    return None


class PluginRegistry:
    """Найденные плагины: список, поиск, создание экземпляра."""

    def __init__(
        self,
        entry_points_iter: Iterable[EntryPointLike] | None = None,
        *,
        core_contract: str,
        product_range: str = CORE_CONTRACT_RANGE,
    ) -> None:
        self._state = discover(
            entry_points_iter,
            core_contract=core_contract,
            product_range=product_range,
        )

    @property
    def available(self) -> tuple[PluginManifest, ...]:
        return self._state.available

    @property
    def rejected(self) -> tuple[RejectedPlugin, ...]:
        return self._state.rejected

    def get(self, name: str) -> PluginManifest:
        for manifest in self._state.available:
            if manifest.name == name:
                return manifest
        reasons = {item.name: item.reason for item in self._state.rejected}
        if name in reasons:
            raise UnknownPluginError(f"plugin {name!r} rejected: {reasons[name]}")
        raise UnknownPluginError(f"plugin {name!r} not found")

    def create(self, name: str, options: Mapping[str, Any] | None = None) -> Any:
        """Создать объект плагина (для storage — `IStorageEngine` ядра)."""
        manifest = self.get(name)
        return manifest.factory(options or {})


__all__ = [
    "ENTRY_GROUPS",
    "EntryPointLike",
    "PluginRegistry",
    "RegistryState",
    "UnknownPluginError",
    "discover",
]
