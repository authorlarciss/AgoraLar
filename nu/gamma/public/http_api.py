"""HTTP-интерфейс продукта: маршрутизация отдельно от транспорта.

Здесь нет доменной логики: разбор JSON → вызов контрактов ядра → JSON.
Транспорт (сокеты) живёт в `http_server.py`, поэтому маршруты тестируются
без сети.
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4

from axislar_core.contracts import (
    AxisDefinition,
    DomainError,
    Entity,
    EntityId,
    ErrorCode,
    Query,
    ScalarDefinition,
    StorageQueryError,
    make_artifact,
    tombstone_entity,
)

from nu.psi.contracts.beta import (
    FacetPatch,
    JournalError,
    bulk_patch,
    entity_links,
    journaled_patch,
    list_objects,
    make_entry,
    store_stats,
    undo_last,
)
from nu.psi.contracts.iota import ParseError, parse_filter
from nu.psi.contracts.mu import CONTRACT_VERSION, KERNEL_CONTRACT_VERSION, __version__
from nu.psi.product import Product

#: (status, payload[, headers]) — ответ маршрута.
Response = tuple[int, dict[str, Any]] | tuple[int, dict[str, Any], dict[str, str]]


#: Путь, который остаётся открытым при включённом токене: пробы живости.
OPEN_PATH = "/health"

#: Каталог статики UI (лежит рядом с модулем, работает при любом имени пакета).
STATIC_DIR = Path(__file__).parent / "static"

#: Типы файлов статики; всё остальное не отдаётся.
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


class HttpApi:
    """Маршруты продукта поверх `Product`.

    `token=None` — режим «только локально»: проверок нет, и это допустимо
    лишь на loopback (ADR-0014, ADR-0017). С токеном каждый запрос, кроме
    `/health`, требует `Authorization: Bearer <token>`.
    """

    def __init__(self, product: Product, token: str | None = None) -> None:
        self.product = product
        self.token = token or None

    async def handle(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        """Обработать один запрос."""
        route = path.split("?", 1)[0].rstrip("/") or "/"
        parts = [part for part in route.split("/") if part]

        if self.token is not None and route != OPEN_PATH and not self._authorized(headers):
            return 401, {"error": "unauthorized"}

        if route == "/api/v1" or route.startswith("/api/v1/"):
            return await self._handle_v1(method, parts[2:], path.partition("?")[2], body, headers)

        if route == "/health" and method == "GET":
            return 200, {
                "product": __version__,
                "product_contract": CONTRACT_VERSION,
                "kernel_contract": KERNEL_CONTRACT_VERSION,
                "store": self.product.store,
            }

        if route == "/schema" and method == "GET":
            schema = await self.product.core.schema()
            return 200, {"schema": _dump(schema)}

        if route == "/axes" and method == "POST":
            return await self._add_axis(body)

        if route == "/scalars" and method == "POST":
            return await self._add_scalar(body)

        if route == "/query" and method == "POST":
            return await self._query(body)

        if route == "/plugins" and method == "GET":
            return 200, {
                "available": [
                    {
                        "name": manifest.name,
                        "kind": manifest.kind.value,
                        "requires_contract": manifest.requires_contract,
                    }
                    for manifest in self.product.registry.available
                ],
                "rejected": [
                    {"name": item.name, "kind": item.kind, "reason": item.reason}
                    for item in self.product.registry.rejected
                ],
            }

        if route == "/artifacts":
            if method == "GET":
                return await self._list_artifacts()
            if method == "POST":
                return await self._put_artifact(body)

        if len(parts) == 2 and parts[0] == "artifacts":
            content_hash = parts[1]
            if method == "GET":
                return await self._get_artifact(content_hash)
            if method == "DELETE":
                return await self._delete_artifact(content_hash)

        if len(parts) == 2 and parts[0] == "objects":
            entity_id = parts[1]
            if method == "GET":
                return await self._get_entity(entity_id)
            if method == "PUT":
                return await self._put_entity(entity_id, body)

        if not _known(route, parts):
            return 404, {"error": "not found", "path": route}
        return 405, {"error": "method not allowed", "method": method, "path": route}

    # ------------------------------------------------------ статика UI

    def static_asset(self, path: str) -> tuple[bytes, str] | None:
        """Файл интерфейса по пути; `None` — не статика.

        Отдаём только известные имена из каталога статики: путь из запроса
        не участвует в сборке имени файла, обход каталога невозможен.
        """
        route = path.split("?", 1)[0]
        name = "index.html" if route in {"", "/"} else route.removeprefix("/static/")
        if route != "/" and not route.startswith("/static/"):
            return None
        if not name or "/" in name or name.startswith("."):
            return None
        target = STATIC_DIR / name
        if not target.is_file():
            return None
        content_type = STATIC_TYPES.get(target.suffix)
        if content_type is None:
            return None
        return target.read_bytes(), content_type

    # ------------------------------------------------------------- API v1

    async def _handle_v1(
        self,
        method: str,
        parts: list[str],
        query_text: str,
        body: bytes | None,
        headers: Mapping[str, str] | None,
    ) -> Response:
        """Версионированный API для клиентов (UI). Старые маршруты не трогаем."""
        params = parse_qs(query_text)
        if not parts:
            return 200, {
                "api": "v1",
                "product": __version__,
                "product_contract": CONTRACT_VERSION,
                "kernel_contract": KERNEL_CONTRACT_VERSION,
            }
        head = parts[0]
        if head == "objects" and len(parts) == 1 and method == "GET":
            return await self._v1_objects(params)
        if head == "objects" and len(parts) == 1 and method == "POST":
            return await self._v1_create(body)
        if head == "objects" and len(parts) == 2 and method == "GET":
            return await self._v1_object(parts[1], headers)
        if head == "objects" and len(parts) == 2 and method == "DELETE":
            return await self._v1_delete(parts[1], headers)
        if head == "objects" and len(parts) == 2 and method == "PUT":
            return await self._v1_put_object(parts[1], body, headers)
        if head == "patch" and len(parts) == 1 and method == "POST":
            return await self._v1_patch(body)
        if head == "undo" and len(parts) == 1 and method == "POST":
            return await self._v1_undo()
        if head == "objects" and len(parts) == 3 and parts[2] == "links" and method == "GET":
            return await self._v1_links(parts[1], params)
        if head == "stats" and len(parts) == 1 and method == "GET":
            return await self._v1_stats()
        if head == "artifacts" and len(parts) == 2 and method == "GET":
            return await self._get_artifact(parts[1])
        return 404, _v1_error("not_found", f"no v1 route {'/'.join(parts)}")

    async def _v1_objects(self, params: dict[str, list[str]]) -> Response:
        try:
            filters = tuple(parse_filter(token) for token in params.get("filter", []))
        except ParseError as error:
            return 400, _v1_error("invalid_filter", str(error))
        offset = _int_param(params, "offset", 0)
        limit = min(_int_param(params, "limit", 50), 200)
        if offset < 0 or limit <= 0:
            return 400, _v1_error("invalid_pagination", "offset >= 0 and 0 < limit <= 200")

        schema = await self.product.core.schema()
        for condition in filters:
            if schema.axis(condition.axis) is None and schema.scalar(condition.axis) is None:
                return 400, _v1_error(
                    "unknown_axis", f"axis {condition.axis!r} is not declared", axis=condition.axis
                )
        try:
            page = await list_objects(
                self.product.core.storage,
                Query(all=filters),
                offset=offset,
                limit=limit,
                title_scalar=params.get("title", ["title"])[0],
            )
        except StorageQueryError as error:
            return 422, _v1_error("invalid_query", str(error))
        return 200, {
            "objects": [{"id": row.id, "kind": row.kind, "title": row.title} for row in page.rows],
            "total": page.total,
            "offset": page.offset,
            "limit": page.limit,
        }

    async def _v1_object(self, entity_id: str, headers: Mapping[str, str] | None) -> Response:
        entity = await self.product.core.get(EntityId(entity_id))
        if entity is None:
            return 404, _v1_error("not_found", f"object {entity_id!r} not found")
        payload = entity.model_dump_json()
        etag = _etag(entity)
        presented = _header(headers, "If-None-Match")
        if presented == etag:
            return 304, {}, {"ETag": etag}
        return 200, {"entity": json.loads(payload)}, {"ETag": etag}

    async def _v1_create(self, body: bytes | None) -> Response:
        """Создать объект. `expected_rev=0` — «объекта быть не должно»."""
        try:
            payload = _json(body)
        except ValueError as error:
            return 400, _v1_error("invalid_body", str(error))
        payload.setdefault("id", uuid4().hex)
        try:
            entity = Entity.model_validate(payload)
        except ValueError as error:
            return 400, _v1_error("invalid_entity", str(error))
        errors = await self.product.core.storage.upsert(entity, expected_rev=0)
        if errors:
            if any(error.code is ErrorCode.CONFLICT for error in errors):
                return await self._conflict(str(entity.id))
            return 422, {"errors": [_dump(error) for error in errors]}
        saved = await self.product.core.get(entity.id)
        if saved is None:  # pragma: no cover - запись прошла, объект есть
            raise RuntimeError(f"object {entity.id!r} disappeared after write")
        _journal(self.product, make_entry("create", None, saved))
        return 201, {"id": str(saved.id), "rev": saved.rev}, {"ETag": _etag(saved)}

    async def _v1_delete(
        self,
        entity_id: str,
        headers: Mapping[str, str] | None,
    ) -> Response:
        """Удалить объект (tombstone) по политике ядра: отказ при живых ссылках."""
        expected, failure = await self._if_match(entity_id, headers)
        if failure is not None:
            return failure
        before = await self.product.core.get(EntityId(entity_id))
        errors = await tombstone_entity(
            self.product.core.storage, EntityId(entity_id), expected_rev=expected
        )
        if errors:
            if any(error.code is ErrorCode.REFERENCE_IN_USE for error in errors):
                return 409, {
                    "error": {
                        "code": "reference_in_use",
                        "message": errors[0].detail,
                        "axis": errors[0].axis,
                        "entity_id": entity_id,
                    }
                }
            if any(error.code is ErrorCode.CONFLICT for error in errors):
                return await self._conflict(entity_id)
            return 422, {"errors": [_dump(error) for error in errors]}
        # `get` не показывает tombstone, поэтому снимок «после» берём из
        # выборки с удалёнными: в журнале нужна ревизия, которую поставила
        # запись, иначе откат упрётся в CONFLICT.
        stored = await self.product.core.storage.list_entities(include_deleted=True)
        after = next((item for item in stored if str(item.id) == entity_id), None)
        _journal(self.product, make_entry("delete", before, after))
        return 200, {"deleted": entity_id}

    async def _v1_put_object(
        self,
        entity_id: str,
        body: bytes | None,
        headers: Mapping[str, str] | None,
    ) -> Response:
        """Записать объект; `If-Match` превращается в `expected_rev` (ADR-0021)."""
        try:
            payload = _json(body)
        except ValueError as error:
            return 400, _v1_error("invalid_body", str(error))
        payload["id"] = entity_id
        try:
            entity = Entity.model_validate(payload)
        except ValueError as error:
            return 400, _v1_error("invalid_entity", str(error), entity_id=entity_id)

        expected, failure = await self._if_match(entity_id, headers)
        if failure is not None:
            return failure

        errors = await self.product.core.storage.upsert(entity, expected_rev=expected)
        if errors:
            if any(error.code is ErrorCode.CONFLICT for error in errors):
                return await self._conflict(entity_id)
            return 422, {"errors": [_dump(error) for error in errors]}
        saved = await self.product.core.get(EntityId(entity_id))
        if saved is None:  # pragma: no cover - запись прошла, объект есть
            raise RuntimeError(f"object {entity_id!r} disappeared after write")
        etag = _etag(saved)
        return 200, {"id": str(saved.id), "rev": saved.rev}, {"ETag": etag}

    async def _v1_patch(self, body: bytes | None) -> Response:
        """Массовая правка фасетов: те же поля, что у `bulk_patch`."""
        try:
            payload = _json(body)
        except ValueError as error:
            return 400, _v1_error("invalid_body", str(error))

        try:
            patches = _patches(payload.get("ops"))
        except ValueError as error:
            return 400, _v1_error("invalid_ops", str(error))

        try:
            ids = await self._patch_targets(payload)
        except ValueError as error:
            return 400, _v1_error("invalid_target", str(error))
        if ids is None:
            return self._patch_route_error(payload)

        expected_raw = payload.get("expected_revs") or {}
        if not isinstance(expected_raw, dict):
            return 400, _v1_error("invalid_expected_revs", "expected_revs must be an object")
        expected: dict[str, int] = {}
        for key, value in expected_raw.items():
            if not isinstance(value, int):
                return 400, _v1_error(
                    "invalid_expected_revs", f"revision for {key!r} must be an integer"
                )
            expected[str(key)] = value

        force = bool(payload.get("force", False))
        storage = self.product.core.storage
        journal = self.product.journal
        if journal is None:
            result = await bulk_patch(
                storage, ids, patches, expected_revs=expected or None, force=force
            )
        else:
            result = await journaled_patch(
                storage,
                journal,
                ids,
                patches,
                expected_revs=expected or None,
                force=force,
            )
        return 200, {
            "changed": [{"id": key, "rev": rev} for key, rev in result.changed],
            "conflicts": list(result.conflicts),
            "skipped": [{"id": key, "axis": axis} for key, axis in result.skipped],
            "errors": [_dump(error) for error in result.errors],
        }

    async def _v1_undo(self) -> Response:
        """Откат последней журнальной правки (кнопка «отменить» в UI)."""
        journal = self.product.journal
        if journal is None:
            return 409, _v1_error("journal_unavailable", "у этого хранилища нет журнала (memory)")
        try:
            entry = await undo_last(self.product.core.storage, journal)
        except JournalError as error:
            return 409, _v1_error("journal_conflict", str(error))
        if entry is None:
            return 404, _v1_error("journal_empty", "откатывать нечего")
        return 200, {"undone": {"kind": entry.kind, "entity_id": entry.entity_id}}

    def _patch_route_error(self, payload: dict[str, Any]) -> Response:
        """`ids` и `filter` взаимоисключающие, и один из них обязателен."""
        has_ids = payload.get("ids") is not None
        has_filter = payload.get("filter") is not None
        if has_ids and has_filter:
            return 400, _v1_error("invalid_target", "use either ids or filter, not both")
        return 400, _v1_error("invalid_target", "either ids or filter is required")

    async def _patch_targets(self, payload: dict[str, Any]) -> list[EntityId] | None:
        """Кого правим: список `id` или результат фильтра."""
        has_ids = payload.get("ids") is not None
        has_filter = payload.get("filter") is not None
        if has_ids == has_filter:
            return None
        if has_ids:
            raw_ids = payload["ids"]
            if not isinstance(raw_ids, list) or not raw_ids:
                raise ValueError("ids must be a non-empty list")
            return [EntityId(str(item)) for item in raw_ids]
        raw_filter = payload["filter"]
        if not isinstance(raw_filter, list):
            raise ValueError("filter must be a list of axis:op=value tokens")
        filters = tuple(parse_filter(str(token)) for token in raw_filter)
        schema = await self.product.core.schema()
        for condition in filters:
            if schema.axis(condition.axis) is None and schema.scalar(condition.axis) is None:
                raise ValueError(f"axis {condition.axis!r} is not declared")
        try:
            result = await self.product.core.query(Query(all=filters))
        except StorageQueryError as error:
            raise ValueError(str(error)) from error
        return [EntityId(item) for item in result.ids]

    async def _if_match(
        self,
        entity_id: str,
        headers: Mapping[str, str] | None,
    ) -> tuple[int | None, Response | None]:
        """`If-Match` → `expected_rev`. Второй элемент — готовый отказ."""
        raw = _header(headers, "If-Match")
        if raw is None:
            return None, None
        value = raw.strip()
        if value == "*":
            current = await self.product.core.get(EntityId(entity_id))
            if current is None:
                return None, (
                    404,
                    _v1_error("not_found", f"object {entity_id!r} not found", entity_id=entity_id),
                )
            return current.rev, None
        rev = _parse_etag(value)
        if rev is None:
            return None, (
                400,
                _v1_error(
                    "invalid_if_match",
                    f'expected ETag like "r3" or *, got {value!r}',
                    entity_id=entity_id,
                ),
            )
        return rev, None

    async def _conflict(self, entity_id: str) -> Response:
        """409 с текущим состоянием: редактор показывает «моё/чужое» (ADR-0021)."""
        current = await self.product.core.get(EntityId(entity_id))
        body: dict[str, Any] = _v1_error(
            "conflict",
            "object was changed by someone else",
            entity_id=entity_id,
        )
        if current is not None:
            body["current"] = {
                "etag": _etag(current),
                "entity": json.loads(current.model_dump_json()),
            }
        return 409, body

    async def _v1_links(self, entity_id: str, params: dict[str, list[str]]) -> Response:
        include_deleted = params.get("include_deleted", ["0"])[0] in {"1", "true", "yes"}
        rows = await entity_links(
            self.product.core.storage,
            EntityId(entity_id),
            include_deleted=include_deleted,
        )
        return 200, {
            "incoming": [
                {"axis": row.axis, "id": row.entity_id, "title": row.title}
                for row in rows
                if row.direction == "in"
            ],
            "outgoing": [
                {"axis": row.axis, "id": row.entity_id, "title": row.title}
                for row in rows
                if row.direction == "out"
            ],
        }

    async def _v1_stats(self) -> Response:
        schema = await self.product.core.schema()
        entities = await self.product.core.storage.list_entities(include_deleted=True)
        artifacts = await self.product.core.storage.list_artifacts()
        stats = store_stats(self.product.store, schema, entities, artifacts)
        return 200, stats.as_dict()

    def _authorized(self, headers: Mapping[str, str] | None) -> bool:
        """Сверить bearer-токен без утечки длины/префикса (`compare_digest`)."""
        if headers is None:
            return False
        presented = headers.get("Authorization") or headers.get("authorization") or ""
        scheme, _, value = presented.partition(" ")
        if scheme.lower() != "bearer" or not value:
            return False
        assert self.token is not None  # вызывается только при включённом токене
        return secrets.compare_digest(value.strip(), self.token)

    # ----------------------------------------------------------- обработчики

    async def _add_axis(self, body: bytes | None) -> Response:
        try:
            axis = AxisDefinition.model_validate(_json(body))
        except ValueError as error:
            return 400, {"error": f"invalid axis: {error}"}

        errors = await self.product.core.add_axis(axis)
        if errors:
            return 422, {"errors": [_dump(error) for error in errors]}
        return 201, {"axis": _dump(axis)}

    async def _add_scalar(self, body: bytes | None) -> Response:
        try:
            scalar = ScalarDefinition.model_validate(_json(body))
        except ValueError as error:
            return 400, {"error": f"invalid scalar: {error}"}

        errors = await self.product.core.add_scalar(scalar)
        if errors:
            return 422, {"errors": [_dump(error) for error in errors]}
        return 201, {"scalar": _dump(scalar)}

    async def _get_entity(self, entity_id: str) -> Response:
        entity = await self.product.core.get(EntityId(entity_id))
        if entity is None:
            return 404, {"error": f"object {entity_id!r} not found"}
        return 200, {"entity": json.loads(entity.model_dump_json())}

    async def _put_entity(self, entity_id: str, body: bytes | None) -> Response:
        payload = _json(body)
        payload["id"] = entity_id
        try:
            entity = Entity.model_validate(payload)
        except ValueError as error:
            return 400, {"error": f"invalid entity: {error}"}

        errors = await self.product.core.upsert(entity)
        if errors:
            return 422, {"errors": [_dump(error) for error in errors]}
        return 201, {"id": str(entity.id)}

    # ----------------------------------------------------------- артефакты

    async def _list_artifacts(self) -> Response:
        references = await self.product.core.list_artifacts()
        return 200, {
            "artifacts": [
                {
                    "content_hash": reference.content_hash,
                    "media_type": reference.media_type,
                    "size": reference.size,
                }
                for reference in references
            ]
        }

    async def _put_artifact(self, body: bytes | None) -> Response:
        payload = _json(body)
        media_type = payload.get("media_type")
        encoded = payload.get("data")
        if not isinstance(media_type, str) or not media_type:
            return 400, {"error": "media_type is required"}
        if not isinstance(encoded, str):
            return 400, {"error": "data must be a base64 string"}
        try:
            data = base64.b64decode(encoded, validate=True)
            artifact = make_artifact(data, media_type)
        except (binascii.Error, ValueError) as error:
            return 400, {"error": f"invalid artifact: {error}"}

        errors = await self.product.core.put_artifact(artifact)
        if errors:
            return 422, {"errors": [_dump(error) for error in errors]}
        return 201, {
            "content_hash": artifact.content_hash,
            "media_type": artifact.media_type,
            "size": artifact.size,
        }

    async def _get_artifact(self, content_hash: str) -> Response:
        artifact = await self.product.core.get_artifact(content_hash)
        if artifact is None:
            return 404, {"error": f"artifact {content_hash!r} not found"}
        return 200, {
            "artifact": {
                "content_hash": artifact.content_hash,
                "media_type": artifact.media_type,
                "size": artifact.size,
                "data": base64.b64encode(artifact.data).decode("ascii"),
            }
        }

    async def _delete_artifact(self, content_hash: str) -> Response:
        errors = await self.product.core.delete_artifact(content_hash)
        if errors:
            return 409, {"errors": [_dump(error) for error in errors]}
        return 200, {"deleted": content_hash}

    async def _query(self, body: bytes | None) -> Response:
        try:
            query = Query.model_validate(_json(body))
        except ValueError as error:
            return 400, {"error": f"invalid query: {error}"}

        try:
            result = await self.product.core.query(query)
        except RuntimeError as error:  # плагины бэкендов
            return 422, {"error": str(error)}
        return 200, json.loads(result.model_dump_json())


def _journal(product: Product, entry: Any) -> None:
    """Записать действие в журнал, если хранилище его ведёт."""
    if product.journal is not None:
        product.journal.append([entry])


#: ETag в v1 — ревизия объекта: `"r5"` (ADR-0021). Хеш тела не годится:
#: `If-Match` обязан давать `expected_rev` без разбора документа.
def _etag(entity: Entity) -> str:
    return f'"r{entity.rev}"'


def _parse_etag(value: str) -> int | None:
    """`"r5"` / `W/"r5"` → 5; всё остальное → `None`."""
    text = value.strip()
    if text.startswith("W/"):
        text = text[2:]
    if len(text) < 4 or text[0] != '"' or text[-1] != '"' or text[1] != "r":
        return None
    try:
        return int(text[2:-1])
    except ValueError:
        return None


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    """Заголовок без учёта регистра: клиенты присылают по-разному."""
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _patches(raw: Any) -> tuple[FacetPatch, ...]:
    """Тело `ops` → операции `bulk_patch`."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("ops must be a non-empty list")
    patches: list[FacetPatch] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each op must be an object with axis, mode, values")
        axis = item.get("axis")
        mode = item.get("mode")
        values = item.get("values") or []
        if not isinstance(axis, str) or not axis:
            raise ValueError("op.axis must be a non-empty string")
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"op.values for {axis!r} must be a list of strings")
        try:
            patches.append(FacetPatch(axis=axis, mode=str(mode), values=tuple(values)))
        except ValueError as error:
            raise ValueError(str(error)) from error
    return tuple(patches)


def _v1_error(code: str, message: str, **context: Any) -> dict[str, Any]:
    """Единая форма ошибки v1: код машинно-читаемый, контекст — поля."""
    error: dict[str, Any] = {"code": code, "message": message}
    error.update({key: value for key, value in context.items() if value is not None})
    return {"error": error}


def _int_param(params: dict[str, list[str]], name: str, default: int) -> int:
    raw = params.get(name, [str(default)])[0]
    try:
        return int(raw)
    except ValueError:
        return default


def _json(body: bytes | None) -> dict[str, Any]:
    if not body:
        raise ValueError("request body is required")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def _dump(value: Any) -> dict[str, Any]:
    if isinstance(value, DomainError):
        return {"code": value.code.value, "detail": value.detail, "axis": value.axis}
    dumped: Any = json.loads(value.model_dump_json())
    return dict(dumped)


def _known(route: str, parts: list[str]) -> bool:
    """Существует ли путь при другом методе."""
    known_paths = {
        "/health",
        "/schema",
        "/axes",
        "/scalars",
        "/query",
        "/plugins",
        "/artifacts",
    }
    if route in known_paths:
        return True
    return len(parts) == 2 and parts[0] in {"objects", "artifacts"}


__all__ = ["HttpApi", "Response"]
