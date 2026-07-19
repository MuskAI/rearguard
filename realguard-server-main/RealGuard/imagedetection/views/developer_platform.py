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

from PIL import Image, UnidentifiedImageError
from flask import Blueprint, Response, jsonify, request

from imagedetection.views import admin_state, reporting
from imagedetection.views.admin import _admin_required, _audit
from imagedetection.views.api import (
    _auth_required,
    _developer_key_required,
    _developer_scopes,
    _developer_usage_from_v1,
    _developer_usage_from_v2,
    _empty_developer_usage,
    _ensure_developer_usage_table,
    _merge_developer_usage,
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
    int(os.environ.get("REALGUARD_DEVELOPER_TASK_MAX_PENDING", "500")),
)
DEVELOPER_SPOOL_MAX_BYTES = max(
    DEVELOPER_MAX_IMAGE_BYTES,
    int(os.environ.get("REALGUARD_DEVELOPER_SPOOL_MAX_BYTES", str(10 * 1024 * 1024 * 1024))),
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


def _error(message, status_code, code):
    return jsonify({"error": {"code": code, "message": message}}), status_code


def _update_job_cache(task_id, payload):
    try:
        return admin_state.update_detection_job(task_id, payload)
    except Exception as exc:
        print(f"[DEVELOPER TASK CACHE ERROR] update {task_id}: {exc}")
        return None


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
        if not stat.S_ISREG(file_stat.st_mode):
            raise TaskSpoolError("task spool file is not regular")
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


def _queue_capacity_error(incoming_size):
    rows = excute_sql(
        """
        SELECT COUNT(*) AS pending_count, COALESCE(SUM(spool_size), 0) AS pending_bytes
        FROM developer_detection_tasks
        WHERE status IN ('preparing', 'queued', 'running')
        """
    )
    if rows is None:
        raise TaskRecoveryError("failed to inspect developer task queue capacity")
    row = rows[0] if rows else {}
    pending_count = int(row.get("pending_count") or 0)
    pending_bytes = int(row.get("pending_bytes") or 0)
    if pending_count >= DEVELOPER_TASK_MAX_PENDING:
        return "检测队列已满，请稍后重试"
    if pending_bytes + int(incoming_size or 0) > DEVELOPER_SPOOL_MAX_BYTES:
        return "检测队列存储空间已达到安全上限，请稍后重试"
    return None


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
              PRIMARY KEY (user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS developer_pricing (
              mode VARCHAR(16) NOT NULL,
              display_name VARCHAR(64) NOT NULL,
              unit_price_fen INT NOT NULL DEFAULT 0,
              enabled TINYINT(1) NOT NULL DEFAULT 0,
              updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (mode)
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
              KEY idx_developer_reservations_status (status, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            """
            CREATE TABLE IF NOT EXISTS developer_billing_ledger (
              id BIGINT NOT NULL AUTO_INCREMENT,
              user_id INT NOT NULL,
              key_id BIGINT NULL,
              task_id VARCHAR(64) NULL,
              entry_type VARCHAR(32) NOT NULL,
              mode VARCHAR(16) NULL,
              free_calls_delta INT NOT NULL DEFAULT 0,
              balance_delta_fen BIGINT NOT NULL DEFAULT 0,
              amount_fen INT NOT NULL DEFAULT 0,
              balance_after_fen BIGINT NOT NULL DEFAULT 0,
              note VARCHAR(500) NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_developer_ledger_user_created (user_id, created_at),
              KEY idx_developer_ledger_task (task_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
        )
        for statement in statements:
            if excute_sql(statement, fetch=False) is None:
                return False
        if not _ensure_developer_usage_table():
            return False
        if not _ensure_task_lease_schema():
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
            idempotency_columns = [str(row.get("COLUMN_NAME") or "") for row in cursor.fetchall()]
            if idempotency_columns != ["account_uuid", "idempotency_key"]:
                if idempotency_columns:
                    cursor.execute(
                        "ALTER TABLE developer_detection_tasks DROP INDEX uk_developer_task_idempotency"
                    )
                cursor.execute(
                    """
                    ALTER TABLE developer_detection_tasks
                    ADD UNIQUE INDEX uk_developer_task_idempotency (account_uuid, idempotency_key)
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


def _ensure_developer_account(user_id):
    if not _ensure_developer_platform_tables():
        return False
    return excute_sql(
        "INSERT IGNORE INTO developer_accounts (user_id, free_total) VALUES (%s, %s)",
        (user_id, DEVELOPER_FREE_CALLS),
        fetch=False,
    ) is not None


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
                "SELECT balance_fen FROM developer_accounts WHERE user_id = %s FOR UPDATE",
                (user_id,),
            )
            account = cursor.fetchone() or {"balance_fen": 0}
            cursor.execute(
                """
                SELECT prompt_tokens, completion_tokens, total_tokens
                FROM developer_detection_tasks
                WHERE task_id = %s
                FOR UPDATE
                """,
                (task_id,),
            )
            usage = cursor.fetchone() or {}
            if reservation.get("source") == "free":
                cursor.execute(
                    """
                    UPDATE developer_accounts
                    SET free_reserved = GREATEST(0, free_reserved - 1), free_used = free_used + 1
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                entry_type = "detection_free"
                free_delta = -1
                balance_delta = 0
                balance_after = int(account.get("balance_fen") or 0)
            else:
                cursor.execute(
                    """
                    UPDATE developer_accounts
                    SET balance_reserved_fen = GREATEST(0, balance_reserved_fen - %s),
                        balance_fen = balance_fen - %s
                    WHERE user_id = %s
                    """,
                    (amount_fen, amount_fen, user_id),
                )
                entry_type = "detection_charge"
                free_delta = 0
                balance_delta = -amount_fen
                balance_after = int(account.get("balance_fen") or 0) - amount_fen
            cursor.execute(
                """
                UPDATE developer_billing_reservations
                SET status = 'settled', settled_at = NOW()
                WHERE task_id = %s
                """,
                (task_id,),
            )
            cursor.execute(
                """
                INSERT INTO developer_billing_ledger
                    (user_id, key_id, task_id, entry_type, mode, free_calls_delta,
                     balance_delta_fen, amount_fen, balance_after_fen, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    reservation.get("key_id"),
                    task_id,
                    entry_type,
                    reservation.get("mode"),
                    free_delta,
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
                     prompt_tokens, completion_tokens, total_tokens)
                VALUES (%s, %s, %s, 'openapi', %s, %s, 200, %s, %s, %s)
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
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        print(f"[DEVELOPER BILLING ERROR] settle {task_id}: {exc}")
        return False
    finally:
        conn.close()


def _release_billing(task_id, note="检测未成功，释放预占额度"):
    if not _ensure_developer_platform_tables():
        return False
    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, source, amount_fen, status
                FROM developer_billing_reservations
                WHERE task_id = %s FOR UPDATE
                """,
                (task_id,),
            )
            reservation = cursor.fetchone()
            if not reservation or reservation.get("status") != "reserved":
                conn.rollback()
                return False
            if reservation.get("source") == "free":
                cursor.execute(
                    "UPDATE developer_accounts SET free_reserved = GREATEST(0, free_reserved - 1) WHERE user_id = %s",
                    (reservation["user_id"],),
                )
            else:
                cursor.execute(
                    """
                    UPDATE developer_accounts
                    SET balance_reserved_fen = GREATEST(0, balance_reserved_fen - %s)
                    WHERE user_id = %s
                    """,
                    (int(reservation.get("amount_fen") or 0), reservation["user_id"]),
                )
            cursor.execute(
                """
                UPDATE developer_billing_reservations
                SET status = 'released', released_at = NOW()
                WHERE task_id = %s
                """,
                (task_id,),
            )
        conn.commit()
        return True
    except Exception as exc:
        conn.rollback()
        print(f"[DEVELOPER BILLING ERROR] release {task_id}: {exc}; {note}")
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

            cursor.execute(
                f"""
                UPDATE developer_detection_tasks
                SET status = 'failed', error_message = %s, completed_at = NOW(),
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


def _reconcile_success_reservation(task_id):
    if _settle_billing(task_id):
        settled_now = True
    else:
        status = _reservation_status_strict(task_id)
        if status != "settled":
            raise TaskRecoveryError(
                f"successful task {task_id} billing settlement remains {status or 'unknown'}"
            )
        settled_now = False
    try:
        _update_job_cache(
            task_id,
            {"status": "success", "progress": 100, "summary": "检测完成，计费对账已完成"},
        )
    except Exception as exc:
        print(f"[DEVELOPER TASK RECOVERY ERROR] settled {task_id}, but job cache update failed: {exc}")
    return settled_now


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
                  AND reservation.status = 'reserved'
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
            else:
                changed = _expire_task_lease(task_id)
            if changed:
                recovered += 1
        except TaskRecoveryError as exc:
            print(f"[DEVELOPER TASK RECOVERY ERROR] isolated expired task {task_id}: {exc}")
    for task_id in success_task_ids:
        try:
            if _reconcile_success_reservation(task_id):
                recovered += 1
        except TaskRecoveryError as exc:
            print(f"[DEVELOPER TASK RECOVERY ERROR] isolated successful task {task_id}: {exc}")
    for task_id in anomalous_task_ids:
        try:
            if _terminalize_anomalous_reservation(task_id):
                recovered += 1
        except TaskRecoveryError as exc:
            print(f"[DEVELOPER TASK RECOVERY ERROR] isolated billing anomaly {task_id}: {exc}")
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
    return public_payload


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
    final_label = str(record.get("aigc") or "").strip() or (
        "AI生成图像" if fake_probability >= 0.5 else "真实图像"
    )
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
        payload = _normalize_task_result_filename(_recovered_business_payload(record), task)
        _record_task_effect(task, user_info, payload)
    return payload


def _task_payload(row):
    job = admin_state.get_detection_job(row["task_id"])
    public_job = detection._public_detection_job(job) if job else None
    # SQL is the authoritative task state; the JSON job cache is progress-only.
    internal_status = row.get("status") or "queued"
    status = "queued" if internal_status == "preparing" else internal_status
    progress = int((public_job or {}).get("progress") or (100 if status in {"success", "failed", "rejected"} else 0))
    result = _stored_task_result(row) if status == "success" else None
    error_message = row.get("error_message") or ((public_job or {}).get("error") if status in {"failed", "rejected"} else "") or ""
    task_id = row["task_id"]
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
        "summary": (public_job or {}).get("summary") or "",
        "createdAt": format_createtime(row.get("created_at")),
        "updatedAt": format_createtime(row.get("updated_at")),
        "completedAt": format_createtime(row.get("completed_at")),
        "result": public_result,
        "error": {"code": "detection_failed", "message": error_message} if status in {"failed", "rejected"} else None,
        "billing": _reservation_payload(task_id),
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
            last_heartbeat_at = NOW(6), attempt_count = attempt_count + 1
        WHERE task_id = %s
          AND status = 'queued'
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
            last_heartbeat_at = NOW(6), attempt_count = attempt_count + 1
        WHERE status = 'queued'
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
        settled_now = _settle_billing(task_id)
        return settled_now

    message = (payload or {}).get("message") if isinstance(payload, dict) else ""
    message = str(message or f"HTTP {status_code}")[:500]
    try:
        return _fail_task_and_release(task_id, message, lease_owner=lease_owner)
    except TaskRecoveryError as exc:
        print(f"[DEVELOPER TASK RECOVERY ERROR] {exc}")
        return False


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


def _run_worker_maintenance():
    recovered = _reconcile_expired_tasks()
    cleaned = _cleanup_terminal_spools()
    orphans = _cleanup_orphan_spool_files()
    return {"recovered": recovered, "cleaned": cleaned, "orphans": orphans}


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
    if scope in scopes or "detect" in scopes:
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
    actor, auth_error = _developer_key_required()
    if auth_error:
        return auth_error
    mode = str(request.form.get("mode") or request.args.get("mode") or "fast").strip().lower()
    if mode not in {"fast", "swarm"}:
        return _error("mode 仅支持 fast 或 swarm", 400, "invalid_mode")
    scope_error = _require_scope(actor, f"image:{mode}")
    if scope_error:
        return scope_error
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
    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if len(idempotency_key) > 128:
        return _error("Idempotency-Key 不能超过 128 个字符", 400, "invalid_idempotency_key")
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
            idempotency_key or None,
        ),
        fetch=False,
    )
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
        _release_task_daily_quota(task_id)
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
        _release_task_daily_quota(task_id)
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
    actor, auth_error = _developer_key_required()
    if auth_error:
        return auth_error
    try:
        _maybe_reconcile_expired_tasks()
    except TaskRecoveryError as exc:
        return _task_recovery_error_response(exc)
    row = _task_row_for_user(task_id, actor["user_id"], actor.get("account_uuid"))
    if not row:
        return _error("任务不存在", 404, "task_not_found")
    if row.get("status") == "success":
        try:
            reservation_status = _reservation_status_strict(task_id)
            if reservation_status == "reserved":
                _reconcile_success_reservation(task_id)
            elif reservation_status != "settled":
                raise TaskRecoveryError(
                    f"successful task {task_id} has invalid billing status {reservation_status or 'unknown'}"
                )
        except TaskRecoveryError as exc:
            return _task_recovery_error_response(exc)
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
    actor, auth_error = _developer_key_required()
    if auth_error:
        return auth_error
    scope_error = _require_scope(actor, "reports")
    if scope_error:
        return scope_error
    row = _task_row_for_user(task_id, actor["user_id"], actor.get("account_uuid"))
    if not row:
        return _error("任务不存在", 404, "task_not_found")
    if row.get("status") != "success" or not row.get("result_item_id"):
        return _error("任务尚未成功完成", 409, "task_not_complete")
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
    actor, auth_error = _developer_key_required()
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
    item = _owned_detection_item(actor, row["result_item_id"])
    if not item:
        return _error("媒体记录不存在", 404, "media_not_found")
    return _serve_detection_media_item("image", item)


def _developer_usage(user_id, days):
    try:
        v1_usage = _developer_usage_from_v1(user_id, days)
    except Exception:
        v1_usage = _empty_developer_usage(days)
    try:
        v2_usage = _developer_usage_from_v2(user_id, days)
    except Exception:
        v2_usage = _empty_developer_usage(days)
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
    ) or []
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
    ) or []
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
    except BillingError as exc:
        return jsonify({"status": "error", "message": str(exc)}), exc.status_code
    usage = _developer_usage(user["Userid"], days)
    return jsonify({
        "status": "success",
        "account": account,
        "pricing": pricing,
        "modeSummary": _mode_summary(user["Userid"], days),
        "usage": usage,
        "recentTasks": _recent_tasks(user["Userid"], user.get("account_uuid")),
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
        SELECT id, key_id, task_id, entry_type, mode, free_calls_delta,
               balance_delta_fen, amount_fen, balance_after_fen, note, created_at
        FROM developer_billing_ledger
        WHERE user_id = %s
        ORDER BY id DESC
        LIMIT %s
        """,
        (user["Userid"], limit),
    ) or []
    return jsonify({
        "status": "success",
        "entries": [
            {
                "id": row.get("id"),
                "keyId": row.get("key_id"),
                "taskId": row.get("task_id"),
                "type": row.get("entry_type"),
                "mode": row.get("mode"),
                "freeCallsDelta": int(row.get("free_calls_delta") or 0),
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
                        "required": False,
                        "schema": {"type": "string", "maxLength": 128},
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


@developer_platform_blueprint.get("/openapi.json")
def developer_openapi_document():
    _, auth_error = _auth_required()
    if auth_error:
        return auth_error
    return jsonify(_openapi_document())


@developer_admin_blueprint.get("/pricing")
def admin_developer_pricing():
    _, auth_error = _admin_required("view")
    if auth_error:
        return auth_error
    if not _ensure_developer_platform_tables():
        return jsonify({"status": "error", "message": "开发者计费表初始化失败"}), 503
    return jsonify({"status": "success", "pricing": _pricing_payload()})


@developer_admin_blueprint.post("/pricing")
def admin_update_developer_pricing():
    admin_user, auth_error = _admin_required("api_key.manage")
    if auth_error:
        return auth_error
    if not _ensure_developer_platform_tables():
        return jsonify({"status": "error", "message": "开发者计费表初始化失败"}), 503
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in {"fast", "swarm"}:
        return jsonify({"status": "error", "message": "mode 仅支持 fast 或 swarm"}), 400
    try:
        price_fen = max(0, int(payload.get("unitPriceFen", 0)))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "unitPriceFen 必须是整数"}), 400
    enabled = bool(payload.get("enabled"))
    if enabled and price_fen <= 0:
        return jsonify({"status": "error", "message": "启用付费计价时，单价必须大于 0 分"}), 400
    before = next((row for row in _pricing_payload() if row["mode"] == mode), None)
    updated = excute_sql(
        "UPDATE developer_pricing SET unit_price_fen = %s, enabled = %s WHERE mode = %s",
        (price_fen, int(enabled), mode),
        fetch=False,
    )
    if updated is None:
        return jsonify({"status": "error", "message": "计费配置更新失败"}), 500
    after = next((row for row in _pricing_payload() if row["mode"] == mode), None)
    _audit(admin_user, "developer.pricing.update", mode, before=before, after=after)
    return jsonify({"status": "success", "pricing": after})


@developer_admin_blueprint.post("/accounts/<int:user_id>/adjust")
def admin_adjust_developer_account(user_id):
    admin_user, auth_error = _admin_required("api_key.manage")
    if auth_error:
        return auth_error
    payload = request.get_json(silent=True) or {}
    try:
        balance_delta = int(payload.get("balanceDeltaFen") or 0)
        free_delta = int(payload.get("freeTotalDelta") or 0)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "调整值必须是整数"}), 400
    if balance_delta == 0 and free_delta == 0:
        return jsonify({"status": "error", "message": "至少提供一项非零调整"}), 400
    users = excute_sql("SELECT Userid FROM user WHERE Userid = %s LIMIT 1", (user_id,))
    if users is None:
        return jsonify({"status": "error", "message": "用户信息读取失败"}), 500
    if not users:
        return jsonify({"status": "error", "message": "用户不存在"}), 404
    note = str(payload.get("note") or "管理员手工调整").strip()[:500]
    if not _ensure_developer_account(user_id):
        return jsonify({"status": "error", "message": "开发者账户初始化失败"}), 503
    before = _account_payload(_account_row(user_id))
    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT free_total, free_used, free_reserved, balance_fen, balance_reserved_fen
                FROM developer_accounts WHERE user_id = %s FOR UPDATE
                """,
                (user_id,),
            )
            account = cursor.fetchone()
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
                    (user_id, entry_type, free_calls_delta, balance_delta_fen,
                     amount_fen, balance_after_fen, note)
                VALUES (%s, 'admin_adjustment', %s, %s, 0, %s, %s)
                """,
                (user_id, free_delta, balance_delta, next_balance, note),
            )
        conn.commit()
    except BillingError as exc:
        conn.rollback()
        return jsonify({"status": "error", "message": str(exc)}), exc.status_code
    except Exception as exc:
        conn.rollback()
        return jsonify({"status": "error", "message": f"账户调整失败: {exc}"}), 500
    finally:
        conn.close()
    after = _account_payload(_account_row(user_id))
    _audit(admin_user, "developer.account.adjust", str(user_id), before=before, after=after, meta={"note": note})
    return jsonify({"status": "success", "account": after})


@developer_admin_blueprint.get("/accounts/<int:user_id>")
def admin_developer_account(user_id):
    _, auth_error = _admin_required("api_key.view")
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
    admin_user, auth_error = _admin_required("api_key.manage")
    if auth_error:
        return auth_error
    payload = request.get_json(silent=True) or {}
    try:
        remaining_calls = int(payload.get("remainingCalls"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "remainingCalls 必须是整数"}), 400
    if remaining_calls < 0 or remaining_calls > 10_000_000:
        return jsonify({"status": "error", "message": "remainingCalls 必须在 0 到 10000000 之间"}), 400

    users = excute_sql("SELECT Userid FROM user WHERE Userid = %s LIMIT 1", (user_id,))
    if users is None:
        return jsonify({"status": "error", "message": "用户信息读取失败"}), 500
    if not users:
        return jsonify({"status": "error", "message": "用户不存在"}), 404
    if not _ensure_developer_account(user_id):
        return jsonify({"status": "error", "message": "开发者账户初始化失败"}), 503

    note = str(payload.get("note") or "管理员设置剩余调用次数").strip()[:500]
    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
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
                        (user_id, entry_type, free_calls_delta, balance_delta_fen,
                         amount_fen, balance_after_fen, note)
                    VALUES (%s, 'admin_quota_set', %s, 0, 0, %s, %s)
                    """,
                    (user_id, free_delta, int(account.get("balance_fen") or 0), note),
                )
            after_row = dict(account)
            after_row["free_total"] = next_free_total
            after = _account_payload(after_row)
        conn.commit()
    except BillingError as exc:
        conn.rollback()
        return jsonify({"status": "error", "message": str(exc)}), exc.status_code
    except Exception as exc:
        conn.rollback()
        return jsonify({"status": "error", "message": f"调用次数设置失败: {exc}"}), 500
    finally:
        conn.close()

    _audit(
        admin_user,
        "developer.account.quota.set",
        str(user_id),
        before=before,
        after=after,
        meta={"note": note},
    )
    return jsonify({"status": "success", "account": after})
