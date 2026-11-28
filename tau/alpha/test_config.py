"""DTO конфига продукта."""

from __future__ import annotations

from pathlib import Path

from nu.psi.contracts.alpha import BUILTIN_STORE, DEFAULT_STORE, Config


def test_defaults_to_sqlite_store() -> None:
    """Архив по умолчанию — sqlite: json остаётся, но не как дефолт."""
    config = Config()
    assert config.store == DEFAULT_STORE == "sqlite"
    assert config.data is None
    assert config.options_for(DEFAULT_STORE) == {}


def test_builtin_store_is_still_memory() -> None:
    assert BUILTIN_STORE == "memory", "эталон ядра для тестов и демо не менялся"


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
