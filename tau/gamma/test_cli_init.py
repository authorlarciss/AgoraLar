"""`agoralar init` и `agoralar links`: шаблоны схемы и обратные ссылки."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from axislar_core.contracts import Entity, EntityId

from nu.gamma._internal.cli import main
from nu.iota.public.templates import BUILTIN_TEMPLATES, load_template
from nu.psi import Product, build_product
from nu.psi.contracts.iota import load_config


def run_in(tmp_path: Path, *argv: str) -> int:
    """Запустить CLI так, будто он вызван из `tmp_path`."""
    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        return main(list(argv))
    finally:
        os.chdir(previous)


@contextmanager
def in_project(tmp_path: Path) -> Generator[Product]:
    """Пути в конфиге относительные: и стору, и CLI нужен cwd проекта."""
    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        yield build_product(config=load_config(tmp_path / "agoralar.toml"))
    finally:
        os.chdir(previous)


def sync(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """CLI сам вызывает `asyncio.run`, поэтому проверки схемы идут отдельно."""
    return asyncio.run(coroutine)


def test_init_list_mentions_both_templates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_in(tmp_path, "init", "--list") == 0
    printed = capsys.readouterr().out
    for name in BUILTIN_TEMPLATES:
        assert name in printed


def test_init_creates_config_and_schema(tmp_path: Path) -> None:
    assert run_in(tmp_path, "init", "--template", "video-archive") == 0

    config = load_config(tmp_path / "agoralar.toml")
    assert config.store == "sqlite"
    assert config.plugins["filesystem"]["levels"] == ["collection", "section", "title"]

    template = load_template("video-archive")
    with in_project(tmp_path) as product:
        schema = sync(product.core.schema())
    assert {axis.name for axis in template.axes} <= {axis.name for axis in schema.axes}
    assert {scalar.name for scalar in template.scalars} <= {
        scalar.name for scalar in schema.scalars
    }


def test_init_does_not_create_objects(tmp_path: Path) -> None:
    assert run_in(tmp_path, "init", "--template", "obsidian-vault") == 0
    with in_project(tmp_path) as product:
        assert sync(product.core.count()) == 0, "шаблон создаёт схему, но не данные"


def test_init_refuses_to_overwrite_without_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_in(tmp_path, "init", "--template", "video-archive") == 0
    before = (tmp_path / "agoralar.toml").read_text(encoding="utf-8")

    assert run_in(tmp_path, "init", "--template", "video-archive") == 2
    assert "--force" in capsys.readouterr().err
    assert (tmp_path / "agoralar.toml").read_text(encoding="utf-8") == before


def test_init_force_adds_only_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_in(tmp_path, "init", "--template", "video-archive") == 0
    capsys.readouterr()

    assert run_in(tmp_path, "init", "--template", "video-archive", "--force") == 0
    printed = capsys.readouterr().out
    assert "added 0" in printed
    assert "оси неизменяемы" in printed


def test_init_unknown_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_in(tmp_path, "init", "--template", "nope") == 2
    assert "video-archive" in capsys.readouterr().err


def test_init_without_template_or_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_in(tmp_path, "init") == 2
    assert "--template" in capsys.readouterr().err


def test_links_prints_incoming_references(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_in(tmp_path, "init", "--template", "video-archive") == 0
    with in_project(tmp_path) as product:
        # Ось `related` объявлена шаблоном video-archive.
        assert (
            sync(
                product.core.storage.bulk_upsert(
                    [
                        Entity(id=EntityId("a"), kind="video"),
                        Entity.model_validate(
                            {
                                "id": "b",
                                "kind": "video",
                                "facets": {"related": [{"kind": "reference", "target": "a"}]},
                            }
                        ),
                    ]
                )
            )
            == []
        )

    capsys.readouterr()

    assert run_in(tmp_path, "links", "--id", "a") == 0
    printed = capsys.readouterr().out
    assert "# total=1" in printed
    assert "related <- b" in printed

    assert run_in(tmp_path, "links", "--id", "a", "--axis", "unknown") == 0
    assert "# total=0" in capsys.readouterr().out


def test_links_on_empty_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_in(tmp_path, "init", "--template", "obsidian-vault") == 0
    capsys.readouterr()
    assert run_in(tmp_path, "links", "--id", "nobody") == 0
    assert "# total=0" in capsys.readouterr().out
