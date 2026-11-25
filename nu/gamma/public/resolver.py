"""Резолвер модуля gamma: доступ к `_internal/` (CLI) и HTTP-фабрики.

HTTP-маршруты и сервер живут в `public/`: это и есть публичный интерфейс
продукта, скрывать нечего. CLI остаётся в `_internal/` — там разбор
аргументов, и он подключается через lazy import.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from http.server import ThreadingHTTPServer

from nu.gamma.public.http_api import HttpApi
from nu.gamma.public.http_server import make_server, serve
from nu.psi.product import Product

#: Тип точки входа CLI: argv -> код выхода.
CliMain = Callable[[Sequence[str] | None], int]


class GammaResolver:
    """Фабрика CLI продукта."""

    def make_cli(self) -> CliMain:
        """Вернуть точку входа CLI (аргументы -> код выхода)."""
        from nu.gamma._internal.cli import main

        return main


class HttpResolver:
    """Фабрика HTTP-интерфейса продукта."""

    def make_http_api(self, product: Product) -> HttpApi:
        """Маршруты продукта (тестируются без сокетов)."""
        return HttpApi(product)

    def make_http_server(
        self, api: HttpApi, host: str = "127.0.0.1", port: int = 8000
    ) -> ThreadingHTTPServer:
        """Готовый HTTP-сервер (не запущенный)."""
        return make_server(api, host, port)

    def serve(self, api: HttpApi, host: str = "127.0.0.1", port: int = 8000) -> None:
        """Запустить сервер и работать до остановки."""
        serve(api, host, port)


__all__ = ["CliMain", "GammaResolver", "HttpResolver"]
