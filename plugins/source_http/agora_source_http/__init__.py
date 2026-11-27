"""HTTP JSON как источник объектов AxisLar.

Источник забирает JSON по URL и превращает записи в объекты по тем же
правилам, что SQL-источник: `key` даёт естественный ключ (`entity_id_for`),
`axes`/`measurable`/`reference`/`scalars` раскладывают поля, `multi`
разрешает несколько значений на объект.

Три вещи зафиксированы жёстко:

- `null` (и отсутствующее поле) — **не значение**, а отсутствие: фасет не
  создаётся, объект остаётся разреженным (ADR-0010 ядра);
- `content_hash` считается по каноническому JSON **сопоставленных** полей,
  поэтому лишние поля ответа не выдают себя за изменение объекта;
- раскладка задаётся явно: адаптер не угадывает, что станет осью.

Источник только читает: `GET` без тела, без ретраев и без записи на сервер.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
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
REQUIRES_CONTRACT = ">=6,<7"

#: Вид объекта, если он не задан в настройках.
DEFAULT_KIND = "record"

#: Поле естественного ключа по умолчанию.
DEFAULT_KEY = ("id",)

#: Разделитель частей естественного ключа (unit separator).
KEY_SEPARATOR = "\x1f"

#: Таймаут запроса по умолчанию, секунды.
DEFAULT_TIMEOUT = 10.0

#: Алгоритм хэша содержимого записи.
HASH_ALGO = "sha256-record-json"


class HttpSourceError(RuntimeError):
    """Источник не может прочитать данные: сеть, статус, JSON, форма."""


@dataclass(slots=True)
class HttpSource(IEntitySource):
    """Источник поверх JSON-API."""

    url: str
    source_id: str
    kind: str = DEFAULT_KIND
    key: tuple[str, ...] = DEFAULT_KEY
    axes: tuple[str, ...] = ()
    measurable: tuple[str, ...] = ()
    reference: Mapping[str, str] = field(default_factory=dict)
    scalars: tuple[str, ...] = ()
    multi: Mapping[str, str] = field(default_factory=dict)
    hash_fields: tuple[str, ...] = ()
    items: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return f"http:{self.url} (source_id={self.source_id})"

    async def scan(self) -> AsyncIterator[Entity]:
        self.warnings = []
        payload = await asyncio.to_thread(self._fetch)
        for index, record in enumerate(self._records(payload)):
            entity = self._build(record, index)
            if entity is not None:
                yield entity

    # ---------------------------------------------------------------- чтение

    def _fetch(self) -> object:
        request = urllib.request.Request(self.url, headers=dict(self.headers))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            raise HttpSourceError(f"http {error.code} on {self.url}") from error
        except urllib.error.URLError as error:
            raise HttpSourceError(f"cannot reach {self.url}: {error.reason}") from error
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise HttpSourceError(f"{self.url}: invalid JSON: {error}") from error

    def _records(self, payload: object) -> list[Mapping[str, object]]:
        current: object = payload
        for part in self.items.split(".") if self.items else ():
            if not isinstance(current, Mapping) or part not in current:
                raise HttpSourceError(f"{self.url}: no {self.items!r} in response")
            current = current[part]
        if not isinstance(current, list):
            raise HttpSourceError(f"{self.url}: expected a JSON array of records")
        records: list[Mapping[str, object]] = []
        for index, item in enumerate(current):
            if not isinstance(item, Mapping):
                raise HttpSourceError(f"{self.url}: record {index} is not an object")
            records.append(item)
        return records

    # ------------------------------------------------------------- построение

    def _build(self, record: Mapping[str, object], index: int) -> Entity | None:
        key_parts: list[str] = []
        for name in self.key:
            raw = record.get(name)
            if raw is None:
                self.warnings.append(f"запись {index} пропущена: нет ключевого поля {name!r}")
                return None
            key_parts.append(text(raw))
        key = KEY_SEPARATOR.join(key_parts)

        facets: dict[str, tuple[FacetValue, ...]] = {}
        for name in self.axes:
            values = self._categorical(record, name)
            if values:
                facets[name] = values
        for name in self.measurable:
            value = self._measurable(record, name, key)
            if value is not None:
                facets[name] = (value,)
        for name in self.reference:
            raw = record.get(name)
            if raw is None:
                continue
            facets[name] = (ReferenceValue(target=EntityId(text(raw))),)

        scalars: dict[str, str] = {}
        for name in self.scalars:
            raw = record.get(name)
            if raw is not None:
                scalars[name] = text(raw)

        return Entity(
            id=entity_id_for(self.source_id, key),
            kind=self.kind,
            scalars=scalars,
            facets=facets,
            owners=dict.fromkeys(facets, FacetOwner.SOURCE),
            origin=Origin(
                source_id=self.source_id,
                key=key,
                content_hash=self._content_hash(record),
                hash_algo=HASH_ALGO,
            ),
        )

    def _categorical(self, record: Mapping[str, object], name: str) -> tuple[FacetValue, ...]:
        raw = record.get(name)
        if raw is None:
            return ()
        separator = self.multi.get(name)
        if separator is None:
            return (CategoricalValue(value=text(raw)),)
        pieces: Iterable[object] = raw if isinstance(raw, list) else text(raw).split(separator)
        values = [text(piece).strip() for piece in pieces]
        return tuple(CategoricalValue(value=piece) for piece in values if piece)

    def _measurable(self, record: Mapping[str, object], name: str, key: str) -> FacetValue | None:
        raw = record.get(name)
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, str | int | float):
            self.warnings.append(f"{key}: поле {name!r} не число ({raw!r}), фасет пропущен")
            return None
        try:
            number = float(raw)
        except ValueError:
            self.warnings.append(f"{key}: поле {name!r} не число ({raw!r}), фасет пропущен")
            return None
        return MeasurableValue(value=number)

    def _content_hash(self, record: Mapping[str, object]) -> str:
        fields = self.hash_fields or tuple(sorted(self._mapped_fields()))
        payload = {name: record.get(name) for name in fields}
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _mapped_fields(self) -> set[str]:
        return (
            set(self.key)
            | set(self.axes)
            | set(self.measurable)
            | set(self.scalars)
            | set(self.reference)
        )


# ------------------------------------------------------------------- утилиты


def text(value: object) -> str:
    """Значение поля как текст (числа и bool — по `str`, списки — JSON)."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _as_tuple(value: object, *, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list | tuple):
        raise ValueError(f"http source: {name} must be a list of field names")
    return tuple(str(item) for item in value)


