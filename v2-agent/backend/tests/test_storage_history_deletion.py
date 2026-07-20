from __future__ import annotations

import hashlib
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


def _result(task_id: str) -> dict:
    return {
        "taskId": task_id,
        "reportId": f"report-{task_id}",
        "createdAt": "2026-07-20T08:00:00+00:00",
        "fileMeta": {
            "name": f"{task_id}.png",
            "type": "image",
            "size": "120KB",
            "resolution": "1200x800",
        },
        "verdict": "unknown",
        "decisionStatus": "review_only",
        "decisionAuthority": "none",
        "reviewRequired": True,
        "confidence": 0.5,
        "modelVersion": "pytest-model",
        "source": "pytest",
    }


def _analysis(marker: str) -> dict:
    return {
        "marker": marker,
        "verdict": "unknown",
        "decisionStatus": "review_only",
        "decisionAuthority": "none",
        "reviewRequired": True,
        "modelVersion": "pytest-model",
        "source": "pytest",
    }


def _cache_types(account_uuid: str) -> tuple[str, str, str]:
    scope = hashlib.sha256(f"account:{account_uuid}".encode("utf-8")).hexdigest()[:16]
    return (
        f"image:tenant:{scope}",
        f"image-forensics:pipeline-v1:model-a:{scope}",
        f"image-forensics:pipeline-v2:model-b:{scope}",
    )


def _put_history(storage, task_id: str, sha256: str, account_uuid: str) -> None:
    storage.put_history(
        _result(task_id),
        sha256=sha256,
        file_size=120_000,
        thumbnail=None,
        actor={"userId": task_id, "accountUuid": account_uuid, "keyId": f"key-{task_id}"},
    )


def _put_caches(storage, cache_types: tuple[str, ...], sha256: str) -> None:
    for cache_type in cache_types:
        storage.put_cached_analysis(cache_type, sha256, _analysis(cache_type))


def _assert_cache_presence(storage, cache_types: tuple[str, ...], sha256: str, *, present: bool) -> None:
    for cache_type in cache_types:
        cached = storage.get_cached_analysis(cache_type, sha256)
        assert (cached is not None) is present, cache_type


def test_delete_history_clears_both_tenant_cache_prefixes_for_resource(isolated_storage):
    storage = isolated_storage
    account_uuid = "tenant-a"
    resource_sha = "a" * 64
    cache_types = _cache_types(account_uuid)
    _put_history(storage, "tenant-a-resource", resource_sha, account_uuid)
    _put_caches(storage, cache_types, resource_sha)

    deleted = storage.delete_history("tenant-a-resource")

    assert deleted is not None
    _assert_cache_presence(storage, cache_types, resource_sha, present=False)


def test_delete_history_preserves_other_tenants_and_resources(isolated_storage):
    storage = isolated_storage
    shared_sha = "b" * 64
    other_sha = "c" * 64
    tenant_a_types = _cache_types("tenant-a")
    tenant_b_types = _cache_types("tenant-b")
    _put_history(storage, "tenant-a-shared", shared_sha, "tenant-a")
    _put_history(storage, "tenant-b-shared", shared_sha, "tenant-b")
    _put_history(storage, "tenant-a-other", other_sha, "tenant-a")
    _put_caches(storage, tenant_a_types, shared_sha)
    _put_caches(storage, tenant_b_types, shared_sha)
    _put_caches(storage, tenant_a_types, other_sha)

    storage.delete_history("tenant-a-shared")

    _assert_cache_presence(storage, tenant_a_types, shared_sha, present=False)
    _assert_cache_presence(storage, tenant_b_types, shared_sha, present=True)
    _assert_cache_presence(storage, tenant_a_types, other_sha, present=True)


def test_delete_history_waits_for_last_tenant_resource_reference(isolated_storage):
    storage = isolated_storage
    account_uuid = "tenant-a"
    resource_sha = "d" * 64
    cache_types = _cache_types(account_uuid)
    _put_history(storage, "tenant-a-first", resource_sha, account_uuid)
    _put_history(storage, "tenant-a-second", resource_sha, account_uuid)
    _put_caches(storage, cache_types, resource_sha)

    storage.delete_history("tenant-a-first")

    _assert_cache_presence(storage, cache_types, resource_sha, present=True)

    storage.delete_history("tenant-a-second")

    _assert_cache_presence(storage, cache_types, resource_sha, present=False)


def test_delete_history_anonymizes_legacy_request_paths_containing_report_ids(isolated_storage):
    storage = isolated_storage
    task_id = "tenant-private-task"
    report_id = f"report-{task_id}"
    _put_history(storage, task_id, "e" * 64, "tenant-a")
    storage.record_event(
        "request",
        client_ip="203.0.113.8",
        user_agent="private-user-agent",
        method="GET",
        path=f"/api/report/{report_id}/download",
        status=200,
    )

    storage.delete_history(task_id)

    with storage._connect() as conn:
        event = conn.execute(
            "SELECT client_ip, user_agent, path FROM request_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert dict(event) == {
        "client_ip": None,
        "user_agent": None,
        "path": "[erased-resource-route]",
    }


def test_delete_history_fails_closed_when_tombstone_cannot_be_persisted(
    isolated_storage,
    monkeypatch,
):
    storage = isolated_storage
    task_id = "ledger-failure-task"
    _put_history(storage, task_id, "1" * 64, "tenant-a")

    def unavailable(*_args, **_kwargs):
        raise storage.privacy_erasure_ledger.PrivacyErasureLedgerError("unavailable")

    monkeypatch.setattr(
        storage.privacy_erasure_ledger,
        "record_tombstone",
        unavailable,
    )

    with pytest.raises(
        storage.privacy_erasure_ledger.PrivacyErasureLedgerError,
        match="unavailable",
    ):
        storage.delete_history(task_id)

    assert storage.get_history(task_id) is not None
