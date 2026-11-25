"""Резолвер модуля gamma: единственный доступ к `_internal/` (lazy import)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

#: Тип точки входа CLI: argv -> код выхода.
CliMain = Callable[[Sequence[str] | None], int]


class GammaResolver:
    """Фабрика CLI продукта."""

    def make_cli(self) -> CliMain:
        """Вернуть точку входа CLI (аргументы -> код выхода)."""
        from nu.gamma._internal.cli import main

        return main


__all__ = ["CliMain", "GammaResolver"]
