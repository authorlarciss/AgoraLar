"""Инвариант деплой-модели: в рабочем дереве пакет называется `nu`.

Переименование (nu -> `<dist>_nu`) делает eta/rename_package.py в копии
дерева при сборке (`make dist`, ADR-0010). Переименовать пакет в самом
репозитории нельзя: сломаются импорты, деплой и мержи.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_package_directory_is_nu() -> None:
    assert (ROOT / "nu" / "__init__.py").is_file()
    assert not (ROOT / "baselar_nu").exists()


def test_pyproject_ships_nu_package() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = data["tool"]["setuptools"]["packages"]["find"]["include"]
    assert include == ["nu*"]
