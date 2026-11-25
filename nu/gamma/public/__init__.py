"""Публичный API модуля gamma продукта: CLI."""

from nu.gamma.public.resolver import CliMain as CliMain
from nu.gamma.public.resolver import GammaResolver as GammaResolver

__all__ = ["CliMain", "GammaResolver"]
