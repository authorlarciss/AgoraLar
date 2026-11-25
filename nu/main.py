"""Точка входа продукта: CLI."""

from __future__ import annotations

import sys

from nu.psi.contracts.gamma import GammaResolver


def main() -> int:
    """Запуск CLI. Возвращает код выхода."""
    return GammaResolver().make_cli()(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
