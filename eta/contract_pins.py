#!/usr/bin/env python3
"""Подъём диапазона контракта во всех проектах монорепозитория.

Продукт и плагины пинют `axislar-contract` (`>=6,<7`): установка гейтится
контрактом, а не минором ядра (ADR-0022/0026). Минор ядра поэтому больше не
трогает плагины, но смена контракта — ломающее событие, и диапазон нужно
поднять синхронно у продукта, всех плагинов и в ядре.

Скрипт делает это одной командой и обновляет lock.

Использование::

    python -m eta.contract_pins --contract 7.0.0            # переписать + lock
    python -m eta.contract_pins --contract 7.0.0 --no-lock  # только переписать
    python -m eta.contract_pins --contract 6.0.0 --check    # проверить (exit 1)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

#: Имя зависимости, диапазон которой поднимаем.
CONTRACT: Final = "axislar-contract"

#: Разбор версии контракта: нужен `major.minor` для диапазона.
VERSION_RE: Final = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")

#: Зависимость контракта в pyproject: `"axislar-contract>=6,<7"` и подобное.
PIN_RE: Final = re.compile(r'"axislar-contract[^"]*"')

#: Где искать pyproject: продукт и всё, что лежит в `plugins/`.
ROOT: Final = Path(__file__).resolve().parent.parent
PROJECTS: Final = ("pyproject.toml", "plugins/*/pyproject.toml")


class VersionError(ValueError):
    """Версия контракта не в формате `X.Y.Z`."""


def pin_range(version: str) -> str:
    """`6.0.0` → `>=6,<7`; `6.1.0` → `>=6.1,<6.2`."""
    match = VERSION_RE.match(version)
    if match is None:
        raise VersionError(f"expected X.Y.Z, got {version!r}")
    major = int(match.group("major"))
    # Номер контракта — мажор версии пакета: контракт 6 живёт в 6.x.
    return f">={major},<{major + 1}"


def rewrite(text: str, specifier: str) -> str:
    """Заменить диапазон контракта в тексте pyproject, не трогая остальное."""
    return PIN_RE.sub(f'"{CONTRACT}{specifier}"', text)


def targets() -> tuple[Path, ...]:
    """Все pyproject, где объявлена зависимость от контракта."""
    found: list[Path] = []
    for pattern in PROJECTS:
        for path in sorted(ROOT.glob(pattern)):
            if CONTRACT in path.read_text(encoding="utf-8"):
                found.append(path)
    return tuple(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Поднять диапазон axislar-contract")
    parser.add_argument("--contract", required=True, help="версия контракта, например 7.0.0")
    parser.add_argument(
        "--check", action="store_true", help="только проверить, exit 1 при расхождении"
    )
    parser.add_argument("--no-lock", action="store_true", help="не запускать uv lock")
    args = parser.parse_args(argv)

    try:
        specifier = pin_range(args.contract)
    except VersionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    files = targets()
    if not files:
        print("error: не найден ни один pyproject с axislar-contract", file=sys.stderr)
        return 2

    stale: list[Path] = []
    changed: list[Path] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        updated = rewrite(text, specifier)
        if updated == text:
            if f'"{CONTRACT}{specifier}"' not in text:
                stale.append(path)
            continue
        if args.check:
            stale.append(path)
            continue
        path.write_text(updated, encoding="utf-8")
        changed.append(path)

    if args.check:
        if stale:
            print(f"error: диапазон {CONTRACT}{specifier} не везде:", file=sys.stderr)
            for path in stale:
                print(f"  - {path.relative_to(ROOT)}", file=sys.stderr)
            return 1
        print(f"ok: {CONTRACT}{specifier} во всех {len(files)} проектах")
        return 0

    for path in changed:
        print(f"pin {path.relative_to(ROOT)}: {CONTRACT}{specifier}")
    if not changed:
        print(f"ok: уже {CONTRACT}{specifier}, менять нечего")

    if args.no_lock:
        return 0
    print("uv lock --refresh")
    result = subprocess.run(["uv", "lock", "--refresh"], cwd=ROOT, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
