"""Резолвер модуля delta продукта: единственная точка доступа к `_internal/`.

Lazy import обязателен (ADR-0002 BaseLar): импорт контрактов не должен
тянуть реализацию хранилища.
"""

from __future__ import annotations

from pathlib import Path

from axislar_core.contracts import IStorageEngine, StorageSchema

#: Имена встроенных плагинов.
JSON_STORE = "json"
SQLITE_STORE = "sqlite"

#: Путь по умолчанию, если конфиг его не задал.
DEFAULT_STORE_PATH = Path(".agoralar") / "vault.json"

#: Путь по умолчанию для SQLite-бэкенда.
DEFAULT_SQLITE_PATH = Path(".agoralar") / "vault.sqlite3"


class DeltaResolver:
    """Фабрика встроенных бэкендов продукта."""

    def make_json_store(self, path: Path | str | None = None) -> IStorageEngine:
        """JSON-хранилище в файле (по умолчанию — `DEFAULT_STORE_PATH`)."""
        from nu.delta._internal.json_store import JsonStore

        return JsonStore(Path(path) if path is not None else DEFAULT_STORE_PATH)

    def make_sqlite_store(
        self, path: Path | str | None = None, schema: StorageSchema | None = None
    ) -> IStorageEngine:
        """SQLite-хранилище (по умолчанию — `DEFAULT_SQLITE_PATH`)."""
        from nu.delta._internal.sqlite_store import SqliteStore

        return SqliteStore(Path(path) if path is not None else DEFAULT_SQLITE_PATH, schema=schema)


__all__ = [
    "DEFAULT_SQLITE_PATH",
    "DEFAULT_STORE_PATH",
    "JSON_STORE",
    "SQLITE_STORE",
    "DeltaResolver",
]
