"""История прогонов синхронизации: сохранение, чтение, отчёт."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nu.psi.contracts.beta import (
    STATE_VERSION,
    SyncState,
    SyncStateError,
    load_state,
    record_sync,
    save_state,
    state_path_for,
    sync_now,
)

STAMP = datetime(2025, 11, 25, 12, 0, tzinfo=UTC)


def state(source_id: str = "src", counts: dict[str, int] | None = None) -> SyncState:
    return SyncState(
        source_id=source_id,
        last_run_at=STAMP,
        scanned=3,
        counts=counts or {"create": 2, "tombstone": 1},
        conflicts=0,
    )


def test_state_path_is_under_data_dir(tmp_path: Path) -> None:
    assert state_path_for(tmp_path) == tmp_path / ".agoralar" / "sync.json"


def test_missing_file_means_empty_history(tmp_path: Path) -> None:
    assert load_state(tmp_path / "sync.json") == {}


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "sync.json"
    save_state(path, {"src": state()})
    loaded = load_state(path)
    assert loaded["src"].scanned == 3
    assert loaded["src"].counts == {"create": 2, "tombstone": 1}
    assert loaded["src"].last_run_at == STAMP


def test_record_replaces_only_its_source() -> None:
    first = record_sync({}, state("a"))
    updated = record_sync(first, state("b"))
    assert set(updated) == {"a", "b"}
    assert record_sync(first, state("a", {"update": 1}))["a"].counts == {"update": 1}


def test_describe_is_human_readable() -> None:
    text = state().describe()
    assert "scanned=3" in text
    assert "create=2" in text


def test_broken_json_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "sync.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(SyncStateError, match="broken sync state"):
        load_state(path)


def test_unsupported_version_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "sync.json"
    path.write_text(json.dumps({"version": 99, "sources": {}}), encoding="utf-8")
    with pytest.raises(SyncStateError, match="unsupported sync state version"):
        load_state(path)


def test_broken_entry_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "sync.json"
    path.write_text(
        json.dumps({"version": STATE_VERSION, "sources": {"a": {"scanned": 1}}}), encoding="utf-8"
    )
    with pytest.raises(SyncStateError, match="broken sync state"):
        load_state(path)


def test_save_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "sync.json"
    save_state(path, {"src": state()})
    assert list((tmp_path / "nested").glob("*.tmp")) == []


def test_now_is_timezone_aware() -> None:
    assert sync_now().tzinfo is not None
