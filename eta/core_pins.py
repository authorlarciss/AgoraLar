#!/usr/bin/env python3
"""Подъём диапазона ядра во всех проектах монорепозитория.

Продукт и плагины пинют `axislar-core` узко (`>=X.Y,<X.Y+1`): так установка
ломается предсказуемо, а не «поставилось и упало в рантайме». Цена — каждый
минор ядра требует синхронного bump'а у продукта и семи плагинов; руками это
забывается (0.14.2 показал: забыли — резолвер не смог поставить ядро).

Скрипт делает это одной командой и обновляет lock.

Использование::

    python -m eta.core_pins --core 0.15.0            # переписать + uv lock
    python -m eta.core_pins --core 0.15.0 --no-lock  # только переписать
    python -m eta.core_pins --core 0.14.2 --check    # проверить (exit 1)

Долгосрочное решение — отдельный дистрибутив контракта (`axislar-contract`,
см. ADR-0022 продукта): тогда установка гейтится контрактом, а минор ядра не
трогает плагины. До тех пор этот скрипт держит поезд собранным.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

#: Имя зависимости, диапазон которой поднимаем.
KERNEL: Final = "axislar-core"

#: Разбор версии ядра: нужен `major.minor` для диапазона.
VERSION_RE: Final = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")

#: Зависимость ядра в pyproject: `"axislar-core>=0.14,<0.15"` и подобное.
PIN_RE: Final = re.compile(r'"axislar-core[^"]*"')

#: Где искать pyproject: продукт и всё, что лежит в `plugins/`.
ROOT: Final = Path(__file__).resolve().parent.parent
PROJECTS: Final = ("pyproject.toml", "plugins/*/pyproject.toml")


class VersionError(ValueError):
    """Версия ядра не в формате `X.Y.Z`."""


def pin_range(version: str) -> str:
    """`0.15.0` → `>=0.15,<0.16`; `1.0.0` → `>=1.0,<2.0`."""
    match = VERSION_RE.match(version)
    if match is None:
        raise VersionError(f"expected X.Y.Z, got {version!r}")
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    if minor == 0:
        return f">={major}.{minor},<{major + 1}.0"
    return f">={major}.{minor},<{major}.{minor + 1}"


def rewrite(text: str, specifier: str) -> str:
    """Заменить диапазон ядра в тексте pyproject, не трогая остальное."""
    return PIN_RE.sub(f'"{KERNEL}{specifier}"', text)


def targets() -> tuple[Path, ...]:
    """Все pyproject, где объявлена зависимость от ядра."""
    found: list[Path] = []
    for pattern in PROJECTS:
        for path in sorted(ROOT.glob(pattern)):
            if KERNEL in path.read_text(encoding="utf-8"):
                found.append(path)
    return tuple(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Поднять диапазон axislar-core")
    parser.add_argument("--core", required=True, help="версия ядра, например 0.15.0")
    parser.add_argument(
        "--check", action="store_true", help="только проверить, exit 1 при расхождении"
    )
    parser.add_argument("--no-lock", action="store_true", help="не запускать uv lock")
    args = parser.parse_args(argv)

    try:
        specifier = pin_range(args.core)
    except VersionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    files = targets()
    if not files:
        print("error: не найден ни один pyproject с axislar-core", file=sys.stderr)
        return 2

    stale: list[Path] = []
    changed: list[Path] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        updated = rewrite(text, specifier)
        if updated == text:
            if f'"{KERNEL}{specifier}"' not in text:
                stale.append(path)
            continue
        if args.check:
            stale.append(path)
            continue
        path.write_text(updated, encoding="utf-8")
        changed.append(path)

    if args.check:
        if stale:
            print(f"error: диапазон {KERNEL}{specifier} не везде:", file=sys.stderr)
            for path in stale:
                print(f"  - {path.relative_to(ROOT)}", file=sys.stderr)
            return 1
        print(f"ok: {KERNEL}{specifier} во всех {len(files)} проектах")
        return 0

    for path in changed:
        print(f"pin {path.relative_to(ROOT)}: {KERNEL}{specifier}")
    if not changed:
        print(f"ok: уже {KERNEL}{specifier}, менять нечего")

    if args.no_lock:
        return 0
    print("uv lock --refresh")
    result = subprocess.run(["uv", "lock", "--refresh"], cwd=ROOT, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
