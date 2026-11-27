"""Текстовый анализатор: обрезка по символам, типы, манифест."""

from __future__ import annotations

import pytest

from agora_analyzer_text import (
    DEFAULT_LIMIT,
    ELLIPSIS,
    VERSION,
    TextPreviewAnalyzer,
    create,
    plugin,
)


def analyzer(**options: object) -> TextPreviewAnalyzer:
    return create(options)


def test_accepts_text_and_structured_types() -> None:
    target = analyzer()
    for media_type in ("text/plain", "text/markdown; charset=utf-8", "application/json"):
        assert target.accepts(media_type), media_type
    for media_type in ("image/png", "video/mp4", "application/octet-stream"):
        assert not target.accepts(media_type), media_type


def test_preview_is_truncated_by_characters() -> None:
    target = analyzer(limit=5)
    role, data, media_type = target.analyze("привет мир".encode(), "text/plain")
    assert role == "preview"
    assert media_type == "text/plain; charset=utf-8"
    assert data.decode() == "приве" + ELLIPSIS, "обрезка по символам, а не байтам"


def test_short_text_is_untouched() -> None:
    _, data, _ = analyzer(limit=50).analyze(b"short", "text/plain")
    assert data == b"short"


def test_invalid_bytes_do_not_crash() -> None:
    _, data, _ = analyzer().analyze(b"\xff\xfe broken", "text/plain")
    assert isinstance(data, bytes), "битый файл не роняет прогон"


def test_custom_role_and_media_types() -> None:
    target = analyzer(role="excerpt", media_types=["text/csv"])
    assert target.role == "excerpt"
    assert target.accepts("text/csv")
    assert not target.accepts("text/plain"), "список media_types сужает область"


def test_version_is_reported() -> None:
    assert analyzer().version == VERSION


def test_manifest_is_plain_data() -> None:
    manifest = plugin()
    assert manifest["requires_contract"] == ">=6,<7"
    assert callable(manifest["factory"])


@pytest.mark.parametrize(
    "options",
    [
        {"limit": 0},
        {"limit": -3},
        {"limit": True},
        {"limit": "10"},
        {"role": "   "},
        {"media_types": {"a": 1}},
    ],
)
def test_create_rejects_bad_options(options: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        create(options)


def test_default_limit_is_used() -> None:
    assert analyzer().limit == DEFAULT_LIMIT
