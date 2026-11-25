"""Реестр плагинов: обнаружение, проверка контракта, отказы (ADR-0011)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from nu.psi.contracts.alpha import PluginKind, PluginManifest
from nu.psi.contracts.beta import ENTRY_GROUPS, PluginRegistry, UnknownPluginError, discover

CONTRACT = "4"


class FakePoint:
    """Минимальная замена importlib.metadata.EntryPoint."""

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
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


def manifest(
    name: str = "json",
    kind: PluginKind = PluginKind.STORAGE,
    requires: str = ">=4,<5",
    factory: Callable[[Any], Any] | None = None,
) -> PluginManifest:
    return PluginManifest(
        name=name,
        kind=kind,
        requires_contract=requires,
        factory=factory or (lambda _options: object()),
    )


def storage_point(name: str, loaded: Any) -> FakePoint:
    return FakePoint(name, ENTRY_GROUPS[PluginKind.STORAGE], loaded)


def test_compatible_plugin_is_available() -> None:
    state = discover([storage_point("json", manifest())], core_contract=CONTRACT)
    assert [item.name for item in state.available] == ["json"]
    assert state.rejected == ()


def test_incompatible_contract_is_rejected_with_reason() -> None:
    state = discover(
        [storage_point("future", manifest(name="future", requires=">=5"))],
        core_contract=CONTRACT,
    )
    assert state.available == ()
    assert "requires contract" in state.rejected[0].reason


def test_invalid_specifier_is_rejected() -> None:
    state = discover(
        [storage_point("broken", manifest(name="broken", requires="two"))],
        core_contract=CONTRACT,
    )
    assert "invalid requires_contract" in state.rejected[0].reason


def test_import_error_does_not_break_discovery() -> None:
    state = discover(
        [storage_point("boom", ImportError("no module")), storage_point("json", manifest())],
        core_contract=CONTRACT,
    )
    assert [item.name for item in state.available] == ["json"]
    assert state.rejected[0].name == "boom"


def test_entry_point_may_return_factory_callable() -> None:
    state = discover([storage_point("lazy", lambda: manifest(name="lazy"))], core_contract=CONTRACT)
    assert state.available[0].name == "lazy"


def test_missing_manifest_is_rejected() -> None:
    state = discover([storage_point("weird", 42)], core_contract=CONTRACT)
    assert "must return PluginManifest" in state.rejected[0].reason


def test_duplicate_names_are_rejected() -> None:
    state = discover(
        [storage_point("json", manifest()), storage_point("json", manifest())],
        core_contract=CONTRACT,
    )
    assert len(state.available) == 1
    assert state.rejected[0].reason == "duplicate plugin name"


def test_foreign_groups_are_ignored() -> None:
    state = discover([FakePoint("x", "console_scripts", manifest())], core_contract=CONTRACT)
    assert state.available == () and state.rejected == ()


def test_registry_lists_and_reports_rejection_reason() -> None:
    registry = PluginRegistry(
        [storage_point("future", manifest(name="future", requires=">=5"))],
        core_contract=CONTRACT,
    )
    assert registry.available == ()
    assert registry.rejected[0].name == "future"
    with pytest.raises(UnknownPluginError, match="rejected"):
        registry.get("future")


def test_registry_unknown_plugin() -> None:
    registry = PluginRegistry([], core_contract=CONTRACT)
    with pytest.raises(UnknownPluginError, match="not found"):
        registry.get("json")


def test_registry_create_calls_factory_with_options() -> None:
    seen: dict[str, Any] = {}

    def factory(options: Any) -> str:
        seen.update(options)
        return "engine"

    registry = PluginRegistry(
        [storage_point("json", manifest(factory=factory))], core_contract=CONTRACT
    )
    assert registry.create("json", {"path": "vault.json"}) == "engine"
    assert seen == {"path": "vault.json"}


def builtin(name: str = "json", requires: str = ">=4,<5") -> PluginManifest:
    return manifest(name=name, requires=requires)


def test_builtin_plugin_is_available() -> None:
    state = discover([], core_contract=CONTRACT, builtins=[builtin()])
    assert [item.name for item in state.available] == ["json"]
    assert state.rejected == ()


def test_incompatible_builtin_is_rejected() -> None:
    state = discover([], core_contract=CONTRACT, builtins=[builtin(requires=">=5")])
    assert state.available == ()
    assert "requires contract" in state.rejected[0].reason


def test_builtin_colliding_with_entry_point_is_rejected() -> None:
    state = discover(
        [storage_point("json", manifest())],
        core_contract=CONTRACT,
        builtins=[builtin()],
    )
    assert len(state.available) == 1
    assert state.rejected[0].reason == "duplicate plugin name"


def test_duplicate_builtins_are_rejected() -> None:
    state = discover([], core_contract=CONTRACT, builtins=[builtin(), builtin()])
    assert len(state.available) == 1
    assert state.rejected[0].reason == "duplicate plugin name"


def test_manifest_factory_failure_is_rejected() -> None:
    def broken() -> PluginManifest:
        raise RuntimeError("boom")

    state = discover([storage_point("broken", broken)], core_contract=CONTRACT)
    assert "manifest factory failed" in state.rejected[0].reason


def test_callable_returning_garbage_is_rejected() -> None:
    state = discover([storage_point("junk", lambda: 42)], core_contract=CONTRACT)
    assert "must return PluginManifest" in state.rejected[0].reason


def test_contract_outside_product_range_is_rejected() -> None:
    state = discover(
        [storage_point("json", manifest())], core_contract=CONTRACT, product_range=">=5"
    )
    assert "outside product range" in state.rejected[0].reason


def test_mapping_manifest_is_accepted() -> None:
    data = {"requires_contract": ">=4,<5", "factory": lambda _options: object()}
    state = discover([storage_point("data-driven", data)], core_contract=CONTRACT)
    assert [item.name for item in state.available] == ["data-driven"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"factory": lambda _options: object()}, "requires 'requires_contract'"),
        ({"requires_contract": ">=4,<5"}, "requires callable 'factory'"),
        ({"requires_contract": 2, "factory": lambda _o: object()}, "requires 'requires_contract'"),
    ],
)
def test_bad_mapping_manifest_is_rejected(payload: dict[str, object], message: str) -> None:
    state = discover([storage_point("bad", payload)], core_contract=CONTRACT)
    assert message in state.rejected[0].reason
