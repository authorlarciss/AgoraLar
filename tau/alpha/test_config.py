"""DTO конфига продукта."""

from __future__ import annotations

from pathlib import Path

from nu.psi.contracts.alpha import BUILTIN_STORE, Config


def test_defaults_to_builtin_store() -> None:
    config = Config()
    assert config.store == BUILTIN_STORE
    assert config.data is None
    assert config.options_for(BUILTIN_STORE) == {}


def test_options_for_plugin_section_and_data() -> None:
    config = Config(
        store="json",
        data=Path("vault.json"),
        plugins={"json": {"pretty": True, "path": "custom.json"}},
    )
    options = config.options_for("json")
    assert options == {"pretty": True, "path": "custom.json"}, "секция плагина приоритетнее data"
    assert config.options_for("other") == {"path": "vault.json"}


def test_data_not_forwarded_to_builtin_store() -> None:
    config = Config(data=Path("vault.json"))
    assert config.options_for(BUILTIN_STORE) == {}
