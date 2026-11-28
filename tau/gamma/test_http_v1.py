"""API v1: список объектов, карточка с ETag, связи, статистика, статика."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest
from axislar_core.contracts import AxisDefinition, AxisKind, Entity, ScalarDefinition

from nu.gamma.public import HttpResolver
from nu.gamma.public.http_server import make_server
from nu.psi import build_product
from nu.psi.contracts.alpha import Config


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[int, Any]]:
    product = build_product(config=Config(store="json", data=tmp_path / "vault.json"))
    api = HttpResolver().make_http_api(product)

    import asyncio

    async def seed() -> None:
        assert (
            await product.core.add_axis(
                AxisDefinition(name="tags", kind=AxisKind.CATEGORICAL, multi=True)
            )
            == []
        )
        assert (
            await product.core.add_axis(
                AxisDefinition(name="links", kind=AxisKind.REFERENCE, multi=True, target="note")
            )
            == []
        )
        assert await product.core.add_scalar(ScalarDefinition(name="title")) == []
        errors = await product.core.storage.bulk_upsert(
            [
                Entity.model_validate(
                    {
                        "id": "n1",
                        "kind": "note",
                        "scalars": {"title": "Первая"},
                        "owners": {"tags": "source"},
                        "facets": {
                            "tags": [{"kind": "categorical", "value": "noir"}],
                            "links": [{"kind": "reference", "target": "n2"}],
                        },
                    }
                ),
                Entity.model_validate({"id": "n2", "kind": "note", "scalars": {"title": "Вторая"}}),
            ]
        )
        assert errors == []

    asyncio.run(seed())
    httpd = make_server(api, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], api
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def request(
    port: int,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: Any = None,
) -> tuple[int, Any, dict[str, str]]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    sent = dict(headers or {})
    if payload is not None:
        sent.setdefault("Content-Type", "application/json")
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, headers=sent, body=payload)
    response = connection.getresponse()
    raw = response.read()
    content_type = response.getheader("Content-Type") or ""
    parsed: Any = (
        json.loads(raw.decode("utf-8")) if "application/json" in content_type and raw else raw
    )
    result_headers = {name.lower(): value for name, value in response.getheaders()}
    return response.status, parsed, result_headers


def test_v1_root_reports_versions(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1")
    assert status == 200
    assert body["api"] == "v1"
    assert body["kernel_contract"]


def test_objects_list_with_filter_and_pagination(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1/objects?limit=1")
    assert status == 200
    assert body["total"] == 2
    assert len(body["objects"]) == 1
    assert body["limit"] == 1

    status, filtered, _ = request(port, "GET", "/api/v1/objects?filter=tags:contains=noir")
    assert status == 200
    assert [row["id"] for row in filtered["objects"]] == ["n1"]
    assert filtered["objects"][0]["title"] == "Первая"


def test_objects_list_rejects_bad_filter_and_unknown_axis(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1/objects?filter=noop")
    assert status == 400
    assert body["error"]["code"] == "invalid_filter"

    status, body, _ = request(port, "GET", "/api/v1/objects?filter=ghost:exists")
    assert status == 400
    assert body["error"]["code"] == "unknown_axis"
    assert body["error"]["axis"] == "ghost"


def test_object_etag_allows_conditional_get(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, headers = request(port, "GET", "/api/v1/objects/n1")
    assert status == 200
    etag = headers["etag"]
    assert etag == '"r1"', "ETag v1 — ревизия объекта (ADR-0021)"
    assert body["entity"]["id"] == "n1"

    status, body, headers = request(
        port, "GET", "/api/v1/objects/n1", headers={"If-None-Match": etag}
    )
    assert status == 304
    assert body == b""
    assert headers["etag"] == etag


def test_links_split_into_incoming_and_outgoing(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1/objects/n2/links")
    assert status == 200
    assert [row["id"] for row in body["incoming"]] == ["n1"]
    assert body["incoming"][0]["axis"] == "links"
    assert body["outgoing"] == []

    status, body, _ = request(port, "GET", "/api/v1/objects/n1/links")
    assert [row["id"] for row in body["outgoing"]] == ["n2"]


def test_stats_shape(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1/stats")
    assert status == 200
    assert body["objects"] == {"total": 2, "visible": 2, "deleted": 0}
    names = {axis["name"] for axis in body["axes"]}
    assert "tags" in names


def test_unknown_v1_route_is_structured_404(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/api/v1/nope")
    assert status == 404
    assert body["error"]["code"] == "not_found"


def test_static_index_and_asset(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, headers = request(port, "GET", "/")
    assert status == 200
    assert "text/html" in headers["content-type"]
    assert b"AgoraLar" in body
    assert b'id="chips"' in body, "фильтры показываются чипами"

    status, body, headers = request(port, "GET", "/static/app.js")
    assert status == 200
    assert "javascript" in headers["content-type"]
    assert b"/api/v1/objects" in body
    assert b"history.pushState" in body, "состояние выборки хранится в адресе"
    assert b"getAll('f')" in body

    status, _, _ = request(port, "GET", "/static/../http_api.py")
    assert status == 404


def test_legacy_routes_still_work(server: tuple[int, Any]) -> None:
    port, _ = server
    status, body, _ = request(port, "GET", "/objects/n1")
    assert status == 200
    assert body["entity"]["id"] == "n1"


def test_put_with_if_match_writes_and_moves_etag(server: tuple[int, Any]) -> None:
    port, _ = server
    _, body, headers = request(port, "GET", "/api/v1/objects/n2")
    etag = headers["etag"]
    assert etag == '"r1"'

    status, reply, sent = request(
        port,
        "PUT",
        "/api/v1/objects/n2",
        headers={"If-Match": etag},
        body={"kind": "note", "scalars": {"title": "Вторая (правка)"}},
    )
    assert status == 200, reply
    assert reply["rev"] == 2
    assert sent["etag"] == '"r2"'

    _, check, _ = request(port, "GET", "/api/v1/objects/n2")
    assert check["entity"]["scalars"]["title"] == "Вторая (правка)"


def test_stale_if_match_gives_conflict_with_current_state(server: tuple[int, Any]) -> None:
    """Две правки одного объекта: вторая получает 409, а не затирает первую."""
    port, _ = server
    _, _, headers = request(port, "GET", "/api/v1/objects/n2")
    first = headers["etag"]

    assert (
        request(
            port,
            "PUT",
            "/api/v1/objects/n2",
            headers={"If-Match": first},
            body={"kind": "note", "scalars": {"title": "моя"}},
        )[0]
        == 200
    )

    status, reply, _ = request(
        port,
        "PUT",
        "/api/v1/objects/n2",
        headers={"If-Match": first},
        body={"kind": "note", "scalars": {"title": "чужая"}},
    )
    assert status == 409
    assert reply["error"]["code"] == "conflict"
    assert reply["current"]["etag"] == '"r2"'
    assert reply["current"]["entity"]["scalars"]["title"] == "моя", "правка не потеряна"

    _, check, _ = request(port, "GET", "/api/v1/objects/n2")
    assert check["entity"]["scalars"]["title"] == "моя"


def test_if_match_star_requires_existing_object(server: tuple[int, Any]) -> None:
    port, _ = server
    status, reply, _ = request(
        port,
        "PUT",
        "/api/v1/objects/ghost",
        headers={"If-Match": "*"},
        body={"kind": "note"},
    )
    assert status == 404
    assert reply["error"]["code"] == "not_found"

    assert (
        request(
            port,
            "PUT",
            "/api/v1/objects/n2",
            headers={"If-Match": "*"},
            body={"kind": "note", "scalars": {"title": "Вторая"}},
        )[0]
        == 200
    )


def test_put_rejects_malformed_if_match(server: tuple[int, Any]) -> None:
    port, _ = server
    status, reply, _ = request(
        port,
        "PUT",
        "/api/v1/objects/n2",
        headers={"If-Match": "deadbeef"},
        body={"kind": "note"},
    )
    assert status == 400
    assert reply["error"]["code"] == "invalid_if_match"


def test_patch_applies_ops_and_skips_source_owned(server: tuple[int, Any]) -> None:
    port, _ = server
    body = {"ids": ["n1"], "ops": [{"axis": "tags", "mode": "add", "values": ["neo"]}]}

    status, reply, _ = request(port, "POST", "/api/v1/patch", body=body)
    assert status == 200, reply
    assert reply["changed"] == []
    assert reply["skipped"] == [{"id": "n1", "axis": "tags"}], "фасет источника не трогаем"

    forced = dict(body, force=True)
    status, reply, _ = request(port, "POST", "/api/v1/patch", body=forced)
    assert status == 200, reply
    assert reply["changed"] == [{"id": "n1", "rev": 2}]
    assert reply["skipped"] == []

    _, check, _ = request(port, "GET", "/api/v1/objects/n1")
    values = [value["value"] for value in check["entity"]["facets"]["tags"]]
    assert "neo" in values and "noir" in values


def test_patch_filter_and_stale_revisions_report_conflicts(server: tuple[int, Any]) -> None:
    port, _ = server
    body = {
        "filter": ["tags:exists"],
        "ops": [{"axis": "tags", "mode": "set", "values": ["neo"]}],
        "expected_revs": {"n1": 1},
        "force": True,
    }
    status, reply, _ = request(port, "POST", "/api/v1/patch", body=body)
    assert status == 200, reply
    assert reply["changed"] == [{"id": "n1", "rev": 2}]

    stale = dict(body, ops=[{"axis": "tags", "mode": "set", "values": ["neo", "vintage"]}])
    status, reply, _ = request(port, "POST", "/api/v1/patch", body=stale)
    assert status == 200, reply
    assert reply["conflicts"] == ["n1"], "устаревшая ревизия — конфликт, а не перезапись"
    assert reply["changed"] == []

    _, check, _ = request(port, "GET", "/api/v1/objects/n1")
    values = [value["value"] for value in check["entity"]["facets"]["tags"]]
    assert "vintage" not in values, "конфликтная правка не записана"


def test_patch_requires_exactly_one_target(server: tuple[int, Any]) -> None:
    port, _ = server
    ops = [{"axis": "tags", "mode": "unset"}]
    status, reply, _ = request(
        port, "POST", "/api/v1/patch", body={"ids": ["n1"], "filter": ["tags:exists"], "ops": ops}
    )
    assert status == 400
    assert reply["error"]["code"] == "invalid_target"

    status, reply, _ = request(port, "POST", "/api/v1/patch", body={"ops": ops})
    assert status == 400
    assert reply["error"]["code"] == "invalid_target"

    status, reply, _ = request(port, "POST", "/api/v1/patch", body={"ids": ["n1"], "ops": []})
    assert status == 400
    assert reply["error"]["code"] == "invalid_ops"


def test_patch_is_journalled_and_undo_restores(server: tuple[int, Any]) -> None:
    """Правка из UI попадает в журнал; «отменить» возвращает прежние фасеты."""
    port, _ = server
    body = {
        "ids": ["n1"],
        "ops": [{"axis": "tags", "mode": "add", "values": ["neo"]}],
        "force": True,
    }
    assert request(port, "POST", "/api/v1/patch", body=body)[0] == 200
    _, after, _ = request(port, "GET", "/api/v1/objects/n1")
    assert "neo" in [value["value"] for value in after["entity"]["facets"]["tags"]]

    status, reply, _ = request(port, "POST", "/api/v1/undo")
    assert status == 200, reply
    assert reply["undone"]["kind"] == "patch"

    _, restored, _ = request(port, "GET", "/api/v1/objects/n1")
    values = [value["value"] for value in restored["entity"]["facets"]["tags"]]
    assert values == ["noir"], "фасеты вернулись к состоянию до правки"


def test_undo_without_journal_entries_is_404(server: tuple[int, Any]) -> None:
    port, _ = server
    status, reply, _ = request(port, "POST", "/api/v1/undo")
    assert status == 404
    assert reply["error"]["code"] == "journal_empty"
