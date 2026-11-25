"""Контракт beta продукта: реестр плагинов."""

from nu.beta.public import ENTRY_GROUPS as ENTRY_GROUPS
from nu.beta.public import PluginRegistry as PluginRegistry
from nu.beta.public import RegistryState as RegistryState
from nu.beta.public import UnknownPluginError as UnknownPluginError
from nu.beta.public import discover as discover

__all__ = ["ENTRY_GROUPS", "PluginRegistry", "RegistryState", "UnknownPluginError", "discover"]
