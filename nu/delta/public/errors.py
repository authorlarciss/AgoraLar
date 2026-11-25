"""Ошибки встроенного хранилища продукта."""

from __future__ import annotations


class JsonStoreError(RuntimeError):
    """Файл хранилища повреждён или несовместим по версии."""


__all__ = ["JsonStoreError"]
