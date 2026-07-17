from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def isolated_storage(monkeypatch, tmp_path):
    import app.storage as storage

    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "jianzhen-v2.sqlite3")
    monkeypatch.setattr(storage, "_INITIALIZED", False)
    yield storage
    storage._INITIALIZED = False


def _result(task_id: str, created_at: str, visible_watermark: dict, *, verdict: str = "real") -> dict:
    return {
        "taskId": task_id,
        "reportId": f"report-{task_id}",
        "createdAt": created_at,
        "fileMeta": {
            "name": f"{task_id}.png",
            "type": "image",
            "size": "120KB",
            "resolution": "1200x800",
        },
        "verdict": verdict,
        "confidence": 0.23,
        "modelVersion": "pytest-model",
        "source": "vlm",
        "visibleWatermark": visible_watermark,
        "synthid": {"detected": False},
    }


def _clear_report() -> dict:
    return {
        "supported": True,
        "detected": False,
        "provider": None,
        "confidence": 0.0,
        "hits": [],
        "note": "本次未检出",
    }


def _boxed_report() -> dict:
    return {
        "supported": True,
        "detected": True,
        "provider": "yolo11x_watermark",
        "confidence": 0.8893,
        "hits": [
            {
                "provider": "yolo11x_watermark",
                "label": "可见水印（平台待确认）",
                "confidence": 0.8893,
                "bbox": {"x": 0.8923, "y": 0.939, "w": 0.0984, "h": 0.0401},
                "method": "yolo11x_watermark_detection",
            }
        ],
        "note": "检测到 1 处可见水印",
    }


def test_history_reuses_boxed_watermark_only_for_same_user_and_exact_sha(isolated_storage):
    storage = isolated_storage
    shared_sha = "a" * 64
    owner_one = {"userId": "user-1", "keyId": "key-1"}
    owner_two = {"userId": "user-2", "keyId": "key-2"}

    storage.put_history(
        _result("old-owner-one", "2026-07-16T08:12:00+00:00", _clear_report(), verdict="real"),
        sha256=shared_sha,
        file_size=120_000,
        thumbnail=None,
        actor=owner_one,
    )
    storage.put_history(
        _result("old-owner-two", "2026-07-16T08:13:00+00:00", _clear_report()),
        sha256=shared_sha,
        file_size=120_000,
        thumbnail=None,
        actor=owner_two,
    )
    storage.put_history(
        _result("new-owner-one", "2026-07-16T09:04:00+00:00", _boxed_report(), verdict="highly_suspected_fake"),
        sha256=shared_sha,
        file_size=120_000,
        thumbnail=None,
        actor=owner_one,
    )
    storage.put_history(
        _result("different-file", "2026-07-16T09:05:00+00:00", _clear_report()),
        sha256="b" * 64,
        file_size=120_000,
        thumbnail=None,
        actor=owner_one,
    )

    reused = storage.get_history("old-owner-one")
    other_owner = storage.get_history("old-owner-two")
    different_file = storage.get_history("different-file")
    original_hit = storage.get_history("new-owner-one")

    assert reused["verdict"] == "real"
    assert reused["confidence"] == 0.23
    assert reused["visibleWatermark"]["detected"] is True
    assert reused["visibleWatermark"]["hits"][0]["bbox"] == {
        "x": 0.8923,
        "y": 0.939,
        "w": 0.0984,
        "h": 0.0401,
    }
    assert reused["visibleWatermark"]["reanalysis"] == {
        "reused": True,
        "basis": "same-user-exact-sha256",
        "sourceTaskId": "new-owner-one",
        "sourceCreatedAt": "2026-07-16T09:04:00+00:00",
    }
    assert other_owner["visibleWatermark"]["detected"] is False
    assert different_file["visibleWatermark"]["detected"] is False
    assert "reanalysis" not in original_hit["visibleWatermark"]

    owner_one_items, _, owner_one_counts = storage.list_history(owner_user_id="user-1")
    owner_two_items, _, owner_two_counts = storage.list_history(owner_user_id="user-2")
    owner_one_by_task = {item["taskId"]: item for item in owner_one_items}

    assert owner_one_by_task["old-owner-one"]["hasVisibleWatermark"] is True
    assert owner_one_counts["watermark"] == 2
    assert owner_two_items[0]["hasVisibleWatermark"] is False
    assert owner_two_counts["watermark"] == 0


def test_admin_only_legacy_rows_reuse_exact_file_watermark(isolated_storage):
    storage = isolated_storage
    shared_sha = "c" * 64
    storage.put_history(
        _result("legacy-old", "2026-07-16T08:12:00+00:00", _clear_report()),
        sha256=shared_sha,
        file_size=120_000,
        thumbnail=None,
        actor=None,
    )
    storage.put_history(
        _result("legacy-new", "2026-07-16T09:04:00+00:00", _boxed_report()),
        sha256=shared_sha,
        file_size=120_000,
        thumbnail=None,
        actor=None,
    )

    reused = storage.get_history("legacy-old")
    all_items, _, counts = storage.list_history(owner_user_id=None)
    by_task = {item["taskId"]: item for item in all_items}

    assert reused["visibleWatermark"]["detected"] is True
    assert reused["visibleWatermark"]["reanalysis"]["basis"] == "legacy-unowned-exact-sha256"
    assert reused["visibleWatermark"]["reanalysis"]["sourceTaskId"] == "legacy-new"
    assert "仅管理员可访问" in reused["visibleWatermark"]["note"]
    assert by_task["legacy-old"]["hasVisibleWatermark"] is True
    assert counts["watermark"] == 2
