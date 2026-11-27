"""API v1: список объектов, карточка с ETag, связи, статистика, статика."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest
from axislar_core.contracts import AxisDefinition, AxisKind, Entity, ScalarDefinition

from nu.gamma.public import HttpResolver
from nu.gamma.public.http_server import make_server
from nu.psi import build_product
from nu.psi.contracts.alpha import Config


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[int, Any]]:
    product = build_product(config=Config(store="json", data=tmp_path / "vault.json"))
    api = HttpResolver().make_http_api(product)

    import asyncio

    async def seed() -> None:
        assert (
            await product.core.add_axis(
                AxisDefinition(name="tags", kind=AxisKind.CATEGORICAL, multi=True)
            )
            == []
        )
        assert (
            await product.core.add_axis(
                AxisDefinition(name="links", kind=AxisKind.REFERENCE, multi=True, target="note")
            )
            == []
        )
        assert await product.core.add_scalar(ScalarDefinition(name="title")) == []
        errors = await product.core.storage.bulk_upsert(
            [
                Entity.model_validate(
                    {
                        "id": "n1",
                        "kind": "note",
                        "scalars": {"title": "Первая"},
                        "facets": {
                            "tags": [{"kind": "categorical", "value": "noir"}],
                            "links": [{"kind": "reference", "target": "n2"}],
                        },
                    }
                ),
                Entity.model_validate({"id": "n2", "kind": "note", "scalars": {"title": "Вторая"}}),
            ]
        )
        assert errors == []

    asyncio.run(seed())
    httpd = make_server(api, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], api
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def request(
    port: int,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any, dict[str, str]]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    raw = response.read()
    body: Any
    content_type = response.getheader("Content-Type") or ""
    body = json.loads(raw.decode("utf-8")) if "application/json" in content_type and raw else raw
    result_headers = {name.lower(): value for name, value in response.getheaders()}
    return response.status, body, result_headers


def test_v1_root_reports_versions(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1")
    assert status == 200
    assert body["api"] == "v1"
    assert body["kernel_contract"]


def test_objects_list_with_filter_and_pagination(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1/objects?limit=1")
    assert status == 200
    assert body["total"] == 2
    assert len(body["objects"]) == 1
    assert body["limit"] == 1

    status, filtered, _ = request(port, "GET", "/api/v1/objects?filter=tags:contains=noir")
    assert status == 200
    assert [row["id"] for row in filtered["objects"]] == ["n1"]
    assert filtered["objects"][0]["title"] == "Первая"


def test_objects_list_rejects_bad_filter_and_unknown_axis(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1/objects?filter=noop")
    assert status == 400
    assert body["error"]["code"] == "invalid_filter"

    status, body, _ = request(port, "GET", "/api/v1/objects?filter=ghost:exists")
    assert status == 400
    assert body["error"]["code"] == "unknown_axis"
    assert body["error"]["axis"] == "ghost"


def test_object_etag_allows_conditional_get(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, headers = request(port, "GET", "/api/v1/objects/n1")
    assert status == 200
    etag = headers["etag"]
    assert etag.startswith('"')
    assert body["entity"]["id"] == "n1"

    status, body, headers = request(
        port, "GET", "/api/v1/objects/n1", headers={"If-None-Match": etag}
    )
    assert status == 304
    assert body == b""
    assert headers["etag"] == etag


def test_links_split_into_incoming_and_outgoing(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1/objects/n2/links")
    assert status == 200
    assert [row["id"] for row in body["incoming"]] == ["n1"]
    assert body["incoming"][0]["axis"] == "links"
    assert body["outgoing"] == []

    status, body, _ = request(port, "GET", "/api/v1/objects/n1/links")
    assert [row["id"] for row in body["outgoing"]] == ["n2"]


def test_stats_shape(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1/stats")
    assert status == 200
    assert body["objects"] == {"total": 2, "visible": 2, "deleted": 0}
    names = {axis["name"] for axis in body["axes"]}
    assert "tags" in names


def test_unknown_v1_route_is_structured_404(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1/nope")
    assert status == 404
    assert body["error"]["code"] == "not_found"


def test_static_index_and_asset(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, headers = request(port, "GET", "/")
    assert status == 200
    assert "text/html" in headers["content-type"]
    assert b"AgoraLar" in body

    status, body, headers = request(port, "GET", "/static/app.js")
    assert status == 200
    assert "javascript" in headers["content-type"]
    assert b"/api/v1/objects" in body

    status, _, _ = request(port, "GET", "/static/../http_api.py")
    assert status == 404


def test_legacy_routes_still_work(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/objects/n1")
    assert status == 200
    assert body["entity"]["id"] == "n1"
