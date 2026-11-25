"""Публичный API модуля mu: версии продукта и диапазон контракта ядра."""

from axislar_core.contracts import CONTRACT_VERSION as KERNEL_CONTRACT_VERSION

__version__ = "0.7.0"

#: Версия публичного контракта продукта (DTO, реестр, интерфейсы).
CONTRACT_VERSION = "1"

#: Диапазон версий контракта ядра (axislar_core), с которым работает продукт.
CORE_CONTRACT_RANGE = ">=4,<5"

__all__ = ["CONTRACT_VERSION", "CORE_CONTRACT_RANGE", "KERNEL_CONTRACT_VERSION", "__version__"]
