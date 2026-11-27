"""Markdown-источник в продукте: шаблон obsidian-vault → sync → связи."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nu.gamma._internal.cli import main
from nu.psi.contracts.iota import load_config


def run_in(tmp_path: Path, *argv: str) -> int:
    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        return main(list(argv))
    finally:
        os.chdir(previous)


def make_vault(root: Path) -> None:
    (root / "notes").mkdir(parents=True)
    (root / "index.md").write_text(
        "---\ntitle: Главная\ntags: [genre, noir]\ncreated: 2024-05-01\n---\n"
        "Смотри [[Daily Note]] и [[missing-note]].\n",
        encoding="utf-8",
    )
    (root / "notes" / "Daily Note.md").write_text("Назад: [[index]].\n", encoding="utf-8")


def test_markdown_template_syncs_links_and_tags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_vault(tmp_path / "vault")
    assert run_in(tmp_path, "init", "--template", "obsidian-vault") == 0
    config = load_config(tmp_path / "agoralar.toml")
    assert config.plugins["markdown"]["root"] == "vault"
    capsys.readouterr()

    assert run_in(tmp_path, "sync", "--source", "markdown", "--apply") == 0
    synced = capsys.readouterr().out
    assert "applied: 2" in synced

    assert run_in(tmp_path, "query", "--filter", "tags:contains=noir") == 0
    assert "# total=1" in capsys.readouterr().out

    assert run_in(tmp_path, "query", "--filter", "folder:contains=notes", "--json") == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    note_id = payload["ids"][0]

    assert run_in(tmp_path, "links", "--id", note_id) == 0
    assert "links <-" in capsys.readouterr().out


def test_markdown_sync_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_vault(tmp_path / "vault")
    assert run_in(tmp_path, "init", "--template", "obsidian-vault") == 0
    assert run_in(tmp_path, "sync", "--source", "markdown", "--apply") == 0
    capsys.readouterr()

    assert run_in(tmp_path, "sync", "--source", "markdown", "--apply") == 0
    assert "applied: 0" in capsys.readouterr().out
