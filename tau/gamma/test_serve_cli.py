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
