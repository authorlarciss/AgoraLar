"""HTTP-транспорт: реальный сокет на порту 0."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from nu.gamma.public import HttpResolver
from nu.gamma.public.http_server import make_server
from nu.psi import build_product
from nu.psi.contracts.alpha import Config


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[int, threading.Thread]]:
    product = build_product(config=Config(store="json", data=tmp_path / "vault.json"))
    api = HttpResolver().make_http_api(product)
    httpd = make_server(api, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], thread
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def request(
    port: int, method: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read().decode("utf-8")
        return response.status, json.loads(data)
    finally:
        connection.close()


def test_health_over_http(server: tuple[int, threading.Thread]) -> None:
    port, _ = server
    status, payload = request(port, "GET", "/health")
    assert status == 200
    assert payload["store"] == "json"


def test_full_roundtrip_over_http(server: tuple[int, threading.Thread]) -> None:
    port, _ = server
    assert request(port, "POST", "/axes", {"name": "theme", "kind": "categorical"})[0] == 201
    assert (
        request(
            port,
            "PUT",
            "/objects/e-1",
            {"kind": "video", "facets": {"theme": [{"kind": "categorical", "value": "noir"}]}},
        )[0]
        == 201
    )

    status, payload = request(port, "POST", "/query", {"all": [{"axis": "theme", "op": "exists"}]})
    assert status == 200
    assert payload["ids"] == ["e-1"]


def test_404_and_405_over_http(server: tuple[int, threading.Thread]) -> None:
    port, _ = server
    assert request(port, "GET", "/nope")[0] == 404
    assert request(port, "DELETE", "/schema")[0] == 405


def test_body_size_limit(server: tuple[int, threading.Thread]) -> None:
    port, _ = server
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.putrequest("POST", "/axes")
        connection.putheader("Content-Length", str(9 * 1024 * 1024))
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 413
        response.read()
    finally:
        connection.close()
