"""SQLite-хранилище продукта: durable-бэкенд, а не SQL-движок.

Таблица `entities` хранит объекты как JSON-документы, `meta` — версию
формата и схему. Фильтрацию по-прежнему исполняет эталонный движок ядра:
SQLite отвечает за долговечность, а не за пушдаун предикатов (ADR-0012).

Это второй независимый слой хранения, и он проверяется контрактным
набором ядра (`run_storage_contract`).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

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

from nu.delta.public.errors import SqliteStoreError

#: Версия формата БД.
STORE_VERSION = 1

_META_TABLE = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
_ENTITIES_TABLE = "CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, doc TEXT NOT NULL)"


class SqliteStore(IStorageEngine):
    """Хранилище в SQLite-файле: ленивое открытие, запись в транзакции."""

    def __init__(self, path: Path | str, schema: StorageSchema | None = None) -> None:
        self._path = Path(path)
        self._schema = schema
        self._engine: IStorageEngine | None = None

    # --------------------------------------------------------------- доступ

    async def _store(self) -> IStorageEngine:
        if self._engine is None:
            self._engine = await self._load()
        return self._engine

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._path)

    async def _load(self) -> IStorageEngine:
        engine = build_core().storage
        if not self._path.is_file():
            for axis in self._schema.axes if self._schema else ():
                await engine.add_axis(axis)
            for scalar in self._schema.scalars if self._schema else ():
                await engine.add_scalar(scalar)
            return engine

        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_META_TABLE)
                connection.execute(_ENTITIES_TABLE)
                meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
                rows = connection.execute("SELECT doc FROM entities").fetchall()
        except sqlite3.DatabaseError as error:
            raise SqliteStoreError(f"broken database {self._path}: {error}") from error

        version = meta.get("version")
        if version != str(STORE_VERSION):
            raise SqliteStoreError(
                f"unsupported store version in {self._path}: {version!r} (expected {STORE_VERSION})"
            )

        try:
            schema = StorageSchema.model_validate(json.loads(meta["schema"]))
            entities = [Entity.model_validate(json.loads(row[0])) for row in rows]
        except (KeyError, TypeError, ValueError) as error:
            raise SqliteStoreError(f"broken store {self._path}: {error}") from error

        for axis in schema.axes:
            await engine.add_axis(axis)
        for scalar in schema.scalars:
            await engine.add_scalar(scalar)

        errors = await engine.bulk_upsert(entities)
        if errors:
            raise SqliteStoreError(f"broken store {self._path}: {errors[0]}")
        return engine

    async def _flush(self) -> None:
        engine = await self._store()
        result = await engine.query(Query())
        entities = await engine.fetch_many(result.ids)
        schema_json = (await engine.get_schema()).model_dump_json()
        payload = [(str(entity.id), entity.model_dump_json()) for entity in entities]

        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_META_TABLE)
                connection.execute(_ENTITIES_TABLE)
                connection.execute("DELETE FROM entities")
                connection.executemany("INSERT INTO entities (id, doc) VALUES (?, ?)", payload)
                connection.executemany(
                    "INSERT INTO meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    [("version", str(STORE_VERSION)), ("schema", schema_json)],
                )
                connection.commit()
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot write {self._path}: {error}") from error

    # ---------------------------------------------------------------- схема

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

    # --------------------------------------------------------------- запись

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

    # --------------------------------------------------------------- чтение

    async def get(self, entity_id: EntityId) -> Entity | None:
        return await (await self._store()).get(entity_id)

    async def fetch_many(self, ids: Sequence[EntityId]) -> tuple[Entity, ...]:
        return await (await self._store()).fetch_many(ids)

    async def count(self) -> int:
        return await (await self._store()).count()

    async def query(self, query: Query) -> QueryResult:
        return await (await self._store()).query(query)

    def capabilities(self) -> frozenset[Capability]:
        return build_core().storage.capabilities()


__all__ = ["STORE_VERSION", "SqliteStore", "SqliteStoreError"]
