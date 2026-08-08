"""Persistent state for document image-detection jobs.

The source document is kept only while a job is active. Public payloads never
expose ownership fields, token hashes, or spool paths.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import base64
import re
import secrets
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import storage


JOB_DIR = Path(os.getenv("JIANZHEN_DOCUMENT_JOB_DIR", storage.DATA_DIR / "document-jobs"))
JOB_RETENTION_HOURS = max(1, int(os.getenv("JIANZHEN_DOCUMENT_JOB_RETENTION_HOURS", "24")))
_LOCK = threading.RLock()
ACTIVE_STATUSES = {"queued", "running"}
TASK_ID_PATTERN = re.compile(r"doc_[0-9a-f]{24}\Z")


class DocumentJobCapacityError(RuntimeError):
    """Raised when the bounded document queue has no admission capacity."""


class DocumentJobIdempotencyConflict(RuntimeError):
    """Raised when one idempotency key is reused with different content."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_path(task_id: str) -> Path:
    return JOB_DIR / task_id / "task.json"


def _source_path(task_id: str) -> Path:
    return JOB_DIR / task_id / "source.bin"


def _asset_dir(task_id: str) -> Path:
    return JOB_DIR / task_id / "assets"


def _asset_path(task_id: str, ordinal: int) -> Path:
    return _asset_dir(task_id) / f"{int(ordinal):06d}.json"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _read(task_id: str) -> dict[str, Any] | None:
    if not TASK_ID_PATTERN.fullmatch(str(task_id or "")):
        return None
    path = _job_path(task_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def create(
    *,
    filename: str,
    mime: str,
    size: int,
    sha256: str,
    mode: str,
    actor: dict[str, Any],
    source: bytes,
    owner_key: str,
    idempotency_key: str,
    token_secret: str,
    max_active: int,
    max_owner_active: int,
) -> tuple[dict[str, Any], str, bool]:
    if len(token_secret) < 32:
        raise ValueError("document task token secret is not configured")
    owner_key = str(owner_key or "").strip()[:200]
    idempotency_key = str(idempotency_key or "").strip()
    if not owner_key or not (8 <= len(idempotency_key) <= 128):
        raise ValueError("document task identity is invalid")
    idempotency_material = f"document-idempotency\0{owner_key}\0{idempotency_key}"
    idempotency_hash = hmac.new(
        token_secret.encode("utf-8"),
        idempotency_material.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    access_material = f"document-access\0{owner_key}\0{idempotency_key}\0{sha256}"
    access_token = base64.urlsafe_b64encode(
        hmac.new(
            token_secret.encode("utf-8"),
            access_material.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("ascii").rstrip("=")
    task_id = f"doc_{secrets.token_hex(12)}"
    created_at = _now()
    owner_uuid = str(actor.get("accountUuid") or "")
    task = {
        "id": task_id,
        "filename": filename,
        "mime": mime,
        "size": int(size),
        "sha256": sha256,
        "mode": mode,
        "status": "queued",
        "stage": "queued",
        "pageCount": None,
        "discovered": 0,
        "completed": 0,
        "succeeded": 0,
        "failed": 0,
        "warnings": [],
        "summary": None,
        "error": None,
        "createdAt": created_at,
        "updatedAt": created_at,
        "_ownerAccountUuid": owner_uuid,
        "_ownerMode": str(actor.get("mode") or "public"),
        "_ownerPhone": str(actor.get("phone") or "")[:20],
        "_ownerOpenid": str(actor.get("openid") or "")[:64],
        "_ownerKey": owner_key,
        "_idempotencyHash": idempotency_hash,
        "_accessTokenHash": hashlib.sha256(access_token.encode("utf-8")).hexdigest(),
        "_sourcePath": str(_source_path(task_id)),
    }
    with _LOCK:
        active_total = 0
        active_owner = 0
        if JOB_DIR.exists():
            for task_file in JOB_DIR.glob("doc_*/task.json"):
                try:
                    existing = json.loads(task_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if hmac.compare_digest(str(existing.get("_idempotencyHash") or ""), idempotency_hash):
                    if not hmac.compare_digest(str(existing.get("sha256") or ""), sha256):
                        raise DocumentJobIdempotencyConflict(
                            "同一 Idempotency-Key 不能用于不同文档"
                        )
                    return existing, access_token, False
                if existing.get("status") not in ACTIVE_STATUSES:
                    continue
                existing_source = Path(str(existing.get("_sourcePath") or ""))
                if not existing_source.is_file():
                    existing.update({
                        "status": "failed",
                        "stage": "failed",
                        "error": "任务源文件不可用，请重新提交",
                        "errorCode": "document_source_missing",
                        "updatedAt": _now(),
                    })
                    _atomic_write(task_file, existing)
                    continue
                active_total += 1
                if hmac.compare_digest(str(existing.get("_ownerKey") or ""), owner_key):
                    active_owner += 1
        if active_total >= max(1, int(max_active)):
            raise DocumentJobCapacityError("文档检测队列已满，请稍后重试")
        if active_owner >= max(1, int(max_owner_active)):
            raise DocumentJobCapacityError("当前账号已有文档任务在处理中，请等待完成")
        source_path = _source_path(task_id)
        source_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        source_path.write_bytes(source)
        os.chmod(source_path, 0o600)
        _atomic_write(_job_path(task_id), task)
    return task, access_token, True


def get(task_id: str) -> dict[str, Any] | None:
    with _LOCK:
        return _read(task_id)


def list_assets(task_id: str, *, offset: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        directory = _asset_dir(task_id)
        if not directory.exists():
            return []
        paths = sorted(directory.glob("*.json"))
        start = max(0, int(offset))
        selected = paths[start:] if limit is None else paths[start:start + max(0, int(limit))]
        assets: list[dict[str, Any]] = []
        for path in selected:
            try:
                assets.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return assets


def update(task_id: str, **changes: Any) -> dict[str, Any]:
    with _LOCK:
        task = _read(task_id)
        if not task:
            raise KeyError(task_id)
        task.update(changes)
        task["updatedAt"] = _now()
        _atomic_write(_job_path(task_id), task)
        return task


def add_asset(task_id: str, asset: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        task = _read(task_id)
        if not task:
            raise KeyError(task_id)
        ordinal = int(asset.get("ordinal") or 0)
        if ordinal <= 0:
            raise ValueError("document asset ordinal must be positive")
        path = _asset_path(task_id, ordinal)
        previous = None
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        _atomic_write(path, asset)
        succeeded = int(task.get("succeeded") or 0)
        failed = int(task.get("failed") or 0)
        if previous and previous.get("status") == "completed":
            succeeded = max(0, succeeded - 1)
        elif previous and previous.get("status") == "failed":
            failed = max(0, failed - 1)
        if asset.get("status") == "completed":
            succeeded += 1
        elif asset.get("status") == "failed":
            failed += 1
        task["completed"] = succeeded + failed
        task["succeeded"] = succeeded
        task["failed"] = failed
        task["updatedAt"] = _now()
        _atomic_write(_job_path(task_id), task)
        return task


def reconcile(task_id: str) -> dict[str, Any]:
    """Repair counters from durable child records after an interrupted write."""
    with _LOCK:
        task = _read(task_id)
        if not task:
            raise KeyError(task_id)
        assets = list_assets(task_id)
        task["completed"] = len(assets)
        task["succeeded"] = sum(1 for item in assets if item.get("status") == "completed")
        task["failed"] = sum(1 for item in assets if item.get("status") == "failed")
        task["updatedAt"] = _now()
        _atomic_write(_job_path(task_id), task)
        return task


def is_authorized(task: dict[str, Any], actor: dict[str, Any], access_token: str = "") -> bool:
    if actor.get("mode") == "admin":
        return True
    owner_uuid = str(task.get("_ownerAccountUuid") or "")
    actor_uuid = str(actor.get("accountUuid") or "")
    if owner_uuid:
        return bool(actor_uuid and hmac.compare_digest(owner_uuid, actor_uuid))
    token_hash = str(task.get("_accessTokenHash") or "")
    submitted_hash = hashlib.sha256(access_token.encode("utf-8")).hexdigest() if access_token else ""
    return bool(token_hash and submitted_hash and hmac.compare_digest(token_hash, submitted_hash))


def public_payload(task: dict[str, Any], *, asset_offset: int = 0, asset_limit: int = 24) -> dict[str, Any]:
    clean = {key: value for key, value in task.items() if not key.startswith("_")}
    start = max(0, int(asset_offset))
    limit = min(max(1, int(asset_limit)), 100)
    clean["assets"] = list_assets(str(task.get("id") or ""), offset=start, limit=limit)
    clean["assetOffset"] = start
    clean["assetLimit"] = limit
    clean["assetTotal"] = int(task.get("completed") or 0)
    clean["hasMoreAssets"] = start + limit < clean["assetTotal"]
    clean["progress"] = progress(task)
    return clean


def progress(task: dict[str, Any]) -> int:
    stage = str(task.get("stage") or "queued")
    if stage == "queued":
        return 4
    if stage == "validating":
        return 12
    if stage == "extracting":
        return 28
    if stage == "detecting":
        discovered = max(1, int(task.get("discovered") or 0))
        completed = min(discovered, int(task.get("completed") or 0))
        return 34 + round(54 * completed / discovered)
    if stage == "aggregating":
        return 94
    if stage in {"completed", "partial_success", "failed", "cancelled"}:
        return 100
    return 4


def remove_source(task: dict[str, Any]) -> None:
    source_path = Path(str(task.get("_sourcePath") or ""))
    try:
        source_path.unlink(missing_ok=True)
    except OSError:
        pass


def recoverable() -> list[dict[str, Any]]:
    with _LOCK:
        if not JOB_DIR.exists():
            return []
        tasks = []
        for task_file in JOB_DIR.glob("doc_*/task.json"):
            try:
                task = json.loads(task_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            source_path = Path(str(task.get("_sourcePath") or ""))
            if task.get("status") in {"queued", "running"} and source_path.is_file():
                tasks.append(task)
        return tasks


def prune_expired() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=JOB_RETENTION_HOURS)
    removed = 0
    with _LOCK:
        if not JOB_DIR.exists():
            return 0
        for task_file in JOB_DIR.glob("doc_*/task.json"):
            try:
                task = json.loads(task_file.read_text(encoding="utf-8"))
                updated = datetime.fromisoformat(str(task.get("updatedAt")))
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                continue
            if updated >= cutoff:
                continue
            directory = task_file.parent
            shutil.rmtree(directory, ignore_errors=True)
            removed += 1
    return removed
