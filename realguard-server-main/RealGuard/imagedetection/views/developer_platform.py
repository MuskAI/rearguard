import fcntl
import hashlib
import io
import json
import os
from contextlib import contextmanager
from pathlib import Path
import stat
import threading
import time
import uuid
import warnings
from datetime import datetime, timedelta

import pymysql
import requests
from PIL import Image, UnidentifiedImageError
from flask import Blueprint, Response, g, has_request_context, jsonify, request

from imagedetection.decision_labels import binary_final_label
from imagedetection.views import admin_state, reporting
from imagedetection.views.admin import _admin_required
from imagedetection.views.api import (
    _append_security_audit,
    _auth_required,
    _developer_key_required,
    _developer_scopes,
    _developer_usage_from_v1,
    _developer_usage_from_v2,
    _ensure_developer_usage_table,
    _ensure_security_audit_tables,
    _merge_developer_usage,
    _retry_pending_privacy_erasures,
    _serve_detection_media_item,
)
from imagedetection.views import detection
from imagedetection.views.utils import (
    excute_detection_sql,
    excute_sql,
    format_createtime,
    get_db_connection,
)


developer_platform_blueprint = Blueprint(
    "developer_platform_blueprint",
    __name__,
    url_prefix="/api/developer",
)
openapi_blueprint = Blueprint(
    "openapi_blueprint",
    __name__,
    url_prefix="/api/openapi/v1",
)
developer_admin_blueprint = Blueprint(
    "developer_admin_blueprint",
    __name__,
    url_prefix="/api/admin/developer",
)

DEVELOPER_FREE_CALLS = max(0, int(os.environ.get("REALGUARD_DEVELOPER_FREE_CALLS", "100")))
DEVELOPER_MAX_IMAGE_BYTES = max(
    1024 * 1024,
    int(os.environ.get("REALGUARD_DEVELOPER_MAX_IMAGE_BYTES", str(25 * 1024 * 1024))),
)
DEVELOPER_MAX_IMAGE_PIXELS = max(
    1_000_000,
    int(os.environ.get("REALGUARD_DEVELOPER_MAX_IMAGE_PIXELS", "24000000")),
)
DEVELOPER_FAST_PRICE_FEN = max(0, int(os.environ.get("REALGUARD_DEVELOPER_FAST_PRICE_FEN", "0")))
DEVELOPER_SWARM_PRICE_FEN = max(0, int(os.environ.get("REALGUARD_DEVELOPER_SWARM_PRICE_FEN", "0")))
DEVELOPER_FAST_BILLING_ENABLED = str(
    os.environ.get("REALGUARD_DEVELOPER_FAST_BILLING_ENABLED", "0")
).strip().lower() in {"1", "true", "yes", "on"}
DEVELOPER_SWARM_BILLING_ENABLED = str(
    os.environ.get("REALGUARD_DEVELOPER_SWARM_BILLING_ENABLED", "0")
).strip().lower() in {"1", "true", "yes", "on"}
BACKGROUND_THREAD_CLASS = threading.Thread
DEVELOPER_TASK_LEASE_SECONDS = max(
    60,
    int(os.environ.get("REALGUARD_DEVELOPER_TASK_LEASE_SECONDS", "300")),
)
DEVELOPER_TASK_HEARTBEAT_SECONDS = max(
    5,
    min(
        DEVELOPER_TASK_LEASE_SECONDS // 3,
        int(os.environ.get("REALGUARD_DEVELOPER_TASK_HEARTBEAT_SECONDS", "30")),
    ),
)
DEVELOPER_TASK_RECONCILE_INTERVAL_SECONDS = max(
    0,
    int(os.environ.get("REALGUARD_DEVELOPER_TASK_RECONCILE_INTERVAL_SECONDS", "15")),
)
DEVELOPER_TASK_RECONCILE_BATCH_SIZE = max(
    1,
    min(200, int(os.environ.get("REALGUARD_DEVELOPER_TASK_RECONCILE_BATCH_SIZE", "50"))),
)
DEVELOPER_TASK_MAX_ATTEMPTS = max(
    1,
    min(10, int(os.environ.get("REALGUARD_DEVELOPER_TASK_MAX_ATTEMPTS", "3"))),
)
DEVELOPER_TASK_PREPARING_TIMEOUT_SECONDS = max(
    60,
    int(os.environ.get("REALGUARD_DEVELOPER_TASK_PREPARING_TIMEOUT_SECONDS", "300")),
)
DEVELOPER_TASK_MAX_PENDING = max(
    1,
    int(os.environ.get("REALGUARD_DEVELOPER_TASK_MAX_PENDING", "50")),
)
WEB_ACCOUNT_TASK_MAX_PENDING = max(
    1,
    int(os.environ.get("REALGUARD_WEB_ACCOUNT_TASK_MAX_PENDING", "20")),
)
WEB_ACCOUNT_OWNER_MAX_PENDING = max(
    1,
    int(os.environ.get("REALGUARD_WEB_ACCOUNT_OWNER_MAX_PENDING", "3")),
)
WEB_GUEST_TASK_MAX_PENDING = max(
    1,
    int(os.environ.get("REALGUARD_WEB_GUEST_TASK_MAX_PENDING", "5")),
)
WEB_GUEST_OWNER_MAX_PENDING = max(
    1,
    int(os.environ.get("REALGUARD_WEB_GUEST_OWNER_MAX_PENDING", "1")),
)
WEB_GUEST_DAILY_GLOBAL_LIMIT = max(
    1,
    int(os.environ.get("REALGUARD_WEB_GUEST_DAILY_GLOBAL_LIMIT", "500")),
)
DEVELOPER_SPOOL_MAX_BYTES = max(
    DEVELOPER_MAX_IMAGE_BYTES,
    int(os.environ.get("REALGUARD_DEVELOPER_SPOOL_MAX_BYTES", str(2 * 1024 * 1024 * 1024))),
)
DEVELOPER_SPOOL_ORPHAN_GRACE_SECONDS = max(
    300,
    int(os.environ.get("REALGUARD_DEVELOPER_SPOOL_ORPHAN_GRACE_SECONDS", "3600")),
)
DEVELOPER_SPOOL_ROOT = Path(
    os.environ.get("REALGUARD_DEVELOPER_SPOOL_ROOT", "/opt/realguard-data/developer-spool")
).expanduser()
if not DEVELOPER_SPOOL_ROOT.is_absolute():
    DEVELOPER_SPOOL_ROOT = Path.cwd() / DEVELOPER_SPOOL_ROOT
WEB_TASK_LEASE_SECONDS = max(
    60,
    int(os.environ.get("REALGUARD_WEB_TASK_LEASE_SECONDS", "600")),
)
WEB_TASK_HEARTBEAT_SECONDS = max(
    5,
    min(
        WEB_TASK_LEASE_SECONDS // 3,
        int(os.environ.get("REALGUARD_WEB_TASK_HEARTBEAT_SECONDS", "30")),
    ),
)
WEB_TASK_MAX_ATTEMPTS = max(
    1,
    min(5, int(os.environ.get("REALGUARD_WEB_TASK_MAX_ATTEMPTS", "3"))),
)
WEB_TASK_SPOOL_ROOT = Path(
    os.environ.get("REALGUARD_WEB_TASK_SPOOL_ROOT", "/opt/realguard-data/web-spool")
).expanduser()
if not WEB_TASK_SPOOL_ROOT.is_absolute():
    WEB_TASK_SPOOL_ROOT = Path.cwd() / WEB_TASK_SPOOL_ROOT
DEVELOPER_FINANCIAL_REAUTH_SECONDS = max(
    60,
    min(3600, int(os.environ.get("REALGUARD_FINANCIAL_REAUTH_SECONDS", "900"))),
)
DEVELOPER_MAX_UNIT_PRICE_FEN = max(
    1,
    int(os.environ.get("REALGUARD_DEVELOPER_MAX_UNIT_PRICE_FEN", "100000")),
)
DEVELOPER_MAX_ADMIN_BALANCE_DELTA_FEN = max(
    1,
    int(os.environ.get("REALGUARD_DEVELOPER_MAX_ADMIN_BALANCE_DELTA_FEN", "100000000")),
)
DEVELOPER_MAX_ADMIN_FREE_DELTA = max(
    1,
    int(os.environ.get("REALGUARD_DEVELOPER_MAX_ADMIN_FREE_DELTA", "10000000")),
)

_PLATFORM_TABLES_READY = False
_PLATFORM_TABLES_LOCK = threading.Lock()
_TASK_RECONCILE_LOCK = threading.Lock()
_TASK_RECONCILE_LAST_MONOTONIC = 0.0


class BillingError(RuntimeError):
    def __init__(self, message, *, code="billing_unavailable", status_code=503):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class TaskRecoveryError(RuntimeError):
    pass


class TaskSpoolError(RuntimeError):
    pass


class QueueCapacityError(RuntimeError):
    pass


def _error(message, status_code, code):
    if has_request_context():
        request_id = getattr(g, "realguard_request_id", "")
        if not request_id:
            supplied = str(request.headers.get("X-Request-Id") or "").strip()
            request_id = supplied[:64] if supplied and len(supplied) <= 64 else uuid.uuid4().hex
            g.realguard_request_id = request_id
    else:
        request_id = uuid.uuid4().hex
    response = jsonify({
        "error": {
            "code": code,
            "message": message,
            "requestId": request_id,
        },
    })
    response.headers["X-Request-Id"] = request_id
    return response, status_code


def _financial_admin_required(permission):
    admin_user, auth_error = _admin_required(permission)
    if auth_error:
        return None, auth_error
    try:
        admin_id = int((admin_user or {}).get("adminId") or 0)
        issued_at = int((admin_user or {}).get("issuedAt") or 0)
    except (TypeError, ValueError):
        admin_id = 0
        issued_at = 0
    now = int(time.time())
    if admin_id <= 0 or (admin_user or {}).get("authType") != "admin_account":
        return None, (
            jsonify({
                "status": "error",
                "code": "named_admin_required",
                "message": "财务操作必须使用实名后台管理员账号",
            }),
            403,
        )
    if issued_at <= 0 or issued_at > now + 60 or now - issued_at > DEVELOPER_FINANCIAL_REAUTH_SECONDS:
        return None, (
            jsonify({
                "status": "error",
                "code": "reauthentication_required",
                "message": "财务操作需要重新登录后台确认身份",
            }),
            428,
        )
    return admin_user, None


