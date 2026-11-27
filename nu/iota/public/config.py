"""Конфиг продукта: поиск, чтение и запись `agoralar.toml`.

Формат намеренно маленький: `store`, `data` и секции `[plugins.<name>]`.
TOML читается стандартным `tomllib`, записывается простым рендером —
полноценный writer не нужен, пока конфиг плоский.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nu.psi.contracts.alpha import BUILTIN_STORE, Config

#: Имя файла конфига по умолчанию.
DEFAULT_CONFIG_NAME = "agoralar.toml"


class ConfigError(ValueError):
    """Конфиг повреждён или содержит недопустимые значения."""


def find_config(start: Path | None = None) -> Path | None:
    """Найти `agoralar.toml` вверх от `start` до корня файловой системы."""
    directory = (start or Path.cwd()).resolve()
    for candidate in (directory, *directory.parents):
        path = candidate / DEFAULT_CONFIG_NAME
        if path.is_file():
            return path
    return None


def load_config(path: Path | None = None, *, start: Path | None = None) -> Config:
    """Прочитать конфиг; отсутствие файла — конфиг по умолчанию."""
    resolved = path or find_config(start)
    if resolved is None:
        return Config()
    return parse_config(resolved.read_text(encoding="utf-8"), source=resolved)


def parse_config(text: str, *, source: Path | None = None) -> Config:
    """Разобрать TOML-текст в `Config`."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"broken config {_name(source)}: {error}") from error

    store = raw.get("store", BUILTIN_STORE)
    if not isinstance(store, str) or not store:
        raise ConfigError(f"config {_name(source)}: 'store' must be a non-empty string")

    data = raw.get("data")
    if data is not None and not isinstance(data, str):
        raise ConfigError(f"config {_name(source)}: 'data' must be a path string")

    raw_plugins = raw.get("plugins", {})
    if not isinstance(raw_plugins, dict):
        raise ConfigError(f"config {_name(source)}: 'plugins' must be a table")

    plugins: dict[str, Mapping[str, Any]] = {}
    for name, section in raw_plugins.items():
        if not isinstance(section, dict):
            raise ConfigError(f"config {_name(source)}: plugin {name!r} must be a table")
        plugins[str(name)] = section

    return Config(store=store, data=Path(data) if data else None, plugins=plugins)


def render_config(config: Config) -> str:
    """Сериализовать конфиг в TOML (плоские пары + секции плагинов)."""
    lines = [f'store = "{config.store}"']
    if config.data is not None:
        lines.append(f'data = "{config.data}"')
    for name, section in sorted(config.plugins.items()):
        lines.append("")
        lines.append(f"[plugins.{name}]")
        for key, value in sorted(section.items()):
            lines.append(f"{key} = {_render_value(value)}")
    return "\n".join(lines) + "\n"


def save_config(config: Config, path: Path) -> Path:
    """Записать конфиг (атомарно, через временный файл)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(render_config(config), encoding="utf-8")
    temporary.replace(path)
    return path


def _render_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, list | tuple):
        # Массивы нужны параметрам плагинов: `levels`, `suffixes`.
        return "[" + ", ".join(_render_value(item) for item in value) + "]"
    raise ConfigError(f"cannot render config value of type {type(value).__name__}")


def _name(source: Path | None) -> str:
    return str(source) if source is not None else "<string>"


__all__ = [
    "DEFAULT_CONFIG_NAME",
    "ConfigError",
    "find_config",
    "load_config",
    "parse_config",
    "render_config",
    "save_config",
]
