"""Резолвер модуля delta продукта: единственная точка доступа к `_internal/`.

Lazy import обязателен (ADR-0002 BaseLar): импорт контрактов не должен
тянуть реализацию хранилища.
"""

from __future__ import annotations

from pathlib import Path

from axislar_core.contracts import IStorageEngine

#: Имя встроенного плагина.
JSON_STORE = "json"

#: Путь по умолчанию, если конфиг его не задал.
DEFAULT_STORE_PATH = Path(".agoralar") / "vault.json"


class DeltaResolver:
    """Фабрика встроенных бэкендов продукта."""

    def make_json_store(self, path: Path | str | None = None) -> IStorageEngine:
        """JSON-хранилище в файле (по умолчанию — `DEFAULT_STORE_PATH`)."""
        from nu.delta._internal.json_store import JsonStore

        return JsonStore(Path(path) if path is not None else DEFAULT_STORE_PATH)


__all__ = ["DEFAULT_STORE_PATH", "JSON_STORE", "DeltaResolver"]
