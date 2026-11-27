"""CLI `patch` и `undo`: массовая правка с журналом."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nu.gamma._internal.cli import main


def run_in(tmp_path: Path, *argv: str) -> int:
    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        return main(list(argv))
    finally:
        os.chdir(previous)


def seed(tmp_path: Path) -> None:
    assert run_in(tmp_path, "init", "--template", "obsidian-vault") == 0
    assert run_in(tmp_path, "put", "--id", "n1", "--kind", "note", "--facet", "tags=old") == 0
    assert run_in(tmp_path, "put", "--id", "n2", "--kind", "note") == 0


def test_patch_is_dry_run_by_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(tmp_path)
    capsys.readouterr()

    assert run_in(tmp_path, "patch", "--filter", "tags:exists", "--add", "tags=noir") == 0
    printed = capsys.readouterr().out
    assert "объектов под фильтром: 1" in printed
    assert "dry-run" in printed

    assert run_in(tmp_path, "query", "--filter", "tags:contains=noir") == 0
    assert "# total=0" in capsys.readouterr().out


def test_patch_applies_and_undo_reverts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(tmp_path)
    capsys.readouterr()

    assert (
        run_in(tmp_path, "patch", "--filter", "tags:exists", "--add", "tags=noir", "--apply") == 0
    )
    assert "изменено: 1" in capsys.readouterr().out

    assert run_in(tmp_path, "query", "--filter", "tags:contains=noir") == 0
    assert "# total=1" in capsys.readouterr().out

    assert run_in(tmp_path, "undo") == 0
    assert "отменено: patch" in capsys.readouterr().out

    assert run_in(tmp_path, "query", "--filter", "tags:contains=noir") == 0
    assert "# total=0" in capsys.readouterr().out
    assert run_in(tmp_path, "query", "--filter", "tags:contains=old") == 0
    assert "# total=1" in capsys.readouterr().out


def test_patch_reports_source_owned_facets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_in(tmp_path, "init", "--template", "obsidian-vault") == 0
    (tmp_path / "vault").mkdir(exist_ok=True)
    (tmp_path / "vault" / "a.md").write_text("---\ntags: [from-source]\n---\n", encoding="utf-8")
    assert run_in(tmp_path, "sync", "--source", "markdown", "--apply") == 0
    capsys.readouterr()

    assert (
        run_in(tmp_path, "patch", "--filter", "tags:exists", "--add", "tags=manual", "--apply") == 0
    )
    printed = capsys.readouterr().out
    assert "принадлежит источнику" in printed
    assert "--force" in printed

    # С --force владение переходит человеку, и правка сохраняется.
    assert (
        run_in(
            tmp_path,
            "patch",
            "--filter",
            "tags:exists",
            "--add",
            "tags=manual",
            "--apply",
            "--force",
        )
        == 0
    )
    capsys.readouterr()
    assert run_in(tmp_path, "query", "--filter", "tags:contains=manual") == 0
    assert "# total=1" in capsys.readouterr().out


def test_undo_on_empty_journal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_in(tmp_path, "init", "--template", "obsidian-vault") == 0
    capsys.readouterr()
    assert run_in(tmp_path, "undo") == 0
    assert "журнал пуст" in capsys.readouterr().out


def test_patch_requires_operations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_in(tmp_path, "init", "--template", "obsidian-vault") == 0
    capsys.readouterr()
    assert run_in(tmp_path, "patch", "--filter", "tags:exists") == 2
    assert "нужен хотя бы один" in capsys.readouterr().err
