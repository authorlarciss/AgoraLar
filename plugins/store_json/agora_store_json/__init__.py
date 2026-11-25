"""JSON-хранилище для AxisLar: файл + эталонная семантика ядра.

Плагин продукта AgoraLar. Зависит только от `axislar-core`: манифест —
обычные данные (ADR-0013 продукта), DTO продукта не импортируются.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from axislar_core.contracts import (
    AxisDefinition,
    Capability,
    DomainError,
    Entity,
    EntityId,
    IStorageEngine,
    Query,
    QueryResult,
    ScalarDefinition,
    StorageSchema,
    build_core,
)

#: Версия формата файла. Меняется при несовместимом изменении.
STORE_VERSION = 1

#: Контракт ядра, с которым совместим плагин.
REQUIRES_CONTRACT = ">=4,<5"

#: Путь по умолчанию, если конфиг его не задал.
DEFAULT_PATH = Path(".agoralar") / "vault.json"


class JsonStoreError(RuntimeError):
    """Файл хранилища повреждён или несовместим по версии."""


class JsonStore(IStorageEngine):
    """Хранилище в JSON-файле (ленивое открытие, атомарная перезапись)."""

    def __init__(self, path: Path | str, schema: StorageSchema | None = None) -> None:
        self._path = Path(path)
        self._schema = schema
        self._engine: IStorageEngine | None = None

    async def _store(self) -> IStorageEngine:
        if self._engine is None:
            self._engine = await self._load()
        return self._engine

    async def _load(self) -> IStorageEngine:
        if not self._path.is_file():
            engine = build_core().storage
            for axis in self._schema.axes if self._schema else ():
                await engine.add_axis(axis)
            for scalar in self._schema.scalars if self._schema else ():
                await engine.add_scalar(scalar)
            return engine

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise JsonStoreError(f"broken JSON in {self._path}: {error}") from error

        if not isinstance(raw, dict) or raw.get("version") != STORE_VERSION:
            found = raw.get("version") if isinstance(raw, dict) else None
            raise JsonStoreError(
                f"unsupported store version in {self._path}: {found!r} (expected {STORE_VERSION})"
            )

        try:
            schema = StorageSchema.model_validate(raw["schema"])
            entities = [Entity.model_validate(item) for item in raw["entities"]]
        except (KeyError, TypeError, ValueError) as error:
            raise JsonStoreError(f"broken store {self._path}: {error}") from error

        engine = build_core().storage
        for axis in schema.axes:
            await engine.add_axis(axis)
        for scalar in schema.scalars:
            await engine.add_scalar(scalar)

        errors = await engine.bulk_upsert(entities)
        if errors:
            raise JsonStoreError(f"broken store {self._path}: {errors[0]}")
        return engine

    async def _flush(self) -> None:
        engine = await self._store()
        result = await engine.query(Query())
        entities = await engine.fetch_many(result.ids)
        payload = {
            "version": STORE_VERSION,
            "schema": json.loads((await engine.get_schema()).model_dump_json()),
            "entities": [json.loads(entity.model_dump_json()) for entity in entities],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self._path)

    async def get_schema(self) -> StorageSchema:
        return await (await self._store()).get_schema()

    async def add_axis(self, axis: AxisDefinition) -> list[DomainError]:
        errors = await (await self._store()).add_axis(axis)
        if not errors:
            await self._flush()
        return errors

    async def add_scalar(self, scalar: ScalarDefinition) -> list[DomainError]:
        errors = await (await self._store()).add_scalar(scalar)
        if not errors:
            await self._flush()
        return errors

    async def upsert(self, entity: Entity) -> list[DomainError]:
        errors = await (await self._store()).upsert(entity)
        if not errors:
            await self._flush()
        return errors

    async def bulk_upsert(self, entities: Sequence[Entity]) -> list[DomainError]:
        errors = await (await self._store()).bulk_upsert(entities)
        if len(errors) < len(entities):
            await self._flush()
        return errors

    async def get(self, entity_id: EntityId) -> Entity | None:
        return await (await self._store()).get(entity_id)

    async def fetch_many(self, ids: Sequence[EntityId]) -> tuple[Entity, ...]:
        return await (await self._store()).fetch_many(ids)

    async def count(self) -> int:
        return await (await self._store()).count()

    async def list_entities(self, include_deleted: bool = False) -> tuple[Entity, ...]:
        return await (await self._store()).list_entities(include_deleted=include_deleted)

    async def query(self, query: Query) -> QueryResult:
        return await (await self._store()).query(query)

    def capabilities(self) -> frozenset[Capability]:
        return build_core().storage.capabilities()


def create(options: Mapping[str, Any]) -> IStorageEngine:
    """Фабрика: путь из настроек плагина или значение по умолчанию."""
    raw_path = options.get("path")
    return JsonStore(Path(str(raw_path)) if raw_path else DEFAULT_PATH)


def plugin() -> dict[str, Any]:
    """Манифест плагина обычными данными (без DTO продукта)."""
    return {"requires_contract": REQUIRES_CONTRACT, "factory": create}


__all__ = [
    "DEFAULT_PATH",
    "REQUIRES_CONTRACT",
    "STORE_VERSION",
    "JsonStore",
    "JsonStoreError",
    "create",
    "plugin",
]
