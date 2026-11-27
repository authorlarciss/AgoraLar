"""SQLite-хранилище для AxisLar: самостоятельный бэкенд, а не обёртка.

Документы объектов лежат в `entities` (JSON как есть), `meta` хранит версию
формата и схему, `facet_index`/`ref_index` — индексы по осям. Чтение,
запись и запросы исполняются в SQL: документы в память целиком не
загружаются, `query` возвращает страницу по `LIMIT/OFFSET`, а `references_to`
читает `ref_index`.

Раньше стор был обёрткой над in-memory движком ядра: каждый запуск читал
весь файл, а каждая запись переписывала всю таблицу. Теперь это честный
бэкенд, и его семантика проверяется контрактными наборами ядра
(`run_storage_contract`, `run_reference_index_contract`) плюс
дифференциальным тестом против эталонного `MemoryStorage`.

Формат 2 (миграция с 1 автоматическая): колонка `entities.deleted` и
индексные таблицы.

Плагин продукта AgoraLar: зависит только от `axislar-core`, манифест —
обычные данные (без DTO продукта).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from axislar_core.contracts import (
    Artifact,
    ArtifactRef,
    AxisDefinition,
    AxisKind,
    Capability,
    DomainError,
    Entity,
    EntityId,
    ErrorCode,
    Filter,
    IReferenceIndex,
    IStorageEngine,
    MeasurableValue,
    Operator,
    Query,
    QueryResult,
    ReferenceLink,
    ReferenceValue,
    ScalarDefinition,
    StorageQueryError,
    StorageSchema,
    validate_artifact,
    validate_axis,
    validate_entity,
    validate_query,
    validate_scalar,
)

#: Версия формата БД. 2 — индексные таблицы, колонка `deleted`.
STORE_VERSION = 2

#: Контракт ядра, с которым совместим плагин.
REQUIRES_CONTRACT = ">=6,<7"

#: Путь по умолчанию, если конфиг его не задал.
DEFAULT_PATH = Path(".agoralar") / "vault.sqlite3"

#: Что умеет этот бэкенд. Объявляем только то, что действительно реализовано:
#: запросы всех трёх видов осей, поиск по скалярам, проверка ссылок, пачка,
#: динамические оси, артефакты и обход ссылок.
SQLITE_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        Capability.QUERY_CATEGORICAL,
        Capability.QUERY_MEASURABLE,
        Capability.QUERY_REFERENCE,
        Capability.SCALAR_SEARCH,
        Capability.REFERENCE_CHECK,
        Capability.WRITE_BULK,
        Capability.DYNAMIC_AXES,
        Capability.ARTIFACTS,
        Capability.TRAVERSE_REFERENCES,
    }
)


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
    "CREATE INDEX IF NOT EXISTS facet_index_entity ON facet_index (entity_id)",
    "CREATE INDEX IF NOT EXISTS ref_index_target ON ref_index (target_id)",
    "CREATE INDEX IF NOT EXISTS ref_index_entity ON ref_index (entity_id)",
    "CREATE INDEX IF NOT EXISTS entities_deleted ON entities (deleted)",
)

#: Числовые операторы и их SQL-эквиваленты.
_NUMERIC_SQL: dict[Operator, str] = {
    Operator.EQ: "=",
    Operator.GT: ">",
    Operator.LT: "<",
    Operator.GTE: ">=",
    Operator.LTE: "<=",
}


class SqliteStore(IStorageEngine, IReferenceIndex):
    """Хранилище в SQLite-файле: ленивое открытие, всё чтение и запись в SQL."""

    def __init__(self, path: Path | str, schema: StorageSchema | None = None) -> None:
        self._path = Path(path)
        self._schema_hint = schema
        self._schema: StorageSchema | None = None
        self._capabilities: frozenset[Capability] = SQLITE_CAPABILITIES

    # --------------------------------------------------------------- доступ

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._path)

    @staticmethod
    def _create_tables(connection: sqlite3.Connection) -> None:
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)

    async def _schema_of(self) -> StorageSchema:
        """Схема из файла; при первом обращении файл создаётся или мигрируется."""
        if self._schema is None:
            self._schema = self._open_or_create()
        return self._schema

    def _open_or_create(self) -> StorageSchema:
        """Схема файла; файл создаётся только записью, чтение его не создаёт."""
        if not self._path.is_file():
            return self._schema_hint or StorageSchema()
        try:
            with closing(self._connect()) as connection, connection:
                meta = _read_meta(connection)
                version = meta.get("version")
                if version is None:
                    schema = self._schema_hint or StorageSchema()
                    self._create_tables(connection)
                    self._store_meta(connection, schema)
                    return schema
                if version == str(STORE_VERSION):
                    self._create_tables(connection)
                    return _parse_schema(meta, self._path)
                if version == "1":
                    self._migrate_v1(connection)
                    schema = _parse_schema(meta, self._path)
                    self._store_meta(connection, schema)
                    return schema
                raise SqliteStoreError(
                    f"unsupported store version in {self._path}: "
                    f"{version!r} (expected {STORE_VERSION})"
                )
        except sqlite3.DatabaseError as error:
            raise SqliteStoreError(f"broken database {self._path}: {error}") from error

    async def _ready(self) -> bool:
        """Схема загружена, и файл существует (пустое хранилище = пустые выдачи)."""
        await self._schema_of()
        return self._path.is_file()

    @staticmethod
    def _store_meta(connection: sqlite3.Connection, schema: StorageSchema) -> None:
        connection.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [("version", str(STORE_VERSION)), ("schema", schema.model_dump_json())],
        )

    def _migrate_v1(self, connection: sqlite3.Connection) -> None:
        """Формат 1 → 2: колонка `deleted` и заполнение индексов.

        Идёт в той же транзакции, что и открытие: файл либо становится
        формата 2, либо остаётся прежним.
        """
        columns = {row[1] for row in connection.execute("PRAGMA table_info(entities)")}
        if "deleted" not in columns:
            connection.execute("ALTER TABLE entities ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        # Индексы по `deleted` создаются только после появления колонки.
        self._create_tables(connection)
        rows = connection.execute("SELECT id, doc FROM entities").fetchall()
        hidden = [
            (str(entity_id),) for entity_id, doc in rows if _document_is_tombstone(doc, self._path)
        ]
        if hidden:
            connection.executemany("UPDATE entities SET deleted = 1 WHERE id = ?", hidden)
        connection.execute("DELETE FROM facet_index")
        connection.execute("DELETE FROM ref_index")
        facet_rows: list[tuple[str, str, float | None, str]] = []
        ref_rows: list[tuple[str, str, str]] = []
        for _, doc in rows:
            entity = _parse_entity(doc, self._path)
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

    # ---------------------------------------------------------------- схема

    async def get_schema(self) -> StorageSchema:
        return await self._schema_of()

    async def add_axis(self, axis: AxisDefinition) -> list[DomainError]:
        schema = await self._schema_of()
        errors = validate_axis(schema, axis)
        if not errors:
            self._schema = schema.with_axis(axis)
            await self._save_schema()
        return errors

    async def add_scalar(self, scalar: ScalarDefinition) -> list[DomainError]:
        schema = await self._schema_of()
        errors = validate_scalar(schema, scalar)
        if not errors:
            self._schema = schema.with_scalar(scalar)
            await self._save_schema()
        return errors

    def _ensure_writable(self, connection: sqlite3.Connection, schema: StorageSchema) -> None:
        """Первая запись создаёт файл: таблицы и `meta` пишутся в той же транзакции."""
        self._create_tables(connection)
        self._store_meta(connection, schema)

    async def _save_schema(self) -> None:
        assert self._schema is not None
        try:
            with closing(self._connect()) as connection, connection:
                self._ensure_writable(connection, self._schema)
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot write {self._path}: {error}") from error

    # --------------------------------------------------------------- запись

    async def upsert(self, entity: Entity, *, expected_rev: int | None = None) -> list[DomainError]:
        return await self._write((entity,), expected_revs={str(entity.id): expected_rev})

    async def bulk_upsert(self, entities: Sequence[Entity]) -> list[DomainError]:
        """Пачка атомарна: при любой ошибке не пишется ничего (ADR-0022).

        Ревизии ставятся, но не проверяются: это путь импорта и синхронизации
        (ADR-0024).
        """
        return await self._write(entities)

    async def _write(
        self,
        entities: Sequence[Entity],
        expected_revs: Mapping[str, int | None] | None = None,
    ) -> list[DomainError]:
        if not entities:
            return []
        schema = await self._schema_of()
        capabilities = self.capabilities()
        batch_ids = {str(entity.id) for entity in entities}
        targets = {
            str(value.target)
            for entity in entities
            for values in entity.facets.values()
            for value in values
            if isinstance(value, ReferenceValue)
        } | batch_ids
        artifact_hashes = {
            ref.content_hash for entity in entities for ref in entity.artifacts.values()
        }

        try:
            with closing(self._connect()) as connection, connection:
                self._create_tables(connection)
                stamped: list[Entity] = []
                conflicts: list[DomainError] = []
                for entity in entities:
                    current = _current_rev(connection, str(entity.id))
                    expected = (expected_revs or {}).get(str(entity.id))
                    if expected is not None and expected != current:
                        conflicts.append(
                            DomainError(
                                code=ErrorCode.CONFLICT,
                                detail=(
                                    f"revision mismatch: expected {expected}, current {current}"
                                ),
                                entity_id=entity.id,
                            )
                        )
                        continue
                    stamped.append(entity.model_copy(update={"rev": current + 1}))
                if conflicts:
                    return conflicts
                entities = tuple(stamped)

                # Ссылка может указывать на любой объект этой же пачки.
                known_ids = _select_ids(connection, "entities", "id", targets) | {
                    EntityId(entity_id) for entity_id in batch_ids
                }
                known_artifacts = _select_ids(
                    connection, "artifacts", "content_hash", artifact_hashes
                )
                errors: list[DomainError] = []
                for entity in entities:
                    errors.extend(
                        validate_entity(
                            schema,
                            entity,
                            known_ids=known_ids,
                            known_artifacts=known_artifacts,
                            capabilities=capabilities,
                        )
                    )
                if errors:
                    return errors
                for entity in entities:
                    self._write_entity(connection, entity)
            return []
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot write {self._path}: {error}") from error

    @staticmethod
    def _write_entity(connection: sqlite3.Connection, entity: Entity) -> None:
        key = str(entity.id)
        connection.execute("DELETE FROM entities WHERE id = ?", (key,))
        connection.execute(
            "INSERT INTO entities (id, doc, deleted) VALUES (?, ?, ?)",
            (key, entity.model_dump_json(), 1 if entity.deleted_at is not None else 0),
        )
        connection.execute("DELETE FROM facet_index WHERE entity_id = ?", (key,))
        connection.execute("DELETE FROM ref_index WHERE entity_id = ?", (key,))
        connection.executemany(
            "INSERT OR REPLACE INTO facet_index (axis, value, num, entity_id) VALUES (?, ?, ?, ?)",
            _facet_rows(entity),
        )
        connection.executemany(
            "INSERT OR REPLACE INTO ref_index (axis, target_id, entity_id) VALUES (?, ?, ?)",
            _ref_rows(entity),
        )

    # --------------------------------------------------------------- чтение

    async def get(self, entity_id: EntityId) -> Entity | None:
        if not await self._ready():
            return None
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT doc FROM entities WHERE id = ? AND deleted = 0",
                    (str(entity_id),),
                ).fetchone()
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot read {self._path}: {error}") from error
        return _parse_entity(row[0], self._path) if row is not None else None

    async def fetch_many(self, ids: Sequence[EntityId]) -> tuple[Entity, ...]:
        if not ids or not await self._ready():
            return ()
        try:
            with closing(self._connect()) as connection:
                rows = _select_rows(
                    connection,
                    "SELECT id, doc FROM entities WHERE id IN ({}) AND deleted = 0",
                    [str(entity_id) for entity_id in ids],
                )
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot read {self._path}: {error}") from error
        by_id = {str(row[0]): _parse_entity(row[1], self._path) for row in rows}
        return tuple(by_id[str(entity_id)] for entity_id in ids if str(entity_id) in by_id)

    async def count(self) -> int:
        if not await self._ready():
            return 0
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT COUNT(*) FROM entities WHERE deleted = 0"
                ).fetchone()
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot read {self._path}: {error}") from error
        return int(row[0]) if row is not None else 0

    async def list_entities(self, include_deleted: bool = False) -> tuple[Entity, ...]:
        if not await self._ready():
            return ()
        sql = "SELECT doc FROM entities"
        if not include_deleted:
            sql += " WHERE deleted = 0"
        sql += " ORDER BY id"
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(sql).fetchall()
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot read {self._path}: {error}") from error
        return tuple(_parse_entity(row[0], self._path) for row in rows)

    # --------------------------------------------------------------- запрос

    async def query(self, query: Query) -> QueryResult:
        schema = await self._schema_of()
        errors = validate_query(schema, query, self.capabilities())
        if errors:
            raise StorageQueryError(errors)
        if not self._path.is_file():
            return QueryResult(ids=(), total=0, limit=query.limit, offset=query.offset)

        conditions: list[str] = []
        params: list[Any] = []
        for condition in query.all:
            fragment, values = _condition(schema, condition)
            conditions.append(fragment)
            params.extend(values)

        where = " WHERE e.deleted = 0"
        if conditions:
            where += " AND " + " AND ".join(conditions)

        page_params = list(params)
        limit_sql = ""
        if query.limit is not None:
            limit_sql = " LIMIT ? OFFSET ?"
            page_params.extend([query.limit, query.offset])
        elif query.offset:
            limit_sql = " LIMIT -1 OFFSET ?"
            page_params.append(query.offset)

        try:
            with closing(self._connect()) as connection:
                total_row = connection.execute(
                    f"SELECT COUNT(*) FROM entities e{where}", params
                ).fetchone()
                rows = connection.execute(
                    f"SELECT e.id FROM entities e{where} ORDER BY e.id{limit_sql}", page_params
                ).fetchall()
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot read {self._path}: {error}") from error

        return QueryResult(
            ids=tuple(EntityId(str(row[0])) for row in rows),
            total=int(total_row[0]) if total_row is not None else 0,
            limit=query.limit,
            offset=query.offset,
        )

    # ------------------------------------------------------------ артефакты

    async def put_artifact(self, artifact: Artifact) -> list[DomainError]:
        schema = await self._schema_of()
        errors = validate_artifact(artifact, self.capabilities())
        if errors:
            return errors
        try:
            with closing(self._connect()) as connection, connection:
                self._ensure_writable(connection, schema)
                row = connection.execute(
                    "SELECT media_type FROM artifacts WHERE content_hash = ?",
                    (artifact.content_hash,),
                ).fetchone()
                if row is not None and str(row[0]) != artifact.media_type:
                    return [
                        DomainError(
                            code=ErrorCode.ARTIFACT_MISMATCH,
                            detail=(
                                f"artifact {artifact.content_hash!r} is already stored as "
                                f"{row[0]!r}, not {artifact.media_type!r}"
                            ),
                        )
                    ]
                connection.execute(
                    "INSERT OR REPLACE INTO artifacts (content_hash, media_type, data) "
                    "VALUES (?, ?, ?)",
                    (artifact.content_hash, artifact.media_type, sqlite3.Binary(artifact.data)),
                )
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot write {self._path}: {error}") from error
        return []

    async def get_artifact(self, content_hash: str) -> Artifact | None:
        if not await self._ready():
            return None
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT media_type, data FROM artifacts WHERE content_hash = ?",
                    (content_hash,),
                ).fetchone()
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot read {self._path}: {error}") from error
        if row is None:
            return None
        return Artifact(content_hash=content_hash, media_type=str(row[0]), data=bytes(row[1]))

    async def list_artifacts(self) -> tuple[ArtifactRef, ...]:
        if not await self._ready():
            return ()
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT content_hash, media_type, LENGTH(data) FROM artifacts "
                    "ORDER BY content_hash"
                ).fetchall()
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot read {self._path}: {error}") from error
        return tuple(
            ArtifactRef(content_hash=str(content_hash), media_type=str(media_type), size=int(size))
            for content_hash, media_type, size in rows
        )

    async def delete_artifact(self, content_hash: str) -> list[DomainError]:
        schema = await self._schema_of()
        try:
            with closing(self._connect()) as connection, connection:
                self._ensure_writable(connection, schema)
                users = _artifact_users(connection, content_hash)
                if users:
                    return [
                        DomainError(
                            code=ErrorCode.ARTIFACT_IN_USE,
                            detail=(
                                f"artifact {content_hash!r} is referenced by "
                                f"{', '.join(users)}; remove the references first"
                            ),
                            entity_id=EntityId(users[0]),
                        )
                    ]
                connection.execute("DELETE FROM artifacts WHERE content_hash = ?", (content_hash,))
        except sqlite3.DatabaseError as error:  # pragma: no cover - диск/права
            raise SqliteStoreError(f"cannot write {self._path}: {error}") from error
        return []

    # ------------------------------------------------------ обратные ссылки

    async def references_to(
        self,
        entity_id: EntityId,
        axes: Sequence[str] | None = None,
        *,
        include_deleted: bool = False,
    ) -> tuple[ReferenceLink, ...]:
        """Входящие ссылки из `ref_index` (ADR-0021), без загрузки документов."""
        if axes is not None and not axes:
            return ()
        if not await self._ready():
            return ()

        sql = (
            "SELECT r.axis, r.entity_id FROM ref_index AS r "
            "JOIN entities AS e ON e.id = r.entity_id WHERE r.target_id = ?"
        )
        params: list[Any] = [str(entity_id)]
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
        return self._capabilities


# ------------------------------------------------------------------ helpers


def _current_rev(connection: sqlite3.Connection, entity_id: str) -> int:
    """Текущая ревизия объекта (0, если объекта нет) — для ADR-0024."""
    row = connection.execute("SELECT doc FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if row is None:
        return 0
    try:
        payload = json.loads(row[0])
    except TypeError:  # pragma: no cover - защита от нестрокового doc
        return 0
    rev = payload.get("rev")
    return int(rev) if isinstance(rev, int) else 0


def _read_meta(connection: sqlite3.Connection) -> dict[str, str]:
    """`meta` может отсутствовать: файл мог создать другой инструмент."""
    try:
        return dict(connection.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.OperationalError:
        return {}


def _parse_schema(meta: Mapping[str, str], path: Path) -> StorageSchema:
    try:
        return StorageSchema.model_validate(json.loads(meta["schema"]))
    except (KeyError, TypeError, ValueError) as error:
        raise SqliteStoreError(f"broken store {path}: {error}") from error


def _parse_entity(doc: str, path: Path) -> Entity:
    try:
        return Entity.model_validate(json.loads(doc))
    except (TypeError, ValueError) as error:
        raise SqliteStoreError(f"broken store {path}: {error}") from error


def _document_is_tombstone(doc: str, path: Path) -> bool:
    try:
        payload = json.loads(doc)
    except TypeError as error:  # pragma: no cover - защита от нестрокового doc
        raise SqliteStoreError(f"broken store {path}: {error}") from error
    return bool(payload.get("deleted_at"))


def _select_ids(
    connection: sqlite3.Connection, table: str, column: str, values: Iterable[str]
) -> set[EntityId]:
    """Существующие значения колонки: выборка по `IN`, без полного скана."""
    wanted = list(values)
    if not wanted:
        return set()
    rows = _select_rows(
        connection,
        f"SELECT {column} FROM {table} WHERE {column} IN ({{}})",
        wanted,
    )
    return {EntityId(str(row[0])) for row in rows}


def _select_rows(
    connection: sqlite3.Connection, sql: str, values: Sequence[Any]
) -> list[tuple[Any, ...]]:
    placeholders = ",".join("?" * len(values))
    return connection.execute(sql.format(placeholders), values).fetchall()


def _facet_rows(entity: Entity) -> list[tuple[str, str, float | None, str]]:
    """Строки индекса фасетов: ссылки уходят в `ref_index`, а не сюда."""
    rows: list[tuple[str, str, float | None, str]] = []
    for axis, values in entity.facets.items():
        for value in values:
            if isinstance(value, ReferenceValue):
                continue
            if isinstance(value, MeasurableValue):
                rows.append((axis, repr(value.value), value.value, str(entity.id)))
            else:
                rows.append((axis, str(value.value), None, str(entity.id)))
    return rows


def _ref_rows(entity: Entity) -> list[tuple[str, str, str]]:
    """Строки ссылочного индекса: одна на пару «ось — цель»."""
    rows: list[tuple[str, str, str]] = []
    for axis, values in entity.facets.items():
        for value in values:
            if isinstance(value, ReferenceValue):
                rows.append((axis, str(value.target), str(entity.id)))
    return rows


def _condition(schema: StorageSchema, condition: Filter) -> tuple[str, list[Any]]:
    """SQL-условие на `e.id` для одного фильтра (DOMAIN §4 и §5).

    Отсутствие значения трактуется по обязательной таблице DOMAIN §5:
    `none_of` и `not_exists` истинны, остальные операторы ложны.
    """
    if condition.op is Operator.SCALAR_EQ:
        path = f'$.scalars."{condition.axis}"'
        return "json_extract(e.doc, ?) = ?", [path, str(condition.values[0])]

    axis = schema.axis(condition.axis)
    # validate_query уже проверил существование оси и совместимость оператора.
    assert axis is not None
    table, column = (
        ("ref_index", "target_id") if axis.kind is AxisKind.REFERENCE else ("facet_index", "value")
    )

    if condition.op is Operator.EXISTS:
        return (
            f"EXISTS (SELECT 1 FROM {table} i WHERE i.entity_id = e.id AND i.axis = ?)",
            [condition.axis],
        )
    if condition.op is Operator.NOT_EXISTS:
        return (
            f"NOT EXISTS (SELECT 1 FROM {table} i WHERE i.entity_id = e.id AND i.axis = ?)",
            [condition.axis],
        )

    values = [str(value) for value in condition.values]
    if condition.op is Operator.CONTAINS:
        return (
            (
                f"EXISTS (SELECT 1 FROM {table} i "
                f"WHERE i.entity_id = e.id AND i.axis = ? AND i.{column} = ?)"
            ),
            [condition.axis, values[0]],
        )
    if condition.op is Operator.ANY_OF:
        placeholders = ",".join("?" * len(values))
        return (
            (
                f"EXISTS (SELECT 1 FROM {table} i "
                f"WHERE i.entity_id = e.id AND i.axis = ? AND i.{column} IN ({placeholders}))"
            ),
            [condition.axis, *values],
        )
    if condition.op is Operator.NONE_OF:
        placeholders = ",".join("?" * len(values))
        return (
            (
                f"NOT EXISTS (SELECT 1 FROM {table} i "
                f"WHERE i.entity_id = e.id AND i.axis = ? AND i.{column} IN ({placeholders}))"
            ),
            [condition.axis, *values],
        )

    numbers = [float(value) for value in condition.values]
    if condition.op is Operator.BETWEEN:
        low, high = sorted(numbers)
        return (
            (
                "EXISTS (SELECT 1 FROM facet_index i "
                "WHERE i.entity_id = e.id AND i.axis = ? AND i.num BETWEEN ? AND ?)"
            ),
            [condition.axis, low, high],
        )
    operator = _NUMERIC_SQL[condition.op]
    return (
        (
            f"EXISTS (SELECT 1 FROM facet_index i "
            f"WHERE i.entity_id = e.id AND i.axis = ? AND i.num {operator} ?)"
        ),
        [condition.axis, numbers[0]],
    )


def _artifact_users(connection: sqlite3.Connection, content_hash: str) -> list[str]:
    """Кто ссылается на артефакт (включая tombstone): иначе воскрешение без данных."""
    rows = connection.execute(
        "SELECT DISTINCT e.id FROM entities e, json_each(e.doc, '$.artifacts') a "
        "WHERE json_extract(a.value, '$.content_hash') = ? ORDER BY e.id",
        (content_hash,),
    ).fetchall()
    return [str(row[0]) for row in rows]


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
    "SQLITE_CAPABILITIES",
    "STORE_VERSION",
    "SqliteStore",
    "SqliteStoreError",
    "create",
    "plugin",
]
