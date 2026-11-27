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
    port: int,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    headers.update(extra_headers or {})
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


def test_auth_over_real_socket(tmp_path: Path) -> None:
    product = build_product(config=Config(store="json", data=tmp_path / "vault.json"))
    api = HttpResolver().make_http_api(product, token="s3cret")
    httpd = make_server(api, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        assert request(port, "GET", "/schema")[0] == 401
        status, payload = request(
            port, "GET", "/schema", extra_headers={"Authorization": "Bearer s3cret"}
        )
        assert status == 200 and "schema" in payload
        assert request(port, "GET", "/health")[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_unsupported_method_is_405_with_allow(server: tuple[int, threading.Thread]) -> None:
    port, _ = server
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("PATCH", "/health", body=b"{}", headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8"))
    assert response.status == 405
    assert response.getheader("Allow") == "GET, POST, PUT, DELETE"
    assert "not allowed" in payload["error"]


def test_internal_error_does_not_leak_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """500 наружу без текста исключения: детали уходят в лог."""
    from nu.gamma.public import http_server

    product = build_product(config=Config(store="json", data=tmp_path / "v2.json"))
    api = HttpResolver().make_http_api(product)

    async def boom(*args: object, **kwargs: object) -> tuple[int, dict[str, object]]:
        raise RuntimeError("секретный путь /home/kai/token")

    monkeypatch.setattr(api, "handle", boom)
    httpd = http_server.make_server(api, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/health")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert response.status == 500
    assert payload == {"error": "internal error"}