def _insert_transactional_admin_audit(cursor, actor, action, target, *, before=None, after=None, meta=None):
    actor = actor or {}
    actor_id = actor.get("Userid") or (
        f"admin:{actor.get('adminId')}" if actor.get("adminId") is not None else ""
    )
    cursor.execute(
        """
        INSERT INTO admin_audit_logs
            (actor_id, actor_username, actor_phone, action, target,
             before_json, after_json, meta_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            str(actor_id)[:64],
            str(actor.get("username") or "")[:64],
            str(actor.get("phone") or "")[:20],
            str(action or "")[:96],
            str(target or "")[:191],
            json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            json.dumps(meta or {}, ensure_ascii=False, default=str),
        ),
    )
    _append_security_audit(
        cursor,
        "admin",
        actor.get("adminId") or actor_id,
        action,
        target,
        {
            "before": before,
            "after": after,
            **(meta or {}),
        },
    )


def _store_admin_operation_result(cursor, operation_id, payload, status_code=200):
    cursor.execute(
        """
        INSERT INTO developer_admin_operation_results
            (operation_id, status_code, response_json)
        VALUES (%s, %s, %s)
        """,
        (
            operation_id,
            int(status_code),
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str),
        ),
    )


def _admin_operation_replay(operation_id):
    rows = excute_sql(
        """
        SELECT status_code, response_json
        FROM developer_admin_operation_results
        WHERE operation_id = %s
        LIMIT 1
        """,
        (operation_id,),
    )
    if not rows:
        return None
    try:
        payload = json.loads(rows[0].get("response_json") or "")
        if not isinstance(payload, dict):
            return None
        payload["idempotentReplay"] = True
        return payload, int(rows[0].get("status_code") or 200)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _openapi_key_required():
    actor, auth_error = _developer_key_required()
    if not auth_error:
        return actor, None
    response, status_code = auth_error
    payload = response.get_json(silent=True) or {}
    raw_error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
    code = str(raw_error.get("code") or ("unauthorized" if status_code == 401 else "authentication_failed"))
    message = str(raw_error.get("message") or "API Key 缺失、无效或已撤销")
    normalized, normalized_status = _error(message, status_code, code)
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        normalized.headers["Retry-After"] = retry_after
    return None, (normalized, normalized_status)


def _update_job_cache(task_id, payload):
    try:
        return admin_state.update_detection_job(task_id, payload)
    except Exception as exc:
        print(f"[DEVELOPER TASK CACHE ERROR] update {task_id}: {exc}")
        return None


def _detector_ready_for_worker():
    endpoint = f"{detection.DETECTION_BACKEND_BASE_URL}/internal/ready"
    token = detection.DETECTOR_INTERNAL_TOKEN
    if not token:
        return False
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(
                endpoint,
                headers={"X-RealGuard-Detector-Token": token},
                timeout=(1, 4),
            )
        payload = response.json()
        return (
            response.status_code == 200
            and payload.get("capabilityReady") is True
            and payload.get("tokenReady") is True
        )
    except (requests.RequestException, TypeError, ValueError):
        return False


def _spool_file_path(spool_name):
    name = str(spool_name or "").strip()
    if not name or Path(name).name != name or name in {".", ".."}:
        raise TaskSpoolError("invalid task spool path")
    return DEVELOPER_SPOOL_ROOT / name


def _ensure_spool_root():
    DEVELOPER_SPOOL_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    root_stat = DEVELOPER_SPOOL_ROOT.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise TaskSpoolError("task spool root is not a private directory")
    os.chmod(DEVELOPER_SPOOL_ROOT, 0o700)


def _validate_private_spool_stat(file_stat, label):
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_nlink != 1
        or file_stat.st_uid != os.geteuid()
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise TaskSpoolError(f"{label} has unsafe ownership or permissions")


def _write_task_spool(task_id, image_bytes):
    """Atomically persist an upload before its database row becomes runnable."""
    _ensure_spool_root()
    spool_name = f"{task_id}.upload"
    final_path = _spool_file_path(spool_name)
    temp_name = f".{task_id}.{uuid.uuid4().hex}.tmp"
    temp_path = _spool_file_path(temp_name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = None
    try:
        fd = os.open(temp_path, flags, 0o600)
        view = memoryview(image_bytes)
        written = 0
        while written < len(view):
            count = os.write(fd, view[written:])
            if count <= 0:
                raise OSError("short write while persisting task upload")
            written += count
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.link(temp_path, final_path, follow_symlinks=False)
        temp_path.unlink()
        os.chmod(final_path, 0o600)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_fd = os.open(DEVELOPER_SPOOL_ROOT, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return spool_name
    except Exception as exc:
        if fd is not None:
            os.close(fd)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise TaskSpoolError(f"failed to persist task upload: {exc}") from exc


def _read_task_spool(row):
    spool_path = _spool_file_path(row.get("spool_path"))
    expected_size = int(row.get("spool_size") or -1)
    expected_digest = str(row.get("request_sha256") or "").lower()
    if expected_size < 1 or expected_size > DEVELOPER_MAX_IMAGE_BYTES:
        raise TaskSpoolError("task spool size is invalid")
    if len(expected_digest) != 64:
        raise TaskSpoolError("task spool digest is invalid")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(spool_path, flags)
    except OSError as exc:
        raise TaskSpoolError(f"task spool file is unavailable: {exc}") from exc
    try:
        file_stat = os.fstat(fd)
        _validate_private_spool_stat(file_stat, "task spool file")
        if file_stat.st_size != expected_size:
            raise TaskSpoolError("task spool size verification failed")
        chunks = []
        remaining = expected_size + 1
        while remaining > 0:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    image_bytes = b"".join(chunks)
    if len(image_bytes) != expected_size:
        raise TaskSpoolError("task spool length verification failed")
    if hashlib.sha256(image_bytes).hexdigest() != expected_digest:
        raise TaskSpoolError("task spool SHA-256 verification failed")
    return image_bytes


@contextmanager
def _task_execution_lock(row):
    """Serialize all attempts for one task on the host that owns its private spool."""
    spool_path = _spool_file_path(row.get("spool_path"))
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(spool_path, flags)
    except OSError as exc:
        raise TaskSpoolError(f"task spool file is unavailable for locking: {exc}") from exc
    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise TaskSpoolError("task spool lock target is not regular")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _task_execution_filename(task_id, original_filename):
    safe_task_id = str(task_id or "").strip()
    if not safe_task_id or len(safe_task_id) > 64 or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for char in safe_task_id
    ):
        raise TaskRecoveryError("task id cannot be used as an execution idempotency key")
    suffix = Path(str(original_filename or "")).suffix.lower()
    if not suffix or len(suffix) > 12 or any(
        char not in ".abcdefghijklmnopqrstuvwxyz0123456789" for char in suffix
    ):
        suffix = ".img"
    return f"developer-{safe_task_id}{suffix}"


def _remove_task_spool(task_id, spool_name):
    try:
        spool_path = _spool_file_path(spool_name)
        spool_path.unlink(missing_ok=True)
    except (OSError, TaskSpoolError) as exc:
        print(f"[DEVELOPER TASK SPOOL ERROR] cleanup {task_id}: {exc}")
        return False
    updated = excute_sql(
        """
        UPDATE developer_detection_tasks
        SET spool_path = NULL, spool_size = NULL, request_context_json = NULL
        WHERE task_id = %s AND spool_path = %s
          AND status IN ('success', 'failed', 'rejected')
        """,
        (task_id, spool_name),
        fetch=False,
    )
    return updated is not None


def _web_spool_file_path(spool_name):
    name = str(spool_name or "").strip()
    if not name or Path(name).name != name or name in {".", ".."}:
        raise TaskSpoolError("invalid Web task spool path")
    return WEB_TASK_SPOOL_ROOT / name


def _ensure_web_spool_root():
    WEB_TASK_SPOOL_ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
    root_stat = WEB_TASK_SPOOL_ROOT.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise TaskSpoolError("Web task spool root is not a private directory")
    os.chmod(WEB_TASK_SPOOL_ROOT, 0o700)


def _write_web_task_spool(job_id, image_bytes):
    _ensure_web_spool_root()
    spool_name = f"{job_id}.upload"
    final_path = _web_spool_file_path(spool_name)
    temp_path = _web_spool_file_path(f".{job_id}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = None
    try:
        fd = os.open(temp_path, flags, 0o600)
        view = memoryview(image_bytes)
        written = 0
        while written < len(view):
            count = os.write(fd, view[written:])
            if count <= 0:
                raise OSError("short write while persisting Web task upload")
            written += count
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.link(temp_path, final_path, follow_symlinks=False)
        temp_path.unlink()
        os.chmod(final_path, 0o600)
        directory_fd = os.open(
            WEB_TASK_SPOOL_ROOT,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return spool_name
    except Exception as exc:
        if fd is not None:
            os.close(fd)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise TaskSpoolError(f"failed to persist Web task upload: {exc}") from exc


def _read_web_task_spool(row):
    spool_path = _web_spool_file_path(row.get("spool_path"))
    expected_size = int(row.get("spool_size") or -1)
    expected_digest = str(row.get("request_sha256") or "").lower()
    if expected_size < 1 or expected_size > DEVELOPER_MAX_IMAGE_BYTES:
        raise TaskSpoolError("Web task spool size is invalid")
    if len(expected_digest) != 64:
        raise TaskSpoolError("Web task spool digest is invalid")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(spool_path, flags)
    except OSError as exc:
        raise TaskSpoolError(f"Web task spool file is unavailable: {exc}") from exc
    try:
        file_stat = os.fstat(fd)
        _validate_private_spool_stat(file_stat, "Web task spool file")
        if file_stat.st_size != expected_size:
            raise TaskSpoolError("Web task spool verification failed")
        chunks = []
        remaining = expected_size + 1
        while remaining > 0:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    image_bytes = b"".join(chunks)
    if len(image_bytes) != expected_size:
        raise TaskSpoolError("Web task spool length verification failed")
    if hashlib.sha256(image_bytes).hexdigest() != expected_digest:
        raise TaskSpoolError("Web task spool SHA-256 verification failed")
    return image_bytes


def _remove_web_task_spool(job_id, spool_name):
    try:
        _web_spool_file_path(spool_name).unlink(missing_ok=True)
    except (OSError, TaskSpoolError) as exc:
        print(f"[WEB TASK SPOOL ERROR] cleanup {job_id}: {exc}")
        return False
    updated = excute_sql(
        """
        UPDATE web_detection_tasks
        SET spool_path = NULL, spool_size = NULL
        WHERE job_id = %s AND spool_path = %s
          AND status IN ('success', 'failed')
        """,
        (job_id, spool_name),
        fetch=False,
    )
    return updated is not None


def _web_request_context(job, user_info, is_guest):
    source = user_info or {}
    actor = (job or {}).get("actor") or {}
    return json.dumps(
        {
            "is_guest": bool(is_guest),
            "actor": {
                "id": actor.get("id"),
                "account_uuid": actor.get("account_uuid") or "",
                "username": actor.get("username") or "",
                "phone": actor.get("phone") or "",
                "openid": actor.get("openid") or "",
            },
            "user_info": {
                "Userid": source.get("Userid"),
                "account_uuid": source.get("account_uuid") or "",
                "username": source.get("username") or ("访客" if is_guest else ""),
                "phone": source.get("phone") or "",
                "openid": source.get("openid") or "",
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _load_web_request_context(row):
    try:
        context = json.loads(row.get("request_context_json") or "")
        actor = context["actor"]
        user_info = context["user_info"]
        is_guest = bool(context["is_guest"])
        if str(actor.get("id") or "") != str(user_info.get("Userid") or ""):
            raise ValueError("Web task account id mismatch")
        if str(actor.get("openid") or "") != str(user_info.get("openid") or ""):
            raise ValueError("Web task openid mismatch")
        if str(actor.get("phone") or "") != str(user_info.get("phone") or ""):
            raise ValueError("Web task phone mismatch")
        actor_uuid = str(actor.get("account_uuid") or "").strip().lower()
        user_uuid = str(user_info.get("account_uuid") or "").strip().lower()
        if actor_uuid != user_uuid:
            raise ValueError("Web task account UUID mismatch")
        if is_guest:
            if actor.get("id") not in (None, "") or actor.get("phone") or not actor.get("openid"):
                raise ValueError("invalid guest Web task owner")
        elif actor.get("id") in (None, "") or not actor_uuid:
            raise ValueError("registered Web task has no immutable owner")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaskSpoolError(f"Web task execution context is invalid: {exc}") from exc
    return actor, user_info, is_guest


def _queue_capacity_error(incoming_size, queue_class="developer", owner_key=""):
    owner_type = queue_class if queue_class in {"account", "guest"} else ""
    rows = excute_sql(
        """
        SELECT
          (SELECT COUNT(*) FROM developer_detection_tasks
           WHERE status IN ('preparing', 'queued', 'running')) AS developer_pending,
          (SELECT COUNT(*) FROM web_detection_tasks
           WHERE owner_type = %s AND status IN ('queued', 'running')) AS web_pending,
          (SELECT COUNT(*) FROM web_detection_tasks
           WHERE owner_type = %s AND owner_key = %s
             AND status IN ('queued', 'running')) AS owner_pending,
          (SELECT COALESCE(SUM(spool_size), 0) FROM developer_detection_tasks
           WHERE status IN ('preparing', 'queued', 'running'))
          +
          (SELECT COALESCE(SUM(spool_size), 0) FROM web_detection_tasks
           WHERE status IN ('queued', 'running')) AS pending_bytes
        """,
        (owner_type, owner_type, owner_key),
    )
    if rows is None:
        raise TaskRecoveryError("failed to inspect developer task queue capacity")
    row = rows[0] if rows else {}
    developer_pending = int(row.get("developer_pending", row.get("pending_count")) or 0)
    web_pending = int(row.get("web_pending", row.get("pending_count")) or 0)
    owner_pending = int(row.get("owner_pending") or 0)
    pending_bytes = int(row.get("pending_bytes") or 0)
    if queue_class == "developer" and developer_pending >= DEVELOPER_TASK_MAX_PENDING:
        return "检测队列已满，请稍后重试"
    if queue_class == "account" and web_pending >= WEB_ACCOUNT_TASK_MAX_PENDING:
        return "网页检测队列已满，请稍后重试"
    if queue_class == "account" and owner_pending >= WEB_ACCOUNT_OWNER_MAX_PENDING:
        return "当前账号待处理任务较多，请等待已有任务完成"
    if queue_class == "guest" and web_pending >= WEB_GUEST_TASK_MAX_PENDING:
        return "访客检测队列已满，请稍后重试或登录使用"
    if queue_class == "guest" and owner_pending >= WEB_GUEST_OWNER_MAX_PENDING:
        return "当前访客已有待处理任务，请等待任务完成"
    if pending_bytes + int(incoming_size or 0) > DEVELOPER_SPOOL_MAX_BYTES:
        return "检测队列存储空间已达到安全上限，请稍后重试"
    return None


@contextmanager
def _queue_submission_guard(incoming_size, queue_class="developer", owner_key=""):
    """Serialize final queue admission across all Web and API processes."""
    conn = None
    lock_acquired = False
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT GET_LOCK('realguard_developer_queue_submission', 5) AS acquired"
            )
            lock_acquired = int((cursor.fetchone() or {}).get("acquired") or 0) == 1
        if not lock_acquired:
            raise QueueCapacityError("检测队列准入繁忙，请稍后重试")
        capacity_error = (
            _queue_capacity_error(incoming_size)
            if queue_class == "developer" and not owner_key
            else _queue_capacity_error(incoming_size, queue_class, owner_key)
        )
        if capacity_error:
            raise QueueCapacityError(capacity_error)
        yield
    except QueueCapacityError:
        raise
    except Exception as exc:
        raise TaskRecoveryError(f"failed to reserve developer queue capacity: {exc}") from exc
    finally:
        if conn is not None:
            if lock_acquired:
                try:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT RELEASE_LOCK('realguard_developer_queue_submission')")
                except Exception as exc:
                    print(f"[DEVELOPER QUEUE LOCK ERROR] {exc}")
            conn.close()


def _enqueue_web_detection_task(
    job,
    image_bytes,
    filename,
    mimetype,
    user_info,
    is_guest,
    guest_subject="",
    idempotency_key="",
):
    if not _ensure_developer_platform_tables():
        raise TaskRecoveryError("detection queue schema is unavailable")
    job_id = str((job or {}).get("id") or "").strip()
    mode = str((job or {}).get("mode") or (job or {}).get("kind") or "fast").strip().lower()
    if not job_id or mode not in {"fast", "image", "swarm"}:
        raise TaskRecoveryError("invalid Web detection job")
    if mode == "image":
        mode = "fast"
    context_json = _web_request_context(job, user_info, is_guest)
    owner_type = "guest" if is_guest else "account"
    owner_key = str(
        guest_subject if is_guest else ((job or {}).get("actor") or {}).get("account_uuid") or ""
    ).strip().lower()
    if not owner_key:
        raise TaskRecoveryError("Web detection owner capacity key is unavailable")
    idempotency_key = str(idempotency_key or "").strip()
    if not (
        8 <= len(idempotency_key) <= 128
        and all(33 <= ord(char) <= 126 for char in idempotency_key)
    ):
        raise TaskRecoveryError("Web detection idempotency key is invalid")
    request_sha256 = hashlib.sha256(image_bytes).hexdigest()
    spool_name = None
    guest_usage_reserved = False

    def resolve_existing(rows):
        if not rows:
            return None
        row = rows[0]
        if str(row.get("mode") or "") != mode or str(row.get("request_sha256") or "") != request_sha256:
            raise TaskRecoveryError("Web detection idempotency key was reused for different input")
        if str(row.get("status") or "") != "failed":
            return str(row["job_id"]), True
        released = excute_sql(
            """
            UPDATE web_detection_tasks
            SET idempotency_key = NULL
            WHERE job_id = %s AND status = 'failed' AND idempotency_key = %s
            """,
            (row["job_id"], idempotency_key),
            fetch=False,
        )
        if released != 1:
            raise TaskRecoveryError("failed to release failed Web detection idempotency key")
        return None

    try:
        existing = excute_sql(
            """
            SELECT job_id, mode, request_sha256, status
            FROM web_detection_tasks
            WHERE owner_type = %s AND owner_key = %s AND idempotency_key = %s
            LIMIT 1
            """,
            (owner_type, owner_key, idempotency_key),
        )
        replay = resolve_existing(existing)
        if replay:
            return replay
        with _queue_submission_guard(len(image_bytes), owner_type, owner_key):
            # Recheck under the cross-process admission lock to close the race
            # between two first submissions carrying the same key.
            existing = excute_sql(
                """
                SELECT job_id, mode, request_sha256, status
                FROM web_detection_tasks
                WHERE owner_type = %s AND owner_key = %s AND idempotency_key = %s
                LIMIT 1
                """,
                (owner_type, owner_key, idempotency_key),
            )
            replay = resolve_existing(existing)
            if replay:
                return replay
            if is_guest:
                daily_rows = excute_sql(
                    "SELECT COUNT(*) AS cnt FROM web_guest_daily_usage WHERE usage_day = CURDATE()"
                )
                if daily_rows is None:
                    raise TaskRecoveryError("failed to inspect global guest detection allowance")
                if int((daily_rows or [{}])[0].get("cnt") or 0) >= WEB_GUEST_DAILY_GLOBAL_LIMIT:
                    raise QueueCapacityError("今日访客免费检测总额度已用完，请登录后继续检测")
                reserved = excute_sql(
                    """
                    INSERT IGNORE INTO web_guest_daily_usage (subject_hash, usage_day)
                    VALUES (%s, CURDATE())
                    """,
                    (owner_key,),
                    fetch=False,
                )
                if reserved is None:
                    raise TaskRecoveryError("failed to reserve guest detection allowance")
                if reserved != 1:
                    raise QueueCapacityError("今日访客免费检测次数已用完，请登录后继续检测")
                guest_usage_reserved = True
            spool_name = _write_web_task_spool(job_id, image_bytes)
            inserted = excute_sql(
                """
                INSERT INTO web_detection_tasks (
                    job_id, mode, filename, mime_type, request_sha256,
                    spool_path, spool_size, request_context_json, owner_type,
                    owner_key, idempotency_key, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued')
                """,
                (
                    job_id,
                    mode,
                    str(filename or "upload.img")[:255],
                    str(mimetype or "application/octet-stream")[:127],
                    request_sha256,
                    spool_name,
                    len(image_bytes),
                    context_json,
                    owner_type,
                    owner_key,
                    idempotency_key,
                ),
                fetch=False,
            )
            if inserted != 1:
                raise TaskRecoveryError("failed to persist Web detection job")
    except Exception:
        if spool_name:
            try:
                _web_spool_file_path(spool_name).unlink(missing_ok=True)
            except (OSError, TaskSpoolError):
                pass
        if guest_usage_reserved:
            excute_sql(
                "DELETE FROM web_guest_daily_usage WHERE subject_hash = %s AND usage_day = CURDATE()",
                (owner_key,),
                fetch=False,
            )
        raise
    return job_id, False


def _cleanup_orphan_spool_files():
    _ensure_spool_root()
    rows = excute_sql(
        "SELECT spool_path FROM developer_detection_tasks WHERE spool_path IS NOT NULL"
    )
    if rows is None:
        raise TaskRecoveryError("failed to load referenced task spools")
    referenced = {str(row.get("spool_path") or "") for row in rows}
    cutoff = time.time() - DEVELOPER_SPOOL_ORPHAN_GRACE_SECONDS
    cleaned = 0
    for path in DEVELOPER_SPOOL_ROOT.iterdir():
        try:
            item_stat = path.lstat()
            if path.name in referenced or item_stat.st_mtime > cutoff or stat.S_ISDIR(item_stat.st_mode):
                continue
            path.unlink()
            cleaned += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"[DEVELOPER TASK SPOOL ERROR] orphan cleanup {path.name}: {exc}")
    return cleaned


def _request_context(actor, user_info):
    return json.dumps(
        {
            "actor": {
                "id": actor.get("id"),
                "user_id": actor.get("user_id"),
                "account_uuid": actor.get("account_uuid") or "",
            },
            "user_info": {
                "Userid": user_info.get("Userid"),
                "account_uuid": user_info.get("account_uuid") or "",
                "username": user_info.get("username") or "developer",
                "phone": user_info.get("phone") or "",
                "openid": user_info.get("openid") or "",
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _load_request_context(row):
    try:
        context = json.loads(row.get("request_context_json") or "")
        actor = context["actor"]
        user_info = context["user_info"]
        if int(actor.get("id")) != int(row.get("key_id")):
            raise ValueError("API key owner mismatch")
        if int(actor.get("user_id")) != int(row.get("user_id")):
            raise ValueError("account owner mismatch")
        if int(user_info.get("Userid")) != int(row.get("user_id")):
            raise ValueError("detection owner mismatch")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaskSpoolError(f"task execution context is invalid: {exc}") from exc
    return actor, user_info


def _ensure_developer_platform_tables():
    global _PLATFORM_TABLES_READY
    if _PLATFORM_TABLES_READY:
        return True
    with _PLATFORM_TABLES_LOCK:
        if _PLATFORM_TABLES_READY:
            return True
        statements = (
            """
            CREATE TABLE IF NOT EXISTS developer_accounts (
              user_id INT NOT NULL,
              status VARCHAR(16) NOT NULL DEFAULT 'active',
              free_total INT NOT NULL DEFAULT 100,
              free_used INT NOT NULL DEFAULT 0,
              free_reserved INT NOT NULL DEFAULT 0,
              balance_fen BIGINT NOT NULL DEFAULT 0,
              balance_reserved_fen BIGINT NOT NULL DEFAULT 0,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (user_id),
              CONSTRAINT chk_developer_accounts_nonnegative CHECK (
                free_total >= 0 AND free_used >= 0 AND free_reserved >= 0
                AND balance_fen >= 0 AND balance_reserved_fen >= 0
              ),
              CONSTRAINT chk_developer_accounts_reservations CHECK (
                free_used + free_reserved <= free_total
                AND balance_reserved_fen <= balance_fen
              )
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS developer_pricing (
              mode VARCHAR(16) NOT NULL,
              display_name VARCHAR(64) NOT NULL,
              unit_price_fen INT NOT NULL DEFAULT 0,
              enabled TINYINT(1) NOT NULL DEFAULT 0,
              updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (mode),
              CONSTRAINT chk_developer_pricing_values CHECK (
                mode IN ('fast', 'swarm') AND unit_price_fen >= 0 AND enabled IN (0, 1)
              )
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS developer_detection_tasks (
              task_id VARCHAR(64) NOT NULL,
              user_id INT NOT NULL,
              account_uuid CHAR(36) NOT NULL,
              key_id BIGINT NOT NULL,
              mode VARCHAR(16) NOT NULL,
              filename VARCHAR(255) NOT NULL,
              mime_type VARCHAR(127) NOT NULL DEFAULT 'application/octet-stream',
              execution_filename VARCHAR(255) NULL,
              request_sha256 CHAR(64) NOT NULL,
              spool_path VARCHAR(255) NULL,
              spool_size BIGINT UNSIGNED NULL,
              request_context_json TEXT NULL,
              idempotency_key VARCHAR(128) NULL,
              status VARCHAR(24) NOT NULL DEFAULT 'preparing',
              next_attempt_at DATETIME(6) NULL,
              lease_owner VARCHAR(64) NULL,
              lease_expires_at DATETIME(6) NULL,
              attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
              last_heartbeat_at DATETIME(6) NULL,
              effect_item_id INT NULL,
              effect_result_json MEDIUMTEXT NULL,
              daily_quota_reserved TINYINT(1) NOT NULL DEFAULT 0,
              daily_quota_day DATE NULL,
              prompt_tokens INT UNSIGNED NOT NULL DEFAULT 0,
              completion_tokens INT UNSIGNED NOT NULL DEFAULT 0,
              total_tokens INT UNSIGNED NOT NULL DEFAULT 0,
              result_item_id INT NULL,
              result_json MEDIUMTEXT NULL,
              error_message VARCHAR(500) NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              completed_at DATETIME NULL,
              PRIMARY KEY (task_id),
              UNIQUE KEY uk_developer_task_idempotency (account_uuid, idempotency_key),
              KEY idx_developer_tasks_user_created (user_id, created_at),
              KEY idx_developer_tasks_key_created (key_id, created_at),
              KEY idx_developer_tasks_lease (status, lease_expires_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS web_detection_tasks (
              job_id VARCHAR(64) NOT NULL,
              mode VARCHAR(16) NOT NULL,
              filename VARCHAR(255) NOT NULL,
              mime_type VARCHAR(127) NOT NULL DEFAULT 'application/octet-stream',
              request_sha256 CHAR(64) NOT NULL,
              spool_path VARCHAR(255) NULL,
              spool_size BIGINT UNSIGNED NULL,
              request_context_json TEXT NULL,
              owner_type VARCHAR(16) NOT NULL,
              owner_key VARCHAR(64) NOT NULL,
              idempotency_key VARCHAR(128) NULL,
              status VARCHAR(24) NOT NULL DEFAULT 'queued',
              next_attempt_at DATETIME(6) NULL,
              lease_owner VARCHAR(64) NULL,
              lease_expires_at DATETIME(6) NULL,
              attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
              effect_item_id INT NULL,
              effect_result_json MEDIUMTEXT NULL,
              result_json MEDIUMTEXT NULL,
              error_message VARCHAR(500) NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              completed_at DATETIME NULL,
              PRIMARY KEY (job_id),
              KEY idx_web_detection_tasks_lease (status, lease_expires_at),
              KEY idx_web_detection_tasks_owner (owner_type, owner_key, status),
              UNIQUE KEY uk_web_detection_tasks_idempotency (owner_type, owner_key, idempotency_key),
              KEY idx_web_detection_tasks_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS web_guest_daily_usage (
              subject_hash CHAR(64) NOT NULL,
              usage_day DATE NOT NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (subject_hash, usage_day),
              KEY idx_web_guest_usage_day (usage_day)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS developer_billing_reservations (
              task_id VARCHAR(64) NOT NULL,
              user_id INT NOT NULL,
              key_id BIGINT NOT NULL,
              mode VARCHAR(16) NOT NULL,
              source VARCHAR(16) NOT NULL,
              amount_fen INT NOT NULL DEFAULT 0,
              status VARCHAR(16) NOT NULL DEFAULT 'reserved',
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              settled_at DATETIME NULL,
              released_at DATETIME NULL,
              PRIMARY KEY (task_id),
              KEY idx_developer_reservations_user_created (user_id, created_at),
              KEY idx_developer_reservations_status (status, created_at),
              CONSTRAINT chk_developer_reservation_values CHECK (
                mode IN ('fast', 'swarm')
                AND source IN ('free', 'balance')
                AND status IN ('reserved', 'settled', 'released')
                AND amount_fen >= 0
                AND (source <> 'free' OR amount_fen = 0)
              )
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS developer_billing_ledger (
              id BIGINT NOT NULL AUTO_INCREMENT,
              user_id INT NOT NULL,
              key_id BIGINT NULL,
              task_id VARCHAR(64) NULL,
              operation_id VARCHAR(128) NULL,
              business_reference VARCHAR(191) NOT NULL,
              currency CHAR(3) NOT NULL DEFAULT 'CNY',
              reversal_of_id BIGINT NULL,
              entry_type VARCHAR(32) NOT NULL,
              mode VARCHAR(16) NULL,
              free_calls_delta INT NOT NULL DEFAULT 0,
              free_calls_after INT NULL,
              balance_delta_fen BIGINT NOT NULL DEFAULT 0,
              amount_fen INT NOT NULL DEFAULT 0,
              balance_after_fen BIGINT NOT NULL DEFAULT 0,
              note VARCHAR(500) NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              UNIQUE KEY uk_developer_ledger_business_reference (business_reference),
              KEY idx_developer_ledger_user_created (user_id, created_at),
              KEY idx_developer_ledger_task (task_id),
              KEY idx_developer_ledger_operation (operation_id),
              KEY idx_developer_ledger_reversal (reversal_of_id),
              CONSTRAINT chk_developer_ledger_values CHECK (
                amount_fen >= 0 AND balance_after_fen >= 0
                AND (free_calls_after IS NULL OR free_calls_after >= 0)
                AND currency = 'CNY'
              )
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS developer_admin_operations (
              operation_id VARCHAR(128) NOT NULL,
              operation_type VARCHAR(32) NOT NULL,
              user_id INT NOT NULL,
              request_sha256 CHAR(64) NOT NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (operation_id),
              KEY idx_developer_admin_operations_user_created (user_id, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS developer_admin_operation_results (
              operation_id VARCHAR(128) NOT NULL,
              status_code INT NOT NULL DEFAULT 200,
              response_json MEDIUMTEXT NOT NULL,
              created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
              PRIMARY KEY (operation_id),
              CONSTRAINT fk_developer_admin_operation_results_operation
                FOREIGN KEY (operation_id) REFERENCES developer_admin_operations(operation_id)
                ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS admin_audit_logs (
              id BIGINT NOT NULL AUTO_INCREMENT,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              actor_id VARCHAR(64) NULL,
              actor_username VARCHAR(64) NULL,
              actor_phone VARCHAR(20) NULL,
              action VARCHAR(96) NOT NULL,
              target VARCHAR(191) NOT NULL,
              before_json LONGTEXT NULL,
              after_json LONGTEXT NULL,
              meta_json LONGTEXT NULL,
              PRIMARY KEY (id),
              KEY idx_admin_audit_created (created_at),
              KEY idx_admin_audit_action (action),
              KEY idx_admin_audit_target (target)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
        )
        for statement in statements:
            if excute_sql(statement, fetch=False) is None:
                return False
        if not _ensure_developer_usage_table():
            return False
        if not _ensure_security_audit_tables():
            return False
        if not _ensure_task_lease_schema():
            return False
        if not _ensure_billing_ledger_schema():
            return False
        if not _ensure_billing_constraints():
            return False
        if not _ensure_all_account_ledger_openings():
            return False
        defaults = (
            ("fast", "快速检测", DEVELOPER_FAST_PRICE_FEN, int(DEVELOPER_FAST_BILLING_ENABLED)),
            ("swarm", "Swarm 多源复核", DEVELOPER_SWARM_PRICE_FEN, int(DEVELOPER_SWARM_BILLING_ENABLED)),
        )
        for row in defaults:
            if excute_sql(
                """
                INSERT IGNORE INTO developer_pricing (mode, display_name, unit_price_fen, enabled)
                VALUES (%s, %s, %s, %s)
                """,
                row,
                fetch=False,
            ) is None:
                return False
        _PLATFORM_TABLES_READY = True
        return True


def _ensure_task_lease_schema():
    """Migrate existing task tables without assuming a specific MySQL minor version."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'developer_detection_tasks'
                """
            )
            columns = {str(row.get("COLUMN_NAME") or "").lower() for row in cursor.fetchall()}
            additions = (
                (
                    "account_uuid",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN account_uuid CHAR(36) NULL AFTER user_id",
                ),
                (
                    "mime_type",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN mime_type VARCHAR(127) "
                    "NOT NULL DEFAULT 'application/octet-stream' AFTER filename",
                ),
                (
                    "execution_filename",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN execution_filename VARCHAR(255) "
                    "NULL AFTER mime_type",
                ),
                (
                    "spool_path",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN spool_path VARCHAR(255) NULL "
                    "AFTER request_sha256",
                ),
                (
                    "spool_size",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN spool_size BIGINT UNSIGNED NULL "
                    "AFTER spool_path",
                ),
                (
                    "request_context_json",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN request_context_json TEXT NULL "
                    "AFTER spool_size",
                ),
                ("lease_owner", "ALTER TABLE developer_detection_tasks ADD COLUMN lease_owner VARCHAR(64) NULL AFTER status"),
                (
                    "next_attempt_at",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN next_attempt_at DATETIME(6) NULL AFTER status",
                ),
                (
                    "lease_expires_at",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN lease_expires_at DATETIME(6) NULL AFTER lease_owner",
                ),
                (
                    "attempt_count",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN attempt_count INT UNSIGNED "
                    "NOT NULL DEFAULT 0 AFTER lease_expires_at",
                ),
                (
                    "last_heartbeat_at",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN last_heartbeat_at DATETIME(6) NULL "
                    "AFTER attempt_count",
                ),
                (
                    "effect_item_id",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN effect_item_id INT NULL "
                    "AFTER last_heartbeat_at",
                ),
                (
                    "effect_result_json",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN effect_result_json MEDIUMTEXT NULL "
                    "AFTER effect_item_id",
                ),
                (
                    "daily_quota_reserved",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN daily_quota_reserved TINYINT(1) "
                    "NOT NULL DEFAULT 0 AFTER effect_result_json",
                ),
                (
                    "daily_quota_day",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN daily_quota_day DATE NULL "
                    "AFTER daily_quota_reserved",
                ),
                (
                    "prompt_tokens",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN prompt_tokens INT UNSIGNED NOT NULL DEFAULT 0 AFTER daily_quota_day",
                ),
                (
                    "completion_tokens",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN completion_tokens INT UNSIGNED NOT NULL DEFAULT 0 AFTER prompt_tokens",
                ),
                (
                    "total_tokens",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN total_tokens INT UNSIGNED NOT NULL DEFAULT 0 AFTER completion_tokens",
                ),
            )
            for column, statement in additions:
                if column in columns:
                    continue
                try:
                    cursor.execute(statement)
                except Exception:
                    cursor.execute(
                        """
                        SELECT COLUMN_NAME
                        FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND TABLE_NAME = 'developer_detection_tasks'
                          AND COLUMN_NAME = %s
                        """,
                        (column,),
                    )
                    if not cursor.fetchone():
                        raise
                columns.add(column)

            cursor.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'developer_detection_tasks'
                  AND INDEX_NAME = 'uk_developer_task_idempotency'
                ORDER BY SEQ_IN_INDEX
                """
            )
            desired_idempotency_columns = ["account_uuid", "idempotency_key"]
            idempotency_columns = [str(row.get("COLUMN_NAME") or "") for row in cursor.fetchall()]
            if idempotency_columns != desired_idempotency_columns:
                replacement_index = "uk_developer_task_account_idempotency_v2"
                cursor.execute(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'developer_detection_tasks'
                      AND INDEX_NAME = %s
                    ORDER BY SEQ_IN_INDEX
                    """,
                    (replacement_index,),
                )
                replacement_columns = [
                    str(row.get("COLUMN_NAME") or "") for row in cursor.fetchall()
                ]
                if replacement_columns and replacement_columns != desired_idempotency_columns:
                    raise RuntimeError("temporary developer task idempotency index has an unexpected definition")
                if not replacement_columns:
                    # Build the stricter replacement while the old uniqueness
                    # guard is still active. Duplicate data therefore aborts
                    # safely without leaving task submission unprotected.
                    cursor.execute(
                        f"""
                        ALTER TABLE developer_detection_tasks
                        ADD UNIQUE INDEX {replacement_index} (account_uuid, idempotency_key)
                        """
                    )
                if idempotency_columns:
                    cursor.execute(
                        "ALTER TABLE developer_detection_tasks DROP INDEX uk_developer_task_idempotency"
                    )
                cursor.execute(
                    f"""
                    ALTER TABLE developer_detection_tasks
                    RENAME INDEX {replacement_index} TO uk_developer_task_idempotency
                    """
                )

            cursor.execute(
                """
                SELECT INDEX_NAME
                FROM INFORMATION_SCHEMA.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'developer_detection_tasks'
                  AND INDEX_NAME = 'idx_developer_tasks_lease'
                LIMIT 1
                """
            )
            if not cursor.fetchone():
                try:
                    cursor.execute(
                        """
                        ALTER TABLE developer_detection_tasks
                        ADD INDEX idx_developer_tasks_lease (status, lease_expires_at)
                        """
                    )
                except Exception:
                    cursor.execute(
                        """
                        SELECT INDEX_NAME
                        FROM INFORMATION_SCHEMA.STATISTICS
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND TABLE_NAME = 'developer_detection_tasks'
                          AND INDEX_NAME = 'idx_developer_tasks_lease'
                        LIMIT 1
                        """
                    )
                    if not cursor.fetchone():
                        raise

            cursor.execute(
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'web_detection_tasks'
                """
            )
            web_columns = {
                str(row.get("COLUMN_NAME") or "").lower() for row in cursor.fetchall()
            }
            web_additions = (
                (
                    "owner_type",
                    "ALTER TABLE web_detection_tasks ADD COLUMN owner_type VARCHAR(16) "
                    "NOT NULL DEFAULT 'legacy' AFTER request_context_json",
                ),
                (
                    "owner_key",
                    "ALTER TABLE web_detection_tasks ADD COLUMN owner_key VARCHAR(64) "
                    "NOT NULL DEFAULT '' AFTER owner_type",
                ),
                (
                    "idempotency_key",
                    "ALTER TABLE web_detection_tasks ADD COLUMN idempotency_key "
                    "VARCHAR(128) NULL AFTER owner_key",
                ),
                (
                    "next_attempt_at",
                    "ALTER TABLE web_detection_tasks ADD COLUMN next_attempt_at DATETIME(6) NULL AFTER status",
                ),
                (
                    "effect_item_id",
                    "ALTER TABLE web_detection_tasks ADD COLUMN effect_item_id INT NULL AFTER attempt_count",
                ),
                (
                    "effect_result_json",
                    "ALTER TABLE web_detection_tasks ADD COLUMN effect_result_json MEDIUMTEXT NULL AFTER effect_item_id",
                ),
            )
            for column, statement in web_additions:
                if column not in web_columns:
                    cursor.execute(statement)
                    web_columns.add(column)
            cursor.execute(
                """
                SELECT INDEX_NAME
                FROM INFORMATION_SCHEMA.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'web_detection_tasks'
                  AND INDEX_NAME = 'idx_web_detection_tasks_owner'
                LIMIT 1
                """
            )
            if not cursor.fetchone():
                cursor.execute(
                    """
                    ALTER TABLE web_detection_tasks
                    ADD INDEX idx_web_detection_tasks_owner (owner_type, owner_key, status)
                    """
                )
            cursor.execute(
                """
                SELECT INDEX_NAME
                FROM INFORMATION_SCHEMA.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'web_detection_tasks'
                  AND INDEX_NAME = 'uk_web_detection_tasks_idempotency'
                LIMIT 1
                """
            )
            if not cursor.fetchone():
                cursor.execute(
                    """
                    ALTER TABLE web_detection_tasks
                    ADD UNIQUE INDEX uk_web_detection_tasks_idempotency
                        (owner_type, owner_key, idempotency_key)
                    """
                )

            # Pre-worker releases kept request bytes only in process memory. Mark
            # those orphaned active rows expired so reconciliation can release
            # their billing reservations instead of pretending they are resumable.
            cursor.execute(
                """
                UPDATE developer_detection_tasks
                SET lease_owner = COALESCE(lease_owner, CONCAT('legacy-', LEFT(task_id, 40))),
                    lease_expires_at = NOW(6)
                WHERE status IN ('queued', 'running')
                  AND spool_path IS NULL
                  AND lease_expires_at IS NULL
                """
            )
        conn.commit()
        return True
    except Exception as exc:
        if conn:
            conn.rollback()
        print(f"[DEVELOPER TASK RECOVERY ERROR] lease schema migration failed: {exc}")
        return False
    finally:
        if conn:
            conn.close()


def _ensure_billing_ledger_schema():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COLUMN_NAME, IS_NULLABLE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'developer_billing_ledger'
                """
            )
            column_rows = cursor.fetchall()
            columns = {
                str(row.get("COLUMN_NAME") or "").lower(): row for row in column_rows
            }
            additions = (
                ("operation_id", "VARCHAR(128) NULL AFTER task_id"),
                ("business_reference", "VARCHAR(191) NULL AFTER operation_id"),
                ("currency", "CHAR(3) NOT NULL DEFAULT 'CNY' AFTER business_reference"),
                ("reversal_of_id", "BIGINT NULL AFTER currency"),
                ("free_calls_after", "INT NULL AFTER free_calls_delta"),
            )
            for column, definition in additions:
                if column not in columns:
                    cursor.execute(
                        f"ALTER TABLE developer_billing_ledger ADD COLUMN {column} {definition}"
                    )
            cursor.execute(
                """
                UPDATE developer_billing_ledger
                SET business_reference = CONCAT('legacy-ledger:', id)
                WHERE business_reference IS NULL OR business_reference = ''
                """
            )
            cursor.execute(
                """
                SELECT COUNT(*) AS invalid_count
                FROM developer_billing_ledger
                WHERE business_reference IS NULL OR business_reference = '' OR currency <> 'CNY'
                """
            )
            if int((cursor.fetchone() or {}).get("invalid_count") or 0):
                raise RuntimeError("billing ledger contains rows without a valid business reference")
            business_reference_row = columns.get("business_reference")
            if (
                business_reference_row is None
                or str(business_reference_row.get("IS_NULLABLE") or "").upper() != "NO"
            ):
                cursor.execute(
                    """
                    ALTER TABLE developer_billing_ledger
                    MODIFY COLUMN business_reference VARCHAR(191) NOT NULL
                    """
                )

            indexes = (
                ("uk_developer_ledger_business_reference", "UNIQUE", "business_reference"),
                ("idx_developer_ledger_operation", "", "operation_id"),
                ("idx_developer_ledger_reversal", "", "reversal_of_id"),
            )
            for index_name, uniqueness, column in indexes:
                cursor.execute(
                    """
                    SELECT INDEX_NAME
                    FROM INFORMATION_SCHEMA.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = 'developer_billing_ledger'
                      AND INDEX_NAME = %s
                    LIMIT 1
                    """,
                    (index_name,),
                )
                if cursor.fetchone():
                    continue
                cursor.execute(
                    f"ALTER TABLE developer_billing_ledger ADD {uniqueness} INDEX {index_name} ({column})"
                )
        conn.commit()
        return True
    except Exception as exc:
        if conn:
            conn.rollback()
        print(f"[DEVELOPER BILLING SCHEMA ERROR] ledger migration failed: {exc}")
        return False
    finally:
        if conn:
            conn.close()


def _ensure_account_ledger_openings(user_id=None):
    """Backfill one auditable opening entry so ledger deltas reconstruct balances."""
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            if user_id is None:
                cursor.execute("SELECT user_id FROM developer_accounts ORDER BY user_id FOR UPDATE")
                cursor.fetchall()
                where = ""
                params = ()
            else:
                cursor.execute(
                    "SELECT user_id FROM developer_accounts WHERE user_id = %s FOR UPDATE",
                    (int(user_id),),
                )
                if not cursor.fetchone():
                    raise RuntimeError("developer account is missing while creating its opening ledger entry")
                where = "WHERE a.user_id = %s"
                params = (int(user_id),)
            cursor.execute(
                f"""
                SELECT COUNT(*) AS invalid_count
                FROM (
                    SELECT a.user_id,
                           a.free_total - a.free_used - COALESCE(SUM(l.free_calls_delta), 0) AS opening_free,
                           a.balance_fen - COALESCE(SUM(l.balance_delta_fen), 0) AS opening_balance
                    FROM developer_accounts a
                    LEFT JOIN developer_billing_ledger l ON l.user_id = a.user_id
                    {where}
                    GROUP BY a.user_id, a.free_total, a.free_used, a.balance_fen
                    HAVING opening_free < 0 OR opening_balance < 0
                ) invalid_openings
                """,
                params,
            )
            if int((cursor.fetchone() or {}).get("invalid_count") or 0):
                raise RuntimeError("existing ledger deltas cannot be reconciled to a nonnegative opening balance")
            cursor.execute(
                f"""
                INSERT IGNORE INTO developer_billing_ledger
                    (user_id, business_reference, currency, entry_type,
                     free_calls_delta, free_calls_after, balance_delta_fen,
                     amount_fen, balance_after_fen, note, created_at)
                SELECT a.user_id,
                       CONCAT('opening:user:', a.user_id),
                       'CNY',
                       'opening_balance',
                       a.free_total - a.free_used - COALESCE(SUM(l.free_calls_delta), 0),
                       a.free_total - a.free_used - COALESCE(SUM(l.free_calls_delta), 0),
                       a.balance_fen - COALESCE(SUM(l.balance_delta_fen), 0),
                       0,
                       a.balance_fen - COALESCE(SUM(l.balance_delta_fen), 0),
                       '账本期初余额',
                       a.created_at
                FROM developer_accounts a
                LEFT JOIN developer_billing_ledger l ON l.user_id = a.user_id
                {where}
                GROUP BY a.user_id, a.free_total, a.free_used, a.balance_fen, a.created_at
                HAVING COALESCE(SUM(
                    l.business_reference = CONCAT('opening:user:', a.user_id)
                ), 0) = 0
                """,
                params,
            )
            cursor.execute(
                f"""
                SELECT COUNT(*) AS missing_count
                FROM developer_accounts a
                LEFT JOIN developer_billing_ledger l
                  ON l.user_id = a.user_id
                 AND l.business_reference = CONCAT('opening:user:', a.user_id)
                {where}
                AND l.id IS NULL
                """ if where else """
                SELECT COUNT(*) AS missing_count
                FROM developer_accounts a
                LEFT JOIN developer_billing_ledger l
                  ON l.user_id = a.user_id
                 AND l.business_reference = CONCAT('opening:user:', a.user_id)
                WHERE l.id IS NULL
                """,
                params,
            )
            if int((cursor.fetchone() or {}).get("missing_count") or 0):
                raise RuntimeError("one or more developer accounts have no opening ledger entry")
        conn.commit()
        return True
    except Exception as exc:
        if conn:
            conn.rollback()
        print(f"[DEVELOPER BILLING SCHEMA ERROR] opening ledger migration failed: {exc}")
        return False
    finally:
        if conn:
            conn.close()


def _ensure_all_account_ledger_openings():
    return _ensure_account_ledger_openings()


def _ensure_billing_constraints():
    """Add financial invariants after validating existing production rows."""
    specifications = (
        (
            "developer_accounts",
            "chk_developer_accounts_nonnegative",
            "free_total >= 0 AND free_used >= 0 AND free_reserved >= 0 "
            "AND balance_fen >= 0 AND balance_reserved_fen >= 0",
            "free_total < 0 OR free_used < 0 OR free_reserved < 0 "
            "OR balance_fen < 0 OR balance_reserved_fen < 0",
        ),
        (
            "developer_accounts",
            "chk_developer_accounts_reservations",
            "free_used + free_reserved <= free_total AND balance_reserved_fen <= balance_fen",
            "free_used + free_reserved > free_total OR balance_reserved_fen > balance_fen",
        ),
        (
            "developer_pricing",
            "chk_developer_pricing_values",
            "mode IN ('fast', 'swarm') AND unit_price_fen >= 0 AND enabled IN (0, 1)",
            "mode NOT IN ('fast', 'swarm') OR unit_price_fen < 0 OR enabled NOT IN (0, 1)",
        ),
        (
            "developer_billing_reservations",
            "chk_developer_reservation_values",
            "mode IN ('fast', 'swarm') AND source IN ('free', 'balance') "
            "AND status IN ('reserved', 'settled', 'released') AND amount_fen >= 0 "
            "AND (source <> 'free' OR amount_fen = 0)",
            "mode NOT IN ('fast', 'swarm') OR source NOT IN ('free', 'balance') "
            "OR status NOT IN ('reserved', 'settled', 'released') OR amount_fen < 0 "
            "OR (source = 'free' AND amount_fen <> 0)",
        ),
        (
            "developer_billing_ledger",
            "chk_developer_ledger_values",
            "amount_fen >= 0 AND balance_after_fen >= 0 "
            "AND (free_calls_after IS NULL OR free_calls_after >= 0) AND currency = 'CNY'",
            "amount_fen < 0 OR balance_after_fen < 0 "
            "OR (free_calls_after IS NOT NULL AND free_calls_after < 0) OR currency <> 'CNY'",
        ),
    )
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            for table, constraint, expression, invalid_expression in specifications:
                cursor.execute(
                    """
                    SELECT CONSTRAINT_NAME
                    FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = %s
                      AND CONSTRAINT_NAME = %s
                      AND CONSTRAINT_TYPE = 'CHECK'
                    LIMIT 1
                    """,
                    (table, constraint),
                )
                if cursor.fetchone():
                    continue
                cursor.execute(
                    f"SELECT COUNT(*) AS invalid_count FROM `{table}` WHERE {invalid_expression}"
                )
                invalid_count = int((cursor.fetchone() or {}).get("invalid_count") or 0)
                if invalid_count:
                    raise RuntimeError(
                        f"cannot add {constraint}: {invalid_count} existing row(s) violate the invariant"
                    )
                try:
                    cursor.execute(
                        f"ALTER TABLE `{table}` ADD CONSTRAINT `{constraint}` CHECK ({expression})"
                    )
                except Exception:
                    # A concurrent release may have installed the same named
                    # constraint. Re-read metadata before deciding it failed.
                    cursor.execute(
                        """
                        SELECT CONSTRAINT_NAME
                        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND TABLE_NAME = %s
                          AND CONSTRAINT_NAME = %s
                          AND CONSTRAINT_TYPE = 'CHECK'
                        LIMIT 1
                        """,
                        (table, constraint),
                    )
                    if not cursor.fetchone():
                        raise
        conn.commit()
        return True
    except Exception as exc:
        if conn:
            conn.rollback()
        print(f"[DEVELOPER BILLING SCHEMA ERROR] constraint migration failed: {exc}")
        return False
    finally:
        if conn:
            conn.close()


def _ensure_developer_account(user_id):
    if not _ensure_developer_platform_tables():
        return False
    inserted = excute_sql(
        "INSERT IGNORE INTO developer_accounts (user_id, free_total) VALUES (%s, %s)",
        (user_id, DEVELOPER_FREE_CALLS),
        fetch=False,
    )
    if inserted is None:
        return False
    opening = excute_sql(
        """
        SELECT id FROM developer_billing_ledger
        WHERE user_id = %s AND business_reference = %s
        LIMIT 1
        """,
        (user_id, f"opening:user:{int(user_id)}"),
    )
    if opening is None:
        return False
    if not opening and not _ensure_account_ledger_openings(user_id):
        return False
    reconciliation = excute_sql(
        """
        SELECT a.free_total - a.free_used AS account_free_calls,
               a.balance_fen AS account_balance_fen,
               COALESCE(SUM(l.free_calls_delta), 0) AS ledger_free_calls,
               COALESCE(SUM(l.balance_delta_fen), 0) AS ledger_balance_fen
        FROM developer_accounts a
        LEFT JOIN developer_billing_ledger l ON l.user_id = a.user_id
        WHERE a.user_id = %s
        GROUP BY a.user_id, a.free_total, a.free_used, a.balance_fen
        """,
        (user_id,),
    )
    if not reconciliation:
        return False
    row = reconciliation[0]
    matches = (
        int(row.get("account_free_calls") or 0) == int(row.get("ledger_free_calls") or 0)
        and int(row.get("account_balance_fen") or 0) == int(row.get("ledger_balance_fen") or 0)
    )
    if not matches:
        print(f"[DEVELOPER BILLING INVARIANT] account {int(user_id)} does not reconcile to its ledger")
    return matches


def _pricing_rows():
    rows = excute_sql(
        """
        SELECT mode, display_name, unit_price_fen, enabled, updated_at
        FROM developer_pricing
        WHERE mode IN ('fast', 'swarm')
        ORDER BY FIELD(mode, 'fast', 'swarm')
        """
    )
    if rows is None:
        raise BillingError("读取计费配置失败")
    return rows


def _pricing_payload(rows=None):
    return [
        {
            "mode": row.get("mode"),
            "name": row.get("display_name"),
            "unitPriceFen": int(row.get("unit_price_fen") or 0),
            "unitPriceCny": f"{int(row.get('unit_price_fen') or 0) / 100:.2f}",
            "enabled": bool(row.get("enabled")),
            "updatedAt": format_createtime(row.get("updated_at")),
        }
        for row in (rows if rows is not None else _pricing_rows())
    ]


def _account_row(user_id):
    if not _ensure_developer_account(user_id):
        raise BillingError("开发者账户初始化失败")
    rows = excute_sql(
        """
        SELECT user_id, status, free_total, free_used, free_reserved,
               balance_fen, balance_reserved_fen, created_at, updated_at
        FROM developer_accounts
        WHERE user_id = %s
        LIMIT 1
        """,
        (user_id,),
    )
    if not rows:
        raise BillingError("开发者账户读取失败")
    return rows[0]


def _account_payload(row):
    free_total = int(row.get("free_total") or 0)
    free_used = int(row.get("free_used") or 0)
    free_reserved = int(row.get("free_reserved") or 0)
    balance_fen = int(row.get("balance_fen") or 0)
    balance_reserved_fen = int(row.get("balance_reserved_fen") or 0)
    return {
        "userId": row.get("user_id"),
        "status": row.get("status") or "active",
        "freeTotal": free_total,
        "freeUsed": free_used,
        "freeReserved": free_reserved,
        "freeRemaining": max(0, free_total - free_used - free_reserved),
        "balanceFen": balance_fen,
        "balanceCny": f"{balance_fen / 100:.2f}",
        "balanceReservedFen": balance_reserved_fen,
        "availableBalanceFen": max(0, balance_fen - balance_reserved_fen),
        "createdAt": format_createtime(row.get("created_at")),
        "updatedAt": format_createtime(row.get("updated_at")),
    }


def _reserve_billing(user_id, key_id, task_id, mode):
    if mode not in {"fast", "swarm"}:
        raise BillingError("不支持的检测模式", code="invalid_mode", status_code=400)
    if not _ensure_developer_platform_tables():
        raise BillingError("计费系统初始化失败")

    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id
                FROM developer_detection_tasks
                WHERE task_id = %s
                  AND status = 'preparing'
                  AND spool_path IS NOT NULL
                FOR UPDATE
                """,
                (task_id,),
            )
            if not cursor.fetchone():
                raise BillingError(
                    "任务尚未完成可靠入队，请重新提交",
                    code="task_lease_expired",
                    status_code=503,
                )
            cursor.execute(
                "INSERT IGNORE INTO developer_accounts (user_id, free_total) VALUES (%s, %s)",
                (user_id, DEVELOPER_FREE_CALLS),
            )
            cursor.execute(
                """
                SELECT status, free_total, free_used, free_reserved, balance_fen, balance_reserved_fen
                FROM developer_accounts WHERE user_id = %s FOR UPDATE
                """,
                (user_id,),
            )
            account = cursor.fetchone()
            if not account or account.get("status") != "active":
                raise BillingError("开发者账户不可用", code="account_disabled", status_code=403)

            free_available = (
                int(account.get("free_total") or 0)
                - int(account.get("free_used") or 0)
                - int(account.get("free_reserved") or 0)
            )
            if free_available > 0:
                source = "free"
                amount_fen = 0
                cursor.execute(
                    "UPDATE developer_accounts SET free_reserved = free_reserved + 1 WHERE user_id = %s",
                    (user_id,),
                )
            else:
                cursor.execute(
                    "SELECT unit_price_fen, enabled FROM developer_pricing WHERE mode = %s FOR UPDATE",
                    (mode,),
                )
                pricing = cursor.fetchone()
                if not pricing or not bool(pricing.get("enabled")) or int(pricing.get("unit_price_fen") or 0) <= 0:
                    raise BillingError(
                        "赠送额度已用完，当前付费套餐尚未开通，请联系管理员",
                        code="paid_plan_unavailable",
                        status_code=402,
                    )
                amount_fen = int(pricing.get("unit_price_fen") or 0)
                available_balance = int(account.get("balance_fen") or 0) - int(account.get("balance_reserved_fen") or 0)
                if available_balance < amount_fen:
                    raise BillingError("账户余额不足，请联系管理员充值", code="insufficient_balance", status_code=402)
                source = "balance"
                cursor.execute(
                    """
                    UPDATE developer_accounts
                    SET balance_reserved_fen = balance_reserved_fen + %s
                    WHERE user_id = %s
                    """,
                    (amount_fen, user_id),
                )

            cursor.execute(
                """
                INSERT INTO developer_billing_reservations
                    (task_id, user_id, key_id, mode, source, amount_fen, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'reserved')
                """,
                (task_id, user_id, key_id, mode, source, amount_fen),
            )
        conn.commit()
        return {"source": source, "amountFen": amount_fen, "status": "reserved"}
    except BillingError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise BillingError(f"额度预占失败: {exc}") from exc
    finally:
        conn.close()


def _settle_billing(task_id):
    if not _ensure_developer_platform_tables():
        return False
    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id, user_id, key_id, mode, source, amount_fen, status
                FROM developer_billing_reservations
                WHERE task_id = %s FOR UPDATE
                """,
                (task_id,),
            )
            reservation = cursor.fetchone()
            if not reservation or reservation.get("status") != "reserved":
                conn.rollback()
                return False
            user_id = reservation["user_id"]
            amount_fen = int(reservation.get("amount_fen") or 0)
            cursor.execute(
                """
                SELECT free_total, free_used, balance_fen
                FROM developer_accounts WHERE user_id = %s FOR UPDATE
                """,
                (user_id,),
            )
            account = cursor.fetchone() or {"free_total": 0, "free_used": 0, "balance_fen": 0}
            cursor.execute(
                """
                SELECT user_id, key_id, mode, status,
                       prompt_tokens, completion_tokens, total_tokens,
                       task_id, daily_quota_reserved, daily_quota_day
                FROM developer_detection_tasks
                WHERE task_id = %s
                FOR UPDATE
                """,
                (task_id,),
            )
            usage = cursor.fetchone()
            if (
                not usage
                or usage.get("status") != "success"
                or int(usage.get("user_id") or 0) != int(user_id)
                or int(usage.get("key_id") or 0) != int(reservation.get("key_id") or 0)
                or str(usage.get("mode") or "") != str(reservation.get("mode") or "")
            ):
                raise BillingError(
                    "检测任务与结算记录不一致",
                    code="billing_invariant_failed",
                    status_code=503,
                )
            _consume_task_daily_quota_in_cursor(cursor, usage)
            if reservation.get("source") not in {"free", "balance"}:
                raise BillingError(
                    "结算来源无效",
                    code="billing_invariant_failed",
                    status_code=503,
                )
            if reservation.get("source") == "free":
                cursor.execute(
                    """
                    UPDATE developer_accounts
                    SET free_reserved = free_reserved - 1, free_used = free_used + 1
                    WHERE user_id = %s AND free_reserved >= 1
                    """,
                    (user_id,),
                )
                if cursor.rowcount != 1:
                    raise BillingError(
                        "免费额度预占与结算记录不一致",
                        code="billing_invariant_failed",
                        status_code=503,
                    )
                entry_type = "detection_free"
                free_delta = -1
                free_after = max(
                    0,
                    int(account.get("free_total") or 0)
                    - int(account.get("free_used") or 0)
                    - 1,
                )
                balance_delta = 0
                balance_after = int(account.get("balance_fen") or 0)
            else:
                cursor.execute(
                    """
                    UPDATE developer_accounts
                    SET balance_reserved_fen = balance_reserved_fen - %s,
                        balance_fen = balance_fen - %s
                    WHERE user_id = %s
                      AND balance_reserved_fen >= %s
                      AND balance_fen >= %s
                    """,
                    (amount_fen, amount_fen, user_id, amount_fen, amount_fen),
                )
                if cursor.rowcount != 1:
                    raise BillingError(
                        "余额预占与结算记录不一致",
                        code="billing_invariant_failed",
                        status_code=503,
                    )
                entry_type = "detection_charge"
                free_delta = 0
                free_after = max(
                    0,
                    int(account.get("free_total") or 0)
                    - int(account.get("free_used") or 0),
                )
                balance_delta = -amount_fen
                balance_after = int(account.get("balance_fen") or 0) - amount_fen
            cursor.execute(
                """
                UPDATE developer_billing_reservations
                SET status = 'settled', settled_at = NOW()
                WHERE task_id = %s AND status = 'reserved'
                """,
                (task_id,),
            )
            if cursor.rowcount != 1:
                raise BillingError(
                    "结算记录状态已变化",
                    code="billing_invariant_failed",
                    status_code=503,
                )
            cursor.execute(
                """
                INSERT INTO developer_billing_ledger
                    (user_id, key_id, task_id, business_reference, currency,
                     entry_type, mode, free_calls_delta, free_calls_after,
                     balance_delta_fen, amount_fen, balance_after_fen, note)
                VALUES (%s, %s, %s, %s, 'CNY', %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    reservation.get("key_id"),
                    task_id,
                    f"settlement:{task_id}",
                    entry_type,
                    reservation.get("mode"),
                    free_delta,
                    free_after,
                    balance_delta,
                    amount_fen,
                    balance_after,
                    "成功检测结算",
                ),
            )
            cursor.execute(
                """
                INSERT IGNORE INTO developer_usage_events
                    (task_id, user_id, key_id, pipeline, endpoint, model_version, status_code,
                     prompt_tokens, completion_tokens, total_tokens, billable, decision_status)
                VALUES (%s, %s, %s, 'openapi', %s, %s, 200, %s, %s, %s, 1, 'verdict')
                """,
                (
                    task_id,
                    user_id,
                    reservation.get("key_id"),
                    f"/api/openapi/v1/image-detections:{reservation.get('mode')}",
                    f"huijian-image-{reservation.get('mode')}",
                    max(0, int(usage.get("prompt_tokens") or 0)),
                    max(0, int(usage.get("completion_tokens") or 0)),
                    max(0, int(usage.get("total_tokens") or 0)),
                ),
            )
            if cursor.rowcount != 1:
                raise BillingError(
                    "用量事件已存在或写入失败",
                    code="billing_invariant_failed",
                    status_code=503,
                )
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        print(f"[DEVELOPER BILLING ERROR] settle {task_id}: {exc}")
        return False
    finally:
        conn.close()


def _release_billing(
    task_id,
    note="检测未成功，释放预占额度",
    *,
    audit_entry_type=None,
):
    if not _ensure_developer_platform_tables():
        return False
    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            reservation_columns = "user_id, source, amount_fen, status"
            if audit_entry_type:
                reservation_columns = "user_id, key_id, mode, source, amount_fen, status"
            cursor.execute(
                f"""
                SELECT {reservation_columns}
                FROM developer_billing_reservations
                WHERE task_id = %s FOR UPDATE
                """,
                (task_id,),
            )
            reservation = cursor.fetchone()
            already_released = bool(
                reservation
                and reservation.get("status") == "released"
                and audit_entry_type
            )
            if not reservation or (
                reservation.get("status") != "reserved" and not already_released
            ):
                conn.rollback()
                return False
            if not already_released and reservation.get("source") == "free":
                cursor.execute(
                    """
                    UPDATE developer_accounts
                    SET free_reserved = free_reserved - 1
                    WHERE user_id = %s AND free_reserved >= 1
                    """,
                    (reservation["user_id"],),
                )
                if cursor.rowcount != 1:
                    raise BillingError(
                        "免费额度预占计数与释放记录不一致",
                        code="billing_invariant_failed",
                        status_code=503,
                    )
            elif not already_released and reservation.get("source") == "balance":
                amount_fen = int(reservation.get("amount_fen") or 0)
                if amount_fen <= 0:
                    raise BillingError(
                        "余额预占金额无效",
                        code="billing_invariant_failed",
                        status_code=503,
                    )
                cursor.execute(
                    """
                    UPDATE developer_accounts
                    SET balance_reserved_fen = balance_reserved_fen - %s
                    WHERE user_id = %s AND balance_reserved_fen >= %s
                    """,
                    (amount_fen, reservation["user_id"], amount_fen),
                )
                if cursor.rowcount != 1:
                    raise BillingError(
                        "余额预占计数与释放记录不一致",
                        code="billing_invariant_failed",
                        status_code=503,
                    )
            elif not already_released:
                raise BillingError(
                    "额度预占来源无效",
                    code="billing_invariant_failed",
                    status_code=503,
                )
            if not already_released:
                cursor.execute(
                    """
                    UPDATE developer_billing_reservations
                    SET status = 'released', released_at = NOW()
                    WHERE task_id = %s AND status = 'reserved'
                    """,
                    (task_id,),
                )
                if cursor.rowcount != 1:
                    raise BillingError(
                        "释放结算记录时状态已变化",
                        code="billing_invariant_failed",
                        status_code=503,
                    )
            if audit_entry_type:
                cursor.execute(
                    """
                    SELECT user_id, key_id, mode, status,
                           prompt_tokens, completion_tokens, total_tokens,
                           task_id, daily_quota_reserved, daily_quota_day
                    FROM developer_detection_tasks
                    WHERE task_id = %s
                    FOR UPDATE
                    """,
                    (task_id,),
                )
                usage = cursor.fetchone()
                if (
                    not usage
                    or usage.get("status") != "success"
                    or int(usage.get("user_id") or 0) != int(reservation["user_id"])
                ):
                    raise BillingError(
                        "复核态任务与释放记录不一致",
                        code="billing_invariant_failed",
                        status_code=503,
                    )
                _consume_task_daily_quota_in_cursor(cursor, usage)
                cursor.execute(
                    "SELECT balance_fen FROM developer_accounts WHERE user_id = %s FOR UPDATE",
                    (reservation["user_id"],),
                )
                account = cursor.fetchone() or {"balance_fen": 0}
                if not already_released:
                    cursor.execute(
                        """
                        INSERT INTO developer_billing_ledger
                            (user_id, key_id, task_id, business_reference, currency,
                             entry_type, mode, free_calls_delta,
                             balance_delta_fen, amount_fen, balance_after_fen, note)
                        VALUES (%s, %s, %s, %s, 'CNY', %s, %s, 0, 0, 0, %s, %s)
                        """,
                        (
                            reservation["user_id"],
                            reservation.get("key_id"),
                            task_id,
                            f"review-only:{task_id}",
                            str(audit_entry_type)[:64],
                            reservation.get("mode"),
                            int(account.get("balance_fen") or 0),
                            str(note)[:255],
                        ),
                    )
                cursor.execute(
                    """
                    INSERT IGNORE INTO developer_usage_events
                        (task_id, user_id, key_id, pipeline, endpoint, model_version, status_code,
                         prompt_tokens, completion_tokens, total_tokens, billable, decision_status)
                    VALUES (%s, %s, %s, 'openapi', %s, %s, 200, %s, %s, %s, 0, 'review_only')
                    """,
                    (
                        task_id,
                        reservation["user_id"],
                        reservation.get("key_id"),
                        f"/api/openapi/v1/image-detections:{reservation.get('mode')}",
                        f"huijian-image-{reservation.get('mode')}-review-only",
                        max(0, int(usage.get("prompt_tokens") or 0)),
                        max(0, int(usage.get("completion_tokens") or 0)),
                        max(0, int(usage.get("total_tokens") or 0)),
                    ),
                )
                if cursor.rowcount != 1 and not already_released:
                    raise BillingError(
                        "复核态用量事件已存在或写入失败",
                        code="billing_invariant_failed",
                        status_code=503,
                    )
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        print(f"[DEVELOPER BILLING INVARIANT] release {task_id}: {exc}; {note}")
        return False
    finally:
        conn.close()


def _reverse_settled_review_only(task_id):
    """Idempotently reverse a legacy settled task that has no verdict authority."""
    if not _ensure_developer_platform_tables():
        return False
    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id, user_id, key_id, mode, status, result_item_id, result_json,
                       daily_quota_reserved, daily_quota_day
                FROM developer_detection_tasks
                WHERE task_id = %s FOR UPDATE
                """,
                (task_id,),
            )
            task = cursor.fetchone()
            if not task or task.get("status") != "success":
                conn.rollback()
                return False
            decision_status, billable, _expected = _task_billing_outcome(task)
            if billable or decision_status != "review_only":
                conn.rollback()
                return False
            cursor.execute(
                """
                SELECT user_id, key_id, mode, source, amount_fen, status
                FROM developer_billing_reservations
                WHERE task_id = %s FOR UPDATE
                """,
                (task_id,),
            )
            reservation = cursor.fetchone()
            if not reservation or reservation.get("status") != "settled":
                conn.rollback()
                return False
            if int(reservation.get("user_id") or 0) != int(task.get("user_id") or 0):
                raise BillingError("冲正任务与结算归属不一致", code="billing_invariant_failed")
            cursor.execute(
                "SELECT balance_fen FROM developer_accounts WHERE user_id = %s FOR UPDATE",
                (reservation["user_id"],),
            )
            account = cursor.fetchone()
            if not account:
                raise BillingError("冲正账户不存在", code="billing_invariant_failed")
            amount_fen = max(0, int(reservation.get("amount_fen") or 0))
            if reservation.get("source") == "free":
                cursor.execute(
                    "UPDATE developer_accounts SET free_used = free_used - 1 WHERE user_id = %s AND free_used >= 1",
                    (reservation["user_id"],),
                )
                if cursor.rowcount != 1:
                    raise BillingError("免费额度冲正不一致", code="billing_invariant_failed")
                free_delta, balance_delta = 1, 0
                balance_after = int(account.get("balance_fen") or 0)
            elif reservation.get("source") == "balance":
                cursor.execute(
                    "UPDATE developer_accounts SET balance_fen = balance_fen + %s WHERE user_id = %s",
                    (amount_fen, reservation["user_id"]),
                )
                if cursor.rowcount != 1:
                    raise BillingError("余额冲正失败", code="billing_invariant_failed")
                free_delta, balance_delta = 0, amount_fen
                balance_after = int(account.get("balance_fen") or 0) + amount_fen
            else:
                raise BillingError("冲正来源无效", code="billing_invariant_failed")
            cursor.execute(
                """
                UPDATE developer_billing_reservations
                SET status = 'released', released_at = NOW()
                WHERE task_id = %s AND status = 'settled'
                """,
                (task_id,),
            )
            if cursor.rowcount != 1:
                raise BillingError("冲正状态已变化", code="billing_invariant_failed")
            _consume_task_daily_quota_in_cursor(cursor, task)
            stored_payload = _stored_task_result(task) or {
                "status": "success",
                "result": {"itemid": task.get("result_item_id")},
            }
            normalized = _public_result_payload(stored_payload, task.get("mode"))
            cursor.execute(
                "UPDATE developer_detection_tasks SET result_json = %s WHERE task_id = %s",
                (json.dumps(normalized, ensure_ascii=False, default=str), task_id),
            )
            cursor.execute(
                "UPDATE developer_usage_events SET billable = 0, decision_status = 'review_only' WHERE task_id = %s",
                (task_id,),
            )
            cursor.execute(
                """
                SELECT id
                FROM developer_billing_ledger
                WHERE task_id = %s AND entry_type IN ('detection_free', 'detection_charge')
                ORDER BY id ASC
                LIMIT 1
                """,
                (task_id,),
            )
            original_ledger = cursor.fetchone()
            if not original_ledger:
                raise BillingError("冲正缺少原始账本记录", code="billing_invariant_failed")
            cursor.execute(
                """
                INSERT INTO developer_billing_ledger
                    (user_id, key_id, task_id, business_reference, currency, reversal_of_id,
                     entry_type, mode, free_calls_delta, balance_delta_fen,
                     amount_fen, balance_after_fen, note)
                VALUES (%s, %s, %s, %s, 'CNY', %s,
                        'detection_review_only_reversal', %s, %s, %s, %s, %s, %s)
                """,
                (
                    reservation["user_id"], reservation.get("key_id"), task_id,
                    f"review-reversal:{task_id}", original_ledger.get("id"),
                    reservation.get("mode"), free_delta, balance_delta, amount_fen,
                    balance_after, "旧版任务缺少可验证决策授权，自动冲正",
                ),
            )
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        print(f"[DEVELOPER BILLING ERROR] reverse {task_id}: {exc}")
        return False
    finally:
        conn.close()


def _daily_quota_retry_after(now):
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


def _reserve_task_daily_quota(actor, task_id):
    """Atomically bind one daily-quota reservation to a durable task row."""
    user_id = int((actor or {}).get("user_id") or 0)
    if user_id <= 0:
        return "开发者账号数据异常", "storage_unavailable", None
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id, user_id, status, daily_quota_reserved
                FROM developer_detection_tasks
                WHERE task_id = %s
                LIMIT 1
                FOR UPDATE
                """,
                (task_id,),
            )
            task = cursor.fetchone()
            if not task or int(task.get("user_id") or 0) != user_id or task.get("status") != "preparing":
                conn.rollback()
                return "任务状态已变化，无法预占调用额度", "task_state_conflict", None
            if int(task.get("daily_quota_reserved") or 0) == 1:
                conn.commit()
                return None, None, None

            cursor.execute(
                """
                SELECT u.Userid, q.daily_limit, NOW() AS quota_now
                FROM `user` u
                LEFT JOIN developer_api_account_quotas q ON q.user_id = u.Userid
                WHERE u.Userid = %s
                LIMIT 1
                FOR UPDATE
                """,
                (user_id,),
            )
            account = cursor.fetchone()
            if not account:
                conn.rollback()
                return "开发者账号不存在", "storage_unavailable", None
            now = account.get("quota_now")
            if not isinstance(now, datetime):
                now = datetime.now()
            day_bucket = now.date()
            cursor.execute(
                """
                SELECT day_bucket, daily_count
                FROM developer_api_account_quota_usage
                WHERE user_id = %s
                FOR UPDATE
                """,
                (user_id,),
            )
            usage = cursor.fetchone() or {}
            daily_count = (
                int(usage.get("daily_count") or 0)
                if usage.get("day_bucket") == day_bucket
                else 0
            )
            daily_limit = account.get("daily_limit")
            if daily_limit is not None and daily_count >= int(daily_limit):
                conn.rollback()
                return "该账号已达到每日调用上限", "daily_limit_exceeded", _daily_quota_retry_after(now)
            cursor.execute(
                """
                INSERT INTO developer_api_account_quota_usage
                    (user_id, day_bucket, daily_count, minute_bucket, minute_count)
                VALUES (%s, %s, %s, %s, 0)
                ON DUPLICATE KEY UPDATE
                    day_bucket = VALUES(day_bucket),
                    daily_count = VALUES(daily_count)
                """,
                (user_id, day_bucket, daily_count + 1, now.replace(second=0, microsecond=0)),
            )
            cursor.execute(
                """
                UPDATE developer_detection_tasks
                SET daily_quota_reserved = 1, daily_quota_day = %s
                WHERE task_id = %s AND status = 'preparing' AND daily_quota_reserved = 0
                """,
                (day_bucket, task_id),
            )
            if cursor.rowcount != 1:
                raise TaskRecoveryError(f"task {task_id} quota reservation lost its task row")
        conn.commit()
        return None, None, None
    except TaskRecoveryError:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        print(f"[DEVELOPER DAILY QUOTA ERROR] reserve {task_id}: {exc}")
        return "API Key 配额服务暂不可用", "storage_unavailable", None
    finally:
        if conn:
            conn.close()


def _release_task_daily_quota_in_cursor(cursor, task):
    if int((task or {}).get("daily_quota_reserved") or 0) != 1:
        return False
    user_id = int((task or {}).get("user_id") or 0)
    reservation_day = (task or {}).get("daily_quota_day")
    cursor.execute(
        """
        SELECT day_bucket, daily_count
        FROM developer_api_account_quota_usage
        WHERE user_id = %s
        FOR UPDATE
        """,
        (user_id,),
    )
    usage = cursor.fetchone() or {}
    if usage.get("day_bucket") == reservation_day and int(usage.get("daily_count") or 0) > 0:
        cursor.execute(
            """
            UPDATE developer_api_account_quota_usage
            SET daily_count = daily_count - 1
            WHERE user_id = %s AND day_bucket = %s AND daily_count > 0
            """,
            (user_id, reservation_day),
        )
        if cursor.rowcount != 1:
            raise TaskRecoveryError(f"task {task['task_id']} daily quota is inconsistent")
    cursor.execute(
        """
        UPDATE developer_detection_tasks
        SET daily_quota_reserved = 0
        WHERE task_id = %s AND daily_quota_reserved = 1
        """,
        (task["task_id"],),
    )
    if cursor.rowcount != 1:
        raise TaskRecoveryError(f"task {task['task_id']} daily quota marker changed")
    return True


def _consume_task_daily_quota_in_cursor(cursor, task):
    """Finalize a successful call without decrementing its daily usage count."""
    if int((task or {}).get("daily_quota_reserved") or 0) != 1:
        return False
    cursor.execute(
        """
        UPDATE developer_detection_tasks
        SET daily_quota_reserved = 0
        WHERE task_id = %s AND daily_quota_reserved = 1
        """,
        (task["task_id"],),
    )
    if cursor.rowcount != 1:
        raise TaskRecoveryError(f"task {task['task_id']} daily quota marker changed")
    return True


def _release_task_daily_quota(task_id):
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id, user_id, daily_quota_reserved, daily_quota_day
                FROM developer_detection_tasks
                WHERE task_id = %s
                LIMIT 1
                FOR UPDATE
                """,
                (task_id,),
            )
            task = cursor.fetchone()
            if not task:
                conn.rollback()
                return False
            _release_task_daily_quota_in_cursor(cursor, task)
        conn.commit()
        return True
    except Exception as exc:
        if conn:
            conn.rollback()
        print(f"[DEVELOPER DAILY QUOTA ERROR] release {task_id}: {exc}")
        return False
    finally:
        if conn:
            conn.close()


def _fail_task_and_release(
    task_id,
    message,
    *,
    lease_owner=None,
    require_expired=False,
    require_exhausted=False,
    require_stale_preparing=False,
    require_rejected=False,
):
    """Atomically terminalize an active task and release its billing reservation."""
    if require_expired:
        lease_condition = (
            " AND status IN ('queued', 'running')"
            " AND lease_expires_at IS NOT NULL AND lease_expires_at <= NOW(6)"
        )
        lease_params = ()
    elif require_exhausted:
        lease_condition = " AND status = 'queued' AND attempt_count >= %s"
        lease_params = (DEVELOPER_TASK_MAX_ATTEMPTS,)
    elif require_stale_preparing:
        lease_condition = (
            " AND status = 'preparing'"
            " AND updated_at <= DATE_SUB(NOW(6), INTERVAL %s SECOND)"
        )
        lease_params = (DEVELOPER_TASK_PREPARING_TIMEOUT_SECONDS,)
    elif require_rejected:
        lease_condition = (
            " AND status = 'rejected'"
            " AND updated_at <= DATE_SUB(NOW(6), INTERVAL 60 SECOND)"
        )
        lease_params = ()
    elif lease_owner:
        lease_condition = (
            " AND status = 'running'"
            " AND lease_owner = %s AND lease_expires_at > NOW(6)"
        )
        lease_params = (lease_owner,)
    else:
        lease_condition = " AND status IN ('preparing', 'queued', 'running')"
        lease_params = ()

    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT task_id, user_id, status, lease_owner, lease_expires_at,
                       daily_quota_reserved, daily_quota_day
                FROM developer_detection_tasks
                WHERE task_id = %s
                  {lease_condition}
                FOR UPDATE
                """,
                (task_id, *lease_params),
            )
            task = cursor.fetchone()
            if not task:
                conn.rollback()
                return False

            cursor.execute(
                """
                SELECT user_id, source, amount_fen, status
                FROM developer_billing_reservations
                WHERE task_id = %s
                FOR UPDATE
                """,
                (task_id,),
            )
            reservation = cursor.fetchone()
            if reservation and int(reservation.get("user_id") or 0) != int(task.get("user_id") or 0):
                raise TaskRecoveryError(f"task {task_id} reservation owner mismatch")

            if reservation and reservation.get("status") == "reserved":
                user_id = reservation["user_id"]
                source = reservation.get("source")
                amount_fen = int(reservation.get("amount_fen") or 0)
                cursor.execute(
                    """
                    SELECT free_reserved, balance_reserved_fen
                    FROM developer_accounts
                    WHERE user_id = %s
                    FOR UPDATE
                    """,
                    (user_id,),
                )
                account = cursor.fetchone()
                if not account:
                    raise TaskRecoveryError(f"task {task_id} developer account is missing")

                if source == "free":
                    cursor.execute(
                        """
                        UPDATE developer_accounts
                        SET free_reserved = free_reserved - 1
                        WHERE user_id = %s AND free_reserved >= 1
                        """,
                        (user_id,),
                    )
                elif source == "balance":
                    if amount_fen <= 0:
                        raise TaskRecoveryError(f"task {task_id} has an invalid balance reservation")
                    cursor.execute(
                        """
                        UPDATE developer_accounts
                        SET balance_reserved_fen = balance_reserved_fen - %s
                        WHERE user_id = %s AND balance_reserved_fen >= %s
                        """,
                        (amount_fen, user_id, amount_fen),
                    )
                else:
                    raise TaskRecoveryError(f"task {task_id} has an unknown reservation source")
                if cursor.rowcount != 1:
                    raise TaskRecoveryError(f"task {task_id} reserved balance is inconsistent")

                cursor.execute(
                    """
                    UPDATE developer_billing_reservations
                    SET status = 'released', released_at = NOW()
                    WHERE task_id = %s AND status = 'reserved'
                    """,
                    (task_id,),
                )
                if cursor.rowcount != 1:
                    raise TaskRecoveryError(f"task {task_id} reservation changed during recovery")
            elif reservation and reservation.get("status") == "settled":
                raise TaskRecoveryError(f"task {task_id} is unfinished but its reservation is settled")
            elif reservation and reservation.get("status") != "released":
                raise TaskRecoveryError(f"task {task_id} has an invalid reservation status")

            _release_task_daily_quota_in_cursor(cursor, task)

            terminal_status_sql = "'rejected'" if require_rejected else "'failed'"
            cursor.execute(
                f"""
                UPDATE developer_detection_tasks
                SET status = {terminal_status_sql}, error_message = %s, completed_at = NOW(),
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE task_id = %s
                  {lease_condition}
                """,
                (str(message or "任务执行失败")[:500], task_id, *lease_params),
            )
            if cursor.rowcount != 1:
                raise TaskRecoveryError(f"task {task_id} lease changed during terminalization")
        conn.commit()
        return True
    except TaskRecoveryError:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise TaskRecoveryError(f"failed to terminalize task {task_id}: {exc}") from exc
    finally:
        if conn:
            conn.close()


def _terminalize_anomalous_reservation(task_id):
    """Stop an active task whose reservation can no longer authorize execution."""
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id, user_id, status
                FROM developer_detection_tasks
                WHERE task_id = %s
                  AND status IN ('queued', 'running')
                FOR UPDATE
                """,
                (task_id,),
            )
            task = cursor.fetchone()
            if not task:
                conn.rollback()
                return False
            cursor.execute(
                """
                SELECT user_id, status
                FROM developer_billing_reservations
                WHERE task_id = %s
                FOR UPDATE
                """,
                (task_id,),
            )
            reservation = cursor.fetchone()
            reservation_status = str((reservation or {}).get("status") or "missing")
            same_owner = not reservation or int(reservation.get("user_id") or 0) == int(
                task.get("user_id") or 0
            )
            if reservation_status == "reserved" and same_owner:
                conn.rollback()
                return False

            if reservation_status == "settled":
                message = "任务尚未完成但额度已结算，系统已终止执行并标记为人工对账"
            elif reservation_status == "released":
                message = "任务预留额度已释放，系统已终止未获授权的执行"
            elif reservation_status == "missing":
                message = "任务缺少计费预留记录，系统已终止未获授权的执行"
            elif not same_owner:
                message = "任务与计费预留归属不一致，系统已终止执行并标记为人工对账"
            else:
                message = f"任务计费预留状态异常（{reservation_status}），系统已终止执行并标记为人工对账"

            cursor.execute(
                """
                UPDATE developer_detection_tasks
                SET status = 'failed', error_message = %s, completed_at = NOW(),
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE task_id = %s
                  AND status IN ('queued', 'running')
                """,
                (message[:500], task_id),
            )
            if cursor.rowcount != 1:
                raise TaskRecoveryError(
                    f"task {task_id} changed during reservation anomaly reconciliation"
                )
        conn.commit()
        _update_job_cache(
            task_id,
            {"status": "failed", "error": message, "progress": 100},
        )
        print(
            f"[DEVELOPER BILLING RECONCILIATION] terminalized {task_id}: "
            f"reservation={reservation_status}, owner_match={same_owner}"
        )
        return True
    except TaskRecoveryError:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise TaskRecoveryError(
            f"failed to reconcile anomalous reservation for task {task_id}: {exc}"
        ) from exc
    finally:
        if conn:
            conn.close()


def _expire_task_lease(task_id):
    """Fail one unrecoverable expired task and release its reservation."""
    message = "任务执行租约已过期且无法恢复，系统已终止任务并释放预留额度"
    recovered = _fail_task_and_release(task_id, message, require_expired=True)
    if not recovered:
        return False
    try:
        _update_job_cache(
            task_id,
            {"status": "failed", "error": message, "progress": 100},
        )
    except Exception as exc:
        print(f"[DEVELOPER TASK RECOVERY ERROR] recovered {task_id}, but job cache update failed: {exc}")
    return True


def _requeue_expired_task(task_id):
    updated = excute_sql(
        """
        UPDATE developer_detection_tasks
        SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
            last_heartbeat_at = NULL, error_message = NULL
        WHERE task_id = %s
          AND status IN ('queued', 'running')
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at <= NOW(6)
          AND spool_path IS NOT NULL
          AND attempt_count < %s
        """,
        (task_id, DEVELOPER_TASK_MAX_ATTEMPTS),
        fetch=False,
    )
    if updated is None:
        raise TaskRecoveryError(f"failed to requeue expired task {task_id}")
    if updated != 1:
        return False
    try:
        _update_job_cache(
            task_id,
            {"status": "queued", "progress": 0, "summary": "任务已恢复，等待重新执行", "error": ""},
        )
    except Exception as exc:
        print(f"[DEVELOPER TASK RECOVERY ERROR] requeued {task_id}, but job cache update failed: {exc}")
    return True


def _reservation_status_strict(task_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT status
                FROM developer_billing_reservations
                WHERE task_id = %s
                LIMIT 1
                """,
                (task_id,),
            )
            reservation = cursor.fetchone()
    except Exception as exc:
        raise TaskRecoveryError(f"failed to verify reservation {task_id}: {exc}") from exc
    finally:
        if conn:
            conn.close()
    if not reservation:
        raise TaskRecoveryError(f"successful task {task_id} has no billing reservation")
    return reservation.get("status")


def _task_billing_outcome_for_id(task_id, row=None):
    task_row = row
    if not isinstance(task_row, dict):
        rows = excute_sql(
            """
            SELECT status, result_json
            FROM developer_detection_tasks
            WHERE task_id = %s
            LIMIT 1
            """,
            (task_id,),
        )
        if rows is None or not rows or rows[0].get("status") != "success":
            raise TaskRecoveryError(f"successful task {task_id} result is unavailable")
        task_row = rows[0]
    return _task_billing_outcome(task_row)


def _reconcile_success_reservation(task_id, row=None):
    decision_status, billable, expected_status = _task_billing_outcome_for_id(task_id, row)
    if billable:
        changed_now = _settle_billing(task_id)
    else:
        changed_now = _release_billing(
            task_id,
            "检测仅形成复核态结论，不消耗调用额度",
            audit_entry_type="detection_review_only",
        )
        if not changed_now and _reservation_status_strict(task_id) == "settled":
            changed_now = _reverse_settled_review_only(task_id)
    if not changed_now:
        status = _reservation_status_strict(task_id)
        if status != expected_status:
            raise TaskRecoveryError(
                f"successful task {task_id} billing settlement remains {status or 'unknown'}"
            )
    try:
        _update_job_cache(
            task_id,
            {
                "status": "success",
                "progress": 100,
                "summary": (
                    "检测完成，需要人工复核；本次未扣除调用额度"
                    if decision_status == "review_only"
                    else "检测完成，计费对账已完成"
                ),
            },
        )
    except Exception as exc:
        print(f"[DEVELOPER TASK RECOVERY ERROR] settled {task_id}, but job cache update failed: {exc}")
    return changed_now


def _reconcile_expired_tasks(limit=None):
    if not _ensure_developer_platform_tables():
        raise TaskRecoveryError("developer platform tables are unavailable")
    batch_size = max(1, min(int(limit or DEVELOPER_TASK_RECONCILE_BATCH_SIZE), 200))
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id, status, spool_path, attempt_count,
                       (lease_expires_at IS NOT NULL AND lease_expires_at <= NOW(6)) AS lease_expired
                FROM developer_detection_tasks
                WHERE (
                    status IN ('queued', 'running')
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= NOW(6)
                  ) OR (
                    status = 'queued' AND attempt_count >= %s
                  ) OR (
                    status = 'preparing'
                    AND updated_at <= DATE_SUB(NOW(6), INTERVAL %s SECOND)
                  ) OR (
                    status = 'rejected'
                    AND updated_at <= DATE_SUB(NOW(6), INTERVAL 60 SECOND)
                  )
                ORDER BY updated_at ASC
                LIMIT %s
                """,
                (
                    DEVELOPER_TASK_MAX_ATTEMPTS,
                    DEVELOPER_TASK_PREPARING_TIMEOUT_SECONDS,
                    batch_size,
                ),
            )
            task_rows = [row for row in cursor.fetchall() if row.get("task_id")]
            cursor.execute(
                """
                SELECT task.task_id
                FROM developer_detection_tasks AS task
                INNER JOIN developer_billing_reservations AS reservation
                    ON reservation.task_id = task.task_id
                WHERE task.status = 'success'
                  AND (
                    reservation.status = 'reserved'
                    OR (
                      reservation.status = 'released'
                      AND task.daily_quota_reserved = 1
                    )
                    OR (
                      reservation.status = 'settled'
                      AND JSON_VALID(task.result_json) = 1
                      AND (
                        COALESCE(
                          JSON_UNQUOTE(JSON_EXTRACT(task.result_json, '$.decisionStatus')),
                          JSON_UNQUOTE(JSON_EXTRACT(task.result_json, '$.result.decisionStatus'))
                        ) IS NULL
                        OR COALESCE(
                          JSON_UNQUOTE(JSON_EXTRACT(task.result_json, '$.decisionStatus')),
                          JSON_UNQUOTE(JSON_EXTRACT(task.result_json, '$.result.decisionStatus'))
                        ) <> 'verdict'
                      )
                    )
                  )
                ORDER BY task.completed_at ASC
                LIMIT %s
                """,
                (batch_size,),
            )
            success_task_ids = [row.get("task_id") for row in cursor.fetchall() if row.get("task_id")]
            cursor.execute(
                """
                SELECT task.task_id
                FROM developer_detection_tasks AS task
                INNER JOIN developer_billing_reservations AS reservation
                    ON reservation.task_id = task.task_id
                WHERE task.status = 'success'
                  AND reservation.status = 'settled'
                  AND JSON_VALID(task.result_json) = 0
                ORDER BY task.completed_at ASC
                LIMIT %s
                """,
                (batch_size,),
            )
            corrupt_result_task_ids = [
                row.get("task_id") for row in cursor.fetchall() if row.get("task_id")
            ]
            cursor.execute(
                """
                SELECT task.task_id
                FROM developer_detection_tasks AS task
                LEFT JOIN developer_billing_reservations AS reservation
                    ON reservation.task_id = task.task_id
                WHERE task.status IN ('queued', 'running')
                  AND (
                    reservation.task_id IS NULL
                    OR reservation.status <> 'reserved'
                    OR reservation.user_id <> task.user_id
                  )
                ORDER BY task.updated_at ASC
                LIMIT %s
                """,
                (batch_size,),
            )
            anomalous_task_ids = [
                row.get("task_id") for row in cursor.fetchall() if row.get("task_id")
            ]
    except Exception as exc:
        raise TaskRecoveryError(f"failed to scan expired tasks: {exc}") from exc
    finally:
        if conn:
            conn.close()

    recovered = 0
    reconcile_errors = [
        f"settled task {task_id} has corrupt result JSON and requires manual reconciliation"
        for task_id in corrupt_result_task_ids
    ]
    for task in task_rows:
        task_id = task["task_id"]
        try:
            status = task.get("status")
            attempts = int(task.get("attempt_count") or 0)
            if task.get("spool_path") and bool(task.get("lease_expired")) and attempts < DEVELOPER_TASK_MAX_ATTEMPTS:
                changed = _requeue_expired_task(task_id)
            elif status == "preparing":
                changed = _fail_task_and_release(
                    task_id,
                    "任务准备超时，系统已释放预留额度",
                    require_stale_preparing=True,
                )
            elif status == "queued" and attempts >= DEVELOPER_TASK_MAX_ATTEMPTS:
                changed = _fail_task_and_release(
                    task_id,
                    "任务已达到最大重试次数，系统已释放预留额度",
                    require_exhausted=True,
                )
            elif status == "rejected":
                # A connection can be lost after the reservation commit but
                # before the client receives the response. Reconcile this
                # terminal marker without charging the account permanently.
                changed = _fail_task_and_release(
                    task_id,
                    "已拒绝任务的额度预留已完成释放",
                    require_rejected=True,
                )
            else:
                changed = _expire_task_lease(task_id)
            if changed:
                recovered += 1
        except TaskRecoveryError as exc:
            print(f"[DEVELOPER TASK RECOVERY ERROR] isolated expired task {task_id}: {exc}")
            reconcile_errors.append(str(exc))
    for task_id in success_task_ids:
        try:
            if _reconcile_success_reservation(task_id):
                recovered += 1
        except TaskRecoveryError as exc:
            print(f"[DEVELOPER TASK RECOVERY ERROR] isolated successful task {task_id}: {exc}")
            reconcile_errors.append(str(exc))
    for task_id in anomalous_task_ids:
        try:
            if _terminalize_anomalous_reservation(task_id):
                recovered += 1
        except TaskRecoveryError as exc:
            print(f"[DEVELOPER TASK RECOVERY ERROR] isolated billing anomaly {task_id}: {exc}")
            reconcile_errors.append(str(exc))
    if reconcile_errors:
        raise TaskRecoveryError(
            f"{len(reconcile_errors)} billing reconciliation task(s) require attention: "
            + "; ".join(reconcile_errors[:3])
        )
    return recovered


def _maybe_reconcile_expired_tasks(force=False):
    global _TASK_RECONCILE_LAST_MONOTONIC
    now = time.monotonic()
    interval = DEVELOPER_TASK_RECONCILE_INTERVAL_SECONDS
    if not force and now - _TASK_RECONCILE_LAST_MONOTONIC < interval:
        return 0
    with _TASK_RECONCILE_LOCK:
        now = time.monotonic()
        if not force and now - _TASK_RECONCILE_LAST_MONOTONIC < interval:
            return 0
        try:
            recovered = _reconcile_expired_tasks()
        except TaskRecoveryError as exc:
            # This is periodic maintenance for all tenants. A stale or
            # temporarily unreadable task must not become a dependency of an
            # unrelated customer's request.
            print(f"[DEVELOPER TASK RECOVERY ERROR] background reconciliation deferred: {exc}")
            recovered = 0
        _TASK_RECONCILE_LAST_MONOTONIC = time.monotonic()
        return recovered


def _task_recovery_error_response(exc):
    print(f"[DEVELOPER TASK RECOVERY ERROR] {exc}")
    return _error(
        "任务恢复对账暂时不可用，请稍后重试；系统未扣除新的调用额度",
        503,
        "task_recovery_unavailable",
    )


def _task_settlement_error(task_id, row=None):
    """Reconcile a successful task and block paid artifacts until settled."""
    try:
        _, _, expected_status = _task_billing_outcome_for_id(task_id, row)
        reservation_status = _reservation_status_strict(task_id)
        if reservation_status == "reserved":
            _reconcile_success_reservation(task_id, row)
            reservation_status = _reservation_status_strict(task_id)
        if reservation_status != expected_status:
            raise TaskRecoveryError(
                f"successful task {task_id} has invalid billing status "
                f"{reservation_status or 'unknown'}"
            )
    except TaskRecoveryError as exc:
        response, status_code = _task_recovery_error_response(exc)
        response.headers["Retry-After"] = "5"
        return response, status_code
    return None


def _reservation_payload(task_id):
    rows = excute_sql(
        """
        SELECT source, amount_fen, status, created_at, settled_at, released_at
        FROM developer_billing_reservations
        WHERE task_id = %s
        LIMIT 1
        """,
        (task_id,),
    ) or []
    if not rows:
        return None
    row = rows[0]
    return {
        "source": row.get("source"),
        "amountFen": int(row.get("amount_fen") or 0),
        "amountCny": f"{int(row.get('amount_fen') or 0) / 100:.2f}",
        "status": row.get("status"),
    }


def _task_row_for_user(task_id, user_id, account_uuid):
    if not _ensure_developer_platform_tables():
        return None
    rows = excute_sql(
        """
        SELECT task_id, user_id, account_uuid, key_id, mode, filename, request_sha256, idempotency_key,
               status, result_item_id, result_json, error_message, created_at, updated_at, completed_at
        FROM developer_detection_tasks
        WHERE task_id = %s AND user_id = %s AND account_uuid = %s
        LIMIT 1
        """,
        (task_id, user_id, account_uuid),
    )
    return rows[0] if rows else None


def _idempotent_task(user_id, account_uuid, idempotency_key):
    if not idempotency_key:
        return None
    rows = excute_sql(
        """
        SELECT task_id, user_id, account_uuid, key_id, mode, filename, request_sha256, idempotency_key,
               status, result_item_id, result_json, error_message, created_at, updated_at, completed_at
        FROM developer_detection_tasks
        WHERE user_id = %s AND account_uuid = %s AND idempotency_key = %s
        LIMIT 1
        """,
        (user_id, account_uuid, idempotency_key),
    )
    return rows[0] if rows else None


def _public_result_payload(payload, mode):
    if not isinstance(payload, dict):
        return None
    public_payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    if mode == "swarm" and isinstance(public_payload.get("result"), dict):
        public_payload["result"] = detection._public_swarm_result(public_payload["result"])
    result = public_payload.get("result")
    if isinstance(result, dict):
        result["final_label"] = binary_final_label(
            result.get("final_label"),
            result.get("probability", result.get("detector_probability")),
        )
        explicit_status = result.get("decisionStatus")
        explicit_billable = result.get("billable")
        review_only = not (
            explicit_status == "verdict" and explicit_billable is True
        )
        decision_status = "review_only" if review_only else "verdict"
        result["decisionStatus"] = decision_status
        result["billable"] = not review_only
        public_payload["decisionStatus"] = decision_status
        public_payload["billable"] = not review_only
        detection._suppress_review_only_scores(result)
    return public_payload


def _task_billing_outcome(row):
    """Return the persisted decision outcome and expected reservation status."""
    payload = _stored_task_result(row or {})
    if not isinstance(payload, dict):
        return "review_only", False, "released"
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    explicit = payload.get("decisionStatus") or result.get("decisionStatus")
    billable = payload.get("billable")
    if billable is None:
        billable = result.get("billable")
    review_only = not (
        explicit == "verdict" and billable is True
    )
    return (
        "review_only" if review_only else "verdict",
        not review_only,
        "released" if review_only else "settled",
    )


def _stored_task_result(row):
    raw = row.get("result_json")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _task_business_rows(task, user_info, *, item_id=None):
    execution_filename = task.get("execution_filename") or _task_execution_filename(
        task.get("task_id"), task.get("filename")
    )
    phone = str(user_info.get("phone") or "").strip()
    openid = str(user_info.get("openid") or "").strip()
    account_uuid = user_info.get("account_uuid")

    def query_rows(owner_where, owner_params):
        item_condition = ""
        params = tuple(owner_params)
        if item_id not in (None, ""):
            item_condition = "itemid = %s AND "
            params = (item_id, *params)
        return excute_detection_sql(
            f"""
            SELECT *
            FROM data
            WHERE {item_condition}({owner_where})
              AND RIGHT(filename, CHAR_LENGTH(%s)) = %s
            ORDER BY itemid ASC
            LIMIT 2
            """,
            (*params, execution_filename, execution_filename),
        )

    owner_where, owner_params = detection._detection_owner_where(
        user_info.get("Userid"), phone, openid, account_uuid
    )
    rows = query_rows(owner_where, owner_params)
    if rows is None:
        raise TaskRecoveryError(f"failed to inspect business result for {task.get('task_id')}")
    if len(rows) > 1:
        raise TaskRecoveryError(
            f"task {task.get('task_id')} has multiple persisted business results"
        )
    return rows


def _recovered_business_payload(record):
    try:
        fake_probability = max(0.0, min(1.0, float(record.get("fake") or 0) / 100.0))
    except (TypeError, ValueError):
        fake_probability = 0.0
    try:
        detector_probability = max(
            0.0,
            min(1.0, float(record.get("detector_probability"))),
        )
    except (TypeError, ValueError):
        detector_probability = fake_probability
    item_id = record.get("itemid")
    metadata = detection._metadata_for_item(item_id) if item_id else {}
    explanation = str(record.get("explantation") or "").strip()
    split_explanation, split_issues = detection._split_reasoning_sections(explanation)
    final_label = binary_final_label(record.get("aigc"), fake_probability)
    result = {
        "itemid": item_id,
        "final_label": final_label,
        "probability": fake_probability,
        "detector_probability": detector_probability,
        "p_visual": None,
        "p_metadata": None,
        "confidence": record.get("clarity") or "",
        "explanation": split_explanation or explanation,
        "agent_reasoning": "",
        "llm_used": False,
        "visual_issues": detection._normalize_visual_issues(
            split_issues,
            final_label=final_label,
        ),
        "image_url": f"/api/media/image/{item_id}" if item_id else "",
        "filename": record.get("filename") or "",
        "file_size": record.get("file_size") or "",
        "img_format": record.get("img_format") or "",
        "resolution": record.get("resolution") or "",
        "all_metadata": metadata,
        "capture_evidence": detection._capture_evidence_for_metadata(metadata),
        "feedback": record.get("feedback"),
    }
    visible_watermark = detection._runtime_visible_watermark_for_item(item_id)
    if isinstance(visible_watermark, dict):
        result["visibleWatermark"] = visible_watermark
    return {"status": "success", "result": result}


def _normalize_task_result_filename(payload, task):
    if not isinstance(payload, dict):
        return payload
    normalized = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    result = normalized.get("result")
    if isinstance(result, dict):
        result["filename"] = task.get("filename") or result.get("filename") or ""
    return normalized


def _record_task_effect(task, user_info, payload):
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return False
    result = payload.get("result") or {}
    item_id = result.get("itemid")
    if item_id in (None, ""):
        raise TaskRecoveryError(f"task {task.get('task_id')} succeeded without a business item id")
    rows = _task_business_rows(task, user_info, item_id=item_id)
    if not rows:
        raise TaskRecoveryError(
            f"task {task.get('task_id')} business result cannot be verified by its idempotency key"
        )
    account_uuid = str(user_info.get("account_uuid") or "").strip()
    visibility_updated = excute_detection_sql(
        """
        UPDATE data
        SET developer_task_id = %s
        WHERE itemid = %s AND owner_account_uuid = %s
          AND (developer_task_id IS NULL OR developer_task_id = %s)
        """,
        (task["task_id"], item_id, account_uuid, task["task_id"]),
        fetch=False,
    )
    if visibility_updated != 1:
        visibility_rows = excute_detection_sql(
            """
            SELECT itemid FROM data
            WHERE itemid = %s AND owner_account_uuid = %s AND developer_task_id = %s
            LIMIT 1
            """,
            (item_id, account_uuid, task["task_id"]),
        )
        if not visibility_rows:
            raise TaskRecoveryError(
                f"task {task.get('task_id')} result visibility could not be linked"
            )
    effect_json = json.dumps(payload, ensure_ascii=False, default=str)
    updated = excute_sql(
        """
        UPDATE developer_detection_tasks
        SET effect_item_id = %s,
            effect_result_json = COALESCE(effect_result_json, %s)
        WHERE task_id = %s
          AND status IN ('queued', 'running')
          AND (effect_item_id IS NULL OR effect_item_id = %s)
        """,
        (item_id, effect_json, task["task_id"], item_id),
        fetch=False,
    )
    if updated is None:
        raise TaskRecoveryError(f"failed to journal business result for {task.get('task_id')}")
    if updated == 1:
        return True
    current = excute_sql(
        """
        SELECT status, effect_item_id
        FROM developer_detection_tasks
        WHERE task_id = %s
        LIMIT 1
        """,
        (task["task_id"],),
    )
    if current and str(current[0].get("effect_item_id")) == str(item_id):
        return False
    raise TaskRecoveryError(
        f"task {task.get('task_id')} changed before its business result was journaled"
    )


def _recover_task_effect(task, user_info):
    rows = excute_sql(
        """
        SELECT status, effect_item_id, effect_result_json
        FROM developer_detection_tasks
        WHERE task_id = %s
        LIMIT 1
        """,
        (task["task_id"],),
    )
    if rows is None or not rows:
        raise TaskRecoveryError(f"task {task.get('task_id')} disappeared during recovery")
    effect_item_id = rows[0].get("effect_item_id")
    business_rows = _task_business_rows(task, user_info, item_id=effect_item_id)
    if not business_rows:
        if effect_item_id not in (None, ""):
            raise TaskRecoveryError(
                f"task {task.get('task_id')} journal references a missing business result"
            )
        return None
    record = business_rows[0]
    if effect_item_id not in (None, "") and str(record.get("itemid")) != str(effect_item_id):
        raise TaskRecoveryError(f"task {task.get('task_id')} business result journal is inconsistent")
    if (
        task.get("mode") == "swarm"
        and effect_item_id in (None, "")
        and not str(record.get("explantation") or "").startswith("Swarm 专家会诊完成：")
    ):
        raise TaskRecoveryError(
            f"task {task.get('task_id')} has an incomplete Swarm primary result; "
            "automatic rerun is blocked to prevent duplicate history"
        )

    payload = None
    raw_payload = rows[0].get("effect_result_json")
    if raw_payload:
        try:
            payload = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
        except (TypeError, ValueError):
            payload = None
        payload_item_id = ((payload or {}).get("result") or {}).get("itemid")
        if not isinstance(payload, dict) or payload.get("status") != "success" or str(
            payload_item_id
        ) != str(record.get("itemid")):
            raise TaskRecoveryError(
                f"task {task.get('task_id')} stored business result is inconsistent"
            )
    if payload is None:
        raise TaskRecoveryError(
            f"task {task.get('task_id')} has a business result without a complete response journal; "
            "automatic recovery is blocked to avoid publishing an ambiguous verdict"
        )
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    decision_status = payload.get("decisionStatus") or result.get("decisionStatus")
    billable = payload.get("billable")
    if billable is None:
        billable = result.get("billable")
    if (
        decision_status not in {"verdict", "review_only"}
        or not isinstance(billable, bool)
        or billable is not (decision_status == "verdict")
    ):
        raise TaskRecoveryError(
            f"task {task.get('task_id')} response journal has no explicit decision outcome"
        )
    return payload


def _task_payload(row):
    job = admin_state.get_detection_job(row["task_id"])
    public_job = detection._public_detection_job(job) if job else None
    # SQL is the authoritative task state; the JSON job cache is progress-only.
    internal_status = row.get("status") or "queued"
    status = "queued" if internal_status == "preparing" else internal_status
    task_id = row["task_id"]
    billing = _reservation_payload(task_id)
    decision_status, billable, expected_billing_status = _task_billing_outcome(row)
    settlement_pending = (
        status == "success"
        and (billing or {}).get("status") != expected_billing_status
    )
    if settlement_pending:
        status = "settlement_pending"
    progress = int(
        (public_job or {}).get("progress")
        or (99 if settlement_pending else 100 if status in {"success", "failed", "rejected"} else 0)
    )
    result = _stored_task_result(row) if status == "success" else None
    error_message = row.get("error_message") or ((public_job or {}).get("error") if status in {"failed", "rejected"} else "") or ""
    public_result = result.get("result") if isinstance(result, dict) and result.get("status") == "success" else None
    media_link = f"/api/openapi/v1/image-detections/{task_id}/media"
    if isinstance(public_result, dict) and row.get("result_item_id"):
        public_result = json.loads(json.dumps(public_result, ensure_ascii=False, default=str))
        public_result["image_url"] = media_link
        if "imageUrl" in public_result:
            public_result["imageUrl"] = media_link
    return {
        "id": task_id,
        "object": "image_detection",
        "status": status,
        "mode": row.get("mode"),
        "filename": row.get("filename"),
        "progress": max(0, min(progress, 100)),
        "summary": (
            "检测已完成，正在完成计费对账"
            if settlement_pending
            else (public_job or {}).get("summary") or ""
        ),
        "createdAt": format_createtime(row.get("created_at")),
        "updatedAt": format_createtime(row.get("updated_at")),
        "completedAt": format_createtime(row.get("completed_at")),
        "result": public_result,
        "decisionStatus": decision_status if status == "success" else None,
        "billable": billable if status == "success" else None,
        "error": {"code": "detection_failed", "message": error_message} if status in {"failed", "rejected"} else None,
        "billing": billing,
        "links": {
            "self": f"/api/openapi/v1/image-detections/{task_id}",
            "report": f"/api/openapi/v1/image-detections/{task_id}/report",
            "media": media_link,
        },
    }


def _token_usage(payload):
    prompt = completion = total = 0

    def visit(value):
        nonlocal prompt, completion, total
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).replace("_", "").lower()
                if normalized == "prompttokens":
                    prompt += int(item or 0)
                elif normalized == "completiontokens":
                    completion += int(item or 0)
                elif normalized == "totaltokens":
                    total += int(item or 0)
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    try:
        visit(payload)
    except (TypeError, ValueError):
        return 0, 0, 0
    return prompt, completion, total or prompt + completion


def _claim_task_lease(task_id, lease_owner):
    updated = excute_sql(
        """
        UPDATE developer_detection_tasks
        SET status = 'running', lease_owner = %s,
            lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND),
            last_heartbeat_at = NOW(6), attempt_count = attempt_count + 1,
            next_attempt_at = NULL
        WHERE task_id = %s
          AND status = 'queued'
          AND (next_attempt_at IS NULL OR next_attempt_at <= NOW(6))
          AND lease_owner IS NULL
          AND lease_expires_at IS NULL
          AND spool_path IS NOT NULL
          AND attempt_count < %s
        """,
        (
            lease_owner,
            DEVELOPER_TASK_LEASE_SECONDS,
            task_id,
            DEVELOPER_TASK_MAX_ATTEMPTS,
        ),
        fetch=False,
    )
    if updated is None:
        raise TaskRecoveryError(f"failed to claim task lease for {task_id}")
    return updated == 1


def _claim_next_task(worker_instance):
    if not _ensure_developer_platform_tables():
        raise TaskRecoveryError("developer platform tables are unavailable")
    lease_owner = f"{str(worker_instance)[:24]}-{uuid.uuid4().hex}"[:64]
    updated = excute_sql(
        """
        UPDATE developer_detection_tasks
        SET status = 'running', lease_owner = %s,
            lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND),
            last_heartbeat_at = NOW(6), attempt_count = attempt_count + 1,
            next_attempt_at = NULL
        WHERE status = 'queued'
          AND (next_attempt_at IS NULL OR next_attempt_at <= NOW(6))
          AND lease_owner IS NULL
          AND lease_expires_at IS NULL
          AND spool_path IS NOT NULL
          AND attempt_count < %s
          AND EXISTS (
              SELECT 1
              FROM developer_billing_reservations AS reservation
              WHERE reservation.task_id = developer_detection_tasks.task_id
                AND reservation.status = 'reserved'
          )
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (lease_owner, DEVELOPER_TASK_LEASE_SECONDS, DEVELOPER_TASK_MAX_ATTEMPTS),
        fetch=False,
    )
    if updated is None:
        raise TaskRecoveryError("failed to claim the next developer task")
    if updated == 0:
        return None
    rows = excute_sql(
        """
        SELECT task_id, user_id, key_id, mode, filename, mime_type, execution_filename,
               request_sha256, spool_path, spool_size, request_context_json,
               status, lease_owner, lease_expires_at, attempt_count,
               effect_item_id, effect_result_json
        FROM developer_detection_tasks
        WHERE lease_owner = %s AND status = 'running'
        LIMIT 1
        """,
        (lease_owner,),
    )
    if not rows:
        raise TaskRecoveryError(f"claimed task row disappeared for lease {lease_owner}")
    return rows[0]


def _web_task_job(row, *, include_cache=True):
    actor, _user_info, _is_guest = _load_web_request_context(row)
    cached = admin_state.get_detection_job(row["job_id"]) if include_cache else None
    mode = str(row.get("mode") or "fast")
    status = str(row.get("status") or "queued")
    result = None
    raw_result = row.get("result_json")
    if raw_result:
        try:
            result = raw_result if isinstance(raw_result, dict) else json.loads(raw_result)
        except (TypeError, ValueError):
            result = None
    progress = int((cached or {}).get("progress") or 0)
    if status in {"success", "failed"}:
        progress = 100
    elif status == "running" and progress <= 0:
        progress = 8 if mode == "swarm" else 38
    return {
        "id": row["job_id"],
        "kind": "swarm" if mode == "swarm" else "image",
        "filename": row.get("filename") or "",
        "status": status,
        "createdAt": format_createtime(row.get("created_at")),
        "updatedAt": format_createtime(row.get("updated_at")),
        "actor": actor,
        "result": result if status in {"success", "failed"} else None,
        "error": str(row.get("error_message") or "") if status == "failed" else "",
        "mode": mode,
        "progress": max(0, min(progress, 100)),
        "experts": (cached or {}).get("experts") or (
            detection._swarm_initial_experts() if mode == "swarm" else []
        ),
        "summary": (cached or {}).get("summary") or (
            "检测完成"
            if status == "success"
            else "检测未完成"
            if status == "failed"
            else "多源复核正在执行"
            if status == "running" and mode == "swarm"
            else "主鉴伪模型正在 GPU 推理"
            if status == "running"
            else "等待检测队列启动"
        ),
    }


def _persistent_web_job(job_id):
    if not _ensure_developer_platform_tables():
        raise TaskRecoveryError("detection queue schema is unavailable")
    rows = excute_sql(
        """
        SELECT job_id, mode, filename, mime_type, request_sha256, spool_path,
               spool_size, request_context_json, status, lease_owner,
               lease_expires_at, attempt_count, effect_item_id,
               effect_result_json, result_json, error_message,
               created_at, updated_at, completed_at
        FROM web_detection_tasks
        WHERE job_id = %s
        LIMIT 1
        """,
        (job_id,),
    )
    if rows is None:
        raise TaskRecoveryError("failed to load Web detection job")
    return _web_task_job(rows[0]) if rows else None


def _active_web_task_ids():
    if not _ensure_developer_platform_tables():
        raise TaskRecoveryError("detection queue schema is unavailable")
    rows = excute_sql(
        "SELECT job_id FROM web_detection_tasks WHERE status IN ('queued', 'running')"
    )
    if rows is None:
        raise TaskRecoveryError("failed to load active Web detection jobs")
    return {str(row.get("job_id")) for row in rows if row.get("job_id")}


def _claim_next_web_task(worker_instance):
    if not _ensure_developer_platform_tables():
        raise TaskRecoveryError("detection queue schema is unavailable")
    lease_owner = f"{str(worker_instance)[:24]}-web-{uuid.uuid4().hex}"[:64]
    updated = excute_sql(
        """
        UPDATE web_detection_tasks
        SET status = 'running', lease_owner = %s,
            lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND),
            attempt_count = attempt_count + 1, next_attempt_at = NULL
        WHERE status = 'queued'
          AND (next_attempt_at IS NULL OR next_attempt_at <= NOW(6))
          AND lease_owner IS NULL
          AND lease_expires_at IS NULL
          AND spool_path IS NOT NULL
          AND attempt_count < %s
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (lease_owner, WEB_TASK_LEASE_SECONDS, WEB_TASK_MAX_ATTEMPTS),
        fetch=False,
    )
    if updated is None:
        raise TaskRecoveryError("failed to claim the next Web task")
    if updated == 0:
        return None
    rows = excute_sql(
        """
        SELECT job_id, mode, filename, mime_type, request_sha256, spool_path,
               spool_size, request_context_json, status, lease_owner,
               lease_expires_at, attempt_count, effect_item_id,
               effect_result_json, result_json, error_message,
               created_at, updated_at, completed_at
        FROM web_detection_tasks
        WHERE lease_owner = %s AND status = 'running'
        LIMIT 1
        """,
        (lease_owner,),
    )
    if not rows:
        raise TaskRecoveryError(f"claimed Web task disappeared for lease {lease_owner}")
    return rows[0]


def _renew_web_task_lease(job_id, lease_owner):
    updated = excute_sql(
        """
        UPDATE web_detection_tasks
        SET lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND)
        WHERE job_id = %s AND status = 'running' AND lease_owner = %s
          AND lease_expires_at > NOW(6)
        """,
        (WEB_TASK_LEASE_SECONDS, job_id, lease_owner),
        fetch=False,
    )
    if updated is None:
        raise TaskRecoveryError(f"failed to renew Web task lease for {job_id}")
    return updated == 1


def _web_task_heartbeat_loop(job_id, lease_owner, stop_event, lease_lost_event):
    last_success = time.monotonic()
    while not stop_event.wait(WEB_TASK_HEARTBEAT_SECONDS):
        try:
            if _renew_web_task_lease(job_id, lease_owner):
                last_success = time.monotonic()
                continue
        except (TaskRecoveryError, TaskSpoolError) as exc:
            print(f"[WEB TASK RECOVERY ERROR] {exc}")
            if time.monotonic() - last_success < WEB_TASK_LEASE_SECONDS - WEB_TASK_HEARTBEAT_SECONDS:
                continue
        lease_lost_event.set()
        return


def _finish_web_task(task, payload, status_code):
    success = status_code < 400 and isinstance(payload, dict) and payload.get("status") == "success"
    error_message = ""
    if not success:
        error_message = str(
            (payload or {}).get("message") if isinstance(payload, dict) else ""
        ) or f"HTTP {status_code}"
    updated = excute_sql(
        """
        UPDATE web_detection_tasks
        SET status = %s, result_json = %s, error_message = %s,
            completed_at = NOW(), lease_owner = NULL, lease_expires_at = NULL
        WHERE job_id = %s AND status = 'running' AND lease_owner = %s
          AND lease_expires_at > NOW(6)
        """,
        (
            "success" if success else "failed",
            json.dumps(payload, ensure_ascii=False, default=str),
            None if success else error_message[:500],
            task["job_id"],
            task["lease_owner"],
        ),
        fetch=False,
    )
    if updated != 1:
        raise TaskRecoveryError(f"Web task {task['job_id']} lost its execution lease")
    return success, error_message


def _retry_after_seconds(payload, default=5):
    try:
        value = int(float((payload or {}).get("retryAfter") or default))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, 60))


def _defer_web_task_after_overload(task, payload):
    delay = _retry_after_seconds(payload)
    updated = excute_sql(
        """
        UPDATE web_detection_tasks
        SET status = 'queued', next_attempt_at = DATE_ADD(NOW(6), INTERVAL %s SECOND),
            lease_owner = NULL, lease_expires_at = NULL,
            error_message = 'GPU 队列繁忙，任务将在后台自动重试'
        WHERE job_id = %s AND status = 'running' AND lease_owner = %s
          AND lease_expires_at > NOW(6) AND attempt_count < %s
        """,
        (delay, task["job_id"], task["lease_owner"], WEB_TASK_MAX_ATTEMPTS),
        fetch=False,
    )
    if updated is None:
        raise TaskRecoveryError(f"failed to defer overloaded Web task {task['job_id']}")
    return updated == 1


def _web_task_business_rows(task, user_info, *, item_id=None):
    owner_where, owner_params = detection._detection_owner_where(
        user_info.get("Userid"),
        str(user_info.get("phone") or "").strip(),
        str(user_info.get("openid") or "").strip(),
        user_info.get("account_uuid"),
    )
    item_condition = ""
    params = (task["job_id"], *owner_params)
    if item_id not in (None, ""):
        item_condition = "itemid = %s AND "
        params = (item_id, task["job_id"], *owner_params)
    rows = excute_detection_sql(
        f"""
        SELECT * FROM data
        WHERE {item_condition}developer_task_id = %s AND ({owner_where})
        ORDER BY itemid ASC
        LIMIT 2
        """,
        params,
    )
    if rows is None:
        raise TaskRecoveryError(f"failed to inspect Web business result for {task['job_id']}")
    if len(rows) > 1:
        raise TaskRecoveryError(f"Web task {task['job_id']} has multiple business results")
    return rows


def _record_web_task_effect(task, user_info, payload):
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return False
    result = payload.get("result") or {}
    item_id = result.get("itemid")
    if item_id in (None, ""):
        raise TaskRecoveryError(f"Web task {task['job_id']} has no business item id")
    owner_where, owner_params = detection._detection_owner_where(
        user_info.get("Userid"),
        str(user_info.get("phone") or "").strip(),
        str(user_info.get("openid") or "").strip(),
        user_info.get("account_uuid"),
    )
    linked = excute_detection_sql(
        f"""
        UPDATE data SET developer_task_id = %s
        WHERE itemid = %s AND ({owner_where})
          AND (developer_task_id IS NULL OR developer_task_id = %s)
        """,
        (task["job_id"], item_id, *owner_params, task["job_id"]),
        fetch=False,
    )
    if linked != 1 and not _web_task_business_rows(task, user_info, item_id=item_id):
        raise TaskRecoveryError(f"Web task {task['job_id']} result could not be linked")
    effect_json = json.dumps(payload, ensure_ascii=False, default=str)
    updated = excute_sql(
        """
        UPDATE web_detection_tasks
        SET effect_item_id = %s,
            effect_result_json = COALESCE(effect_result_json, %s)
        WHERE job_id = %s AND status IN ('queued', 'running')
          AND (effect_item_id IS NULL OR effect_item_id = %s)
        """,
        (item_id, effect_json, task["job_id"], item_id),
        fetch=False,
    )
    if updated is None:
        raise TaskRecoveryError(f"failed to journal Web result for {task['job_id']}")
    if updated == 1:
        return True
    current = excute_sql(
        "SELECT effect_item_id FROM web_detection_tasks WHERE job_id = %s LIMIT 1",
        (task["job_id"],),
    )
    if current and str(current[0].get("effect_item_id")) == str(item_id):
        return False
    raise TaskRecoveryError(f"Web task {task['job_id']} changed before result journaling")


def _recover_web_task_effect(task, user_info):
    rows = excute_sql(
        """
        SELECT status, effect_item_id, effect_result_json
        FROM web_detection_tasks WHERE job_id = %s LIMIT 1
        """,
        (task["job_id"],),
    )
    if rows is None or not rows:
        raise TaskRecoveryError(f"Web task {task['job_id']} disappeared during recovery")
    effect_item_id = rows[0].get("effect_item_id")
    business_rows = _web_task_business_rows(task, user_info, item_id=effect_item_id)
    if not business_rows:
        if effect_item_id not in (None, ""):
            raise TaskRecoveryError(f"Web task {task['job_id']} references a missing result")
        return None
    record = business_rows[0]
    if (
        str(task.get("mode") or "fast") == "swarm"
        and effect_item_id in (None, "")
        and not str(record.get("explantation") or "").startswith("Swarm 专家会诊完成：")
    ):
        raise TaskRecoveryError(
            f"Web task {task['job_id']} stopped after its primary result; automatic Swarm replay is blocked"
        )
    payload = None
    raw_payload = rows[0].get("effect_result_json")
    if not raw_payload:
        raise TaskRecoveryError(
            f"Web task {task['job_id']} has a primary result without a finalized response journal"
        )
    try:
        payload = raw_payload if isinstance(raw_payload, dict) else json.loads(raw_payload)
    except (TypeError, ValueError) as exc:
        raise TaskRecoveryError(
            f"Web task {task['job_id']} finalized response journal is unreadable"
        ) from exc
    payload_item_id = ((payload or {}).get("result") or {}).get("itemid")
    if not isinstance(payload, dict) or payload.get("status") != "success" or str(
        payload_item_id
    ) != str(record.get("itemid")):
        raise TaskRecoveryError(f"Web task {task['job_id']} stored result is inconsistent")
    normalized = _public_result_payload(payload, str(task.get("mode") or "fast"))
    result = normalized.get("result") if isinstance(normalized, dict) else None
    if not isinstance(result, dict) or result.get("decisionStatus") not in {"verdict", "review_only"}:
        raise TaskRecoveryError(f"Web task {task['job_id']} stored result lacks a valid decision contract")
    return normalized


def _run_web_detection_job(task):
    job_id = task["job_id"]
    mode = str(task.get("mode") or "fast")
    restored = _web_task_job(task, include_cache=False)
    admin_state.restore_detection_job(restored)
    admin_state.update_detection_job(job_id, {
        "status": "running",
        "progress": 8 if mode == "swarm" else 38,
        "summary": "多源复核已开始" if mode == "swarm" else "主鉴伪模型正在 GPU 推理",
    })
    heartbeat_stop = threading.Event()
    lease_lost = threading.Event()
    heartbeat = BACKGROUND_THREAD_CLASS(
        target=_web_task_heartbeat_loop,
        args=(job_id, task["lease_owner"], heartbeat_stop, lease_lost),
        daemon=True,
    )
    heartbeat.start()
    try:
        _actor, user_info, is_guest = _load_web_request_context(task)
        payload = _recover_web_task_effect(task, user_info)
        recovered = payload is not None
        status_code = 200
        if not recovered:
            image_bytes = _read_web_task_spool(task)
            if mode == "swarm":
                payload, status_code = detection._run_swarm_detection_payload(
                    image_bytes,
                    task.get("filename") or "upload.img",
                    task.get("mime_type") or "application/octet-stream",
                    user_info,
                    is_guest=is_guest,
                    job_id=job_id,
                )
            else:
                payload, status_code = detection._run_image_detection_payload(
                    image_bytes,
                    task.get("filename") or "upload.img",
                    task.get("mime_type") or "application/octet-stream",
                    user_info,
                    is_guest=is_guest,
                    mark_guest=False,
                    source_task_id=job_id,
                )
            if status_code < 400 and isinstance(payload, dict) and payload.get("status") == "success":
                _record_web_task_effect(task, user_info, payload)
        heartbeat_stop.set()
        heartbeat.join(timeout=WEB_TASK_HEARTBEAT_SECONDS + 1)
        if lease_lost.is_set():
            raise TaskRecoveryError(f"Web task {job_id} lost its execution lease")
        if status_code == 429 and _defer_web_task_after_overload(task, payload):
            admin_state.update_detection_job(job_id, {
                "status": "queued",
                "result": None,
                "error": "",
                "progress": 0,
                "summary": "GPU 队列繁忙，任务将在后台自动重试",
            })
            return
        success, error_message = _finish_web_task(task, payload, status_code)
        admin_state.update_detection_job(job_id, {
            "status": "success" if success else "failed",
            "result": payload,
            "error": "" if success else error_message,
            "progress": 100,
            "summary": "检测完成（已恢复持久化结果）" if success and recovered else (
                "检测完成" if success else "检测未完成"
            ),
        })
    except Exception as exc:
        message = str(exc)[:500]
        heartbeat_stop.set()
        heartbeat.join(timeout=WEB_TASK_HEARTBEAT_SECONDS + 1)
        try:
            _finish_web_task(
                task,
                {"status": "error", "message": message},
                500,
            )
        except TaskRecoveryError:
            pass
        admin_state.update_detection_job(job_id, {
            "status": "failed",
            "error": message,
            "progress": 100,
            "summary": "检测未完成",
        })
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=WEB_TASK_HEARTBEAT_SECONDS + 1)
        rows = excute_sql(
            "SELECT status, spool_path FROM web_detection_tasks WHERE job_id = %s LIMIT 1",
            (job_id,),
        ) or []
        if rows and rows[0].get("status") in {"success", "failed"} and rows[0].get("spool_path"):
            _remove_web_task_spool(job_id, rows[0]["spool_path"])


def _renew_task_lease(task_id, lease_owner):
    updated = excute_sql(
        """
        UPDATE developer_detection_tasks
        SET lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND),
            last_heartbeat_at = NOW(6)
        WHERE task_id = %s
          AND status = 'running'
          AND lease_owner = %s
          AND lease_expires_at > NOW(6)
        """,
        (DEVELOPER_TASK_LEASE_SECONDS, task_id, lease_owner),
        fetch=False,
    )
    if updated is None:
        raise TaskRecoveryError(f"failed to renew task lease for {task_id}")
    return updated == 1


def _task_heartbeat_loop(task_id, lease_owner, stop_event, lease_lost_event):
    last_success = time.monotonic()
    while not stop_event.wait(DEVELOPER_TASK_HEARTBEAT_SECONDS):
        try:
            if _renew_task_lease(task_id, lease_owner):
                last_success = time.monotonic()
                continue
            print(f"[DEVELOPER TASK RECOVERY ERROR] task {task_id} lost its execution lease")
        except TaskRecoveryError as exc:
            print(f"[DEVELOPER TASK RECOVERY ERROR] {exc}")
            remaining = DEVELOPER_TASK_LEASE_SECONDS - (time.monotonic() - last_success)
            if remaining > DEVELOPER_TASK_HEARTBEAT_SECONDS:
                continue
        lease_lost_event.set()
        return


def _finish_task(task_id, actor, mode, payload, status_code, lease_owner=None):
    success = status_code < 400 and isinstance(payload, dict) and payload.get("status") == "success"
    lease_condition = ""
    lease_params = ()
    if lease_owner:
        lease_condition = " AND lease_owner = %s AND lease_expires_at > NOW(6)"
        lease_params = (lease_owner,)
    if success:
        public_payload = _public_result_payload(payload, mode)
        result = (public_payload or {}).get("result") or {}
        item_id = result.get("itemid")
        prompt, completion, total = _token_usage(payload)
        persisted = excute_sql(
            f"""
            UPDATE developer_detection_tasks
            SET status = 'success', result_item_id = %s, result_json = %s,
                prompt_tokens = %s, completion_tokens = %s, total_tokens = %s,
                error_message = NULL, completed_at = NOW(),
                lease_owner = NULL, lease_expires_at = NULL
            WHERE task_id = %s AND status IN ('queued', 'running'){lease_condition}
            """,
            (
                item_id,
                json.dumps(public_payload, ensure_ascii=False, default=str),
                prompt,
                completion,
                total,
                task_id,
                *lease_params,
            ),
            fetch=False,
        )
        if persisted != 1:
            return False
        if public_payload.get("billable") is False:
            return _release_billing(
                task_id,
                "检测仅形成复核态结论，不消耗调用额度",
                audit_entry_type="detection_review_only",
            )
        return _settle_billing(task_id)

    message = (payload or {}).get("message") if isinstance(payload, dict) else ""
    message = str(message or f"HTTP {status_code}")[:500]
    try:
        return _fail_task_and_release(task_id, message, lease_owner=lease_owner)
    except TaskRecoveryError as exc:
        print(f"[DEVELOPER TASK RECOVERY ERROR] {exc}")
        return False


def _defer_developer_task_after_overload(task, payload):
    delay = _retry_after_seconds(payload, default=5)
    updated = excute_sql(
        """
        UPDATE developer_detection_tasks
        SET status = 'queued', next_attempt_at = DATE_ADD(NOW(6), INTERVAL %s SECOND),
            lease_owner = NULL, lease_expires_at = NULL, last_heartbeat_at = NULL,
            error_message = 'GPU 队列繁忙，任务将在后台自动重试'
        WHERE task_id = %s AND status = 'running' AND lease_owner = %s
          AND lease_expires_at > NOW(6) AND attempt_count < %s
        """,
        (
            delay,
            task["task_id"],
            task["lease_owner"],
            DEVELOPER_TASK_MAX_ATTEMPTS,
        ),
        fetch=False,
    )
    if updated is None:
        raise TaskRecoveryError(f"failed to defer overloaded task {task['task_id']}")
    return updated == 1


def _task_terminal_row(task_id):
    rows = excute_sql(
        """
        SELECT status, spool_path
        FROM developer_detection_tasks
        WHERE task_id = %s
        LIMIT 1
        """,
        (task_id,),
    )
    if not rows:
        return None
    return rows[0] if rows[0].get("status") in {"success", "failed", "rejected"} else None


def _execute_or_recover_task(task, user_info, image_bytes):
    recovered = _recover_task_effect(task, user_info)
    if recovered is not None:
        return recovered, 200, True

    execution_filename = task.get("execution_filename") or _task_execution_filename(
        task.get("task_id"), task.get("filename")
    )
    mimetype = task.get("mime_type") or "application/octet-stream"
    if task.get("mode") == "swarm":
        payload, status_code = detection._run_swarm_detection_payload(
            image_bytes,
            execution_filename,
            mimetype,
            user_info,
            is_guest=False,
            job_id=task["task_id"],
        )
    else:
        payload, status_code = detection._run_image_detection_payload(
            image_bytes,
            execution_filename,
            mimetype,
            user_info,
            is_guest=False,
            mark_guest=False,
        )
    payload = _normalize_task_result_filename(payload, task)
    if status_code < 400 and isinstance(payload, dict) and payload.get("status") == "success":
        payload = _public_result_payload(payload, task.get("mode"))
        _record_task_effect(task, user_info, payload)
    return payload, status_code, False


def _run_openapi_job(task):
    task_id = task["task_id"]
    lease_owner = task["lease_owner"]
    mode = task["mode"]
    _update_job_cache(task_id, {
        "status": "running",
        "progress": 8 if mode == "swarm" else 38,
        "summary": "多源复核已开始" if mode == "swarm" else "主鉴伪模型正在 GPU 推理",
    })
    heartbeat_stop = threading.Event()
    lease_lost = threading.Event()
    heartbeat = BACKGROUND_THREAD_CLASS(
        target=_task_heartbeat_loop,
        args=(task_id, lease_owner, heartbeat_stop, lease_lost),
        daemon=True,
    )
    actor = {
        "id": task.get("key_id"),
        "user_id": task.get("user_id"),
        "account_uuid": "",
    }
    heartbeat.start()
    try:
        with _task_execution_lock(task):
            actor, user_info = _load_request_context(task)
            image_bytes = _read_task_spool(task)
            payload, status_code, recovered = _execute_or_recover_task(
                task,
                user_info,
                image_bytes,
            )
            heartbeat_stop.set()
            heartbeat.join(timeout=DEVELOPER_TASK_HEARTBEAT_SECONDS + 1)
            if lease_lost.is_set():
                _update_job_cache(task_id, {
                    "status": "running",
                    "error": "",
                    "summary": "执行租约已交接，已落库结果将由恢复任务复用并对账",
                })
                return
            if status_code == 429 and _defer_developer_task_after_overload(task, payload):
                _update_job_cache(task_id, {
                    "status": "queued",
                    "result": None,
                    "error": "",
                    "progress": 0,
                    "summary": "GPU 队列繁忙，任务将在后台自动重试",
                })
                return
            finished = _finish_task(task_id, actor, mode, payload, status_code, lease_owner=lease_owner)
            if finished:
                _update_job_cache(task_id, {
                    "status": "success" if status_code < 400 and payload.get("status") == "success" else "failed",
                    "result": payload,
                    "error": "" if status_code < 400 else (payload or {}).get("message") or f"HTTP {status_code}",
                    "progress": 100,
                    "summary": "检测完成（已复用持久化结果）" if recovered else (
                        "检测完成" if status_code < 400 else "检测未完成"
                    ),
                })
            else:
                rows = excute_sql(
                    "SELECT status FROM developer_detection_tasks WHERE task_id = %s LIMIT 1",
                    (task_id,),
                ) or []
                if rows and rows[0].get("status") == "success":
                    _update_job_cache(task_id, {
                        "status": "success",
                        "result": payload,
                        "progress": 100,
                        "summary": "检测完成，计费对账将在下次请求自动重试",
                    })
                else:
                    _update_job_cache(task_id, {
                        "status": "failed",
                        "error": (payload or {}).get("message") or f"HTTP {status_code}",
                        "result": payload,
                        "progress": 100,
                    })
    except TaskRecoveryError as exc:
        message = str(exc)[:500]
        heartbeat_stop.set()
        heartbeat.join(timeout=DEVELOPER_TASK_HEARTBEAT_SECONDS + 1)
        _update_job_cache(task_id, {
            "status": "running",
            "error": "",
            "summary": f"结果恢复暂不可用，等待租约重试：{message}",
        })
        print(f"[DEVELOPER TASK RECOVERY ERROR] deferred {task_id}: {message}")
    except Exception as exc:
        message = str(exc)[:500]
        heartbeat_stop.set()
        heartbeat.join(timeout=DEVELOPER_TASK_HEARTBEAT_SECONDS + 1)
        _finish_task(
            task_id,
            actor,
            mode,
            {"status": "error", "message": message},
            500,
            lease_owner=lease_owner,
        )
        _update_job_cache(task_id, {
            "status": "failed",
            "error": message,
            "progress": 100,
        })
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=DEVELOPER_TASK_HEARTBEAT_SECONDS + 1)
        terminal = _task_terminal_row(task_id)
        if terminal and terminal.get("spool_path"):
            _remove_task_spool(task_id, terminal["spool_path"])


def _cleanup_terminal_spools(limit=None):
    batch_size = max(1, min(int(limit or DEVELOPER_TASK_RECONCILE_BATCH_SIZE), 200))
    rows = excute_sql(
        """
        SELECT task_id, spool_path
        FROM developer_detection_tasks
        WHERE status IN ('success', 'failed', 'rejected')
          AND spool_path IS NOT NULL
        ORDER BY completed_at ASC
        LIMIT %s
        """,
        (batch_size,),
    )
    if rows is None:
        raise TaskRecoveryError("failed to scan terminal task spools")
    cleaned = 0
    for row in rows:
        if _remove_task_spool(row["task_id"], row["spool_path"]):
            cleaned += 1
    return cleaned


def _reconcile_web_tasks(limit=None):
    if not _ensure_developer_platform_tables():
        raise TaskRecoveryError("detection queue schema is unavailable")
    batch_size = max(1, min(int(limit or DEVELOPER_TASK_RECONCILE_BATCH_SIZE), 200))
    rows = excute_sql(
        """
        SELECT job_id, mode, filename, mime_type, request_sha256, spool_path,
               spool_size, request_context_json, status, lease_owner,
               lease_expires_at, attempt_count, effect_item_id,
               effect_result_json, result_json, error_message,
               created_at, updated_at, completed_at
        FROM web_detection_tasks
        WHERE (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= NOW(6))
           OR (status = 'queued' AND spool_path IS NULL)
           OR (status = 'queued' AND attempt_count >= %s
               AND (next_attempt_at IS NULL OR next_attempt_at <= NOW(6)))
        ORDER BY updated_at ASC
        LIMIT %s
        """,
        (WEB_TASK_MAX_ATTEMPTS, batch_size),
    )
    if rows is None:
        raise TaskRecoveryError("failed to scan interrupted Web tasks")
    recovered = 0
    for row in rows:
        job_id = row["job_id"]
        recovered_payload = None
        try:
            _actor, user_info, _is_guest = _load_web_request_context(row)
            recovered_payload = _recover_web_task_effect(row, user_info)
        except (TaskRecoveryError, TaskSpoolError) as exc:
            print(f"[WEB TASK RECOVERY ERROR] {exc}")
        if recovered_payload is not None:
            updated = excute_sql(
                """
                UPDATE web_detection_tasks
                SET status = 'success', result_json = %s, error_message = NULL,
                    completed_at = NOW(), lease_owner = NULL, lease_expires_at = NULL
                WHERE job_id = %s AND status = %s
                """,
                (
                    json.dumps(recovered_payload, ensure_ascii=False, default=str),
                    job_id,
                    row.get("status"),
                ),
                fetch=False,
            )
            if updated == 1:
                admin_state.update_detection_job(job_id, {
                    "status": "success",
                    "result": recovered_payload,
                    "error": "",
                    "progress": 100,
                    "summary": "检测完成（已从持久化结果恢复）",
                })
                recovered += 1
            continue
        message = (
            "任务执行进程中断，系统未自动重复推理，以避免重复历史记录"
            if row.get("status") == "running"
            else "任务上传文件不可用，未进入模型推理"
        )
        updated = excute_sql(
            """
            UPDATE web_detection_tasks
            SET status = 'failed', error_message = %s, completed_at = NOW(),
                lease_owner = NULL, lease_expires_at = NULL
            WHERE job_id = %s AND status = %s
            """,
            (message, job_id, row.get("status")),
            fetch=False,
        )
        if updated == 1:
            admin_state.update_detection_job(job_id, {
                "status": "failed",
                "error": message,
                "progress": 100,
                "summary": "检测未完成",
            })
            recovered += 1
    return recovered


def _cleanup_terminal_web_spools(limit=None):
    batch_size = max(1, min(int(limit or DEVELOPER_TASK_RECONCILE_BATCH_SIZE), 200))
    rows = excute_sql(
        """
        SELECT job_id, spool_path
        FROM web_detection_tasks
        WHERE status IN ('success', 'failed') AND spool_path IS NOT NULL
        ORDER BY completed_at ASC
        LIMIT %s
        """,
        (batch_size,),
    )
    if rows is None:
        raise TaskRecoveryError("failed to scan terminal Web task spools")
    cleaned = 0
    for row in rows:
        if _remove_web_task_spool(row["job_id"], row["spool_path"]):
            cleaned += 1
    return cleaned


def _cleanup_orphan_web_spool_files():
    _ensure_web_spool_root()
    rows = excute_sql("SELECT spool_path FROM web_detection_tasks WHERE spool_path IS NOT NULL")
    if rows is None:
        raise TaskRecoveryError("failed to load referenced Web task spools")
    referenced = {str(row.get("spool_path") or "") for row in rows}
    cutoff = time.time() - DEVELOPER_SPOOL_ORPHAN_GRACE_SECONDS
    cleaned = 0
    for path in WEB_TASK_SPOOL_ROOT.iterdir():
        try:
            item_stat = path.lstat()
            if path.name in referenced or item_stat.st_mtime > cutoff or stat.S_ISDIR(item_stat.st_mode):
                continue
            path.unlink()
            cleaned += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"[WEB TASK SPOOL ERROR] orphan cleanup {path.name}: {exc}")
    return cleaned


def _cleanup_expired_guest_usage():
    deleted = excute_sql(
        "DELETE FROM web_guest_daily_usage WHERE usage_day < DATE_SUB(CURDATE(), INTERVAL 90 DAY)",
        fetch=False,
    )
    if deleted is None:
        raise TaskRecoveryError("failed to clean expired guest usage")
    return int(deleted or 0)


def _run_worker_maintenance():
    recovered = _reconcile_expired_tasks()
    cleaned = _cleanup_terminal_spools()
    orphans = _cleanup_orphan_spool_files()
    web_recovered = _reconcile_web_tasks()
    web_cleaned = _cleanup_terminal_web_spools()
    web_orphans = _cleanup_orphan_web_spool_files()
    guest_usage_cleaned = _cleanup_expired_guest_usage()
    privacy_erasure = _retry_pending_privacy_erasures()
    return {
        "recovered": recovered,
        "cleaned": cleaned,
        "orphans": orphans,
        "web_recovered": web_recovered,
        "web_cleaned": web_cleaned,
        "web_orphans": web_orphans,
        "guest_usage_cleaned": guest_usage_cleaned,
        "privacy_erasure": privacy_erasure,
    }


def _allow_idempotent_retry(task_id):
    updated = excute_sql(
        """
        UPDATE developer_detection_tasks
        SET idempotency_key = NULL
        WHERE task_id = %s AND status = 'failed'
        """,
        (task_id,),
        fetch=False,
    )
    return updated == 1


def _require_scope(actor, scope):
    raw_scopes = actor.get("scopes") or []
    scopes = set(raw_scopes if isinstance(raw_scopes, (list, tuple, set)) else _developer_scopes(raw_scopes))
    # Legacy `detect` keys retain fast-image access only. They must not silently
    # gain Swarm or report privileges introduced after the key was issued.
    if scope in scopes or (scope == "image:fast" and "detect" in scopes):
        return None
    return _error(f"当前 API Key 缺少 {scope} 权限", 403, "insufficient_scope")


def _validate_image(image_bytes):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size
                image.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None, {
            "message": "图片像素数量过高，无法安全处理",
            "status": 413,
            "code": "image_pixels_too_large",
        }
    except (UnidentifiedImageError, OSError, ValueError):
        return None, {"message": "文件不是可读取的图片", "status": 400, "code": "invalid_image"}
    if width <= 0 or height <= 0:
        return None, {"message": "图片尺寸无效", "status": 400, "code": "invalid_image"}
    if width * height > DEVELOPER_MAX_IMAGE_PIXELS:
        return None, {
            "message": f"图片像素不能超过 {DEVELOPER_MAX_IMAGE_PIXELS}，当前为 {width}x{height}",
            "status": 413,
            "code": "image_pixels_too_large",
        }
    return {"width": width, "height": height}, None


@openapi_blueprint.post("/image-detections")
def create_image_detection():
    actor, auth_error = _openapi_key_required()
    if auth_error:
        return auth_error
    if request.content_length is None:
        return _error("上传请求必须提供 Content-Length", 411, "length_required")
    if request.content_length > DEVELOPER_MAX_IMAGE_BYTES + (1024 * 1024):
        return _error("图片不能超过 25 MB", 413, "image_too_large")
    mode = str(request.form.get("mode") or request.args.get("mode") or "fast").strip().lower()
    if mode not in {"fast", "swarm"}:
        return _error("mode 仅支持 fast 或 swarm", 400, "invalid_mode")
    scope_error = _require_scope(actor, f"image:{mode}")
    if scope_error:
        return scope_error
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if not idempotency_key:
        return _error(
            "创建检测任务必须提供 Idempotency-Key",
            400,
            "idempotency_key_required",
        )
    if not (
        8 <= len(idempotency_key) <= 128
        and all(33 <= ord(char) <= 126 for char in idempotency_key)
    ):
        return _error(
            "Idempotency-Key 必须是 8 到 128 位可见 ASCII 字符",
            400,
            "invalid_idempotency_key",
        )
    if not _ensure_developer_platform_tables():
        return _error("开发者平台存储初始化失败", 503, "platform_unavailable")
    try:
        _maybe_reconcile_expired_tasks()
    except TaskRecoveryError as exc:
        return _task_recovery_error_response(exc)

    upload = request.files.get("image") or request.files.get("file")
    if not upload or not upload.filename:
        return _error("请使用 multipart/form-data 上传 image 文件", 400, "image_required")
    if not detection.allowed_file(upload.filename):
        return _error("不支持的图片格式", 415, "unsupported_media_type")
    image_bytes = upload.stream.read(DEVELOPER_MAX_IMAGE_BYTES + 1)
    if not image_bytes:
        return _error("图片文件为空", 400, "empty_image")
    if len(image_bytes) > DEVELOPER_MAX_IMAGE_BYTES:
        return _error("图片不能超过 25 MB", 413, "image_too_large")
    _, image_error = _validate_image(image_bytes)
    if image_error:
        return _error(image_error["message"], image_error["status"], image_error["code"])

    digest = hashlib.sha256(image_bytes).hexdigest()
    account_uuid = str(actor.get("account_uuid") or "").strip()
    if not account_uuid:
        return _error("开发者账号缺少不可变身份标识，请重新登录", 401, "account_identity_required")
    existing = _idempotent_task(actor["user_id"], account_uuid, idempotency_key)
    if existing:
        if existing.get("mode") != mode or existing.get("request_sha256") != digest:
            return _error("该 Idempotency-Key 已用于其他请求", 409, "idempotency_conflict")
        return jsonify(_task_payload(existing)), 200

    try:
        capacity_error = _queue_capacity_error(len(image_bytes))
    except TaskRecoveryError as exc:
        return _task_recovery_error_response(exc)
    if capacity_error:
        response, status = _error(capacity_error, 429, "task_queue_full")
        response.headers["Retry-After"] = "15"
        return response, status

    user_info = {
        "Userid": actor.get("user_id"),
        "account_uuid": actor.get("account_uuid"),
        "username": actor.get("username") or actor.get("phone") or "developer",
        "phone": actor.get("phone") or "",
        "openid": actor.get("openid") or actor.get("phone") or f"developer-{actor.get('user_id')}",
    }
    experts = detection._swarm_initial_experts() if mode == "swarm" else []
    task_id = ""
    spool_name = None
    inserted = None
    try:
        with _queue_submission_guard(len(image_bytes)):
            duplicate = _idempotent_task(actor["user_id"], account_uuid, idempotency_key)
            if duplicate:
                if duplicate.get("mode") != mode or duplicate.get("request_sha256") != digest:
                    return _error(
                        "该 Idempotency-Key 已用于其他请求",
                        409,
                        "idempotency_conflict",
                    )
                return jsonify(_task_payload(duplicate)), 200
            try:
                job = admin_state.create_detection_job(
                    user_info,
                    upload.filename,
                    kind="swarm" if mode == "swarm" else "image",
                    mode=mode,
                    experts=experts,
                )
            except Exception:
                return _error("任务创建失败，请稍后重试", 503, "task_create_failed")
            task_id = job["id"]
            execution_filename = _task_execution_filename(task_id, upload.filename)
            try:
                spool_name = _write_task_spool(task_id, image_bytes)
            except TaskSpoolError as exc:
                _update_job_cache(
                    task_id,
                    {"status": "failed", "error": "任务文件持久化失败", "progress": 100},
                )
                print(f"[DEVELOPER TASK SPOOL ERROR] create {task_id}: {exc}")
                return _error("任务文件持久化失败，请稍后重试", 503, "task_spool_unavailable")
            mimetype = (upload.mimetype or "application/octet-stream")[:127]
            inserted = excute_sql(
                """
                INSERT INTO developer_detection_tasks
                    (task_id, user_id, account_uuid, key_id, mode, filename, mime_type, execution_filename, request_sha256,
                     spool_path, spool_size, request_context_json, idempotency_key, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'preparing')
                """,
                (
                    task_id,
                    actor["user_id"],
                    account_uuid,
                    actor["id"],
                    mode,
                    upload.filename[:255],
                    mimetype,
                    execution_filename,
                    digest,
                    spool_name,
                    len(image_bytes),
                    _request_context(actor, user_info),
                    idempotency_key,
                ),
                fetch=False,
            )
    except QueueCapacityError as exc:
        _update_job_cache(
            task_id,
            {"status": "failed", "error": str(exc), "progress": 100},
        )
        response, status = _error(str(exc), 429, "task_queue_full")
        response.headers["Retry-After"] = "15"
        return response, status
    except TaskRecoveryError as exc:
        if spool_name:
            try:
                _spool_file_path(spool_name).unlink(missing_ok=True)
            except (OSError, TaskSpoolError) as cleanup_exc:
                print(f"[DEVELOPER TASK SPOOL ERROR] cleanup {task_id}: {cleanup_exc}")
        _update_job_cache(
            task_id,
            {"status": "failed", "error": "队列准入暂不可用", "progress": 100},
        )
        return _task_recovery_error_response(exc)
    if inserted != 1:
        _remove_task_spool(task_id, spool_name)
        duplicate = _idempotent_task(actor["user_id"], account_uuid, idempotency_key)
        if duplicate and duplicate.get("mode") == mode and duplicate.get("request_sha256") == digest:
            return jsonify(_task_payload(duplicate)), 200
        _update_job_cache(task_id, {"status": "failed", "error": "任务写入失败", "progress": 100})
        return _error("任务创建失败，请稍后重试", 503, "task_create_failed")

    try:
        quota_error, quota_code, retry_after = _reserve_task_daily_quota(actor, task_id)
    except TaskRecoveryError as exc:
        quota_error, quota_code, retry_after = str(exc), "storage_unavailable", None
    if quota_error:
        excute_sql(
            """
            UPDATE developer_detection_tasks
            SET status = 'rejected', error_message = %s, completed_at = NOW()
            WHERE task_id = %s AND status = 'preparing'
            """,
            (str(quota_error)[:500], task_id),
            fetch=False,
        )
        _remove_task_spool(task_id, spool_name)
        _update_job_cache(task_id, {"status": "failed", "error": quota_error, "progress": 100})
        response, status = _error(
            quota_error,
            429 if quota_code == "daily_limit_exceeded" else 503,
            quota_code,
        )
        if retry_after:
            response.headers["Retry-After"] = str(retry_after)
        return response, status

    try:
        _reserve_billing(actor["user_id"], actor["id"], task_id, mode)
    except BillingError as exc:
        released = _release_task_daily_quota(task_id)
        if not released:
            excute_sql(
                """
                UPDATE developer_detection_tasks
                SET error_message = %s
                WHERE task_id = %s AND status = 'preparing'
                """,
                ("计费预占失败，日配额等待后台对账释放", task_id),
                fetch=False,
            )
            return _error(
                "计费预占失败且配额正在后台对账，请稍后查询原任务",
                503,
                "billing_reconciliation_required",
            )
        excute_sql(
            """
            UPDATE developer_detection_tasks
            SET status = 'rejected', error_message = %s, completed_at = NOW(),
                lease_owner = NULL, lease_expires_at = NULL
            WHERE task_id = %s AND status = 'preparing'
            """,
            (str(exc)[:500], task_id),
            fetch=False,
        )
        _remove_task_spool(task_id, spool_name)
        _update_job_cache(task_id, {"status": "failed", "error": str(exc), "progress": 100})
        return _error(str(exc), exc.status_code, exc.code)
    except Exception:
        released = _release_task_daily_quota(task_id)
        if not released:
            return _error(
                "计费服务暂不可用且配额正在后台对账，请稍后查询原任务",
                503,
                "billing_reconciliation_required",
            )
        excute_sql(
            """
            UPDATE developer_detection_tasks
            SET status = 'failed', error_message = %s, completed_at = NOW()
            WHERE task_id = %s AND status = 'preparing'
            """,
            ("计费服务暂不可用", task_id),
            fetch=False,
        )
        _remove_task_spool(task_id, spool_name)
        return _error("计费服务暂不可用", 503, "billing_unavailable")

    enqueued = excute_sql(
        """
        UPDATE developer_detection_tasks
        SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL
        WHERE task_id = %s AND status = 'preparing'
          AND spool_path = %s
          AND EXISTS (
              SELECT 1 FROM developer_billing_reservations AS reservation
              WHERE reservation.task_id = developer_detection_tasks.task_id
                AND reservation.status = 'reserved'
          )
        """,
        (task_id, spool_name),
        fetch=False,
    )
    if enqueued != 1:
        message = "任务可靠入队失败"
        try:
            released = _fail_task_and_release(task_id, message)
        except TaskRecoveryError as exc:
            print(f"[DEVELOPER TASK RECOVERY ERROR] {exc}")
            released = False
        _update_job_cache(
            task_id,
            {"status": "failed", "error": message, "progress": 100},
        )
        terminal = _task_terminal_row(task_id)
        if terminal and terminal.get("spool_path"):
            _remove_task_spool(task_id, terminal["spool_path"])
        if not released:
            return _error(
                "任务未入队，但额度释放尚未确认；系统将自动对账，请勿重复提交",
                503,
                "billing_release_pending",
            )
        _allow_idempotent_retry(task_id)
        return _error("任务可靠入队失败，请稍后重试", 503, "task_enqueue_failed")
    _update_job_cache(
        task_id,
        {"status": "queued", "progress": 0, "summary": "任务已可靠入队，等待 worker 执行"},
    )
    row = _task_row_for_user(task_id, actor["user_id"], actor.get("account_uuid"))
    return jsonify(_task_payload(row)), 202


@openapi_blueprint.get("/image-detections/<task_id>")
def get_image_detection(task_id):
    actor, auth_error = _openapi_key_required()
    if auth_error:
        return auth_error
    try:
        _maybe_reconcile_expired_tasks()
    except TaskRecoveryError as exc:
        return _task_recovery_error_response(exc)
    row = _task_row_for_user(task_id, actor["user_id"], actor.get("account_uuid"))
    if not row:
        return _error("任务不存在", 404, "task_not_found")
    scope_error = _require_scope(actor, f"image:{row.get('mode') or 'fast'}")
    if scope_error:
        return scope_error
    if row.get("status") == "success":
        settlement_error = _task_settlement_error(task_id, row)
        if settlement_error:
            return settlement_error
    payload = _task_payload(row)
    return jsonify(payload)


def _owned_detection_item(actor, item_id):
    owner_where, owner_params = detection._detection_owner_where(
        actor.get("user_id"),
        str(actor.get("phone") or "").strip(),
        str(actor.get("openid") or "").strip(),
        actor.get("account_uuid"),
    )
    rows = excute_detection_sql(
        f"SELECT * FROM data WHERE itemid = %s AND ({owner_where}) LIMIT 1",
        (item_id, *owner_params),
    )
    return rows[0] if rows else None


@openapi_blueprint.get("/image-detections/<task_id>/report")
def get_image_detection_report(task_id):
    actor, auth_error = _openapi_key_required()
    if auth_error:
        return auth_error
    scope_error = _require_scope(actor, "reports")
    if scope_error:
        return scope_error
    row = _task_row_for_user(task_id, actor["user_id"], actor.get("account_uuid"))
    if not row:
        return _error("任务不存在", 404, "task_not_found")
    mode_scope_error = _require_scope(actor, f"image:{row.get('mode') or 'fast'}")
    if mode_scope_error:
        return mode_scope_error
    if row.get("status") != "success" or not row.get("result_item_id"):
        return _error("任务尚未成功完成", 409, "task_not_complete")
    settlement_error = _task_settlement_error(task_id, row)
    if settlement_error:
        return settlement_error
    item = _owned_detection_item(actor, row["result_item_id"])
    if not item:
        return _error("报告记录不存在", 404, "report_not_found")
    payload = _stored_task_result(row) or {}
    result = payload.get("result") or {}
    pdf = reporting.image_report_pdf(item, result)
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": reporting.attachment_header(f"huijian-{task_id}.pdf")},
    )


