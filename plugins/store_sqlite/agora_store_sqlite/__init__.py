"""SQLite-хранилище для AxisLar: durable-бэкенд, а не SQL-движок.

Таблица `entities` хранит объекты как JSON-документы, `meta` — версию
формата и схему. Фильтрацию по-прежнему исполняет эталонный движок ядра:
SQLite отвечает за долговечность, а не за пушдаун предикатов (ADR-0012).

Плагин продукта AgoraLar: зависит только от `axislar-core`, манифест —
обычные данные (без DTO продукта). Проверяется контрактным набором ядра
(`run_storage_contract`).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from axislar_core.contracts import (
    Artifact,
    ArtifactRef,
    AxisDefinition,
    Capability,
    DomainError,
    Entity,
    EntityId,
    IReferenceIndex,
    IStorageEngine,
    Query,
    QueryResult,
    ReferenceLink,
    ScalarDefinition,
    StorageSchema,
    build_core,
)

#: Версия формата БД.
STORE_VERSION = 1

#: Контракт ядра, с которым совместим плагин.
REQUIRES_CONTRACT = ">=5,<6"

#: Путь по умолчанию, если конфиг его не задал.
DEFAULT_PATH = Path(".agoralar") / "vault.sqlite3"


class SqliteStoreError(RuntimeError):
    """База повреждена, недоступна или несовместима по версии."""


_META_TABLE = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
_ENTITIES_TABLE = "CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, doc TEXT NOT NULL)"
_ARTIFACTS_TABLE = (
    "CREATE TABLE IF NOT EXISTS artifacts ("
    "content_hash TEXT PRIMARY KEY, media_type TEXT NOT NULL, data BLOB NOT NULL)"
)


class SqliteStore(IStorageEngine, IReferenceIndex):
    """Хранилище в SQLite-файле: ленивое открытие, запись в транзакции."""

    def __init__(self, path: Path | str, schema: StorageSchema | None = None) -> None:
        self._path = Path(path)
        self._schema = schema
        self._engine: IStorageEngine | None = None
        #: Возможности бэкенда не меняются в рантайме: считаем один раз.
        self._capabilities: frozenset[Capability] | None = None

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
                connection.execute(_ARTIFACTS_TABLE)
                meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
                rows = connection.execute("SELECT doc FROM entities").fetchall()
                artifact_rows = connection.execute(
                    "SELECT content_hash, media_type, data FROM artifacts"
                ).fetchall()
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

        for content_hash, media_type, data in artifact_rows:
            artifact_errors = await engine.put_artifact(
                Artifact(
                    content_hash=str(content_hash), media_type=str(media_type), data=bytes(data)
                )
            )
            if artifact_errors:
                raise SqliteStoreError(f"broken store {self._path}: {artifact_errors[0]}")

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
        artifacts = []
        for reference in await engine.list_artifacts():
            artifact = await engine.get_artifact(reference.content_hash)
            if artifact is not None:
                artifacts.append(
                    (artifact.content_hash, artifact.media_type, sqlite3.Binary(artifact.data))
                )

        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(_META_TABLE)
                connection.execute(_ENTITIES_TABLE)
                connection.execute(_ARTIFACTS_TABLE)
                connection.execute("DELETE FROM entities")
                connection.execute("DELETE FROM artifacts")
                connection.executemany("INSERT INTO entities (id, doc) VALUES (?, ?)", payload)
                connection.executemany(
                    "INSERT INTO artifacts (content_hash, media_type, data) VALUES (?, ?, ?)",
                    artifacts,
                )
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

    async def list_entities(self, include_deleted: bool = False) -> tuple[Entity, ...]:
        return await (await self._store()).list_entities(include_deleted=include_deleted)

    async def query(self, query: Query) -> QueryResult:
        return await (await self._store()).query(query)

    # ------------------------------------------------------------ артефакты

    async def put_artifact(self, artifact: Artifact) -> list[DomainError]:
        errors = await (await self._store()).put_artifact(artifact)
        if not errors:
            await self._flush()
        return errors

    async def get_artifact(self, content_hash: str) -> Artifact | None:
        return await (await self._store()).get_artifact(content_hash)

    async def list_artifacts(self) -> tuple[ArtifactRef, ...]:
        return await (await self._store()).list_artifacts()

    async def delete_artifact(self, content_hash: str) -> list[DomainError]:
        errors = await (await self._store()).delete_artifact(content_hash)
        if not errors:
            await self._flush()
        return errors

    async def references_to(
        self,
        entity_id: EntityId,
        axes: Sequence[str] | None = None,
        *,
        include_deleted: bool = False,
    ) -> tuple[ReferenceLink, ...]:
        """Входящие ссылки: обход делегируется обратному индексу ядра (ADR-0021).

        Стор не ведёт второго индекса: записи и чтения идут через эталонный
        движок, поэтому «кто на меня ссылается» — это его индекс, а не
        копия в файле. SQL-пушдаун обхода появится вместе с пушдауном
        запросов (отдельный ADR).
        """
        engine = await self._store()
        if not isinstance(engine, IReferenceIndex):  # pragma: no cover - ядро умеет всегда
            return ()
        return await engine.references_to(entity_id, axes, include_deleted=include_deleted)

    def capabilities(self) -> frozenset[Capability]:
        if self._capabilities is None:
            self._capabilities = build_core().storage.capabilities()
        return self._capabilities


__all__ = ["STORE_VERSION", "SqliteStore", "SqliteStoreError"]


def create(options: Mapping[str, Any]) -> IStorageEngine:
    """Фабрика: путь из настроек плагина или значение по умолчанию."""
    raw_path = options.get("path")
    return SqliteStore(Path(str(raw_path)) if raw_path else DEFAULT_PATH)


def plugin() -> dict[str, Any]:
    """Манифест плагина обычными данными (без DTO продукта)."""
    return {"requires_contract": REQUIRES_CONTRACT, "factory": create}


__all__ = [
    "DEFAULT_PATH",
    "REQUIRES_CONTRACT",
    "STORE_VERSION",
    "SqliteStore",
    "SqliteStoreError",
    "create",
    "plugin",
]
