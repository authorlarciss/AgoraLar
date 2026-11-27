"""SQLite-хранилище для AxisLar: durable-бэкенд с индексом фасетов.

Документы объектов лежат в `entities` (JSON как есть), `meta` хранит версию
формата и схему, `facet_index`/`ref_index` — производные индексы по осям.
Фильтрацию по-прежнему исполняет эталонный движок ядра (ADR-0012): SQLite
отвечает за долговечность и за обход ссылок, а не за пушдаун предикатов.

В формате 2 запись инкрементальная: `_flush` пишет только объекты, которые
изменились с прошлого вызова, и пересобирает индексные строки только для
них. Раньше каждая запись удаляла и заново вставляла всю таблицу — это
давало O(n) на один `upsert` и делало индексы бессмысленными.

Плагин продукта AgoraLar: зависит только от `axislar-core`, манифест —
обычные данные (без DTO продукта). Проверяется контрактными наборами ядра
(`run_storage_contract`, `run_reference_index_contract`).
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
    MeasurableValue,
    Query,
    QueryResult,
    ReferenceLink,
    ReferenceValue,
    ScalarDefinition,
    StorageSchema,
    build_core,
)

#: Версия формата БД. 2 — индексные таблицы и инкрементальная запись.
STORE_VERSION = 2

#: Контракт ядра, с которым совместим плагин.
REQUIRES_CONTRACT = ">=5,<6"

#: Путь по умолчанию, если конфиг его не задал.
DEFAULT_PATH = Path(".agoralar") / "vault.sqlite3"


class SqliteStoreError(RuntimeError):
    """База повреждена, недоступна или несовместима по версии."""


_META_TABLE = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
_ENTITIES_TABLE = (
    "CREATE TABLE IF NOT EXISTS entities ("
    "id TEXT PRIMARY KEY, doc TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)"
)
_ARTIFACTS_TABLE = (
    "CREATE TABLE IF NOT EXISTS artifacts ("
    "content_hash TEXT PRIMARY KEY, media_type TEXT NOT NULL, data BLOB NOT NULL)"
)
_FACET_INDEX_TABLE = (
    "CREATE TABLE IF NOT EXISTS facet_index ("
    "axis TEXT NOT NULL, value TEXT NOT NULL, num REAL, entity_id TEXT NOT NULL, "
    "PRIMARY KEY (axis, value, entity_id))"
)
_REF_INDEX_TABLE = (
    "CREATE TABLE IF NOT EXISTS ref_index ("
    "axis TEXT NOT NULL, target_id TEXT NOT NULL, entity_id TEXT NOT NULL, "
    "PRIMARY KEY (axis, target_id, entity_id))"
)
_SCHEMA_STATEMENTS = (
    _META_TABLE,
    _ENTITIES_TABLE,
    _ARTIFACTS_TABLE,
    _FACET_INDEX_TABLE,
    _REF_INDEX_TABLE,
    "CREATE INDEX IF NOT EXISTS facet_index_axis ON facet_index (axis)",
    "CREATE INDEX IF NOT EXISTS ref_index_target ON ref_index (target_id)",
)

#: Строки индексов одного объекта: (ось, значение, число, id).
FacetRow = tuple[str, str, float | None, str]
#: Строки ссылочного индекса одного объекта: (ось, цель, id).
RefRow = tuple[str, str, str]


class SqliteStore(IStorageEngine, IReferenceIndex):
    """Хранилище в SQLite-файле: ленивое открытие, инкрементальная запись."""

    def __init__(self, path: Path | str, schema: StorageSchema | None = None) -> None:
        self._path = Path(path)
        self._schema = schema
        self._engine: IStorageEngine | None = None
        #: Возможности бэкенда не меняются в рантайме: считаем один раз.
        self._capabilities: frozenset[Capability] | None = None
        #: Объекты, изменённые с прошлого flush. Стор помнит их сам: у порта
        #: нет способа получить tombstone по id (`get` скрывает удалённые).
        self._pending: dict[str, Entity] = {}
        self._dirty_artifacts: set[str] = set()
        self._removed_artifacts: set[str] = set()
        self._schema_dirty = False

    # --------------------------------------------------------------- доступ

    async def _store(self) -> IStorageEngine:
        if self._engine is None:
            self._engine = await self._load()
        return self._engine

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._path)

    def _file_version(self) -> str | None:
        """Версия формата в файле; `None`, если файла/таблицы нет."""
        if not self._path.is_file():
            return None
        try:
            with closing(self._connect()) as connection:
                row = connection.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
        except sqlite3.DatabaseError:
            return None
        return str(row[0]) if row else None

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)

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
                self._create_schema(connection)
                meta = dict(connection.execute("SELECT key, value FROM meta").fetchall())
                version = meta.get("version")
                if version not in {"1", str(STORE_VERSION)}:
                    raise SqliteStoreError(
                        f"unsupported store version in {self._path}: "
                        f"{version!r} (expected {STORE_VERSION})"
                    )
                if version == "1":
                    self._migrate_v1(connection)
                rows = connection.execute("SELECT doc FROM entities").fetchall()
                artifact_rows = connection.execute(
                    "SELECT content_hash, media_type, data FROM artifacts"
                ).fetchall()
        except sqlite3.DatabaseError as error:
            raise SqliteStoreError(f"broken database {self._path}: {error}") from error

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

    def _migrate_v1(self, connection: sqlite3.Connection) -> None:
        """Формат 1 → 2: колонка `deleted` и индексные таблицы.

        Миграция идёт в той же транзакции, что и открытие: файл либо
        становится формата 2, либо остаётся прежним.
        """
        columns = {row[1] for row in connection.execute("PRAGMA table_info(entities)")}
        if "deleted" not in columns:
            connection.execute("ALTER TABLE entities ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        rows = connection.execute("SELECT id, doc FROM entities").fetchall()
        hidden = [
            (str(entity_id),) for entity_id, doc in rows if _document_is_tombstone(doc, self._path)
        ]
        if hidden:
            connection.executemany("UPDATE entities SET deleted = 1 WHERE id = ?", hidden)
        self._rebuild_indexes(connection, rows)
        connection.execute(
            "INSERT INTO meta (key, value) VALUES ('version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(STORE_VERSION),),
        )

    def _rebuild_indexes(
        self, connection: sqlite3.Connection, rows: Sequence[tuple[str, str]]
    ) -> None:
        """Пересобрать индексы из документов (миграция и полный flush)."""
        connection.execute("DELETE FROM facet_index")
        connection.execute("DELETE FROM ref_index")
        facet_rows: list[FacetRow] = []
        ref_rows: list[RefRow] = []
        for _, doc in rows:
            try:
                entity = Entity.model_validate(json.loads(doc))
            except (TypeError, ValueError) as error:
                raise SqliteStoreError(f"broken store {self._path}: {error}") from error
            facet_rows.extend(_facet_rows(entity))
            ref_rows.extend(_ref_rows(entity))
        connection.executemany(
            "INSERT OR REPLACE INTO facet_index (axis, value, num, entity_id) VALUES (?, ?, ?, ?)",
            facet_rows,
        )
        connection.executemany(
            "INSERT OR REPLACE INTO ref_index (axis, target_id, entity_id) VALUES (?, ?, ?)",
            ref_rows,
        )

    # --------------------------------------------------------------- запись

    async def _flush(self) -> None:
        """Записать только то, что изменилось с прошлого вызова."""
        if not (
            self._pending or self._dirty_artifacts or self._removed_artifacts or self._schema_dirty
        ):
            return

        engine = await self._store()
        entities = list(self._pending.values())
        artifacts = []
        for content_hash in self._dirty_artifacts:
            artifact = await engine.get_artifact(content_hash)
            if artifact is not None:
                artifacts.append(
                    (artifact.content_hash, artifact.media_type, sqlite3.Binary(artifact.data))
                )
        schema_json = (await engine.get_schema()).model_dump_json()

        try:
            with closing(self._connect()) as connection, connection:
                self._create_schema(connection)
                if entities:
                    placeholders = ",".join("?" * len(entities))
                    connection.execute(
                        f"DELETE FROM entities WHERE id IN ({placeholders})",
                        [str(entity.id) for entity in entities],
                    )
                    connection.executemany(
                        "INSERT INTO entities (id, doc, deleted) VALUES (?, ?, ?)",
                        [
                            (
                                str(entity.id),
                                entity.model_dump_json(),
                                1 if entity.deleted_at is not None else 0,
                            )
                            for entity in entities
                        ],
                    )
                    connection.execute(
                        f"DELETE FROM facet_index WHERE entity_id IN ({placeholders})",
                        [str(entity.id) for entity in entities],
                    )
                    connection.execute(
                        f"DELETE FROM ref_index WHERE entity_id IN ({placeholders})",
                        [str(entity.id) for entity in entities],
                    )
                    connection.executemany(
                        "INSERT OR REPLACE INTO facet_index (axis, value, num, entity_id) "
                        "VALUES (?, ?, ?, ?)",
                        [row for entity in entities for row in _facet_rows(entity)],
                    )
                    connection.executemany(
                        "INSERT OR REPLACE INTO ref_index (axis, target_id, entity_id) "
                        "VALUES (?, ?, ?)",
                        [row for entity in entities for row in _ref_rows(entity)],
                    )
                if self._removed_artifacts:
                    connection.executemany(
                        "DELETE FROM artifacts WHERE content_hash = ?",
                        [(content_hash,) for content_hash in sorted(self._removed_artifacts)],
                    )
                if artifacts:
                    connection.executemany(
                        "INSERT OR REPLACE INTO artifacts (content_hash, media_type, data) "
                        "VALUES (?, ?, ?)",
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

        self._pending.clear()
        self._dirty_artifacts.clear()
        self._removed_artifacts.clear()
        self._schema_dirty = False

    async def _flush_all(self) -> None:
        """Переписать файл целиком: редкий путь после частичного отказа.

        Инкрементальный flush требует знания «что изменилось»; при частичном
        отказе пачки проще пересобрать состояние из движка, чем угадывать.
        """
        engine = await self._store()
        entities = await engine.list_entities(include_deleted=True)
        artifacts = []
        for reference in await engine.list_artifacts():
            artifact = await engine.get_artifact(reference.content_hash)
            if artifact is not None:
                artifacts.append(
                    (artifact.content_hash, artifact.media_type, sqlite3.Binary(artifact.data))
                )
        schema_json = (await engine.get_schema()).model_dump_json()

        try:
            with closing(self._connect()) as connection, connection:
                self._create_schema(connection)
                connection.execute("DELETE FROM entities")
                connection.execute("DELETE FROM artifacts")
                connection.executemany(
                    "INSERT INTO entities (id, doc, deleted) VALUES (?, ?, ?)",
                    [
                        (
                            str(entity.id),
                            entity.model_dump_json(),
                            1 if entity.deleted_at is not None else 0,
                        )
                        for entity in entities
                    ],
                )
                connection.executemany(
                    "INSERT INTO artifacts (content_hash, media_type, data) VALUES (?, ?, ?)",
                    artifacts,
                )
                self._rebuild_indexes(
                    connection, [(str(entity.id), entity.model_dump_json()) for entity in entities]
                )
                connection.executemany(
                    "INSERT INTO meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    [("version", str(STORE_VERSION)), ("schema", schema_json)],
                )
                connection.commit()
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot write {self._path}: {error}") from error

        self._pending.clear()
        self._dirty_artifacts.clear()
        self._removed_artifacts.clear()
        self._schema_dirty = False

    # ---------------------------------------------------------------- схема

    async def get_schema(self) -> StorageSchema:
        return await (await self._store()).get_schema()

    async def add_axis(self, axis: AxisDefinition) -> list[DomainError]:
        errors = await (await self._store()).add_axis(axis)
        if not errors:
            self._schema_dirty = True
            await self._flush()
        return errors

    async def add_scalar(self, scalar: ScalarDefinition) -> list[DomainError]:
        errors = await (await self._store()).add_scalar(scalar)
        if not errors:
            self._schema_dirty = True
            await self._flush()
        return errors

    # --------------------------------------------------------------- запись

    async def upsert(self, entity: Entity) -> list[DomainError]:
        errors = await (await self._store()).upsert(entity)
        if not errors:
            self._pending[str(entity.id)] = entity
            await self._flush()
        return errors

    async def bulk_upsert(self, entities: Sequence[Entity]) -> list[DomainError]:
        errors = await (await self._store()).bulk_upsert(entities)
        if errors:
            await self._flush_all()
        elif entities:
            for entity in entities:
                self._pending[str(entity.id)] = entity
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
            self._removed_artifacts.discard(artifact.content_hash)
            self._dirty_artifacts.add(artifact.content_hash)
            await self._flush()
        return errors

    async def get_artifact(self, content_hash: str) -> Artifact | None:
        return await (await self._store()).get_artifact(content_hash)

    async def list_artifacts(self) -> tuple[ArtifactRef, ...]:
        return await (await self._store()).list_artifacts()

    async def delete_artifact(self, content_hash: str) -> list[DomainError]:
        errors = await (await self._store()).delete_artifact(content_hash)
        if not errors:
            self._dirty_artifacts.discard(content_hash)
            self._removed_artifacts.add(content_hash)
            await self._flush()
        return errors

    # ------------------------------------------------------ обратные ссылки

    async def references_to(
        self,
        entity_id: EntityId,
        axes: Sequence[str] | None = None,
        *,
        include_deleted: bool = False,
    ) -> tuple[ReferenceLink, ...]:
        """Входящие ссылки из `ref_index` (ADR-0021).

        Ходит в SQL напрямую: обход не требует загрузки всех документов в
        память. Файл формата 1 сначала мигрируется (через полную загрузку),
        новый файл не создаётся вовсе — ссылок в нём быть не может.
        """
        if axes is not None and not axes:
            return ()
        if self._pending or self._dirty_artifacts or self._removed_artifacts:
            await self._flush()
        version = self._file_version()
        if version is None:
            return ()
        if version != str(STORE_VERSION):
            await self._store()

        sql = (
            "SELECT r.axis, r.entity_id FROM ref_index AS r "
            "JOIN entities AS e ON e.id = r.entity_id WHERE r.target_id = ?"
        )
        params: list[str] = [str(entity_id)]
        if axes is not None:
            sql += " AND r.axis IN (" + ",".join("?" * len(axes)) + ")"
            params.extend(axes)
        if not include_deleted:
            sql += " AND e.deleted = 0"
        sql += " ORDER BY r.axis, r.entity_id"

        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(sql, params).fetchall()
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot read {self._path}: {error}") from error
        return tuple(
            ReferenceLink(axis=str(axis), entity_id=EntityId(str(source))) for axis, source in rows
        )

    def capabilities(self) -> frozenset[Capability]:
        if self._capabilities is None:
            self._capabilities = build_core().storage.capabilities()
        return self._capabilities


def _document_is_tombstone(doc: str, path: Path) -> bool:
    try:
        payload = json.loads(doc)
    except TypeError as error:  # pragma: no cover - защита от нестрокового doc
        raise SqliteStoreError(f"broken store {path}: {error}") from error
    return bool(payload.get("deleted_at"))


def _facet_rows(entity: Entity) -> list[FacetRow]:
    """Строки индекса фасетов: ссылки уходят в `ref_index`, а не сюда."""
    rows: list[FacetRow] = []
    for axis, values in entity.facets.items():
        for value in values:
            if isinstance(value, ReferenceValue):
                continue
            if isinstance(value, MeasurableValue):
                rows.append((axis, repr(value.value), value.value, str(entity.id)))
            else:
                rows.append((axis, str(value.value), None, str(entity.id)))
    return rows


def _ref_rows(entity: Entity) -> list[RefRow]:
    """Строки ссылочного индекса: одна на пару «ось — цель»."""
    rows: list[RefRow] = []
    for axis, values in entity.facets.items():
        for value in values:
            if isinstance(value, ReferenceValue):
                rows.append((axis, str(value.target), str(entity.id)))
    return rows


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
