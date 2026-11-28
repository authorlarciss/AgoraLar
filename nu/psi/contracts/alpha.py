"""Контракт alpha продукта: DTO плагинов."""

from nu.alpha.public import BUILTIN_STORE as BUILTIN_STORE
from nu.alpha.public import DEFAULT_STORE as DEFAULT_STORE
from nu.alpha.public import Config as Config
from nu.alpha.public import PluginKind as PluginKind
from nu.alpha.public import PluginManifest as PluginManifest
from nu.alpha.public import RejectedPlugin as RejectedPlugin

__all__ = [
    "BUILTIN_STORE",
    "DEFAULT_STORE",
    "Config",
    "PluginKind",
    "PluginManifest",
    "RejectedPlugin",
]
