"""Симлинк-экспорт: план, политики мультизначности, безопасность записи."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from axislar_core.contracts import Entity

from nu.gamma._internal.cli import main
from nu.gamma._internal.export import (
    ExportError,
    apply_export,
    plan_export,
    render_plan,
)


def note(entity_id: str, path: str, **facets: str) -> Entity:
    payload: dict[str, object] = {
        "id": entity_id,
        "kind": "note",
        "scalars": {"path": path, "filename": Path(path).name},
    }
    if facets:
        payload["facets"] = {
            axis: [{"kind": "categorical", "value": value}]
            for axis, value in (item.split("=", 1) for item in facets.values())
        }
    return Entity.model_validate(payload)


def entity(entity_id: str, path: str, facets: dict[str, list[str]] | None = None) -> Entity:
    return Entity.model_validate(
        {
            "id": entity_id,
            "kind": "note",
            "scalars": {"path": path, "filename": Path(path).name},
            "facets": {
                axis: [{"kind": "categorical", "value": value} for value in values]
                for axis, values in (facets or {}).items()
            },
        }
    )


def make_files(root: Path, *names: str) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")


def test_plan_groups_by_axes_and_buckets_missing(tmp_path: Path) -> None:
    make_files(tmp_path, "library/a/first.md", "library/second.md")
    entities = [
        entity("1", "library/a/first.md", {"folder": ["a"], "tags": ["noir"]}),
        entity("2", "library/second.md", {"tags": ["epic"]}),
    ]
    plan = plan_export(
        entities, ["folder", "tags"], output_root=tmp_path / "views", source_root=tmp_path
    )

    assert [link.relative for link in plan.links] == [
        "a/noir/first.md",
        "_none/epic/second.md",
    ]
    assert all(link.target.is_file() for link in plan.links)


def test_multi_policies(tmp_path: Path) -> None:
    make_files(tmp_path, "one.md")
    item = entity("x", "one.md", {"tags": ["noir", "epic"]})

    def relatives(multi: str) -> list[str]:
        plan = plan_export(
            [item],
            ["tags"],
            output_root=tmp_path / "views",
            source_root=tmp_path,
            multi=multi,
        )
        return [link.relative for link in plan.links]

    assert relatives("first") == ["noir/one.md"]
    assert relatives("all") == ["epic/one.md", "noir/one.md"]
    assert relatives("join") == ["epic+noir/one.md"]


def test_name_collisions_get_id_suffix(tmp_path: Path) -> None:
    make_files(tmp_path, "a/one.md", "b/one.md")
    entities = [
        entity("aaaaaaaa-1", "a/one.md", {"folder": ["same"]}),
        entity("bbbbbbbb-2", "b/one.md", {"folder": ["same"]}),
    ]
    plan = plan_export(entities, ["folder"], output_root=tmp_path / "views", source_root=tmp_path)

    assert [link.relative for link in plan.links] == [
        "same/one.md",
        "same/one-bbbbbbbb.md",
    ]
    assert plan.collisions == ("same/one-bbbbbbbb.md",)


def test_missing_file_and_missing_path_are_reported(tmp_path: Path) -> None:
    entities = [
        entity("1", "ghost.md", {"folder": ["f"]}),
        Entity.model_validate({"id": "2", "kind": "note"}),
    ]
    plan = plan_export(entities, ["folder"], output_root=tmp_path / "views", source_root=tmp_path)

    assert plan.links == ()
    assert plan.missing_files == ("ghost.md",)
    assert plan.skipped_no_path == ("2",)
    printed = render_plan(plan)
    assert "файлов нет на диске: 1" in printed


def test_apply_creates_symlinks_and_keeps_originals(tmp_path: Path) -> None:
    make_files(tmp_path, "src/one.md")
    entity_item = entity("1", "src/one.md", {"folder": ["f"]})
    plan = plan_export(
        [entity_item], ["folder"], output_root=tmp_path / "views", source_root=tmp_path
    )

    report = apply_export(plan)
    assert (report.created, report.existing, report.conflicts) == (1, 0, [])
    link = tmp_path / "views" / "f" / "one.md"
    assert link.is_symlink()
    assert link.read_text(encoding="utf-8") == "src/one.md"
    assert (tmp_path / "src" / "one.md").is_file(), "оригинал остался на месте"

    again = apply_export(plan)
    assert (again.created, again.existing) == (0, 1)


def test_apply_refuses_non_empty_dir_without_force(tmp_path: Path) -> None:
    make_files(tmp_path, "src/one.md", "views/busy.txt")
    plan = plan_export(
        [entity("1", "src/one.md", {"folder": ["f"]})],
        ["folder"],
        output_root=tmp_path / "views",
        source_root=tmp_path,
    )

    with pytest.raises(ExportError, match="чужое содержимое"):
        apply_export(plan)

    assert apply_export(plan, force=True).created == 1


def test_existing_conflicting_file_is_reported_not_deleted(tmp_path: Path) -> None:
    make_files(tmp_path, "src/one.md")
    conflict = tmp_path / "views" / "f" / "one.md"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("чужой файл", encoding="utf-8")

    plan = plan_export(
        [entity("1", "src/one.md", {"folder": ["f"]})],
        ["folder"],
        output_root=tmp_path / "views",
        source_root=tmp_path,
    )
    report = apply_export(plan, force=True)

    assert report.conflicts == ["f/one.md"]
    assert conflict.read_text(encoding="utf-8") == "чужой файл"


def test_hardlink_mode_uses_same_inode(tmp_path: Path) -> None:
    make_files(tmp_path, "src/one.md")
    plan = plan_export(
        [entity("1", "src/one.md", {"folder": ["f"]})],
        ["folder"],
        output_root=tmp_path / "views",
        source_root=tmp_path,
    )

    apply_export(plan, hardlink=True)
    link = tmp_path / "views" / "f" / "one.md"
    assert not link.is_symlink()
    assert os.stat(link).st_ino == os.stat(tmp_path / "src" / "one.md").st_ino


def run_in(tmp_path: Path, *argv: str) -> int:
    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        return main(list(argv))
    finally:
        os.chdir(previous)


def test_cli_is_dry_run_by_default_and_reads_root_from_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_in(tmp_path, "init", "--template", "video-archive") == 0
    make_files(tmp_path, "library/c/noir/VTA One/a_1600000000.mp4")
    assert run_in(tmp_path, "sync", "--apply") == 0
    capsys.readouterr()

    assert run_in(tmp_path, "export", "--group-by", "collection,section") == 0
    printed = capsys.readouterr().out
    assert "dry-run" in printed
    assert not (tmp_path / "views").exists(), "dry-run ничего не создаёт"

    assert (
        run_in(
            tmp_path, "export", "--group-by", "collection,section", "--output", "tree", "--apply"
        )
        == 0
    )
    created = capsys.readouterr().out
    assert "created 1" in created
    assert (tmp_path / "tree" / "c" / "noir" / "a_1600000000.mp4").is_symlink()


def test_cli_unknown_multi_is_rejected_by_parser(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_in(tmp_path, "export", "--group-by", "folder", "--multi", "bogus")
    assert exit_info.value.code == 2
    assert "multi" in capsys.readouterr().err
