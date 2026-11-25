"""Точка входа продукта: собрать Product и показать состояние."""

from __future__ import annotations

import sys

from nu.psi import build_product
from nu.psi.contracts.mu import __version__


def main(argv: list[str] | None = None) -> int:
    """Показать версию, активное хранилище и найденные плагины."""
    del argv  # CLI появится в gamma; пока — сводка
    product = build_product()
    print(f"AgoraLar {__version__}")
    print(product.describe())
    for item in product.registry.rejected:
        print(f"rejected: {item.describe()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
