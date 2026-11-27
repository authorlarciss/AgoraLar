"""HTTP-источник: контракт синхронизации, транспорт, раскладка полей."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from axislar_core.contracts import (
    AxisDefinition,
    AxisKind,
    CategoricalValue,
    EntityId,
    MeasurableValue,
    ReferenceValue,
    ScalarDefinition,
    StorageSchema,
    entity_id_for,
    run_source_sync_contract,
)

from agora_source_http import (
    KEY_SEPARATOR,
    HttpSource,
    HttpSourceError,
    create,
    plugin,
)

SCHEMA = StorageSchema(
    axes=(
        AxisDefinition(name="author", kind=AxisKind.CATEGORICAL),
        AxisDefinition(name="genre", kind=AxisKind.CATEGORICAL, multi=True),
        AxisDefinition(name="year", kind=AxisKind.MEASURABLE),
        AxisDefinition(name="editor", kind=AxisKind.REFERENCE, target="person"),
    ),
    scalars=(ScalarDefinition(name="title"),),
)

RECORDS: list[dict[str, Any]] = [
    {
        "id": "e-1",
        "title": "Alpha",
        "author": "ann",
        "genre": ["noir", "epic"],
        "year": 1999,
        "editor": "p-1",
        "noise": "n1",
    },
    {"id": "e-2", "title": "Beta", "author": None, "genre": None, "year": None, "editor": None},
    {
        "id": "e-3",
        "title": "Gamma",
        "author": "bob",
        "genre": "noir",
        "year": "2004",
        "editor": "p-2",
        "noise": "n3",
    },
]


@contextmanager
def serve(
    payload: object, *, status: int = 200, raw: bytes | None = None
) -> Iterator[tuple[str, dict[str, str]]]:
    """Поднять локальный сервер на порту 0; отдать URL и место для заголовков."""
    body = raw if raw is not None else json.dumps(payload).encode()
    seen: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - имя задаёт stdlib
            seen.update(dict(self.headers))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield f"http://127.0.0.1:{port}/edits", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def source(url: str, **options: object) -> HttpSource:
    settings: dict[str, object] = {
        "url": url,
        "source_id": "api-edits",
        "kind": "edit",
        "axes": ["author", "genre"],
        "multi": {"genre": ","},
        "measurable": ["year"],
        "reference": {"editor": "person"},
        "scalars": ["title"],
    }
    settings.update(options)
    return create(settings)


def categorical(value: object) -> CategoricalValue:
    """Сузить фасет до категориального значения в тестах."""
    assert isinstance(value, CategoricalValue)
    return value


async def test_source_passes_sync_contract() -> None:
    with serve(RECORDS) as (url, _):
        await run_source_sync_contract(lambda: source(url), SCHEMA)


async def test_fields_become_facets_and_scalars() -> None:
    with serve(RECORDS) as (url, _):
        entities = [entity async for entity in source(url).scan()]

    assert len(entities) == 3
    first = entities[0]
    assert first.kind == "edit"
    assert [categorical(value).value for value in first.facets["author"]] == ["ann"]
    assert [categorical(value).value for value in first.facets["genre"]] == ["noir", "epic"]
    year = first.facets["year"][0]
    assert isinstance(year, MeasurableValue) and year.value == pytest.approx(1999.0)
    editor = first.facets["editor"][0]
    assert isinstance(editor, ReferenceValue) and editor.target == EntityId("p-1")
    assert first.scalars == {"title": "Alpha"}
    assert {owner.value for owner in first.owners.values()} == {"source"}
    assert set(first.owners) == set(first.facets)


async def test_null_means_absent_not_empty() -> None:
    with serve(RECORDS) as (url, _):
        entities = [entity async for entity in source(url).scan()]
    beta = entities[1]
    assert beta.facets == {}, "null не создаёт фасет: объект разреженный"
    assert beta.scalars == {"title": "Beta"}


async def test_string_multi_is_split_too() -> None:
    with serve(RECORDS) as (url, _):
        entities = [entity async for entity in source(url).scan()]
    gamma = entities[2]
    assert [categorical(value).value for value in gamma.facets["genre"]] == ["noir"]
    year = gamma.facets["year"][0]
    assert isinstance(year, MeasurableValue) and year.value == pytest.approx(2004.0)


async def test_id_follows_kernel_policy() -> None:
    with serve(RECORDS) as (url, _):
        first = await anext(source(url).scan())
    assert first.origin is not None
    assert first.id == entity_id_for("api-edits", first.origin.key)
    assert KEY_SEPARATOR not in first.origin.key, "ключ из одного поля — без разделителя"


async def test_items_path_reaches_nested_array() -> None:
    payload = {"data": {"items": RECORDS[2:]}}
    with serve(payload) as (url, _):
        entities = [entity async for entity in source(url, items="data.items").scan()]
    assert [entity.scalars["title"] for entity in entities] == ["Gamma"]


async def test_missing_items_path_is_reported() -> None:
    with serve({"data": {}}) as (url, _):
        src = source(url, items="data.items")
        with pytest.raises(HttpSourceError, match="no 'data.items'"):
            _ = [entity async for entity in src.scan()]


async def test_non_array_response_is_reported() -> None:
    with serve({"data": "nope"}) as (url, _):
        src = source(url)
        with pytest.raises(HttpSourceError, match="array"):
            _ = [entity async for entity in src.scan()]


async def test_http_error_status_is_reported() -> None:
    with serve({}, status=500) as (url, _):
        src = source(url)
        with pytest.raises(HttpSourceError, match="http 500"):
            _ = [entity async for entity in src.scan()]


async def test_invalid_json_is_reported() -> None:
    with serve({}, raw=b"{broken") as (url, _):
        src = source(url)
        with pytest.raises(HttpSourceError, match="invalid JSON"):
            _ = [entity async for entity in src.scan()]


async def test_unreachable_host_is_reported() -> None:
    with serve({}) as (url, _):
        pass
    src = source(url, timeout=1.0)
    with pytest.raises(HttpSourceError, match="cannot reach"):
        _ = [entity async for entity in src.scan()]


async def test_record_without_key_is_skipped() -> None:
    payload = [{"title": "Broken"}, RECORDS[0]]
    with serve(payload) as (url, _):
        src = source(url)
        entities = [entity async for entity in src.scan()]
    assert len(entities) == 1
    assert any("ключевого поля" in warning for warning in src.warnings)


async def test_bool_measurable_is_reported_and_skipped() -> None:
    payload = [{"id": "e-1", "title": "Alpha", "year": True}]
    with serve(payload) as (url, _):
        src = source(url)
        entity = await anext(src.scan())
    assert "year" not in entity.facets
    assert any("не число" in warning for warning in src.warnings)


async def test_hash_ignores_unmapped_fields() -> None:
    with serve(RECORDS) as (url, _):
        first = await anext(source(url).scan())

    changed = {**RECORDS[0], "noise": "other"}
    with serve([changed]) as (url, _):
        second = await anext(source(url).scan())
    assert first.origin is not None and second.origin is not None
    assert first.origin.content_hash == second.origin.content_hash

    changed_title = {**RECORDS[0], "title": "Alpha 2"}
    with serve([changed_title]) as (url, _):
        third = await anext(source(url).scan())
    assert third.origin is not None
    assert third.origin.content_hash != first.origin.content_hash


async def test_headers_are_sent() -> None:
    with serve(RECORDS) as (url, seen):
        src = source(url, headers={"X-Token": "secret"})
        _ = [entity async for entity in src.scan()]
    assert seen.get("X-Token") == "secret"


def test_plugin_manifest() -> None:
    manifest = plugin()
    assert manifest["requires_contract"] == ">=6,<7"
    assert callable(manifest["factory"])


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"url": "http://x/"},
        {"url": "http://x/", "source_id": "s", "axes": ["a"], "scalars": ["a"]},
        {"url": "http://x/", "source_id": "s", "multi": {"a": ","}},
        {"url": "http://x/", "source_id": "s", "axes": ["a"], "multi": {"a": ","}, "timeout": 0},
        {"url": "http://x/", "source_id": "s", "key": {"a": 1}},
        {"url": "http://x/", "source_id": "s", "headers": ["x"]},
    ],
)
def test_create_rejects_bad_options(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        create(options)
