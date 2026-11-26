"""CLI analyze: производный артефакт из исходного, с провенансом (ADR-0018)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nu.gamma._internal.cli import main

STATE = "vault.json"


def run(tmp_path: Path, *argv: str) -> int:
    return main(["--store", "json", "--data", str(tmp_path / STATE), *argv])


def analyze(tmp_path: Path, *extra: str) -> int:
    return run(tmp_path, "analyze", "--analyzer", "text", "--apply", *extra)


def test_analyze_creates_preview_with_provenance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = ("x" * 500).encode()
    source = tmp_path / "doc.txt"
    source.write_bytes(payload)
    assert run(tmp_path, "artifact", "put", str(source), "--media-type", "text/plain") == 0
    content_hash = capsys.readouterr().out.split()[0]

    assert (
        run(
            tmp_path,
            "put",
            "--id",
            "e-1",
            "--kind",
            "note",
            "--artifact",
            f"original={content_hash}",
        )
        == 0
    )

    assert analyze(tmp_path) == 0
    out = capsys.readouterr().out
    assert "analyzed: 1" in out
    assert "applied: 1" in out

    assert run(tmp_path, "show", "--id", "e-1", "--json") == 0
    entity = json.loads(capsys.readouterr().out)
    preview = entity["artifacts"]["preview"]
    assert preview["media_type"] == "text/plain; charset=utf-8"
    assert preview["derived_by"]["plugin"] == "text"
    assert preview["derived_by"]["version"], "версия анализатора обязательна"


def test_dry_run_does_not_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "doc.txt"
    source.write_bytes(b"hello")
    run(tmp_path, "artifact", "put", str(source), "--media-type", "text/plain")
    content_hash = capsys.readouterr().out.split()[0]
    run(tmp_path, "put", "--id", "e-1", "--kind", "note", "--artifact", f"original={content_hash}")

    assert run(tmp_path, "analyze", "--analyzer", "text") == 0
    out = capsys.readouterr().out
    assert "analyzed: 1" in out and "dry run" in out

    assert run(tmp_path, "show", "--id", "e-1", "--json") == 0
    assert "preview" not in json.loads(capsys.readouterr().out)["artifacts"]


def test_second_run_is_up_to_date(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "doc.txt"
    source.write_bytes(b"hello " * 100)
    run(tmp_path, "artifact", "put", str(source), "--media-type", "text/plain")
    content_hash = capsys.readouterr().out.split()[0]
    run(tmp_path, "put", "--id", "e-1", "--kind", "note", "--artifact", f"original={content_hash}")

    assert analyze(tmp_path, "--output-role", "preview") == 0
    capsys.readouterr()
    assert analyze(tmp_path, "--output-role", "preview") == 0
    out = capsys.readouterr().out
    assert "analyzed: 0" in out and "up to date: 1" in out


def test_force_recomputes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "doc.txt"
    source.write_bytes(b"hello " * 100)
    run(tmp_path, "artifact", "put", str(source), "--media-type", "text/plain")
    content_hash = capsys.readouterr().out.split()[0]
    run(tmp_path, "put", "--id", "e-1", "--kind", "note", "--artifact", f"original={content_hash}")

    analyze(tmp_path)
    capsys.readouterr()
    assert analyze(tmp_path, "--output-role", "preview", "--force") == 0
    assert "analyzed: 1" in capsys.readouterr().out


def test_non_text_media_is_skipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "img.png"
    source.write_bytes(b"\x89PNG\r\n")
    run(tmp_path, "artifact", "put", str(source), "--media-type", "image/png")
    content_hash = capsys.readouterr().out.split()[0]
    run(tmp_path, "put", "--id", "e-1", "--kind", "image", "--artifact", f"original={content_hash}")

    assert analyze(tmp_path) == 0
    assert "skipped by media type: 1" in capsys.readouterr().out


def test_unknown_analyzer_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, "analyze", "--analyzer", "nope") == 2
    assert "not found" in capsys.readouterr().err


def test_storage_plugin_is_not_an_analyzer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, "analyze", "--analyzer", "json") == 2
    assert "not an analyzer" in capsys.readouterr().err


def test_option_overrides_config_limit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "doc.txt"
    source.write_bytes(b"abcdefghij")
    run(tmp_path, "artifact", "put", str(source), "--media-type", "text/plain")
    content_hash = capsys.readouterr().out.split()[0]
    run(tmp_path, "put", "--id", "e-1", "--kind", "note", "--artifact", f"original={content_hash}")

    assert analyze(tmp_path, "--option", "limit=3") == 0
    capsys.readouterr()
    assert run(tmp_path, "artifact", "ls", "--json") == 0
    entries = json.loads(capsys.readouterr().out)
    preview = next(item for item in entries if item["size"] < 10)
    # 3 символа + многоточие (3 байта в UTF-8)
    assert preview["size"] in {4, 6}, entries
