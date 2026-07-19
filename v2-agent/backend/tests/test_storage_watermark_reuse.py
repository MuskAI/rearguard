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


def test_history_results_remain_immutable_for_same_user_and_exact_sha(isolated_storage):
    storage = isolated_storage
    shared_sha = "a" * 64
    owner_one = {"userId": "user-1", "accountUuid": "account-1", "keyId": "key-1"}
    owner_two = {"userId": "user-2", "accountUuid": "account-2", "keyId": "key-2"}

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

    original = storage.get_history("old-owner-one")
    other_owner = storage.get_history("old-owner-two")
    different_file = storage.get_history("different-file")
    original_hit = storage.get_history("new-owner-one")

    assert original["verdict"] == "real"
    assert original["confidence"] == 0.23
    assert original["visibleWatermark"]["detected"] is False
    assert other_owner["visibleWatermark"]["detected"] is False
    assert different_file["visibleWatermark"]["detected"] is False
    assert "reanalysis" not in original_hit["visibleWatermark"]
    owner_one_items, _, owner_one_counts = storage.list_history(owner_account_uuid="account-1")
    owner_two_items, _, owner_two_counts = storage.list_history(owner_account_uuid="account-2")
    owner_one_by_task = {item["taskId"]: item for item in owner_one_items}

    assert owner_one_by_task["old-owner-one"]["hasVisibleWatermark"] is False
    assert owner_one_counts["watermark"] == 1
    assert owner_two_items[0]["hasVisibleWatermark"] is False
    assert owner_two_counts["watermark"] == 0


def test_metrics_fail_closed_for_legacy_unauthorized_verdicts(isolated_storage):
    storage = isolated_storage
    legacy = _result(
        "legacy-verdict",
        "2026-07-19T08:12:00+00:00",
        _clear_report(),
        verdict="real",
    )
    authorized = _result(
        "authorized-provenance",
        "2026-07-19T08:13:00+00:00",
        _clear_report(),
        verdict="highly_suspected_fake",
    )
    authorized.update({
        "source": "provenance",
        "decisionStatus": "verdict",
        "decisionAuthority": "decisive_provenance",
    })
    storage.put_history(legacy, sha256="c" * 64, file_size=10, thumbnail=None)
    storage.put_history(authorized, sha256="d" * 64, file_size=10, thumbnail=None)

    report = storage.metrics(days=14)

    assert report["byVerdict"]["unknown"] == 1
    assert report["byVerdict"]["highly_suspected_fake"] == 1
    assert report["byVerdict"].get("real", 0) == 0

def test_admin_legacy_history_results_also_remain_immutable(isolated_storage):
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

    original = storage.get_history("legacy-old")
    all_items, _, counts = storage.list_history(owner_account_uuid=None)
    by_task = {item["taskId"]: item for item in all_items}

    assert original["visibleWatermark"]["detected"] is False
    assert by_task["legacy-old"]["hasVisibleWatermark"] is False
    assert counts["watermark"] == 1
