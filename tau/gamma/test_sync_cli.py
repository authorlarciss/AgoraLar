"""CLI sync: файловое дерево → хранилище (ADR-0016)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nu.gamma._internal.cli import main, parse_option_value

STATE = "v.json"
SOURCE_ID = "owner-media"
FILE_NAME = "gutsu__2025-01-29-00-26-24_1738099584068.mp4"


def make_tree(root: Path) -> Path:
    folder = root / "Owner" / "Video" / "Treat" / "Anime" / "VTA Berserk"
    folder.mkdir(parents=True)
    (folder / FILE_NAME).write_bytes(b"video")
    return folder


def run(tmp_path: Path, *argv: str) -> int:
    return main(["--store", "json", "--data", str(tmp_path / STATE), *argv])


def declare_schema(tmp_path: Path) -> None:
    for axis in ("owner", "media", "shelf", "section", "title", "code", "author", "format"):
        assert run(tmp_path, "schema", "add-axis", "--name", axis, "--kind", "categorical") == 0
    assert run(tmp_path, "schema", "add-axis", "--name", "taken_at", "--kind", "measurable") == 0
    assert run(tmp_path, "schema", "add-scalar", "--name", "path") == 0
    assert run(tmp_path, "schema", "add-scalar", "--name", "filename") == 0


def sync(tmp_path: Path, *extra: str) -> int:
    root = tmp_path / "data"
    return run(
        tmp_path,
        "sync",
        "--source",
        "filesystem",
        "--root",
        str(root),
        "--source-id",
        SOURCE_ID,
        "--state-file",
        str(tmp_path / "sync.json"),
        *extra,
    )


def test_dry_run_reports_plan_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")

    assert sync(tmp_path) == 0
    out = capsys.readouterr().out
    assert "scanned: 1" in out
    assert "create=1" in out
    assert "dry run" in out

    assert run(tmp_path, "query") == 0
    assert "total=0" in capsys.readouterr().out


def test_apply_creates_objects(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")

    assert sync(tmp_path, "--apply") == 0
    assert "applied: 1" in capsys.readouterr().out

    assert run(tmp_path, "query", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 1


def test_rescan_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")
    assert sync(tmp_path, "--apply") == 0

    capsys.readouterr()
    assert sync(tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "план" not in out
    assert "нет изменений" in out
    assert "applied: 0" in out


def test_deleted_file_is_tombstoned(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    folder = make_tree(tmp_path / "data")
    assert sync(tmp_path, "--apply") == 0

    next(folder.glob("*.mp4")).unlink()
    capsys.readouterr()
    assert sync(tmp_path, "--apply") == 0
    assert "tombstone=1" in capsys.readouterr().out

    assert run(tmp_path, "query") == 0
    assert "total=0" in capsys.readouterr().out


def test_incomplete_scan_does_not_delete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    declare_schema(tmp_path)
    folder = make_tree(tmp_path / "data")
    assert sync(tmp_path, "--apply") == 0

    next(folder.glob("*.mp4")).unlink()
    capsys.readouterr()
    assert sync(tmp_path, "--apply", "--incomplete") == 0
    out = capsys.readouterr().out
    assert "tombstone" not in out
    assert run(tmp_path, "query") == 0
    assert "total=1" in capsys.readouterr().out


def test_rename_preserves_object(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    folder = make_tree(tmp_path / "data")
    assert sync(tmp_path, "--apply") == 0

    old = next(folder.glob("*.mp4"))
    old.rename(folder / "other__2025-01-29-00-26-24_1738099584068.mp4")
    capsys.readouterr()
    assert sync(tmp_path, "--apply") == 0
    assert "move=1" in capsys.readouterr().out

    assert run(tmp_path, "query") == 0
    assert "total=1" in capsys.readouterr().out, "объект остался один"


def test_missing_schema_axes_report_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    make_tree(tmp_path / "data")
    assert sync(tmp_path, "--apply") == 2
    assert "unknown_" in capsys.readouterr().err


def test_unknown_source_reports_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, "sync", "--source", "nope", "--root", str(tmp_path)) == 2
    assert "not found" in capsys.readouterr().err


def test_source_requires_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, "sync", "--source", "filesystem", "--source-id", SOURCE_ID) == 2
    assert "root" in capsys.readouterr().err


def test_custom_levels_and_code_mapping(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")

    assert (
        run(
            tmp_path,
            "sync",
            "--source",
            "filesystem",
            "--root",
            str(tmp_path / "data"),
            "--source-id",
            SOURCE_ID,
            "--levels",
            "owner, media, shelf, section, title",
            "--code",
            "VTA=anime",
        )
        == 0
    )
    out = capsys.readouterr()
    assert "create=1" in out.out
    assert "warning:" not in out.err, "код совпадает с каталогом — предупреждений нет"


def test_code_mismatch_is_printed_as_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")

    assert (
        run(
            tmp_path,
            "sync",
            "--source",
            "filesystem",
            "--root",
            str(tmp_path / "data"),
            "--source-id",
            SOURCE_ID,
            "--code",
            "VTA=games",
        )
        == 0
    )
    assert "warning:" in capsys.readouterr().err


def test_bad_code_option_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")
    assert sync(tmp_path, "--code", "VTA") == 2
    assert "bad --code" in capsys.readouterr().err


def test_storage_plugin_is_not_a_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, "sync", "--source", "json", "--root", str(tmp_path)) == 2
    assert "not an entity source" in capsys.readouterr().err


def test_store_show_renders_plugin_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agoralar.toml").write_text(
        'store = "json"\ndata = "v.json"\n\n[plugins.filesystem]\nroot = "/data"\nsource_id = "s"\n',
        encoding="utf-8",
    )
    assert main(["store", "show"]) == 0
    out = capsys.readouterr().out
    assert "plugins.filesystem:" in out
    assert "root=/data" in out


def test_sync_records_history_and_reports_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")

    assert sync(tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "state:" in out

    assert sync(tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "previous:" in out
    assert "scanned=1" in out


def test_watch_runs_iterations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")

    code = run(
        tmp_path,
        "watch",
        "--source",
        "filesystem",
        "--root",
        str(tmp_path / "data"),
        "--source-id",
        SOURCE_ID,
        "--apply",
        "--iterations",
        "2",
        "--interval",
        "0",
    )
    assert code == 0
    out = capsys.readouterr().out
    assert out.count("scanned:") == 2
    assert "applied: 1" in out
    assert "applied: 0" in out


def test_watch_dry_run_does_not_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")

    code = run(
        tmp_path,
        "watch",
        "--source",
        "filesystem",
        "--root",
        str(tmp_path / "data"),
        "--source-id",
        SOURCE_ID,
        "--iterations",
        "1",
        "--interval",
        "0",
    )
    assert code == 0
    assert "dry run" in capsys.readouterr().out
    assert run(tmp_path, "query") == 0
    assert "total=0" in capsys.readouterr().out


def test_explicit_state_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")
    state = tmp_path / "history.json"
    assert sync(tmp_path, "--apply", "--state-file", str(state)) == 0
    assert state.is_file()
    assert "history.json" in capsys.readouterr().out


def test_broken_state_file_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")
    state = tmp_path / "history.json"
    state.write_text("{broken", encoding="utf-8")
    assert sync(tmp_path, "--apply", "--state-file", str(state)) == 2
    assert "broken sync state" in capsys.readouterr().err


# ------------------------------------------------- параметры источника (0.10.0)


def test_source_line_is_printed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")
    assert sync(tmp_path) == 0
    assert "source: filesystem:" in capsys.readouterr().out


def test_config_supplies_source_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    make_tree(tmp_path / "data")
    (tmp_path / "agoralar.toml").write_text(
        'store = "json"\n'
        f'data = "{(tmp_path / STATE).as_posix()}"\n\n'
        "[plugins.filesystem]\n"
        f'root = "{(tmp_path / "data").as_posix()}"\n'
        f'source_id = "{SOURCE_ID}"\n',
        encoding="utf-8",
    )
    for axis in ("owner", "media", "shelf", "section", "title", "code", "author", "format"):
        assert main(["schema", "add-axis", "--name", axis, "--kind", "categorical"]) == 0
    assert main(["schema", "add-axis", "--name", "taken_at", "--kind", "measurable"]) == 0
    assert main(["schema", "add-scalar", "--name", "path"]) == 0
    assert main(["schema", "add-scalar", "--name", "filename"]) == 0
    capsys.readouterr()

    assert main(["sync", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "scanned: 1" in out
    assert "applied: 1" in out


def test_option_overrides_config_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    declare_schema(tmp_path)
    make_tree(tmp_path / "data")
    (tmp_path / "agoralar.toml").write_text(
        'store = "json"\n'
        f'data = "{(tmp_path / STATE).as_posix()}"\n\n'
        "[plugins.filesystem]\n"
        'root = "/nope"\n'
        f'source_id = "{SOURCE_ID}"\n',
        encoding="utf-8",
    )
    capsys.readouterr()

    assert main(["sync", "--option", f"root={(tmp_path / 'data').as_posix()}"]) == 0
    assert "create=1" in capsys.readouterr().out


def test_bad_option_syntax_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    declare_schema(tmp_path)
    assert sync(tmp_path, "--option", "root") == 2
    assert "bad --option" in capsys.readouterr().err


def test_option_values_are_toml_scalars() -> None:
    assert parse_option_value("42") == 42
    assert parse_option_value("3.5") == 3.5
    assert parse_option_value("true") is True
    assert parse_option_value('["a", "b"]') == ["a", "b"]
    assert parse_option_value("SELECT * FROM t") == "SELECT * FROM t"


def test_sql_source_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import sqlite3

    db = tmp_path / "export.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE edits (id TEXT, title TEXT, author TEXT, year INTEGER)")
    connection.executemany(
        "INSERT INTO edits VALUES (?, ?, ?, ?)",
        [("e-1", "Alpha", "ann", 1999), ("e-2", "Beta", "bob", 2004)],
    )
    connection.commit()
    connection.close()

    assert run(tmp_path, "schema", "add-axis", "--name", "author", "--kind", "categorical") == 0
    assert run(tmp_path, "schema", "add-axis", "--name", "year", "--kind", "measurable") == 0
    assert run(tmp_path, "schema", "add-scalar", "--name", "title") == 0

    assert (
        run(
            tmp_path,
            "sync",
            "--source",
            "sql",
            "--source-id",
            "db-edits",
            "--option",
            f"path={db}",
            "--option",
            "table=edits",
            "--option",
            'axes=["author"]',
            "--option",
            'measurable=["year"]',
            "--option",
            'scalars=["title"]',
            "--apply",
            "--state-file",
            str(tmp_path / "sync.json"),
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "source: sql:" in out
    assert "applied: 2" in out

    assert run(tmp_path, "query", "--filter", "author:contains=ann", "--json") == 0
    assert json.loads(capsys.readouterr().out)["total"] == 1


def test_apply_warns_when_tombstone_leaves_dangling_reference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0025 ядра: sync не блокируется ссылками, но обязан предупредить."""
    from axislar_core.contracts import entity_id_for

    declare_schema(tmp_path)
    assert (
        run(
            tmp_path,
            "schema",
            "add-axis",
            "--name",
            "related",
            "--kind",
            "reference",
            "--target",
            "item",
        )
        == 0
    )
    folder = make_tree(tmp_path / "data")
    assert sync(tmp_path, "--apply") == 0
    capsys.readouterr()

    relative = (folder / FILE_NAME).relative_to(tmp_path / "data")
    target = entity_id_for(SOURCE_ID, relative.as_posix())
    assert (
        run(
            tmp_path,
            "put",
            "--id",
            "ref-1",
            "--kind",
            "note",
            "--facet",
            f"related={target}",
        )
        == 0
    )

    (folder / FILE_NAME).unlink()
    assert sync(tmp_path, "--apply") == 0
    captured = capsys.readouterr()
    assert "tombstone=1" in captured.out
    assert "ссылки стали висячими" in captured.err
    assert str(target) in captured.err
