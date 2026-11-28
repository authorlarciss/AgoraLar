"""Десктоп-оболочка: окно с текущим UI поверх локального API (фаза 5.2).

Эксперимент, а не финальная упаковка: оболочка делает ровно три вещи —
поднимает сервер на свободном порту, открывает окно и отдаёт системный
диалог выбора папки. Всё остальное остаётся HTTP API v1, поэтому замена
оболочки (pywebview → Tauri) не трогает ни продукт, ни интерфейс.

pywebview — необязательная зависимость (`agoralar[desktop]`): импорт ленивый,
без него CLI работает как обычно.
"""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from nu.gamma.public.http_api import HttpApi
from nu.psi.contracts.gamma import HttpResolver
from nu.psi.product import Product

#: Заголовок окна по умолчанию.
TITLE = "AgoraLar"

#: Маркер проекта: папка с ним открывается двойным кликом (ADR-0020).
PROJECT_MARKER = "project.agoralar"


class DesktopUnavailableError(RuntimeError):
    """pywebview не установлен или окно не поднимается."""


class DesktopBridge:
    """Мост JS → Python: только те действия, которых нет в HTTP.

    Путь к папке не приходит из браузера (ADR-0020): его возвращает системный
    диалог, вызванный отсюда. Всё, что можно сделать по HTTP, здесь не
    дублируется.
    """

    def __init__(self) -> None:
        self.window: object | None = None
        self.recent: list[str] = []

    def pick_folder(self) -> str | None:
        """Открыть системный диалог и вернуть путь или `None`."""
        import webview  # ленивый импорт: без extra окно не нужно

        if self.window is None:
            raise DesktopUnavailableError("окно ещё не создано")
        chosen = self.window.create_file_dialog(webview.FOLDER_DIALOG)  # type: ignore[attr-defined]
        if not chosen:
            return None
        path = chosen[0] if isinstance(chosen, (list, tuple)) else str(chosen)
        if path not in self.recent:
            self.recent.append(path)
        return path

    def open_project(self, path: str) -> dict[str, object]:
        """Принять путь от оболочки (не из HTTP) и запомнить как недавний."""
        target = Path(path).expanduser()
        if not target.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}
        text = str(target.resolve())
        if text not in self.recent:
            self.recent.append(text)
        return {
            "ok": True,
            "path": text,
            "has_config": (target / "agoralar.toml").is_file(),
            "has_marker": (target / PROJECT_MARKER).is_file(),
        }


def run_desktop(
    product: Product,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    title: str = TITLE,
) -> None:
    """Поднять сервер и открыть окно; вернуться, когда окно закрыто."""
    try:
        import webview
    except ImportError as error:  # pragma: no cover - зависит от установки extra
        raise DesktopUnavailableError(
            "десктоп-оболочка недоступна: установите agoralar[desktop]"
        ) from error

    api = HttpResolver().make_http_api(product)
    server = webview_server(api, host, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bridge = DesktopBridge()
    url = f"http://{host}:{server.server_address[1]}/"
    try:
        window = webview.create_window(title, url, js_api=bridge)
        bridge.window = window
        webview.start()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def webview_server(api: HttpApi, host: str, port: int) -> ThreadingHTTPServer:
    """Сервер для оболочки: отдельная функция, чтобы её подменяли в тестах."""
    from nu.gamma.public.http_server import make_server

    return make_server(api, host, port)


__all__ = [
    "PROJECT_MARKER",
    "TITLE",
    "DesktopBridge",
    "DesktopUnavailableError",
    "run_desktop",
]
