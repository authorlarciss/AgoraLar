"""Публичный API модуля mu: версии продукта и диапазон контракта ядра."""

__version__ = "0.1.0.dev0"

#: Версия публичного контракта продукта (DTO, реестр, интерфейсы).
CONTRACT_VERSION = "1"

#: Диапазон версий контракта ядра (axislar_core), с которым работает продукт.
CORE_CONTRACT_RANGE = ">=2,<3"

__all__ = ["CONTRACT_VERSION", "CORE_CONTRACT_RANGE", "__version__"]
