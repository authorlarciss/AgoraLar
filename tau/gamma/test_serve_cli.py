"""CLI serve: токен и запрет слушать сеть без аутентификации (ADR-0017)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from nu.gamma._internal.cli import is_loopback, main, serve_token


def serve_args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--store",
        "json",
        "--data",
        str(tmp_path / "vault.json"),
        "serve",
        *extra,
    ]


def test_refuses_non_loopback_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("AGORALAR_TOKEN", raising=False)
    assert main(serve_args(tmp_path, "--host", "0.0.0.0")) == 2
    assert "without auth" in capsys.readouterr().err


def test_loopback_without_token_is_allowed() -> None:
    for host in ("127.0.0.1", "localhost", "::1", "127.0.0.5"):
        assert is_loopback(host)
    for host in ("0.0.0.0", "10.0.0.1", "example.com"):
        assert not is_loopback(host)


def test_token_from_argument() -> None:
    assert serve_token(argparse.Namespace(token="abc")) == "abc"


def test_token_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGORALAR_TOKEN", "from-env")
    assert serve_token(argparse.Namespace(token=None)) == "from-env"


def test_no_token_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGORALAR_TOKEN", raising=False)
    assert serve_token(argparse.Namespace(token=None)) is None
    assert serve_token(argparse.Namespace(token="")) is None, "пустой токен = нет токена"


@pytest.mark.integration
def test_serve_stops_on_first_sigint(tmp_path: Path) -> None:
    """Один Ctrl+C останавливает `serve`.

    Регрессия: `asyncio.run` в CLI ставит свой обработчик SIGINT, который
    отменяет главную задачу; заблокированный `serve_forever` отмену не видит,
    и Ctrl+C «не работал» — процесс жил до закрытия терминала.
    """
    import signal
    import socket
    import subprocess
    import sys
    import time

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "nu.main",
            "--store",
            "json",
            "--data",
            str(tmp_path / "vault.json"),
            "serve",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for _ in range(100):
            time.sleep(0.05)
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                continue
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=10) == 0, "serve должен выйти по Ctrl+C"
    finally:
        if proc.poll() is None:  # pragma: no cover - защита от зависшего процесса
            proc.kill()
