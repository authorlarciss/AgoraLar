"""Контракт gamma продукта: CLI и HTTP."""

from nu.gamma.public import CliMain as CliMain
from nu.gamma.public import GammaResolver as GammaResolver
from nu.gamma.public import HttpResolver as HttpResolver

__all__ = ["CliMain", "GammaResolver", "HttpResolver"]
