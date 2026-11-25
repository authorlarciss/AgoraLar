"""HTTP-интерфейс продукта: маршрутизация отдельно от транспорта.

Здесь нет доменной логики: разбор JSON → вызов контрактов ядра → JSON.
Транспорт (сокеты) живёт в `http_server.py`, поэтому маршруты тестируются
без сети.
"""

from __future__ import annotations

import json
from typing import Any

from axislar_core.contracts import (
    AxisDefinition,
    DomainError,
    Entity,
    EntityId,
    Query,
    ScalarDefinition,
)

from nu.psi.contracts.mu import CONTRACT_VERSION, KERNEL_CONTRACT_VERSION, __version__
from nu.psi.product import Product

#: (status, payload) — ответ маршрута.
Response = tuple[int, dict[str, Any]]


class HttpApi:
    """Маршруты продукта поверх `Product`."""

    def __init__(self, product: Product) -> None:
        self.product = product

    async def handle(self, method: str, path: str, body: bytes | None) -> Response:
        """Обработать один запрос."""
        route = path.split("?", 1)[0].rstrip("/") or "/"
        parts = [part for part in route.split("/") if part]

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

        if len(parts) == 2 and parts[0] == "objects":
            entity_id = parts[1]
            if method == "GET":
                return await self._get_entity(entity_id)
            if method == "PUT":
                return await self._put_entity(entity_id, body)

        if not _known(route, parts):
            return 404, {"error": "not found", "path": route}
        return 405, {"error": "method not allowed", "method": method, "path": route}

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
    known_paths = {"/health", "/schema", "/axes", "/scalars", "/query", "/plugins"}
    if route in known_paths:
        return True
    return len(parts) == 2 and parts[0] == "objects"


__all__ = ["HttpApi", "Response"]
