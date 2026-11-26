"""CLI артефактов: положить, получить, перечислить, удалить (contract 5)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from nu.gamma._internal.cli import main

STATE = "vault.json"


def run(tmp_path: Path, *argv: str) -> int:
    return main(["--store", "json", "--data", str(tmp_path / STATE), *argv])


def put(tmp_path: Path, data: bytes, media_type: str = "image/png") -> str:
    source = tmp_path / "preview.bin"
    source.write_bytes(data)
    return str(source)


def test_put_reports_hash_and_deduplicates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = put(tmp_path, b"preview-bytes")
    assert run(tmp_path, "artifact", "put", file, "--media-type", "image/png") == 0
    first = capsys.readouterr().out.split()[0]

    assert run(tmp_path, "artifact", "put", file, "--media-type", "image/png") == 0
    second = capsys.readouterr().out.split()[0]
    assert first == second, "те же байты — тот же адрес"

    assert run(tmp_path, "artifact", "ls", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{"content_hash": first, "media_type": "image/png", "size": 13}]


def test_get_writes_bytes_to_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = put(tmp_path, b"preview-bytes")
    assert run(tmp_path, "artifact", "put", file, "--media-type", "image/png") == 0
    content_hash = capsys.readouterr().out.split()[0]

    target = tmp_path / "out" / "copy.bin"
    assert run(tmp_path, "artifact", "get", content_hash, "--out", str(target)) == 0
    assert target.read_bytes() == b"preview-bytes"
    assert "13B" in capsys.readouterr().out


def test_get_prints_base64_without_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = put(tmp_path, b"preview-bytes")
    run(tmp_path, "artifact", "put", file, "--media-type", "image/png")
    content_hash = capsys.readouterr().out.split()[0]

    assert run(tmp_path, "artifact", "get", content_hash) == 0
    assert base64.b64decode(capsys.readouterr().out.strip()) == b"preview-bytes"


def test_get_missing_artifact(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, "artifact", "get", "0" * 64) == 1
    assert "not found" in capsys.readouterr().err


def test_remove_unreferenced_artifact(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = put(tmp_path, b"preview-bytes")
    run(tmp_path, "artifact", "put", file, "--media-type", "image/png")
    content_hash = capsys.readouterr().out.split()[0]

    assert run(tmp_path, "artifact", "rm", content_hash) == 0
    assert "deleted" in capsys.readouterr().out
    assert run(tmp_path, "artifact", "ls") == 0
    assert "<артефактов нет>" in capsys.readouterr().out


def test_remove_referenced_artifact_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = put(tmp_path, b"preview-bytes")
    run(tmp_path, "artifact", "put", file, "--media-type", "image/png")
    content_hash = capsys.readouterr().out.split()[0]

    assert (
        run(
            tmp_path,
            "put",
            "--id",
            "e-1",
            "--kind",
            "edit",
            "--artifact",
            f"preview={content_hash}",
        )
        == 0
    )

    assert run(tmp_path, "artifact", "rm", content_hash) == 2
    assert "artifact_in_use" in capsys.readouterr().err


def test_empty_media_type_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = put(tmp_path, b"x")
    assert run(tmp_path, "artifact", "put", file, "--media-type", "") == 2
    assert "invalid artifact" in capsys.readouterr().err
