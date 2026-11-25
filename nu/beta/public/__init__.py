"""Публичный API модуля beta продукта: реестр плагинов."""

from nu.beta.public.registry import ENTRY_GROUPS as ENTRY_GROUPS
from nu.beta.public.registry import PluginRegistry as PluginRegistry
from nu.beta.public.registry import RegistryState as RegistryState
from nu.beta.public.registry import UnknownPluginError as UnknownPluginError
from nu.beta.public.registry import discover as discover

__all__ = [
    "ENTRY_GROUPS",
    "PluginRegistry",
    "RegistryState",
    "UnknownPluginError",
    "discover",
]
