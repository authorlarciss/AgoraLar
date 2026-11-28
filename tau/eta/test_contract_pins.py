"""Подъём диапазона ядра по всем проектам монорепозитория."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from eta import contract_pins
from eta.contract_pins import VersionError, pin_range, rewrite, targets


def test_pin_range_uses_contract_major() -> None:
    assert pin_range("6.0.0") == ">=6,<7"
    assert pin_range("7.0.0") == ">=7,<8"
    assert pin_range("10.0.3") == ">=10,<11"


def test_pin_range_rejects_bad_version() -> None:
    with pytest.raises(VersionError):
        pin_range("0.15")


def test_rewrite_replaces_only_contract_dependency() -> None:
    text = (
        "dependencies = [\n"
        '    "packaging>=24.0",\n'
        '    "axislar-contract>=6,<7",\n'
        '    "agoralar-store-json>=0.5,<0.6",\n'
        "]\n"
    )
    updated = rewrite(text, ">=7,<8")
    assert '"axislar-contract>=7,<8"' in updated
    assert "axislar-contract>=6" not in updated
    assert '"packaging>=24.0"' in updated
    assert '"agoralar-store-json>=0.5,<0.6"' in updated


def test_rewrite_is_idempotent() -> None:
    text = '"axislar-contract>=7,<8"'
    assert rewrite(rewrite(text, ">=7,<8"), ">=7,<8") == text


def test_targets_cover_product_and_plugins() -> None:
    names = {path.parent.name for path in targets()}
    assert "plugins" not in names
    assert len(targets()) >= 7, "продукт + плагины с зависимостью от контракта"


def test_check_accepts_current_state() -> None:
    """Самопроверка: в репозитории диапазон уже согласован."""
    assert contract_pins.main(["--contract", "6.0.0", "--check"]) == 0


def test_bad_version_returns_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert contract_pins.main(["--contract", "0.15", "--check"]) == 2
    assert "expected X.Y.Z" in capsys.readouterr().err


def test_main_rewrites_projects_and_check_detects_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = tmp_path / "pyproject.toml"
    product.write_text('dependencies = ["axislar-contract>=6,<7"]\n', encoding="utf-8")
    plugin_dir = tmp_path / "plugins" / "store_json"
    plugin_dir.mkdir(parents=True)
    plugin = plugin_dir / "pyproject.toml"
    plugin.write_text('dependencies = ["axislar-contract>=6,<7"]\n', encoding="utf-8")
    monkeypatch.setattr(contract_pins, "ROOT", tmp_path)

    assert contract_pins.main(["--contract", "7.0.0", "--no-lock"]) == 0
    assert "axislar-contract>=7,<8" in product.read_text(encoding="utf-8")
    assert "axislar-contract>=7,<8" in plugin.read_text(encoding="utf-8")

    assert contract_pins.main(["--contract", "8.0.0", "--check"]) == 1
    assert contract_pins.main(["--contract", "7.0.0", "--check"]) == 0


def test_main_without_targets_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(contract_pins, "ROOT", tmp_path)
    assert contract_pins.main(["--contract", "7.0.0", "--no-lock"]) == 2
    assert "не найден" in capsys.readouterr().err


def test_main_runs_uv_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text(
        'dependencies = ["axislar-contract>=6,<7"]\n', encoding="utf-8"
    )
    monkeypatch.setattr(contract_pins, "ROOT", tmp_path)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> object:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert contract_pins.main(["--contract", "7.0.0"]) == 0
    assert calls == [["uv", "lock", "--refresh"]]
