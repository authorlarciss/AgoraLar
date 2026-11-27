"""HTTP-маршруты продукта: без сокетов, через HttpApi.handle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nu.gamma.public import GammaResolver, HttpResolver
from nu.gamma.public.http_api import HttpApi
from nu.psi import build_product
from nu.psi.contracts.alpha import Config


def api(tmp_path: Path) -> HttpApi:
    product = build_product(config=Config(store="json", data=tmp_path / "vault.json"))
    return HttpResolver().make_http_api(product)


async def call(
    target: HttpApi, method: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    result = await target.handle(method, path, body)
    return result[0], result[1]


async def test_health(tmp_path: Path) -> None:
    target = api(tmp_path)
    status, payload = await call(target, "GET", "/health")
    assert status == 200
    assert payload["store"] == "json"
    assert payload["kernel_contract"] == "5"


async def test_schema_is_empty_initially(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "GET", "/schema")
    assert status == 200
    assert payload["schema"]["axes"] == []


async def test_add_axis_and_scalar(tmp_path: Path) -> None:
    target = api(tmp_path)
    status, payload = await call(
        target, "POST", "/axes", {"name": "theme", "kind": "categorical", "multi": True}
    )
    assert status == 201, payload
    assert payload["axis"]["name"] == "theme"

    status, _ = await call(target, "POST", "/scalars", {"name": "title", "searchable": True})
    assert status == 201

    status, payload = await call(target, "GET", "/schema")
    assert status == 200
    assert [axis["name"] for axis in payload["schema"]["axes"]] == ["theme"]
    assert [scalar["name"] for scalar in payload["schema"]["scalars"]] == ["title"]


async def test_duplicate_axis_returns_422(tmp_path: Path) -> None:
    target = api(tmp_path)
    await call(target, "POST", "/axes", {"name": "theme", "kind": "categorical"})
    status, payload = await call(target, "POST", "/axes", {"name": "theme", "kind": "categorical"})
    assert status == 422
    assert payload["errors"][0]["code"] == "duplicate_axis"


async def test_invalid_axis_returns_400(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "POST", "/axes", {"name": "Bad Name"})
    assert status == 400
    assert "invalid axis" in payload["error"]


async def test_put_and_get_object(tmp_path: Path) -> None:
    target = api(tmp_path)
    await call(target, "POST", "/axes", {"name": "theme", "kind": "categorical"})
    entity = {
        "kind": "video",
        "facets": {"theme": [{"kind": "categorical", "value": "noir"}]},
    }
    status, payload = await call(target, "PUT", "/objects/e-1", entity)
    assert status == 201, payload

    status, payload = await call(target, "GET", "/objects/e-1")
    assert status == 200
    assert payload["entity"]["id"] == "e-1"
    assert payload["entity"]["facets"]["theme"][0]["value"] == "noir"


async def test_get_missing_object_returns_404(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "GET", "/objects/none")
    assert status == 404
    assert "not found" in payload["error"]


async def test_invalid_entity_returns_400(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "PUT", "/objects/e-1", {"kind": "video", "x": 1})
    assert status == 400
    assert "invalid entity" in payload["error"]


async def test_unknown_axis_on_put_returns_422(tmp_path: Path) -> None:
    entity = {"kind": "video", "facets": {"colour": [{"kind": "categorical", "value": "red"}]}}
    status, payload = await call(api(tmp_path), "PUT", "/objects/e-1", entity)
    assert status == 422
    assert payload["errors"][0]["code"] == "unknown_axis"


async def test_query_returns_ids(tmp_path: Path) -> None:
    target = api(tmp_path)
    await call(target, "POST", "/axes", {"name": "theme", "kind": "categorical"})
    await call(
        target,
        "PUT",
        "/objects/e-1",
        {"kind": "video", "facets": {"theme": [{"kind": "categorical", "value": "noir"}]}},
    )
    status, payload = await call(
        target, "POST", "/query", {"all": [{"axis": "theme", "op": "contains", "values": ["noir"]}]}
    )
    assert status == 200
    assert payload["ids"] == ["e-1"]
    assert payload["total"] == 1


async def test_invalid_query_returns_400(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "POST", "/query", {"all": [{"axis": "x"}]})
    assert status == 400
    assert "invalid query" in payload["error"]


async def test_plugins_listing(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "GET", "/plugins")
    assert status == 200
    names = {item["name"] for item in payload["available"]}
    assert {"filesystem", "json", "sqlite"} <= names


async def test_unknown_path_and_method(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "GET", "/nope")
    assert status == 404
    assert payload["error"] == "not found"

    status, payload = await call(api(tmp_path), "DELETE", "/schema")
    assert status == 405
    assert payload["error"] == "method not allowed"


async def test_missing_and_broken_body(tmp_path: Path) -> None:
    status, payload = await call(api(tmp_path), "POST", "/axes")
    assert status == 400

    broken = await api(tmp_path).handle("POST", "/axes", b"{not json")
    assert broken[0] == 400
    assert "invalid JSON" in broken[1]["error"]

    array = await api(tmp_path).handle("POST", "/axes", b"[]")
    assert array[0] == 400
    assert "JSON object" in array[1]["error"]


def test_resolvers_are_wired() -> None:
    product = build_product()
    api_ = HttpResolver().make_http_api(product)
    assert hasattr(api_, "handle")
    assert callable(GammaResolver().make_cli())


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
async def test_every_method_has_a_route_or_405(tmp_path: Path, method: str) -> None:
    status, _ = await call(api(tmp_path), method, "/unknown")
    assert status in {404, 405}


# ------------------------------------------------------- артефакты (contract 5)


async def test_artifact_roundtrip(tmp_path: Path) -> None:
    import base64

    target = api(tmp_path)
    encoded = base64.b64encode(b"preview-bytes").decode("ascii")

    status, payload = await call(
        target, "POST", "/artifacts", {"media_type": "image/png", "data": encoded}
    )
    assert status == 201, payload
    content_hash = payload["content_hash"]
    assert payload["size"] == len(b"preview-bytes")

    status, payload = await call(target, "GET", "/artifacts")
    assert status == 200
    assert [item["content_hash"] for item in payload["artifacts"]] == [content_hash]

    status, payload = await call(target, "GET", f"/artifacts/{content_hash}")
    assert status == 200
    assert payload["artifact"]["media_type"] == "image/png"
    assert payload["artifact"]["data"] == encoded

    status, payload = await call(target, "DELETE", f"/artifacts/{content_hash}")
    assert status == 200 and payload["deleted"] == content_hash

    status, _ = await call(target, "GET", f"/artifacts/{content_hash}")
    assert status == 404


async def test_artifact_put_is_idempotent(tmp_path: Path) -> None:
    import base64

    target = api(tmp_path)
    body = {"media_type": "image/png", "data": base64.b64encode(b"same").decode("ascii")}
    first = await call(target, "POST", "/artifacts", body)
    second = await call(target, "POST", "/artifacts", body)
    assert first[0] == second[0] == 201
    assert first[1]["content_hash"] == second[1]["content_hash"]

    status, payload = await call(target, "GET", "/artifacts")
    assert status == 200 and len(payload["artifacts"]) == 1


async def test_artifact_rejects_bad_body(tmp_path: Path) -> None:
    target = api(tmp_path)
    assert (await call(target, "POST", "/artifacts", {"data": "AAAA"}))[0] == 400
    assert (await call(target, "POST", "/artifacts", {"media_type": "image/png"}))[0] == 400

    status, _ = await call(
        target, "POST", "/artifacts", {"media_type": "image/png", "data": "not-base64!"}
    )
    assert status == 400


async def test_artifact_in_use_is_conflict(tmp_path: Path) -> None:
    import base64

    target = api(tmp_path)
    encoded = base64.b64encode(b"held").decode("ascii")
    _, payload = await call(
        target, "POST", "/artifacts", {"media_type": "image/png", "data": encoded}
    )
    content_hash = payload["content_hash"]

    entity = {
        "kind": "edit",
        "artifacts": {
            "preview": {"content_hash": content_hash, "media_type": "image/png", "size": 4}
        },
    }
    status, _ = await call(target, "PUT", "/objects/e-1", entity)
    assert status == 201

    status, payload = await call(target, "DELETE", f"/artifacts/{content_hash}")
    assert status == 409
    assert payload["errors"][0]["code"] == "artifact_in_use"


# ------------------------------------------------- аутентификация (ADR-0017)


def secured(tmp_path: Path) -> HttpApi:
    product = build_product(config=Config(store="json", data=tmp_path / "vault.json"))
    return HttpResolver().make_http_api(product, token="s3cret")


async def call_with(
    target: HttpApi,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    result = await target.handle(method, path, body, headers)
    return result[0], result[1]


async def test_token_is_required(tmp_path: Path) -> None:
    target = secured(tmp_path)
    assert (await call(target, "GET", "/schema"))[0] == 401
    assert (await call(target, "GET", "/artifacts"))[0] == 401

    status, payload = await call_with(
        target, "GET", "/schema", headers={"Authorization": "Bearer s3cret"}
    )
    assert status == 200 and "schema" in payload


async def test_health_stays_open(tmp_path: Path) -> None:
    status, payload = await call_with(secured(tmp_path), "GET", "/health")
    assert status == 200
    assert payload["store"] == "json", "проба живости не требует токена"


async def test_wrong_scheme_and_value_are_rejected(tmp_path: Path) -> None:
    target = secured(tmp_path)
    for header in ("s3cret", "Basic s3cret", "Bearer wrong", "Bearer "):
        status, _ = await call_with(target, "GET", "/schema", headers={"Authorization": header})
        assert status == 401, header


async def test_lowercase_header_name_is_accepted(tmp_path: Path) -> None:
    status, _ = await call_with(
        secured(tmp_path), "GET", "/schema", headers={"authorization": "Bearer s3cret"}
    )
    assert status == 200


async def test_no_token_means_open_local_mode(tmp_path: Path) -> None:
    assert (await call(api(tmp_path), "GET", "/schema"))[0] == 200
