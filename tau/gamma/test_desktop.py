"""Десктоп-оболочка: обвязка без реального окна (5.2).

GUI в песочнице не поднять, поэтому проверяем шов: сервер поднимается на
свободном порту, окно создаётся с правильным URL и мостом, диалог папки
возвращает путь, окно закрывается — сервер гасится.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from nu.gamma._internal import desktop
from nu.psi import build_product
from nu.psi.contracts.alpha import Config


class FakeServer:
    def __init__(self, port: int) -> None:
        self.server_address = ("127.0.0.1", port)
        self.started = False
        self.stopped = False

    def serve_forever(self) -> None:
        self.started = True

    def shutdown(self) -> None:
        self.stopped = True

    def server_close(self) -> None:
        pass


class FakeWindow:
    def __init__(self, folder: list[str] | None) -> None:
        self.folder = folder or []
        self.bridge: Any = None

    def create_file_dialog(self, kind: str) -> list[str]:
        assert kind == "folder"
        return self.folder


@pytest.fixture
def fake_webview(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"windows": [], "started": False}
    module = types.SimpleNamespace(FOLDER_DIALOG="folder")

    def create_window(title: str, url: str, js_api: Any) -> FakeWindow:
        window = FakeWindow(["/tmp/archive"])
        calls["windows"].append({"title": title, "url": url, "js_api": js_api})
        return window

    def start() -> None:
        calls["started"] = True

    module.create_window = create_window
    module.start = start
    monkeypatch.setitem(sys.modules, "webview", module)
    return calls


def test_run_desktop_opens_window_and_stops_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_webview: dict[str, Any]
) -> None:
    server = FakeServer(4321)
    monkeypatch.setattr(desktop, "webview_server", lambda api, host, port: server)
    product = build_product(config=Config(store="json", data=tmp_path / "vault.json"))

    desktop.run_desktop(product)

    assert fake_webview["started"] is True
    [window] = fake_webview["windows"]
    assert window["url"] == "http://127.0.0.1:4321/"
    assert isinstance(window["js_api"], desktop.DesktopBridge)
    assert server.stopped is True, "окно закрыто — сервер погашен"


def test_bridge_picks_folder_and_remembers_recent(fake_webview: dict[str, Any]) -> None:
    bridge = desktop.DesktopBridge()
    bridge.window = FakeWindow(["/tmp/archive"])

    assert bridge.pick_folder() == "/tmp/archive"
    assert bridge.recent == ["/tmp/archive"]

    bridge.window = FakeWindow([])
    assert bridge.pick_folder() is None


def test_bridge_open_project_validates_path(tmp_path: Path) -> None:
    bridge = desktop.DesktopBridge()
    (tmp_path / "agoralar.toml").write_text('store = "sqlite"\n', encoding="utf-8")

    result = bridge.open_project(str(tmp_path))
    assert result["ok"] is True
    assert result["has_config"] is True
    assert result["has_marker"] is False
    assert bridge.recent == [str(tmp_path.resolve())]

    missing = bridge.open_project(str(tmp_path / "ghost"))
    assert missing["ok"] is False


def test_missing_pywebview_reports_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "webview", None)
    product = build_product()
    with pytest.raises(desktop.DesktopUnavailableError, match="agoralar\\[desktop\\]"):
        desktop.run_desktop(product)