@openapi_blueprint.get("/image-detections/<task_id>/media")
def get_image_detection_media(task_id):
    actor, auth_error = _openapi_key_required()
    if auth_error:
        return auth_error
    row = _task_row_for_user(task_id, actor["user_id"], actor.get("account_uuid"))
    if not row:
        return _error("任务不存在", 404, "task_not_found")
    scope_error = _require_scope(actor, f"image:{row.get('mode') or 'fast'}")
    if scope_error:
        return scope_error
    if row.get("status") != "success" or not row.get("result_item_id"):
        return _error("任务尚未成功完成", 409, "task_not_complete")
    settlement_error = _task_settlement_error(task_id, row)
    if settlement_error:
        return settlement_error
    item = _owned_detection_item(actor, row["result_item_id"])
    if not item:
        return _error("媒体记录不存在", 404, "media_not_found")
    return _serve_detection_media_item("image", item)


def _developer_usage(user_id, days):
    try:
        v1_usage = _developer_usage_from_v1(user_id, days)
        v2_usage = _developer_usage_from_v2(user_id, days)
    except Exception as exc:
        raise BillingError(
            "用量统计暂不可用，请稍后重试",
            code="usage_storage_unavailable",
            status_code=503,
        ) from exc
    return _merge_developer_usage(v1_usage, v2_usage, days)


