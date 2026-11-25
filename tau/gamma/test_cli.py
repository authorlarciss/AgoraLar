"""CLI продукта: разбор аргументов, команды, коды выхода."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from axislar_core.contracts import AxisDefinition, AxisKind, Operator, StorageSchema

from nu.gamma._internal.cli import (
    build_parser,
    main,
    parse_facet,
    parse_filter,
    parse_scalar,
    render_plugins,
    render_result,
    render_schema,
)
from nu.psi import build_product
from nu.psi.contracts.alpha import PluginKind, PluginManifest
from nu.psi.contracts.beta import PluginRegistry


def parser_ns(*argv: str) -> argparse.Namespace:
    return build_parser().parse_args(list(argv))


# ------------------------------------------------------------------ разбор


def test_parse_filter_categorical() -> None:
    condition = parse_filter("theme:contains=cyberpunk,noir")
    assert condition.op is Operator.CONTAINS
    assert condition.values == ("cyberpunk", "noir")


def test_parse_filter_numeric() -> None:
    assert parse_filter("year:between=2019,2024").values == (2019.0, 2024.0)


def test_parse_filter_nullary() -> None:
    assert parse_filter("year:exists").values == ()


@pytest.mark.parametrize(
    "token",
    ["theme", "theme:contains", "theme:bogus=x", "year:gte=abc", "theme:contains="],
)
def test_parse_filter_rejects_bad_input(token: str) -> None:
    with pytest.raises(ValueError):
        parse_filter(token)


def test_parse_scalar() -> None:
    assert parse_scalar("title=Alpha") == ("title", "Alpha")
    with pytest.raises(ValueError, match="bad scalar"):
        parse_scalar("title")


def schema() -> StorageSchema:
    return StorageSchema(
        axes=(
            AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL, multi=True),
            AxisDefinition(name="year", kind=AxisKind.MEASURABLE),
            AxisDefinition(name="author", kind=AxisKind.REFERENCE),
        )
    )


def test_parse_facet_uses_axis_kind() -> None:
    name, values = parse_facet(schema(), "year=2024")
    assert name == "year" and values[0].kind == "measurable"
    _, references = parse_facet(schema(), "author=p-1")
    assert references[0].kind == "reference"
    _, categorical = parse_facet(schema(), "theme=noir")
    assert categorical[0].kind == "categorical"


@pytest.mark.parametrize(
    ("token", "message"),
    [
        ("theme", "bad facet"),
        ("colour=red", "unknown axis"),
        ("theme=", "requires at least one value"),
        ("year=soon", "is measurable"),
    ],
)
def test_parse_facet_rejects_bad_input(token: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_facet(schema(), token)


# ------------------------------------------------------------------ печать


def test_render_schema_empty_and_json() -> None:
    assert render_schema(StorageSchema(), False) == "<пустая схема>"
    assert '"axes": []' in render_schema(StorageSchema(), True)


def test_render_schema_lists_axes_and_scalars() -> None:
    from axislar_core.contracts import ScalarDefinition

    rendered = render_schema(
        StorageSchema(
            axes=(
                AxisDefinition(name="theme", kind=AxisKind.CATEGORICAL, multi=True),
                AxisDefinition(name="author", kind=AxisKind.REFERENCE, target="person"),
                AxisDefinition(
                    name="genre", kind=AxisKind.CATEGORICAL, strict=True, values=("noir",)
                ),
            ),
            scalars=(ScalarDefinition(name="title", searchable=True),),
        ),
        False,
    )
    assert "axis theme: categorical multi" in rendered
    assert "target=person" in rendered
    assert "strict=['noir']" in rendered
    assert "scalar title searchable" in rendered


def test_render_result_text_and_json() -> None:
    from axislar_core.contracts import QueryResult

    result = QueryResult(ids=(), total=0)
    assert render_result(result, False) == "# total=0 limit=None offset=0"
    assert json.loads(render_result(result, True))["total"] == 0


def test_render_plugins_reports_rejected() -> None:
    incompatible = PluginManifest(
        name="future",
        kind=PluginKind.STORAGE,
        requires_contract=">=9",
        factory=lambda _options: None,
    )
    registry = PluginRegistry([], core_contract="2", builtins=[incompatible])
    product = build_product(registry=registry)

    text = render_plugins(product, False)
    assert "available: <нет>" in text
    assert "rejected: future" in text

    payload = json.loads(render_plugins(product, True))
    assert payload["rejected"][0]["name"] == "future"


# ------------------------------------------------------------------ команды


def run_cli(*argv: str) -> int:
    return main(list(argv))


def test_schema_commands_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = ["--store", "json", "--data", str(tmp_path / "v.json")]
    assert run_cli(*data, "schema", "add-axis", "--name", "theme", "--kind", "categorical") == 0
    assert run_cli(*data, "schema", "add-scalar", "--name", "title", "--searchable") == 0

    capsys.readouterr()
    assert run_cli(*data, "schema", "show") == 0
    out = capsys.readouterr().out
    assert "axis theme: categorical" in out
    assert "scalar title searchable" in out

    assert run_cli(*data, "schema", "show", "--json") == 0
    assert json.loads(capsys.readouterr().out)["axes"][0]["name"] == "theme"


def test_put_query_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = ["--store", "json", "--data", str(tmp_path / "v.json")]
    assert (
        run_cli(*data, "schema", "add-axis", "--name", "theme", "--kind", "categorical", "--multi")
        == 0
    )
    assert run_cli(*data, "schema", "add-axis", "--name", "year", "--kind", "measurable") == 0
    assert (
        run_cli(
            *data,
            "put",
            "--id",
            "a-1",
            "--kind",
            "edit",
            "--facet",
            "theme=noir,cyberpunk",
            "--facet",
            "year=2024",
        )
        == 0
    )
    assert run_cli(*data, "put", "--id", "b-2", "--kind", "edit", "--facet", "theme=fantasy") == 0

    capsys.readouterr()
    assert run_cli(*data, "query", "--filter", "theme:contains=noir") == 0
    assert "a-1" in capsys.readouterr().out

    assert run_cli(*data, "query", "--filter", "year:gte=2020", "--json") == 0
    assert json.loads(capsys.readouterr().out)["ids"] == ["a-1"]

    assert run_cli(*data, "show", "--id", "a-1", "--json") == 0
    assert json.loads(capsys.readouterr().out)["id"] == "a-1"


def test_put_reports_duplicate_facet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = ["--store", "json", "--data", str(tmp_path / "v.json")]
    assert run_cli(*data, "schema", "add-axis", "--name", "theme", "--kind", "categorical") == 0
    assert (
        run_cli(
            *data,
            "put",
            "--id",
            "x",
            "--kind",
            "edit",
            "--facet",
            "theme=a",
            "--facet",
            "theme=b",
        )
        == 2
    )
    assert "given twice" in capsys.readouterr().err


def test_put_reports_domain_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = ["--store", "json", "--data", str(tmp_path / "v.json")]
    assert run_cli(*data, "put", "--id", "x", "--kind", "edit", "--facet", "colour=red") == 2
    assert "unknown axis" in capsys.readouterr().err


def test_show_missing_object(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        run_cli("--store", "json", "--data", str(tmp_path / "v.json"), "show", "--id", "nope") == 1
    )
    assert "not found" in capsys.readouterr().err


def test_bad_filter_and_unknown_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli("query", "--filter", "theme") == 2
    assert "bad filter" in capsys.readouterr().err

    assert run_cli("--store", "nope", "schema", "show") == 2
    assert "not found" in capsys.readouterr().err


def test_broken_store_file_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "v.json"
    path.write_text("{broken", encoding="utf-8")
    assert run_cli("--store", "json", "--data", str(path), "schema", "show") == 2
    assert "broken JSON" in capsys.readouterr().err


def test_plugins_list_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli("plugins", "list", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["name"] for item in payload["available"]] == ["json", "sqlite"]


def test_memory_store_is_default(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli("plugins", "list") == 0
    output = capsys.readouterr().out
    assert "json (storage" in output and "sqlite (storage" in output


def test_parser_requires_subcommand() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_namespace_maps_to_command() -> None:
    args = parser_ns("query", "--filter", "year:exists")
    assert args.command == "query"


def test_duplicate_axis_and_scalar_report_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = ["--store", "json", "--data", str(tmp_path / "v.json")]
    assert run_cli(*data, "schema", "add-axis", "--name", "theme", "--kind", "categorical") == 0
    assert run_cli(*data, "schema", "add-axis", "--name", "theme", "--kind", "categorical") == 2
    assert "duplicate_axis" in capsys.readouterr().err

    assert run_cli(*data, "schema", "add-scalar", "--name", "title") == 0
    assert run_cli(*data, "schema", "add-scalar", "--name", "title") == 2
    assert "duplicate_scalar" in capsys.readouterr().err


def test_show_without_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = ["--store", "json", "--data", str(tmp_path / "v.json")]
    assert run_cli(*data, "put", "--id", "x", "--kind", "edit") == 0
    capsys.readouterr()
    assert run_cli(*data, "show", "--id", "x") == 0
    assert '"id":"x"' in capsys.readouterr().out


def test_invalid_entity_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = ["--store", "json", "--data", str(tmp_path / "v.json")]
    assert run_cli(*data, "put", "--id", "x", "--kind", "") == 2
    assert "error:" in capsys.readouterr().err


def test_put_domain_error_from_engine(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = ["--store", "json", "--data", str(tmp_path / "v.json")]
    assert run_cli(*data, "put", "--id", "x", "--kind", "edit", "--scalar", "nope=x") == 2
    assert "unknown_scalar" in capsys.readouterr().err


def test_store_show_without_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run_cli("store", "show") == 0
    out = capsys.readouterr().out
    assert "config: <нет файла" in out
    assert "store: memory" in out


def test_store_use_writes_config_and_show_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run_cli("store", "use", "json", "--data", "v.json") == 0
    assert "->" in capsys.readouterr().out
    assert (tmp_path / "agoralar.toml").is_file()

    assert run_cli("store", "show") == 0
    out = capsys.readouterr().out
    assert "store: json" in out
    assert "data: v.json" in out

    # конфиг подхватывается командами без --store/--data
    assert run_cli("schema", "add-axis", "--name", "theme", "--kind", "categorical") == 0
    assert (tmp_path / "v.json").is_file()


def test_store_use_rejects_unknown_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run_cli("store", "use", "nope") == 2
    assert "not found" in capsys.readouterr().err
    assert not (tmp_path / "agoralar.toml").exists()


def test_store_use_builtin_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run_cli("store", "use", "memory") == 0
    capsys.readouterr()
    assert run_cli("store", "show") == 0
    assert "store: memory" in capsys.readouterr().out


def test_broken_config_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agoralar.toml").write_text("store = ", encoding="utf-8")
    assert run_cli("schema", "show") == 2
    assert "broken config" in capsys.readouterr().err


def test_plugin_options_from_config_are_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agoralar.toml").write_text(
        'store = "json"\n\n[plugins.json]\npath = "custom.json"\n', encoding="utf-8"
    )
    assert run_cli("put", "--id", "x", "--kind", "edit") == 0
    assert (tmp_path / "custom.json").is_file()
    capsys.readouterr()
    assert run_cli("show", "--id", "x") == 0
    assert '"id":"x"' in capsys.readouterr().out
