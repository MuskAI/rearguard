import hashlib
import io
import json
import os
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
    _release_developer_daily_detection,
    _reserve_developer_daily_detection,
    _developer_scopes,
    _developer_usage_from_v1,
    _developer_usage_from_v2,
    _empty_developer_usage,
    _merge_developer_usage,
    _record_developer_usage_event,
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


def _error(message, status_code, code):
    return jsonify({"error": {"code": code, "message": message}}), status_code


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
              key_id BIGINT NOT NULL,
              mode VARCHAR(16) NOT NULL,
              filename VARCHAR(255) NOT NULL,
              request_sha256 CHAR(64) NOT NULL,
              idempotency_key VARCHAR(128) NULL,
              status VARCHAR(24) NOT NULL DEFAULT 'queued',
              lease_owner VARCHAR(64) NULL,
              lease_expires_at DATETIME(6) NULL,
              result_item_id INT NULL,
              result_json MEDIUMTEXT NULL,
              error_message VARCHAR(500) NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              completed_at DATETIME NULL,
              PRIMARY KEY (task_id),
              UNIQUE KEY uk_developer_task_idempotency (user_id, idempotency_key),
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
                ("lease_owner", "ALTER TABLE developer_detection_tasks ADD COLUMN lease_owner VARCHAR(64) NULL AFTER status"),
                (
                    "lease_expires_at",
                    "ALTER TABLE developer_detection_tasks ADD COLUMN lease_expires_at DATETIME(6) NULL AFTER lease_owner",
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
                UPDATE developer_detection_tasks
                SET lease_owner = COALESCE(lease_owner, CONCAT('legacy-', task_id)),
                    lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND)
                WHERE status IN ('queued', 'running')
                  AND lease_expires_at IS NULL
                """,
                (DEVELOPER_TASK_LEASE_SECONDS,),
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
                  AND status = 'queued'
                  AND lease_expires_at > NOW(6)
                FOR UPDATE
                """,
                (task_id,),
            )
            if not cursor.fetchone():
                raise BillingError(
                    "任务调度租约已失效，请重新提交",
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


def _fail_task_and_release(task_id, message, *, lease_owner=None, require_expired=False):
    """Atomically terminalize an active task and release its billing reservation."""
    if require_expired:
        lease_condition = " AND lease_expires_at IS NOT NULL AND lease_expires_at <= NOW(6)"
        lease_params = ()
    elif lease_owner:
        lease_condition = " AND lease_owner = %s AND lease_expires_at > NOW(6)"
        lease_params = (lease_owner,)
    else:
        lease_condition = ""
        lease_params = ()

    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT task_id, user_id, status, lease_owner, lease_expires_at
                FROM developer_detection_tasks
                WHERE task_id = %s
                  AND status IN ('queued', 'running')
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

            cursor.execute(
                f"""
                UPDATE developer_detection_tasks
                SET status = 'failed', error_message = %s, completed_at = NOW(),
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE task_id = %s
                  AND status IN ('queued', 'running')
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


def _expire_task_lease(task_id):
    """Fail one expired task and release its reservation in one transaction."""
    message = "任务执行租约已过期，系统已终止任务并释放预留额度"
    recovered = _fail_task_and_release(task_id, message, require_expired=True)
    if not recovered:
        return False
    try:
        admin_state.update_detection_job(
            task_id,
            {"status": "failed", "error": message, "progress": 100},
        )
    except Exception as exc:
        print(f"[DEVELOPER TASK RECOVERY ERROR] recovered {task_id}, but job cache update failed: {exc}")
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
        admin_state.update_detection_job(
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
                SELECT task_id
                FROM developer_detection_tasks
                WHERE status IN ('queued', 'running')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= NOW(6)
                ORDER BY lease_expires_at ASC
                LIMIT %s
                """,
                (batch_size,),
            )
            task_ids = [row.get("task_id") for row in cursor.fetchall() if row.get("task_id")]
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
    except Exception as exc:
        raise TaskRecoveryError(f"failed to scan expired tasks: {exc}") from exc
    finally:
        if conn:
            conn.close()

    recovered = 0
    for task_id in task_ids:
        try:
            if _expire_task_lease(task_id):
                recovered += 1
        except TaskRecoveryError as exc:
            print(f"[DEVELOPER TASK RECOVERY ERROR] isolated expired task {task_id}: {exc}")
    for task_id in success_task_ids:
        try:
            if _reconcile_success_reservation(task_id):
                recovered += 1
        except TaskRecoveryError as exc:
            print(f"[DEVELOPER TASK RECOVERY ERROR] isolated successful task {task_id}: {exc}")
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


def _task_row_for_user(task_id, user_id):
    if not _ensure_developer_platform_tables():
        return None
    rows = excute_sql(
        """
        SELECT task_id, user_id, key_id, mode, filename, request_sha256, idempotency_key,
               status, result_item_id, result_json, error_message, created_at, updated_at, completed_at
        FROM developer_detection_tasks
        WHERE task_id = %s AND user_id = %s
        LIMIT 1
        """,
        (task_id, user_id),
    )
    return rows[0] if rows else None


def _idempotent_task(user_id, idempotency_key):
    if not idempotency_key:
        return None
    rows = excute_sql(
        """
        SELECT task_id, user_id, key_id, mode, filename, request_sha256, idempotency_key,
               status, result_item_id, result_json, error_message, created_at, updated_at, completed_at
        FROM developer_detection_tasks
        WHERE user_id = %s AND idempotency_key = %s
        LIMIT 1
        """,
        (user_id, idempotency_key),
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


def _task_payload(row):
    job = admin_state.get_detection_job(row["task_id"])
    public_job = detection._public_detection_job(job) if job else None
    # SQL is the authoritative task state; the JSON job cache is progress-only.
    status = row.get("status") or "queued"
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
        SET status = 'running', lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND)
        WHERE task_id = %s
          AND status = 'queued'
          AND lease_owner = %s
          AND lease_expires_at > NOW(6)
        """,
        (DEVELOPER_TASK_LEASE_SECONDS, task_id, lease_owner),
        fetch=False,
    )
    if updated is None:
        raise TaskRecoveryError(f"failed to claim task lease for {task_id}")
    return updated == 1


def _renew_task_lease(task_id, lease_owner):
    updated = excute_sql(
        """
        UPDATE developer_detection_tasks
        SET lease_expires_at = DATE_ADD(NOW(6), INTERVAL %s SECOND)
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
    while not stop_event.wait(DEVELOPER_TASK_HEARTBEAT_SECONDS):
        try:
            if _renew_task_lease(task_id, lease_owner):
                continue
            print(f"[DEVELOPER TASK RECOVERY ERROR] task {task_id} lost its execution lease")
        except TaskRecoveryError as exc:
            print(f"[DEVELOPER TASK RECOVERY ERROR] {exc}")
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
        persisted = excute_sql(
            f"""
            UPDATE developer_detection_tasks
            SET status = 'success', result_item_id = %s, result_json = %s,
                error_message = NULL, completed_at = NOW(),
                lease_owner = NULL, lease_expires_at = NULL
            WHERE task_id = %s AND status IN ('queued', 'running'){lease_condition}
            """,
            (item_id, json.dumps(public_payload, ensure_ascii=False, default=str), task_id, *lease_params),
            fetch=False,
        )
        if persisted != 1:
            return False
        settled_now = _settle_billing(task_id)
        if settled_now:
            prompt, completion, total = _token_usage(payload)
            _record_developer_usage_event(
                actor,
                pipeline="openapi",
                endpoint=f"/api/openapi/v1/image-detections:{mode}",
                model_version=f"huijian-image-{mode}",
                status_code=200,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
            )
        return settled_now

    message = (payload or {}).get("message") if isinstance(payload, dict) else ""
    message = str(message or f"HTTP {status_code}")[:500]
    try:
        return _fail_task_and_release(task_id, message, lease_owner=lease_owner)
    except TaskRecoveryError as exc:
        print(f"[DEVELOPER TASK RECOVERY ERROR] {exc}")
        return False


def _run_openapi_job(task_id, image_bytes, filename, mimetype, user_info, actor, mode, lease_owner):
    try:
        claimed = _claim_task_lease(task_id, lease_owner)
    except TaskRecoveryError as exc:
        admin_state.update_detection_job(
            task_id,
            {"status": "failed", "error": str(exc), "progress": 100},
        )
        return
    if not claimed:
        admin_state.update_detection_job(
            task_id,
            {"status": "failed", "error": "任务执行租约已失效，等待系统自动对账", "progress": 100},
        )
        return

    admin_state.update_detection_job(task_id, {
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
    heartbeat.start()
    try:
        if mode == "swarm":
            payload, status_code = detection._run_swarm_detection_payload(
                image_bytes,
                filename,
                mimetype,
                user_info,
                is_guest=False,
                job_id=task_id,
            )
        else:
            payload, status_code = detection._run_image_detection_payload(
                image_bytes,
                filename,
                mimetype,
                user_info,
                is_guest=False,
                mark_guest=False,
            )
        heartbeat_stop.set()
        heartbeat.join(timeout=DEVELOPER_TASK_HEARTBEAT_SECONDS + 1)
        if lease_lost.is_set():
            admin_state.update_detection_job(task_id, {
                "status": "failed",
                "error": "任务执行期间数据库租约丢失，结果未结算，等待系统自动对账",
                "progress": 100,
            })
            return
        finished = _finish_task(task_id, actor, mode, payload, status_code, lease_owner=lease_owner)
        if finished:
            admin_state.update_detection_job(task_id, {
                "status": "success" if status_code < 400 and payload.get("status") == "success" else "failed",
                "result": payload,
                "error": "" if status_code < 400 else (payload or {}).get("message") or f"HTTP {status_code}",
                "progress": 100,
                "summary": "检测完成" if status_code < 400 else "检测未完成",
            })
        else:
            rows = excute_sql(
                "SELECT status FROM developer_detection_tasks WHERE task_id = %s LIMIT 1",
                (task_id,),
            ) or []
            if rows and rows[0].get("status") == "success":
                admin_state.update_detection_job(task_id, {
                    "status": "success",
                    "result": payload,
                    "progress": 100,
                    "summary": "检测完成，计费对账将在下次请求自动重试",
                })
            else:
                admin_state.update_detection_job(task_id, {
                    "status": "failed",
                    "error": (payload or {}).get("message") or f"HTTP {status_code}",
                    "result": payload,
                    "progress": 100,
                })
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
        admin_state.update_detection_job(task_id, {
            "status": "failed",
            "error": message,
            "progress": 100,
        })
    finally:
        heartbeat_stop.set()


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
    existing = _idempotent_task(actor["user_id"], idempotency_key)
    if existing:
        if existing.get("mode") != mode or existing.get("request_sha256") != digest:
            return _error("该 Idempotency-Key 已用于其他请求", 409, "idempotency_conflict")
        return jsonify(_task_payload(existing)), 200

    daily_quota_error = _reserve_developer_daily_detection(actor)
    if daily_quota_error:
        return daily_quota_error

    user_info = {
        "Userid": actor.get("user_id"),
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
        _release_developer_daily_detection(actor)
        return _error("任务创建失败，请稍后重试", 503, "task_create_failed")
    task_id = job["id"]
    lease_owner = uuid.uuid4().hex
    inserted = excute_sql(
        """
        INSERT INTO developer_detection_tasks
            (task_id, user_id, key_id, mode, filename, request_sha256, idempotency_key,
             status, lease_owner, lease_expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', %s,
                DATE_ADD(NOW(6), INTERVAL %s SECOND))
        """,
        (
            task_id,
            actor["user_id"],
            actor["id"],
            mode,
            upload.filename[:255],
            digest,
            idempotency_key or None,
            lease_owner,
            DEVELOPER_TASK_LEASE_SECONDS,
        ),
        fetch=False,
    )
    if inserted is None:
        _release_developer_daily_detection(actor)
        duplicate = _idempotent_task(actor["user_id"], idempotency_key)
        if duplicate and duplicate.get("mode") == mode and duplicate.get("request_sha256") == digest:
            return jsonify(_task_payload(duplicate)), 200
        admin_state.update_detection_job(task_id, {"status": "failed", "error": "任务写入失败", "progress": 100})
        return _error("任务创建失败，请稍后重试", 503, "task_create_failed")

    try:
        _reserve_billing(actor["user_id"], actor["id"], task_id, mode)
    except BillingError as exc:
        _release_developer_daily_detection(actor)
        excute_sql(
            "UPDATE developer_detection_tasks SET status = 'rejected', error_message = %s, completed_at = NOW() WHERE task_id = %s",
            (str(exc)[:500], task_id),
            fetch=False,
        )
        admin_state.update_detection_job(task_id, {"status": "failed", "error": str(exc), "progress": 100})
        return _error(str(exc), exc.status_code, exc.code)
    except Exception:
        _release_developer_daily_detection(actor)
        excute_sql(
            "UPDATE developer_detection_tasks SET status = 'failed', error_message = %s, completed_at = NOW() WHERE task_id = %s",
            ("计费服务暂不可用", task_id),
            fetch=False,
        )
        return _error("计费服务暂不可用", 503, "billing_unavailable")

    job_args = (
            task_id,
            image_bytes,
            upload.filename,
            upload.mimetype or "application/octet-stream",
            user_info,
            dict(actor),
            mode,
            lease_owner,
    )
    try:
        started = detection._start_background_job(_run_openapi_job, job_args)
    except Exception as exc:
        message = f"任务调度失败: {str(exc)[:420]}"
        released = _finish_task(
            task_id,
            dict(actor),
            mode,
            {"status": "error", "message": message},
            503,
            lease_owner=lease_owner,
        )
        admin_state.update_detection_job(task_id, {"status": "failed", "error": message, "progress": 100})
        _release_developer_daily_detection(actor)
        if released:
            _allow_idempotent_retry(task_id)
        return _error("任务调度失败，请稍后重试", 503, "task_dispatch_failed")
    if not started:
        message = "当前检测任务较多，请稍后重试"
        released = _finish_task(
            task_id,
            dict(actor),
            mode,
            {"status": "error", "message": message},
            429,
            lease_owner=lease_owner,
        )
        admin_state.update_detection_job(task_id, {"status": "failed", "error": message, "progress": 100})
        _release_developer_daily_detection(actor)
        if not released:
            return _error(
                "任务未启动，但额度释放尚未确认；系统将自动对账，请勿重复提交",
                503,
                "billing_release_pending",
            )
        _allow_idempotent_retry(task_id)
        response, status = _error(message, 429, "server_busy")
        response.headers["Retry-After"] = "5"
        return response, status
    row = _task_row_for_user(task_id, actor["user_id"])
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
    row = _task_row_for_user(task_id, actor["user_id"])
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
    row = _task_row_for_user(task_id, actor["user_id"])
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
    row = _task_row_for_user(task_id, actor["user_id"])
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


def _recent_tasks(user_id, limit=8):
    rows = excute_sql(
        """
        SELECT task_id, user_id, key_id, mode, filename, request_sha256, idempotency_key,
               status, result_item_id, result_json, error_message, created_at, updated_at, completed_at
        FROM developer_detection_tasks
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (user_id, max(1, min(int(limit), 50))),
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
        "recentTasks": _recent_tasks(user["Userid"]),
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
