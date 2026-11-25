"""Резолвер gamma: фабрика CLI (resolver pattern)."""

from __future__ import annotations

import pytest

from nu.gamma.public import CliMain
from nu.psi.contracts.gamma import GammaResolver


def test_factory_returns_typed_callable() -> None:
    entry: CliMain = GammaResolver().make_cli()
    assert callable(entry)


def test_cli_is_reachable_through_resolver(capsys: pytest.CaptureFixture[str]) -> None:
    entry = GammaResolver().make_cli()
    assert entry(["plugins", "list"]) == 0
    assert "available:" in capsys.readouterr().out


def test_resolver_builds_fresh_callable_each_time() -> None:
    assert GammaResolver().make_cli() is GammaResolver().make_cli()
