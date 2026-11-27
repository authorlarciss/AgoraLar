"""`agoralar visualize`: отчёт о разреженности и распределении по осям."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    Entity,
    ScalarDefinition,
)

from nu.gamma._internal.cli import main
from nu.gamma._internal.visualize import collect, render
from nu.psi import Product, build_product
from nu.psi.contracts.iota import load_config


def run_in(tmp_path: Path, *argv: str) -> int:
    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        return main(list(argv))
    finally:
        os.chdir(previous)


@contextmanager
def in_project(tmp_path: Path) -> Generator[Product]:
    """Пути в конфиге относительные: продукт собирается внутри каталога."""
    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        yield build_product(config=load_config(tmp_path / "agoralar.toml"))
    finally:
        os.chdir(previous)


def test_render_escapes_user_data() -> None:
    """Данные в отчёте не доверенные: имена и значения экранируются."""
    from axislar_core.contracts import StorageSchema

    schema: StorageSchema = StorageSchema(
        axes=(AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL, multi=True),)
    )
    entity = Entity.model_validate(
        {
            "id": "x",
            "kind": "note",
            "facets": {"theme": [{"kind": "categorical", "value": "<script>alert(1)</script>"}]},
        }
    )
    html = render(collect("sqlite", schema, (entity,), ()))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_report_counts_sparsity() -> None:
    from axislar_core.contracts import StorageSchema

    schema = StorageSchema(
        axes=(
            AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL, multi=True),
            AxisDefinition(name="year", kind=AxisKind.MEASURABLE),
            AxisDefinition(name="empty", kind=AxisKind.CATEGORICAL),
        ),
        scalars=(ScalarDefinition(name="title"),),
    )
    entities = (
        Entity.model_validate(
            {
                "id": "a",
                "kind": "note",
                "scalars": {"title": "A"},
                "facets": {
                    "theme": [{"kind": "categorical", "value": "noir"}],
                    "year": [{"kind": "measurable", "value": 2020.0}],
                },
            }
        ),
        Entity.model_validate({"id": "b", "kind": "note"}),
    )
    report = collect("sqlite", schema, entities, ())

    by_name = {axis.name: axis for axis in report.axes}
    assert by_name["theme"].filled == 1
    assert by_name["theme"].share == pytest.approx(50.0)
    assert by_name["year"].minimum == 2020.0
    assert by_name["empty"].filled == 0
    assert report.empty_axes == ("empty",)
    assert {scalar.name: scalar.filled for scalar in report.scalars} == {"title": 1}

    html = render(report)
    assert "Пустые оси" in html
    assert "empty" in html


def test_visualize_writes_standalone_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_in(tmp_path, "init", "--template", "obsidian-vault") == 0
    with in_project(tmp_path) as product:
        errors = asyncio.run(
            product.core.storage.bulk_upsert(
                [
                    Entity.model_validate(
                        {
                            "id": "n1",
                            "kind": "note",
                            "scalars": {"title": "Первая"},
                            "facets": {
                                "folder": [{"kind": "categorical", "value": "notes"}],
                                "tags": [{"kind": "categorical", "value": "noir"}],
                            },
                        }
                    ),
                    Entity.model_validate({"id": "n2", "kind": "note"}),
                ]
            )
        )
    assert errors == []
    capsys.readouterr()

    assert run_in(tmp_path, "visualize") == 0
    printed = capsys.readouterr().out
    assert "2 объектов" in printed

    report = (tmp_path / "agoralar-report.html").read_text(encoding="utf-8")
    assert report.startswith("<!doctype html>")
    assert "tags" in report and "folder" in report
    assert "<script src=" not in report, "отчёт не должен тянуть внешние скрипты"
    assert "http://" not in report and "https://" not in report


def test_visualize_custom_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_in(tmp_path, "init", "--template", "video-archive") == 0
    capsys.readouterr()
    assert run_in(tmp_path, "visualize", "--output", "out/report.html") == 0
    assert (tmp_path / "out" / "report.html").is_file(), "каталог отчёта создаётся сам"