def _as_mapping(value: object, *, name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"http source: {name} must be a table")
    return {str(key): str(item) for key, item in value.items()}


def create(options: Mapping[str, Any]) -> HttpSource:
    """Фабрика из настроек плагина (конфиг + CLI)."""
    url = options.get("url")
    source_id = options.get("source_id")
    if not url:
        raise ValueError("http source requires 'url'")
    if not source_id:
        raise ValueError("http source requires 'source_id'")

    axes = _as_tuple(options.get("axes"), name="axes")
    measurable = _as_tuple(options.get("measurable"), name="measurable")
    scalars = _as_tuple(options.get("scalars"), name="scalars")
    reference = _as_mapping(options.get("reference"), name="reference")
    multi = _as_mapping(options.get("multi"), name="multi")

    mapped = list(axes) + list(measurable) + list(scalars) + list(reference)
    duplicated = sorted({name for name in mapped if mapped.count(name) > 1})
    if duplicated:
        raise ValueError("http source: field mapped more than once: " + ", ".join(duplicated))
    unknown_multi = sorted(set(multi) - set(axes))
    if unknown_multi:
        raise ValueError(
            "http source: 'multi' is allowed only for categorical axes: " + ", ".join(unknown_multi)
        )

    timeout = options.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, int | float) or timeout <= 0:
        raise ValueError("http source: 'timeout' must be a positive number")

    return HttpSource(
        url=str(url),
        source_id=str(source_id),
        kind=str(options.get("kind") or DEFAULT_KIND),
        key=_as_tuple(options.get("key"), name="key") or DEFAULT_KEY,
        axes=axes,
        measurable=measurable,
        reference=reference,
        scalars=scalars,
        multi=multi,
        hash_fields=_as_tuple(options.get("hash"), name="hash"),
        items=str(options.get("items") or ""),
        headers=_as_mapping(options.get("headers"), name="headers"),
        timeout=float(timeout),
    )


def plugin() -> dict[str, Any]:
    """Манифест плагина обычными данными (ADR-0013)."""
    return {"requires_contract": REQUIRES_CONTRACT, "factory": create}


__all__ = [
    "DEFAULT_KIND",
    "DEFAULT_KEY",
    "DEFAULT_TIMEOUT",
    "HASH_ALGO",
    "KEY_SEPARATOR",
    "REQUIRES_CONTRACT",
    "HttpSource",
    "HttpSourceError",
    "create",
    "plugin",
    "text",
]
