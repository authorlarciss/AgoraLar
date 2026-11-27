"""Утилиты конфига: поиск, разбор, запись."""

from __future__ import annotations

from pathlib import Path

import pytest

from nu.psi.contracts.alpha import Config
from nu.psi.contracts.iota import (
    DEFAULT_CONFIG_NAME,
    ConfigError,
    find_config,
    load_config,
    parse_config,
    render_config,
    save_config,
)


def write_config(directory: Path, text: str) -> Path:
    path = directory / DEFAULT_CONFIG_NAME
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_minimal_config() -> None:
    config = parse_config('store = "json"\ndata = "vault.json"\n')
    assert config.store == "json"
    assert config.data == Path("vault.json")


def test_parse_plugin_sections() -> None:
    config = parse_config('[plugins.json]\npretty = true\npath = "v.json"\n')
    assert config.plugins["json"] == {"pretty": True, "path": "v.json"}
    assert config.store == "memory"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("store = 42\n", "must be a non-empty string"),
        ('store = ""\n', "must be a non-empty string"),
        ("data = 42\n", "must be a path string"),
        ('plugins = "x"\n', "must be a table"),
        ('[plugins]\njson = "x"\n', "must be a table"),
    ],
)
def test_parse_rejects_bad_config(text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(text)


def test_broken_toml_is_reported() -> None:
    with pytest.raises(ConfigError, match="broken config"):
        parse_config("store = ")


def test_find_config_walks_up(tmp_path: Path) -> None:
    write_config(tmp_path, 'store = "json"\n')
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_config(nested) == tmp_path / DEFAULT_CONFIG_NAME


def test_find_config_returns_none_when_missing(tmp_path: Path) -> None:
    assert find_config(tmp_path) is None


def test_load_config_without_file_returns_defaults(tmp_path: Path) -> None:
    assert load_config(None, start=tmp_path) == Config()


def test_load_config_reads_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, 'store = "json"\n')
    assert load_config(path).store == "json"


def test_render_and_roundtrip() -> None:
    config = Config(
        store="json",
        data=Path("v.json"),
        plugins={"json": {"pretty": True, "limit": 10, "label": "x"}},
    )
    restored = parse_config(render_config(config))
    assert restored.store == config.store
    assert restored.data == config.data
    assert restored.plugins["json"] == {"pretty": True, "limit": 10, "label": "x"}


def test_render_rejects_unknown_value() -> None:
    with pytest.raises(ConfigError, match="cannot render"):
        render_config(Config(plugins={"json": {"bad": {"nested": "table"}}}))


def test_render_supports_arrays() -> None:
    rendered = render_config(Config(plugins={"filesystem": {"levels": ["a", "b"]}}))
    assert 'levels = ["a", "b"]' in rendered


def test_save_is_atomic_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / DEFAULT_CONFIG_NAME
    config = Config(store="json")
    save_config(config, path)
    assert load_config(path) == config
    save_config(config, path)
    assert list((tmp_path / "nested").glob("*.tmp")) == []
