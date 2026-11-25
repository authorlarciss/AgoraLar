"""SQL (sqlite3, только чтение) как источник объектов AxisLar.

Таблица или `SELECT` превращаются в объекты: колонки ключа дают
естественный ключ (`entity_id_for`), колонки-оси — фасеты, колонки-скаляры
— текстовые поля. `NULL` — не значение, а **отсутствие**: фасет не
создаётся (ADR-0010 ядра), поэтому разреженность источника сохраняется как
есть, без «дефолтов» и пустых строк.

Раскладка колонок задаётся явно. Продукт не угадывает, что станет осью, а
что скаляром: адаптер сообщает, а не решает молча (ADR-0016 ядра).
Рассогласования (пустой ключ, нечисловое значение у `measurable`) попадают
в `warnings`; строка остаётся импортируемой, если у неё есть ключ.

`content_hash` считается по каноническому JSON значений тех колонок,
которые попали в объект, — этого хватает синхронизации, чтобы отличить
изменение данных от переименования (ADR-0016/0017 ядра).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from axislar_core.contracts import (
    CategoricalValue,
    Entity,
    EntityId,
    FacetOwner,
    FacetValue,
    IEntitySource,
    MeasurableValue,
    Origin,
    ReferenceValue,
    entity_id_for,
)

#: Контракт ядра, с которым совместим плагин.
REQUIRES_CONTRACT = ">=4,<5"

#: Вид объекта, если он не задан в настройках.
DEFAULT_KIND = "row"

#: Колонка (или колонки) естественного ключа по умолчанию.
DEFAULT_KEY = ("id",)

#: Разделитель частей естественного ключа (unit separator).
KEY_SEPARATOR = "\x1f"

#: Сколько строк забирать из курсора за раз.
DEFAULT_BATCH = 500

#: Алгоритм хэша содержимого строки.
HASH_ALGO = "sha256-row-json"


class SqlSourceError(RuntimeError):
    """Источник не может прочитать данные: файл, SQL, отсутствие колонок."""


@dataclass(slots=True)
class SqlSource(IEntitySource):
    """Источник поверх таблицы или запроса sqlite."""

    path: Path
    source_id: str
    table: str | None = None
    query: str | None = None
    kind: str = DEFAULT_KIND
    key: tuple[str, ...] = DEFAULT_KEY
    axes: tuple[str, ...] = ()
    measurable: tuple[str, ...] = ()
    reference: Mapping[str, str] = field(default_factory=dict)
    scalars: tuple[str, ...] = ()
    multi: Mapping[str, str] = field(default_factory=dict)
    hash_columns: tuple[str, ...] = ()
    batch: int = DEFAULT_BATCH
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        where = f"table={self.table}" if self.table is not None else f"query={self.query}"
        return f"sql:{self.path} ({where}, source_id={self.source_id})"

    async def scan(self) -> AsyncIterator[Entity]:
        self.warnings = []
        string = self._statement()
        try:
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        except sqlite3.Error as error:  # pragma: no cover - зависит от sqlite
            raise SqlSourceError(f"cannot open sqlite {self.path}: {error}") from error
        try:
            connection.row_factory = sqlite3.Row
            cursor = connection.execute(string)
            self._check_columns(cursor.description)
            while True:
                rows = cursor.fetchmany(self.batch)
                if not rows:
                    return
                for row in rows:
                    entity = self._build(row)
                    if entity is not None:
                        yield entity
        except sqlite3.Error as error:
            raise SqlSourceError(f"sqlite error on {self.describe()}: {error}") from error
        finally:
            connection.close()

    # ------------------------------------------------------------- построение

    def _statement(self) -> str:
        if self.query is not None:
            return self.query
        assert self.table is not None  # гарантирует create()
        columns = ", ".join(f'"{name}"' for name in self.key)
        return f'SELECT * FROM "{self.table}" ORDER BY {columns}'

    def _check_columns(self, description: Iterable[Any] | None) -> None:
        available = {str(item[0]) for item in description or ()}
        required = self._mapped_columns()
        missing = sorted(required - available)
        if missing:
            raise SqlSourceError(
                f"{self.describe()}: нет колонок {', '.join(missing)}; доступны: "
                f"{', '.join(sorted(available)) or '<нет>'}"
            )

    def _mapped_columns(self) -> set[str]:
        columns = set(self.key) | set(self.axes) | set(self.measurable) | set(self.scalars)
        columns |= set(self.reference)
        return columns

    def _build(self, row: sqlite3.Row) -> Entity | None:
        key_parts: list[str] = []
        for column in self.key:
            raw = row[column]
            if raw is None:
                self.warnings.append(f"строка пропущена: ключевая колонка {column!r} пуста")
                return None
            key_parts.append(text(raw))
        key = KEY_SEPARATOR.join(key_parts)

        facets: dict[str, tuple[FacetValue, ...]] = {}
        for column in self.axes:
            values = self._categorical(row, column)
            if values:
                facets[column] = values
        for column in self.measurable:
            value = self._measurable(row, column, key)
            if value is not None:
                facets[column] = (value,)
        for column in self.reference:
            raw = row[column]
            if raw is None:
                continue
            facets[column] = (ReferenceValue(target=EntityId(text(raw))),)
        facet_axes = set(facets)

        scalars: dict[str, str] = {}
        for column in self.scalars:
            raw = row[column]
            if raw is not None:
                scalars[column] = text(raw)

        return Entity(
            id=entity_id_for(self.source_id, key),
            kind=self.kind,
            scalars=scalars,
            facets=facets,
            owners=dict.fromkeys(facet_axes, FacetOwner.SOURCE),
            origin=Origin(
                source_id=self.source_id,
                key=key,
                content_hash=self._content_hash(row),
                hash_algo=HASH_ALGO,
            ),
        )

    def _categorical(self, row: sqlite3.Row, column: str) -> tuple[FacetValue, ...]:
        raw = row[column]
        if raw is None:
            return ()
        separator = self.multi.get(column)
        if separator is None:
            return (CategoricalValue(value=text(raw)),)
        pieces = [piece.strip() for piece in text(raw).split(separator)]
        return tuple(CategoricalValue(value=piece) for piece in pieces if piece)

    def _measurable(self, row: sqlite3.Row, column: str, key: str) -> FacetValue | None:
        raw = row[column]
        if raw is None:
            return None
        try:
            number = float(raw)
        except (TypeError, ValueError):
            self.warnings.append(f"{key}: колонка {column!r} не число ({raw!r}), фасет пропущен")
            return None
        return MeasurableValue(value=number)

    def _content_hash(self, row: sqlite3.Row) -> str:
        columns = self.hash_columns or tuple(sorted(self._mapped_columns()))
        payload = {column: canonical(row[column]) for column in columns}
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- утилиты


def text(value: object) -> str:
    """Значение колонки как текст: байты — hex, остальное — `str`."""
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def canonical(value: object) -> object:
    """Значение, пригодное для канонического JSON (стабильный хэш)."""
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _as_tuple(value: object, *, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list | tuple):
        raise ValueError(f"sql source: {name} must be a list of column names")
    return tuple(str(item) for item in value)


def _as_mapping(value: object, *, name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"sql source: {name} must be a table")
    return {str(key): str(item) for key, item in value.items()}


def create(options: Mapping[str, Any]) -> SqlSource:
    """Фабрика из настроек плагина (конфиг + CLI)."""
    path = options.get("path")
    source_id = options.get("source_id")
    if not path:
        raise ValueError("sql source requires 'path'")
    if not source_id:
        raise ValueError("sql source requires 'source_id'")

    table = options.get("table")
    query = options.get("query")
    if bool(table) == bool(query):
        raise ValueError("sql source requires exactly one of 'table' or 'query'")

    key = _as_tuple(options.get("key"), name="key") or DEFAULT_KEY
    axes = _as_tuple(options.get("axes"), name="axes")
    measurable = _as_tuple(options.get("measurable"), name="measurable")
    scalars = _as_tuple(options.get("scalars"), name="scalars")
    reference = _as_mapping(options.get("reference"), name="reference")
    multi = _as_mapping(options.get("multi"), name="multi")

    mapped = list(axes) + list(measurable) + list(scalars) + list(reference)
    duplicated = sorted({column for column in mapped if mapped.count(column) > 1})
    if duplicated:
        raise ValueError("sql source: column mapped more than once: " + ", ".join(duplicated))
    unknown_multi = sorted(set(multi) - set(axes))
    if unknown_multi:
        raise ValueError(
            "sql source: 'multi' is allowed only for categorical axes: " + ", ".join(unknown_multi)
        )

    hash_columns = _as_tuple(options.get("hash"), name="hash")
    batch = options.get("batch", DEFAULT_BATCH)
    if not isinstance(batch, int) or batch <= 0:
        raise ValueError("sql source: 'batch' must be a positive integer")

    return SqlSource(
        path=Path(str(path)),
        source_id=str(source_id),
        table=str(table) if table else None,
        query=str(query) if query else None,
        kind=str(options.get("kind") or DEFAULT_KIND),
        key=key,
        axes=axes,
        measurable=measurable,
        reference=reference,
        scalars=scalars,
        multi=multi,
        hash_columns=hash_columns,
        batch=batch,
    )


def plugin() -> dict[str, Any]:
    """Манифест плагина обычными данными (ADR-0013)."""
    return {"requires_contract": REQUIRES_CONTRACT, "factory": create}


__all__ = [
    "DEFAULT_BATCH",
    "DEFAULT_KEY",
    "DEFAULT_KIND",
    "HASH_ALGO",
    "KEY_SEPARATOR",
    "REQUIRES_CONTRACT",
    "SqlSource",
    "SqlSourceError",
    "canonical",
    "create",
    "plugin",
    "text",
]
