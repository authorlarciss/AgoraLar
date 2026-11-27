"""HTTP-транспорт: stdlib-сервер, без внешних зависимостей."""

from __future__ import annotations

import asyncio
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from nu.gamma.public.http_api import HttpApi

#: Размер тела запроса, который сервер готов принять.
MAX_BODY = 8 * 1024 * 1024

#: Методы, которые обслуживает `HttpApi`.
ALLOWED_METHODS = "GET, POST, PUT, DELETE"

_LOG = logging.getLogger("agoralar.http")


class _Handler(BaseHTTPRequestHandler):
    """Один HTTP-обработчик: разбор запроса → `HttpApi.handle` → JSON."""

    api: HttpApi

    def do_GET(self) -> None:  # noqa: N802 - имена методов задаёт stdlib
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802
        self._not_allowed("HEAD")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._not_allowed("OPTIONS")

    def do_PATCH(self) -> None:  # noqa: N802
        self._not_allowed("PATCH")

    def _not_allowed(self, method: str) -> None:
        """Явный 405 вместо 501 из stdlib: неизвестный метод — не «не реализовано»."""
        self._send(405, {"error": f"method {method} not allowed"}, allow=ALLOWED_METHODS)

    def _dispatch(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send(413, {"error": "request body too large"})
            return

        if method == "GET":
            asset = self.api.static_asset(self.path)
            if asset is not None:
                body_bytes, content_type = asset
                self._send_bytes(body_bytes, content_type)
                return

        body = self.rfile.read(length) if length else None
        extra: dict[str, str] = {}
        try:
            result = asyncio.run(self.api.handle(method, self.path, body, dict(self.headers)))
            status, payload = result[0], result[1]
            if len(result) > 2:
                extra = result[2]
        except Exception:  # pragma: no cover - защита от падения сервера
            # Наружу — ничего о внутренностях: текст исключения уходит в лог.
            _LOG.exception("unhandled error in HTTP handler")
            status, payload = 500, {"error": "internal error"}
        self._send(status, payload, headers=extra)

    def _send(
        self,
        status: int,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        allow: str | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if allow:
            self.send_header("Allow", allow)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Тишина в stderr: логи продукта идут через sigma, а не сокет-сервер."""


def make_server(api: HttpApi, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    """Создать сервер (не запуская его) — удобно для тестов на порту 0."""
    handler = type("BoundHandler", (_Handler,), {"api": api})
    return ThreadingHTTPServer((host, port), handler)


def serve(api: HttpApi, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Запустить сервер и работать до остановки."""
    server = make_server(api, host, port)
    host_name, port_number = str(server.server_address[0]), server.server_address[1]
    print(f"agoralar http: http://{host_name}:{port_number}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - ручная остановка
        pass
    finally:
        server.server_close()


__all__ = ["ALLOWED_METHODS", "MAX_BODY", "make_server", "serve"]
