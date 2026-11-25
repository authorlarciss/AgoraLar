"""HTTP-маршруты продукта: без сокетов, через HttpApi.handle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nu.gamma.public import GammaResolver, HttpResolver
from nu.gamma.public.http_api import HttpApi
from nu.psi import build_product
from nu.psi.contracts.alpha import Config


def api(tmp_path: Path) -> HttpApi:
    product = build_product(config=Config(store="json", data=tmp_path / "vault.json"))
    return HttpResolver().make_http_api(product)


async def call(
    target: HttpApi, method: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    return await target.handle(method, path, body)


async def test_health(tmp_path: Path) -> None:
    target = api(tmp_path)
    status, payload = await call(target, "GET", "/health")
    assert status == 200
    assert payload["store"] == "json"
    assert payload["kernel_contract"] == "4"


async def test_schema_is_empty_initially(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "GET", "/schema")
    assert status == 200
    assert payload["schema"]["axes"] == []


async def test_add_axis_and_scalar(tmp_path: Path) -> None:
    target = api(tmp_path)
    status, payload = await call(
        target, "POST", "/axes", {"name": "theme", "kind": "categorical", "multi": True}
    )
    assert status == 201, payload
    assert payload["axis"]["name"] == "theme"

    status, _ = await call(target, "POST", "/scalars", {"name": "title", "searchable": True})
    assert status == 201

    status, payload = await call(target, "GET", "/schema")
    assert status == 200
    assert [axis["name"] for axis in payload["schema"]["axes"]] == ["theme"]
    assert [scalar["name"] for scalar in payload["schema"]["scalars"]] == ["title"]


async def test_duplicate_axis_returns_422(tmp_path: Path) -> None:
    target = api(tmp_path)
    await call(target, "POST", "/axes", {"name": "theme", "kind": "categorical"})
    status, payload = await call(target, "POST", "/axes", {"name": "theme", "kind": "categorical"})
    assert status == 422
    assert payload["errors"][0]["code"] == "duplicate_axis"


async def test_invalid_axis_returns_400(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "POST", "/axes", {"name": "Bad Name"})
    assert status == 400
    assert "invalid axis" in payload["error"]


async def test_put_and_get_object(tmp_path: Path) -> None:
    target = api(tmp_path)
    await call(target, "POST", "/axes", {"name": "theme", "kind": "categorical"})
    entity = {
        "kind": "video",
        "facets": {"theme": [{"kind": "categorical", "value": "noir"}]},
    }
    status, payload = await call(target, "PUT", "/objects/e-1", entity)
    assert status == 201, payload

    status, payload = await call(target, "GET", "/objects/e-1")
    assert status == 200
    assert payload["entity"]["id"] == "e-1"
    assert payload["entity"]["facets"]["theme"][0]["value"] == "noir"


async def test_get_missing_object_returns_404(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "GET", "/objects/none")
    assert status == 404
    assert "not found" in payload["error"]


async def test_invalid_entity_returns_400(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "PUT", "/objects/e-1", {"kind": "video", "x": 1})
    assert status == 400
    assert "invalid entity" in payload["error"]


async def test_unknown_axis_on_put_returns_422(tmp_path: Path) -> None:
    entity = {"kind": "video", "facets": {"colour": [{"kind": "categorical", "value": "red"}]}}
    status, payload = await call(api(tmp_path), "PUT", "/objects/e-1", entity)
    assert status == 422
    assert payload["errors"][0]["code"] == "unknown_axis"


async def test_query_returns_ids(tmp_path: Path) -> None:
    target = api(tmp_path)
    await call(target, "POST", "/axes", {"name": "theme", "kind": "categorical"})
    await call(
        target,
        "PUT",
        "/objects/e-1",
        {"kind": "video", "facets": {"theme": [{"kind": "categorical", "value": "noir"}]}},
    )
    status, payload = await call(
        target, "POST", "/query", {"all": [{"axis": "theme", "op": "contains", "values": ["noir"]}]}
    )
    assert status == 200
    assert payload["ids"] == ["e-1"]
    assert payload["total"] == 1


async def test_invalid_query_returns_400(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "POST", "/query", {"all": [{"axis": "x"}]})
    assert status == 400
    assert "invalid query" in payload["error"]


async def test_plugins_listing(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "GET", "/plugins")
    assert status == 200
    names = {item["name"] for item in payload["available"]}
    assert {"filesystem", "json", "sqlite"} <= names


async def test_unknown_path_and_method(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "GET", "/nope")
    assert status == 404
    assert payload["error"] == "not found"

    status, payload = await call(api(tmp_path), "DELETE", "/schema")
    assert status == 405
    assert payload["error"] == "method not allowed"


async def test_missing_and_broken_body(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "POST", "/axes")
    assert status == 400

    broken = await api(tmp_path).handle("POST", "/axes", b"{not json")
    assert broken[0] == 400
    assert "invalid JSON" in broken[1]["error"]

    array = await api(tmp_path).handle("POST", "/axes", b"[]")
    assert array[0] == 400
    assert "JSON object" in array[1]["error"]


def test_resolvers_are_wired() -> None:
    product = build_product()
    api_ = HttpResolver().make_http_api(product)
    assert hasattr(api_, "handle")
    assert callable(GammaResolver().make_cli())


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
async def test_every_method_has_a_route_or_405(tmp_path: Path, method: str) -> None:
    status, _ = await call(api(tmp_path), method, "/unknown")
    assert status in {404, 405}