def _mode_summary(user_id, days):
    since = datetime.now() - timedelta(days=days - 1)
    rows = excute_sql(
        """
        SELECT mode, COUNT(*) AS calls, COALESCE(SUM(amount_fen), 0) AS spend_fen
        FROM developer_billing_reservations
        WHERE user_id = %s AND status = 'settled' AND settled_at >= %s
        GROUP BY mode
        """,
        (user_id, since.strftime("%Y-%m-%d 00:00:00")),
    )
    if rows is None:
        raise BillingError(
            "模式用量统计暂不可用，请稍后重试",
            code="usage_storage_unavailable",
            status_code=503,
        )
    values = {"fast": {"calls": 0, "spendFen": 0}, "swarm": {"calls": 0, "spendFen": 0}}
    for row in rows:
        if row.get("mode") in values:
            values[row["mode"]] = {
                "calls": int(row.get("calls") or 0),
                "spendFen": int(row.get("spend_fen") or 0),
            }
    return values


def _recent_tasks(user_id, account_uuid, limit=8):
    rows = excute_sql(
        """
        SELECT task_id, user_id, key_id, mode, filename, request_sha256, idempotency_key,
               status, result_item_id, result_json, error_message, created_at, updated_at, completed_at
        FROM developer_detection_tasks
        WHERE user_id = %s AND account_uuid = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (user_id, account_uuid, max(1, min(int(limit), 50))),
    )
    if rows is None:
        raise BillingError(
            "近期任务暂不可用，请稍后重试",
            code="usage_storage_unavailable",
            status_code=503,
        )
    return [_task_payload(row) for row in rows]


@developer_platform_blueprint.get("/account")
def developer_account():
    user, auth_error = _auth_required()
    if auth_error:
        return auth_error
    try:
        days = int(request.args.get("days", "30"))
    except ValueError:
        return jsonify({"status": "error", "message": "days 必须是整数"}), 400
    if days not in {7, 14, 30, 90}:
        return jsonify({"status": "error", "message": "days 仅支持 7、14、30、90"}), 400
    try:
        account = _account_payload(_account_row(user["Userid"]))
        pricing = _pricing_payload()
        usage = _developer_usage(user["Userid"], days)
        mode_summary = _mode_summary(user["Userid"], days)
        recent_tasks = _recent_tasks(user["Userid"], user.get("account_uuid"))
    except BillingError as exc:
        return _error(str(exc), exc.status_code, exc.code)
    return jsonify({
        "status": "success",
        "account": account,
        "pricing": pricing,
        "modeSummary": mode_summary,
        "usage": usage,
        "recentTasks": recent_tasks,
    })


@developer_platform_blueprint.get("/ledger")
def developer_ledger():
    user, auth_error = _auth_required()
    if auth_error:
        return auth_error
    if not _ensure_developer_account(user["Userid"]):
        return jsonify({"status": "error", "message": "开发者账户初始化失败"}), 503
    try:
        limit = max(1, min(int(request.args.get("limit", "50")), 200))
    except ValueError:
        return jsonify({"status": "error", "message": "limit 必须是整数"}), 400
    rows = excute_sql(
        """
        SELECT id, key_id, task_id, operation_id, business_reference, currency,
               reversal_of_id, entry_type, mode, free_calls_delta, free_calls_after,
               balance_delta_fen, amount_fen, balance_after_fen, note, created_at
        FROM developer_billing_ledger
        WHERE user_id = %s
        ORDER BY created_at DESC, id DESC
        LIMIT %s
        """,
        (user["Userid"], limit),
    )
    if rows is None:
        return _error("账本读取失败，请稍后重试", 503, "ledger_storage_unavailable")
    return jsonify({
        "status": "success",
        "entries": [
            {
                "id": row.get("id"),
                "keyId": row.get("key_id"),
                "taskId": row.get("task_id"),
                "operationId": row.get("operation_id"),
                "businessReference": row.get("business_reference"),
                "currency": row.get("currency") or "CNY",
                "reversalOfId": row.get("reversal_of_id"),
                "type": row.get("entry_type"),
                "mode": row.get("mode"),
                "freeCallsDelta": int(row.get("free_calls_delta") or 0),
                "freeCallsAfter": (
                    int(row.get("free_calls_after"))
                    if row.get("free_calls_after") is not None
                    else None
                ),
                "balanceDeltaFen": int(row.get("balance_delta_fen") or 0),
                "amountFen": int(row.get("amount_fen") or 0),
                "balanceAfterFen": int(row.get("balance_after_fen") or 0),
                "note": row.get("note") or "",
                "createdAt": format_createtime(row.get("created_at")),
            }
            for row in rows
        ],
    })


def _openapi_document():
    origin = request.host_url.rstrip("/")
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "慧鉴AI 图像鉴伪 API",
            "version": "1.0.0",
            "description": "一期开放快速检测与 Swarm 多源复核。任务异步执行，仅成功任务结算额度。",
        },
        "servers": [{"url": f"{origin}/api/openapi/v1"}],
        "security": [{"bearerAuth": []}],
        "paths": {
            "/image-detections": {
                "post": {
                    "summary": "创建图像鉴伪任务",
                    "parameters": [{
                        "name": "Idempotency-Key",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string", "minLength": 8, "maxLength": 128},
                    }],
                    "requestBody": {
                        "required": True,
                        "content": {"multipart/form-data": {"schema": {
                            "type": "object",
                            "required": ["image", "mode"],
                            "properties": {
                                "image": {"type": "string", "format": "binary"},
                                "mode": {"type": "string", "enum": ["fast", "swarm"]},
                            },
                        }}},
                    },
                    "responses": {"202": {"description": "任务已创建"}},
                },
            },
            "/image-detections/{task_id}": {
                "get": {
                    "summary": "查询任务状态",
                    "parameters": [{"name": "task_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "任务状态"}, "404": {"description": "任务不存在或不属于当前账号"}},
                },
            },
            "/image-detections/{task_id}/report": {
                "get": {
                    "summary": "下载 PDF 报告",
                    "parameters": [{"name": "task_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "PDF 报告", "content": {"application/pdf": {}}}},
                },
            },
            "/image-detections/{task_id}/media": {
                "get": {
                    "summary": "下载任务原图",
                    "parameters": [{"name": "task_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "原始图像"}, "404": {"description": "任务或媒体不存在"}},
                },
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "API Key"},
            },
        },
    }


def _strict_optional_quota_limit(payload, field, maximum):
    if field not in payload:
        raise BillingError(f"{field} 不能为空", code="invalid_quota", status_code=400)
    value = payload.get(field)
    if value is None:
        return None
    if type(value) is not int:
        raise BillingError(f"{field} 必须是整数或 null", code="invalid_quota", status_code=400)
    if value < 0 or value > maximum:
        raise BillingError(
            f"{field} 必须在 0 到 {maximum} 之间或设为 null",
            code="invalid_quota",
            status_code=400,
        )
    return value


def admin_update_request_quota(key_id):
    """CAS-update account request limits with idempotent, chained auditing."""
    admin_user, auth_error = _financial_admin_required("quota.manage")
    if auth_error:
        return auth_error
    if not _ensure_developer_platform_tables() or not admin_state.ensure_api_key_quota_storage():
        return _error("API 配额存储初始化失败", 503, "quota_storage_unavailable")

    payload = request.get_json(silent=True) or {}
    try:
        daily_limit = _strict_optional_quota_limit(payload, "dailyLimit", 10_000_000)
        rate_limit = _strict_optional_quota_limit(payload, "rateLimitPerMinute", 100_000)
        expected_daily = _strict_optional_quota_limit(payload, "expectedDailyLimit", 10_000_000)
        expected_rate = _strict_optional_quota_limit(
            payload, "expectedRateLimitPerMinute", 100_000
        )
    except BillingError as exc:
        return _error(str(exc), exc.status_code, exc.code)

    operation_id = str(
        payload.get("operationId") or request.headers.get("Idempotency-Key") or ""
    ).strip()
    if not (
        8 <= len(operation_id) <= 128
        and all(char.isalnum() or char in "-_.:" for char in operation_id)
    ):
        return _error("operationId 必须是 8 到 128 位安全字符", 400, "invalid_operation_id")
    note = str(payload.get("note") or "管理员调整 API 请求配额").strip()[:500]
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "keyId": int(key_id),
                "dailyLimit": daily_limit,
                "rateLimitPerMinute": rate_limit,
                "expectedDailyLimit": expected_daily,
                "expectedRateLimitPerMinute": expected_rate,
                "note": note,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    conn = get_db_connection()
    account_user_id = 0
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT user_id FROM developer_api_keys WHERE id = %s LIMIT 1",
                (key_id,),
            )
            owner = cursor.fetchone()
            if not owner:
                raise BillingError("API Key 不存在", code="api_key_not_found", status_code=404)
            account_user_id = int(owner.get("user_id") or 0)
            cursor.execute(
                "SELECT Userid FROM `user` WHERE Userid = %s FOR UPDATE",
                (account_user_id,),
            )
            if not cursor.fetchone():
                raise BillingError("开发者账号不存在", code="account_not_found", status_code=404)
            cursor.execute(
                "SELECT id, user_id FROM developer_api_keys WHERE id = %s AND user_id = %s FOR UPDATE",
                (key_id, account_user_id),
            )
            if not cursor.fetchone():
                raise BillingError("API Key 不存在", code="api_key_not_found", status_code=404)
            cursor.execute(
                """
                INSERT INTO developer_admin_operations
                    (operation_id, operation_type, user_id, request_sha256)
                VALUES (%s, 'request_quota_update', %s, %s)
                """,
                (operation_id, account_user_id, request_fingerprint),
            )
            cursor.execute(
                """
                SELECT daily_limit, rate_limit_per_minute, scopes, notes
                FROM developer_api_account_quotas
                WHERE user_id = %s
                FOR UPDATE
                """,
                (account_user_id,),
            )
            current = cursor.fetchone() or {}
            before = {
                "dailyLimit": current.get("daily_limit"),
                "rateLimitPerMinute": current.get("rate_limit_per_minute"),
                "scopes": str(current.get("scopes") or ""),
                "notes": str(current.get("notes") or ""),
            }
            if (
                before["dailyLimit"] != expected_daily
                or before["rateLimitPerMinute"] != expected_rate
            ):
                raise BillingError(
                    "API 配额已被其他管理员更新，请刷新后重试",
                    code="quota_conflict",
                    status_code=409,
                )
            after = {
                "dailyLimit": daily_limit,
                "rateLimitPerMinute": rate_limit,
                "scopes": before["scopes"],
                "notes": note,
            }
            cursor.execute(
                """
                INSERT INTO developer_api_account_quotas
                    (user_id, daily_limit, rate_limit_per_minute, scopes, notes)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    daily_limit = VALUES(daily_limit),
                    rate_limit_per_minute = VALUES(rate_limit_per_minute),
                    scopes = VALUES(scopes),
                    notes = VALUES(notes)
                """,
                (
                    account_user_id,
                    daily_limit,
                    rate_limit,
                    after["scopes"],
                    after["notes"],
                ),
            )
            _insert_transactional_admin_audit(
                cursor,
                admin_user,
                "developer.request_quota.update",
                str(account_user_id),
                before=before,
                after=after,
                meta={"keyId": key_id, "operationId": operation_id},
            )
            response_payload = {
                "status": "success",
                "quota": after,
                "scope": "account",
                "userId": account_user_id,
                "keyId": key_id,
                "operationId": operation_id,
                "idempotentReplay": False,
            }
            _store_admin_operation_result(cursor, operation_id, response_payload)
        conn.commit()
    except pymysql.err.IntegrityError as exc:
        conn.rollback()
        if int(exc.args[0] or 0) != 1062:
            print(f"[DEVELOPER FINANCIAL ERROR] request quota integrity failure: {exc}")
            return _error("API 配额更新失败，请稍后重试", 500, "financial_operation_failed")
        operations = excute_sql(
            """
            SELECT operation_type, user_id, request_sha256
            FROM developer_admin_operations WHERE operation_id = %s LIMIT 1
            """,
            (operation_id,),
        )
        if not operations:
            return _error("幂等操作状态读取失败", 503, "operation_state_unavailable")
        operation = operations[0]
        if (
            str(operation.get("operation_type") or "") != "request_quota_update"
            or int(operation.get("user_id") or 0) != account_user_id
            or str(operation.get("request_sha256") or "") != request_fingerprint
        ):
            return _error("operationId 已被其他操作使用", 409, "operation_id_conflict")
        replay = _admin_operation_replay(operation_id)
        if replay is None:
            return _error(
                "幂等操作已完成，但首次响应快照不可用，请联系管理员核对审计记录",
                503,
                "operation_result_unavailable",
            )
        replay_payload, replay_status = replay
        replay_response = jsonify(replay_payload)
        return replay_response if replay_status == 200 else (replay_response, replay_status)
    except BillingError as exc:
        conn.rollback()
        return _error(str(exc), exc.status_code, exc.code)
    except Exception as exc:
        conn.rollback()
        print(f"[DEVELOPER FINANCIAL ERROR] request quota update failed: {exc}")
        return _error("API 配额更新失败，请稍后重试", 500, "financial_operation_failed")
    finally:
        conn.close()
    return jsonify(response_payload)


@developer_platform_blueprint.get("/openapi.json")
def developer_openapi_document():
    _, auth_error = _auth_required()
    if auth_error:
        return auth_error
    return jsonify(_openapi_document())


@developer_admin_blueprint.get("/pricing")
def admin_developer_pricing():
    _, auth_error = _admin_required("billing.view")
    if auth_error:
        return auth_error
    if not _ensure_developer_platform_tables():
        return jsonify({"status": "error", "message": "开发者计费表初始化失败"}), 503
    return jsonify({"status": "success", "pricing": _pricing_payload()})


@developer_admin_blueprint.post("/pricing")
def admin_update_developer_pricing():
    admin_user, auth_error = _financial_admin_required("billing.pricing")
    if auth_error:
        return auth_error
    if not _ensure_developer_platform_tables():
        return jsonify({"status": "error", "message": "开发者计费表初始化失败"}), 503
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in {"fast", "swarm"}:
        return jsonify({"status": "error", "message": "mode 仅支持 fast 或 swarm"}), 400
    raw_price_fen = payload.get("unitPriceFen", 0)
    if type(raw_price_fen) is not int:
        return jsonify({"status": "error", "message": "unitPriceFen 必须是整数"}), 400
    price_fen = raw_price_fen
    if price_fen < 0 or price_fen > DEVELOPER_MAX_UNIT_PRICE_FEN:
        return jsonify({
            "status": "error",
            "message": f"unitPriceFen 必须在 0 到 {DEVELOPER_MAX_UNIT_PRICE_FEN} 之间",
        }), 400
    if not isinstance(payload.get("enabled"), bool):
        return jsonify({"status": "error", "message": "enabled 必须是布尔值"}), 400
    enabled = payload["enabled"]
    if enabled and price_fen <= 0:
        return jsonify({"status": "error", "message": "启用付费计价时，单价必须大于 0 分"}), 400
    raw_expected_price_fen = payload.get("expectedUnitPriceFen")
    if type(raw_expected_price_fen) is not int:
        return jsonify({"status": "error", "message": "expectedUnitPriceFen 必须是整数"}), 400
    expected_price_fen = raw_expected_price_fen
    if not isinstance(payload.get("expectedEnabled"), bool):
        return jsonify({"status": "error", "message": "expectedEnabled 必须是布尔值"}), 400
    expected_enabled = payload["expectedEnabled"]
    operation_id = str(
        payload.get("operationId") or request.headers.get("Idempotency-Key") or ""
    ).strip()
    if not (
        8 <= len(operation_id) <= 128
        and all(char.isalnum() or char in "-_.:" for char in operation_id)
    ):
        return jsonify({"status": "error", "message": "operationId 必须是 8 到 128 位安全字符"}), 400
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "mode": mode,
                "unitPriceFen": price_fen,
                "enabled": enabled,
                "expectedUnitPriceFen": expected_price_fen,
                "expectedEnabled": expected_enabled,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO developer_admin_operations
                    (operation_id, operation_type, user_id, request_sha256)
                VALUES (%s, 'pricing_update', 0, %s)
                """,
                (operation_id, request_fingerprint),
            )
            cursor.execute(
                """
                SELECT mode, display_name, unit_price_fen, enabled, updated_at
                FROM developer_pricing WHERE mode = %s FOR UPDATE
                """,
                (mode,),
            )
            row = cursor.fetchone()
            if not row:
                raise BillingError("计费配置不存在", code="pricing_not_found", status_code=404)
            before = _pricing_payload([row])[0]
            if (
                before["unitPriceFen"] != expected_price_fen
                or before["enabled"] is not expected_enabled
            ):
                raise BillingError(
                    "计费配置已被其他管理员更新，请刷新后重试",
                    code="pricing_conflict",
                    status_code=409,
                )
            cursor.execute(
                "UPDATE developer_pricing SET unit_price_fen = %s, enabled = %s WHERE mode = %s",
                (price_fen, int(enabled), mode),
            )
            cursor.execute(
                """
                SELECT mode, display_name, unit_price_fen, enabled, updated_at
                FROM developer_pricing WHERE mode = %s
                """,
                (mode,),
            )
            after = _pricing_payload([cursor.fetchone()])[0]
            _insert_transactional_admin_audit(
                cursor,
                admin_user,
                "developer.pricing.update",
                mode,
                before=before,
                after=after,
                meta={"operationId": operation_id},
            )
            response_payload = {
                "status": "success",
                "pricing": after,
                "operationId": operation_id,
                "idempotentReplay": False,
            }
            _store_admin_operation_result(cursor, operation_id, response_payload)
        conn.commit()
    except pymysql.err.IntegrityError as exc:
        conn.rollback()
        if int(exc.args[0] or 0) != 1062:
            print(f"[DEVELOPER FINANCIAL ERROR] pricing integrity failure: {exc}")
            return jsonify({
                "status": "error",
                "code": "financial_operation_failed",
                "message": "计费配置更新失败，请稍后重试",
            }), 500
        operations = excute_sql(
            """
            SELECT operation_type, user_id, request_sha256
            FROM developer_admin_operations WHERE operation_id = %s LIMIT 1
            """,
            (operation_id,),
        )
        if not operations:
            return jsonify({"status": "error", "message": "幂等操作状态读取失败"}), 503
        operation = operations[0]
        if (
            str(operation.get("operation_type") or "") != "pricing_update"
            or int(operation.get("user_id") or 0) != 0
            or str(operation.get("request_sha256") or "") != request_fingerprint
        ):
            return jsonify({"status": "error", "message": "operationId 已被其他操作使用"}), 409
        replay = _admin_operation_replay(operation_id)
        if replay is None:
            return jsonify({
                "status": "error",
                "code": "operation_result_unavailable",
                "message": "幂等操作已完成，但首次响应快照不可用，请联系管理员核对审计记录",
            }), 503
        replay_payload, replay_status = replay
        replay_response = jsonify(replay_payload)
        return replay_response if replay_status == 200 else (replay_response, replay_status)
    except BillingError as exc:
        conn.rollback()
        return jsonify({"status": "error", "code": exc.code, "message": str(exc)}), exc.status_code
    except Exception as exc:
        conn.rollback()
        print(f"[DEVELOPER FINANCIAL ERROR] pricing update failed: {exc}")
        return jsonify({
            "status": "error",
            "code": "financial_operation_failed",
            "message": "计费配置更新失败，请稍后重试",
        }), 500
    finally:
        conn.close()
    return jsonify(response_payload)


