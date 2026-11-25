"""Ошибки встроенного хранилища продукта."""

from __future__ import annotations


class JsonStoreError(RuntimeError):
    """Файл хранилища повреждён или несовместим по версии."""


class SqliteStoreError(RuntimeError):
    """База SQLite повреждена, недоступна или несовместима по версии."""


__all__ = ["JsonStoreError", "SqliteStoreError"]
