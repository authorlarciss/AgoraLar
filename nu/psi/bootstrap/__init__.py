"""Сборка продукта: реестр плагинов + ядро с выбранным хранилищем."""

from nu.alpha.public.config import BUILTIN_STORE as BUILTIN_STORE
from nu.psi.product import Product as Product
from nu.psi.product import build_product as build_product

__all__ = ["BUILTIN_STORE", "Product", "build_product"]