@developer_admin_blueprint.post("/accounts/<int:user_id>/adjust")
def admin_adjust_developer_account(user_id):
    admin_user, auth_error = _financial_admin_required("billing.adjust")
    if auth_error:
        return auth_error
    payload = request.get_json(silent=True) or {}
    raw_balance_delta = payload.get("balanceDeltaFen", 0)
    raw_free_delta = payload.get("freeTotalDelta", 0)
    if type(raw_balance_delta) is not int or type(raw_free_delta) is not int:
        return jsonify({"status": "error", "message": "调整值必须是整数"}), 400
    balance_delta = raw_balance_delta
    free_delta = raw_free_delta
    if balance_delta == 0 and free_delta == 0:
        return jsonify({"status": "error", "message": "至少提供一项非零调整"}), 400
    if abs(balance_delta) > DEVELOPER_MAX_ADMIN_BALANCE_DELTA_FEN:
        return jsonify({"status": "error", "message": "余额调整值超过单次允许范围"}), 400
    if abs(free_delta) > DEVELOPER_MAX_ADMIN_FREE_DELTA:
        return jsonify({"status": "error", "message": "赠送额度调整值超过单次允许范围"}), 400
    operation_id = str(
        payload.get("operationId") or request.headers.get("Idempotency-Key") or ""
    ).strip()
    if not (
        8 <= len(operation_id) <= 128
        and all(char.isalnum() or char in "-_.:" for char in operation_id)
    ):
        return jsonify({"status": "error", "message": "operationId 必须是 8 到 128 位安全字符"}), 400
    users = excute_sql("SELECT Userid FROM user WHERE Userid = %s LIMIT 1", (user_id,))
    if users is None:
        return jsonify({"status": "error", "message": "用户信息读取失败"}), 500
    if not users:
        return jsonify({"status": "error", "message": "用户不存在"}), 404
    note = str(payload.get("note") or "管理员手工调整").strip()[:500]
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "userId": user_id,
                "balanceDeltaFen": balance_delta,
                "freeTotalDelta": free_delta,
                "note": note,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if not _ensure_developer_account(user_id):
        return jsonify({"status": "error", "message": "开发者账户初始化失败"}), 503
    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO developer_admin_operations
                    (operation_id, operation_type, user_id, request_sha256)
                VALUES (%s, 'account_adjustment', %s, %s)
                """,
                (operation_id, user_id, request_fingerprint),
            )
            cursor.execute(
                """
                SELECT user_id, status, free_total, free_used, free_reserved,
                       balance_fen, balance_reserved_fen, created_at, updated_at
                FROM developer_accounts WHERE user_id = %s FOR UPDATE
                """,
                (user_id,),
            )
            account = cursor.fetchone()
            if not account:
                raise BillingError("开发者账户读取失败")
            before = _account_payload(account)
            next_free_total = int(account.get("free_total") or 0) + free_delta
            next_balance = int(account.get("balance_fen") or 0) + balance_delta
            if next_free_total < int(account.get("free_used") or 0) + int(account.get("free_reserved") or 0):
                raise BillingError("赠送总额不能低于已使用和已预占额度", code="invalid_adjustment", status_code=400)
            if next_balance < int(account.get("balance_reserved_fen") or 0):
                raise BillingError("余额不能低于已预占金额", code="invalid_adjustment", status_code=400)
            cursor.execute(
                "UPDATE developer_accounts SET free_total = %s, balance_fen = %s WHERE user_id = %s",
                (next_free_total, next_balance, user_id),
            )
            cursor.execute(
                """
                INSERT INTO developer_billing_ledger
                    (user_id, operation_id, business_reference, currency, entry_type,
                     free_calls_delta, free_calls_after, balance_delta_fen,
                     amount_fen, balance_after_fen, note)
                VALUES (%s, %s, %s, 'CNY', 'admin_adjustment', %s, %s, %s, 0, %s, %s)
                """,
                (
                    user_id,
                    operation_id,
                    f"admin-adjustment:{operation_id}",
                    free_delta,
                    next_free_total - int(account.get("free_used") or 0),
                    balance_delta,
                    next_balance,
                    note,
                ),
            )
            after_row = dict(account)
            after_row["free_total"] = next_free_total
            after_row["balance_fen"] = next_balance
            after = _account_payload(after_row)
            _insert_transactional_admin_audit(
                cursor,
                admin_user,
                "developer.account.adjust",
                str(user_id),
                before=before,
                after=after,
                meta={"note": note, "operationId": operation_id},
            )
            response_payload = {
                "status": "success",
                "account": after,
                "operationId": operation_id,
                "idempotentReplay": False,
            }
            _store_admin_operation_result(cursor, operation_id, response_payload)
        conn.commit()
    except pymysql.err.IntegrityError as exc:
        conn.rollback()
        if int(exc.args[0] or 0) != 1062:
            print(f"[DEVELOPER FINANCIAL ERROR] account adjustment integrity failure: {exc}")
            return jsonify({
                "status": "error",
                "code": "financial_operation_failed",
                "message": "账户调整失败，请稍后重试",
            }), 500
        operations = excute_sql(
            """
            SELECT operation_type, user_id, request_sha256
            FROM developer_admin_operations WHERE operation_id = %s LIMIT 1
            """,
            (operation_id,),
        )
        if not operations:
            return jsonify({"status": "error", "message": "幂等操作状态读取失败"}), 503
        operation = operations[0]
        if (
            str(operation.get("operation_type") or "") != "account_adjustment"
            or int(operation.get("user_id") or 0) != int(user_id)
            or str(operation.get("request_sha256") or "") != request_fingerprint
        ):
            return jsonify({"status": "error", "message": "operationId 已被其他操作使用"}), 409
        replay = _admin_operation_replay(operation_id)
        if replay is None:
            return jsonify({
                "status": "error",
                "code": "operation_result_unavailable",
                "message": "幂等操作已完成，但首次响应快照不可用，请联系管理员核对审计记录",
            }), 503
        replay_payload, replay_status = replay
        replay_response = jsonify(replay_payload)
        return replay_response if replay_status == 200 else (replay_response, replay_status)
    except BillingError as exc:
        conn.rollback()
        return jsonify({"status": "error", "message": str(exc)}), exc.status_code
    except Exception as exc:
        conn.rollback()
        print(f"[DEVELOPER FINANCIAL ERROR] account adjustment failed: {exc}")
        return jsonify({
            "status": "error",
            "code": "financial_operation_failed",
            "message": "账户调整失败，请稍后重试",
        }), 500
    finally:
        conn.close()
    return jsonify(response_payload)


@developer_admin_blueprint.get("/accounts/<int:user_id>")
def admin_developer_account(user_id):
    _, auth_error = _admin_required("billing.view")
    if auth_error:
        return auth_error
    users = excute_sql("SELECT Userid FROM user WHERE Userid = %s LIMIT 1", (user_id,))
    if users is None:
        return jsonify({"status": "error", "message": "用户信息读取失败"}), 500
    if not users:
        return jsonify({"status": "error", "message": "用户不存在"}), 404
    try:
        account = _account_payload(_account_row(user_id))
    except BillingError as exc:
        return jsonify({"status": "error", "message": str(exc)}), exc.status_code
    return jsonify({"status": "success", "account": account})


@developer_admin_blueprint.route("/accounts/<int:user_id>/quota", methods=["PATCH", "POST"])
def admin_set_developer_quota(user_id):
    admin_user, auth_error = _financial_admin_required("billing.adjust")
    if auth_error:
        return auth_error
    payload = request.get_json(silent=True) or {}
    raw_remaining_calls = payload.get("remainingCalls")
    if type(raw_remaining_calls) is not int:
        return jsonify({"status": "error", "message": "remainingCalls 必须是整数"}), 400
    remaining_calls = raw_remaining_calls
    if remaining_calls < 0 or remaining_calls > 10_000_000:
        return jsonify({"status": "error", "message": "remainingCalls 必须在 0 到 10000000 之间"}), 400
    operation_id = str(
        payload.get("operationId") or request.headers.get("Idempotency-Key") or ""
    ).strip()
    if not (
        8 <= len(operation_id) <= 128
        and all(char.isalnum() or char in "-_.:" for char in operation_id)
    ):
        return jsonify({"status": "error", "message": "operationId 必须是 8 到 128 位安全字符"}), 400

    users = excute_sql("SELECT Userid FROM user WHERE Userid = %s LIMIT 1", (user_id,))
    if users is None:
        return jsonify({"status": "error", "message": "用户信息读取失败"}), 500
    if not users:
        return jsonify({"status": "error", "message": "用户不存在"}), 404
    if not _ensure_developer_account(user_id):
        return jsonify({"status": "error", "message": "开发者账户初始化失败"}), 503

    note = str(payload.get("note") or "管理员设置剩余调用次数").strip()[:500]
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "userId": user_id,
                "remainingCalls": remaining_calls,
                "note": note,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO developer_admin_operations
                    (operation_id, operation_type, user_id, request_sha256)
                VALUES (%s, 'quota_set', %s, %s)
                """,
                (operation_id, user_id, request_fingerprint),
            )
            cursor.execute(
                """
                SELECT user_id, status, free_total, free_used, free_reserved,
                       balance_fen, balance_reserved_fen, created_at, updated_at
                FROM developer_accounts
                WHERE user_id = %s
                FOR UPDATE
                """,
                (user_id,),
            )
            account = cursor.fetchone()
            if not account:
                raise BillingError("开发者账户读取失败")
            before = _account_payload(account)
            next_free_total = (
                int(account.get("free_used") or 0)
                + int(account.get("free_reserved") or 0)
                + remaining_calls
            )
            free_delta = next_free_total - int(account.get("free_total") or 0)
            if free_delta:
                cursor.execute(
                    "UPDATE developer_accounts SET free_total = %s WHERE user_id = %s",
                    (next_free_total, user_id),
                )
                cursor.execute(
                    """
                    INSERT INTO developer_billing_ledger
                        (user_id, operation_id, business_reference, currency, entry_type,
                         free_calls_delta, free_calls_after, balance_delta_fen,
                         amount_fen, balance_after_fen, note)
                    VALUES (%s, %s, %s, 'CNY', 'admin_quota_set', %s, %s, 0, 0, %s, %s)
                    """,
                    (
                        user_id,
                        operation_id,
                        f"admin-quota:{operation_id}",
                        free_delta,
                        next_free_total - int(account.get("free_used") or 0),
                        int(account.get("balance_fen") or 0),
                        note,
                    ),
                )
            after_row = dict(account)
            after_row["free_total"] = next_free_total
            after = _account_payload(after_row)
            _insert_transactional_admin_audit(
                cursor,
                admin_user,
                "developer.account.quota.set",
                str(user_id),
                before=before,
                after=after,
                meta={"note": note, "operationId": operation_id},
            )
            response_payload = {
                "status": "success",
                "account": after,
                "operationId": operation_id,
                "idempotentReplay": False,
            }
            _store_admin_operation_result(cursor, operation_id, response_payload)
        conn.commit()
    except pymysql.err.IntegrityError as exc:
        conn.rollback()
        if int(exc.args[0] or 0) != 1062:
            print(f"[DEVELOPER FINANCIAL ERROR] quota update integrity failure: {exc}")
            return jsonify({
                "status": "error",
                "code": "financial_operation_failed",
                "message": "调用次数设置失败，请稍后重试",
            }), 500
        operations = excute_sql(
            """
            SELECT operation_type, user_id, request_sha256
            FROM developer_admin_operations WHERE operation_id = %s LIMIT 1
            """,
            (operation_id,),
        )
        if not operations:
            return jsonify({"status": "error", "message": "幂等操作状态读取失败"}), 503
        operation = operations[0]
        if (
            str(operation.get("operation_type") or "") != "quota_set"
            or int(operation.get("user_id") or 0) != int(user_id)
            or str(operation.get("request_sha256") or "") != request_fingerprint
        ):
            return jsonify({"status": "error", "message": "operationId 已被其他操作使用"}), 409
        replay = _admin_operation_replay(operation_id)
        if replay is None:
            return jsonify({
                "status": "error",
                "code": "operation_result_unavailable",
                "message": "幂等操作已完成，但首次响应快照不可用，请联系管理员核对审计记录",
            }), 503
        replay_payload, replay_status = replay
        replay_response = jsonify(replay_payload)
        return replay_response if replay_status == 200 else (replay_response, replay_status)
    except BillingError as exc:
        conn.rollback()
        return jsonify({"status": "error", "message": str(exc)}), exc.status_code
    except Exception as exc:
        conn.rollback()
        print(f"[DEVELOPER FINANCIAL ERROR] quota update failed: {exc}")
        return jsonify({
            "status": "error",
            "code": "financial_operation_failed",
            "message": "调用次数设置失败，请稍后重试",
        }), 500
    finally:
        conn.close()
    return jsonify(response_payload)
