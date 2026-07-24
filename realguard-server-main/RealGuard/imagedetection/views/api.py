import hashlib
import hmac
import ipaddress
import io
import json
import os
import base64
import secrets
import stat
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image
from flask import Blueprint, Response, jsonify, request, send_file, session, stream_with_context

from imagedetection.decision_labels import binary_final_label
from imagedetection.views.historical_record import DETECTION_BACKEND_BASE_URL
from imagedetection.views import (
    admin_state,
    evidence_manifest,
    privacy_erasure_ledger,
    traffic_geo,
)
from imagedetection.views.login import (
    PasswordLoginRateLimitError,
    SMS_PASSWORD_SETUP_TTL,
    SmsStorageError,
    _authenticate_password_user,
    _begin_sms_password_setup,
    _clear_password_phone_attempts,
    _complete_sms_password_setup,
    _find_user_by_phone,
    _hash_password,
    _is_valid_phone,
    _reserve_password_login_attempt,
    _record_terms_acceptance as _append_terms_acceptance,
    TERMS_VERSION as CURRENT_CONSENT_VERSION,
    revoke_current_user_sessions,
    _session_version,
    _sync_detection_user,
    _user_session_payload,
    _verify_sms_code,
)
from imagedetection.views.utils import (
    detection_record_is_publishable,
    detection_owner_where,
    excute_detection_sql,
    excute_sql,
    format_createtime,
    get_db_connection,
    get_detection_db_connection,
    normalize_account_uuid,
)


api_blueprint = Blueprint("api_blueprint", __name__, url_prefix="/api")
DEVELOPER_WORKER_HEARTBEAT = Path(
    os.environ.get("REALGUARD_DEVELOPER_WORKER_HEARTBEAT", "/opt/realguard-data/developer-worker.heartbeat")
)
DETECTOR_READY_URL = (
    os.environ.get("REALGUARD_DETECTION_BACKEND_URL", "http://127.0.0.1:15001").rstrip("/")
    + "/internal/ready"
)
DETECTOR_INTERNAL_TOKEN = os.environ.get("REALGUARD_DETECTOR_INTERNAL_TOKEN", "").strip()
DEVELOPER_WORKER_MAX_HEARTBEAT_AGE = max(
    10,
    int(os.environ.get("REALGUARD_DEVELOPER_WORKER_MAX_HEARTBEAT_AGE", "60")),
)
THUMBNAIL_CACHE_DIR = Path(os.environ.get("REALGUARD_THUMBNAIL_CACHE_DIR", "/tmp/realguard-thumbnails"))
PRIVACY_DEVELOPER_SPOOL_ROOT = Path(
    os.environ.get("REALGUARD_DEVELOPER_SPOOL_ROOT", "/opt/realguard-data/developer-spool")
)
PRIVACY_WEB_SPOOL_ROOT = Path(
    os.environ.get("REALGUARD_WEB_TASK_SPOOL_ROOT", "/opt/realguard-data/web-spool")
)
THUMBNAIL_MAX_SIZE = (
    int(os.environ.get("REALGUARD_THUMBNAIL_MAX_WIDTH", "220")),
    int(os.environ.get("REALGUARD_THUMBNAIL_MAX_HEIGHT", "165")),
)
THUMBNAIL_QUALITY = int(os.environ.get("REALGUARD_THUMBNAIL_QUALITY", "45"))
PRIVACY_ERASURE_PRECOMMIT_GRACE_SECONDS = max(
    600,
    int(os.environ.get("REALGUARD_PRIVACY_ERASURE_PRECOMMIT_GRACE_SECONDS", "900")),
)
HISTORY_ORDER_BY = (
    "CASE WHEN CAST(createtime AS CHAR) REGEXP '^[0-9]{14}$' "
    "THEN STR_TO_DATE(CAST(createtime AS CHAR), '%%Y%%m%%d%%H%%i%%s') "
    "ELSE STR_TO_DATE(CAST(createtime AS CHAR), '%%Y-%%m-%%d %%H:%%i:%%s') "
    "END DESC, itemid DESC"
)
DEVELOPER_API_KEY_PREFIX = "rg_sk_"
DEVELOPER_API_KEY_MAX_ACTIVE = int(os.environ.get("REALGUARD_DEVELOPER_API_KEY_MAX_ACTIVE", "5"))
DEVELOPER_API_KEY_DEFAULT_TTL_SECONDS = max(
    86400,
    int(os.environ.get("REALGUARD_DEVELOPER_API_KEY_DEFAULT_TTL_SECONDS", str(90 * 86400))),
)
DEVELOPER_API_KEY_MAX_TTL_SECONDS = max(
    DEVELOPER_API_KEY_DEFAULT_TTL_SECONDS,
    int(os.environ.get("REALGUARD_DEVELOPER_API_KEY_MAX_TTL_SECONDS", str(365 * 86400))),
)
DEVELOPER_API_KEY_DEFAULT_SCOPES = "image:fast"
DEVELOPER_API_KEY_ALLOWED_SCOPES = frozenset({"image:fast", "image:swarm", "reports"})
DEVELOPER_AUTH_SECRET = os.environ.get("REALGUARD_DEVELOPER_AUTH_SECRET", "").strip()
DEVELOPER_IDEMPOTENCY_SECRET = os.environ.get(
    "REALGUARD_DEVELOPER_IDEMPOTENCY_SECRET", ""
).strip()
DEVELOPER_TRUSTED_PROXY_CIDRS = tuple(
    value.strip()
    for value in os.environ.get("REALGUARD_TRUSTED_PROXY_CIDRS", "127.0.0.0/8,::1/128").split(",")
    if value.strip()
)
DEVELOPER_USAGE_URL = os.environ.get(
    "REALGUARD_DEVELOPER_USAGE_URL",
    "http://127.0.0.1:8848/api/developer/token-usage",
).strip()
TERMS_VERSION = CURRENT_CONSENT_VERSION
PASSWORD_MIN_LENGTH = int(os.environ.get("REALGUARD_PASSWORD_MIN_LENGTH", "8"))
_DEVELOPER_KEY_TABLE_READY = False
_DEVELOPER_USAGE_TABLE_READY = False
_SECURITY_AUDIT_TABLES_READY = False
_USER_ACCOUNT_COLUMNS_READY = False
_PRIVACY_ERASURE_TABLE_READY = False


def _current_user():
    user = session.get("user_info")
    return user if isinstance(user, dict) else None


def _auth_required():
    user = _current_user()
    if not user:
        return None, (jsonify({"status": "error", "message": "用户未登录"}), 401)
    return user, None


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "agree", "accepted")


def _password_policy_error(secret):
    value = str(secret or "")
    if len(value) < PASSWORD_MIN_LENGTH:
        return f"密码至少需要 {PASSWORD_MIN_LENGTH} 位"
    if len(value) > 128:
        return "密码不能超过 128 位"
    if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
        return "密码需同时包含字母和数字"
    return ""


def _ensure_column(table, column, definition):
    rows = excute_sql(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    if rows is None:
        return False
    if rows:
        return True
    result = excute_sql(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}", fetch=False)
    return result is not None


def _ensure_user_account_columns():
    global _USER_ACCOUNT_COLUMNS_READY
    if _USER_ACCOUNT_COLUMNS_READY:
        return True
    columns = [
        ("account_uuid", "CHAR(36) NULL COMMENT '不可变账号标识'"),
        ("created_at", "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'"),
        ("terms_version", "VARCHAR(32) NULL COMMENT '用户协议版本'"),
        ("terms_accepted_at", "DATETIME NULL COMMENT '用户协议同意时间'"),
        ("password_updated_at", "DATETIME NULL COMMENT '密码更新时间'"),
        ("session_version", "INT NOT NULL DEFAULT 1 COMMENT '登录态版本'"),
    ]
    for column, definition in columns:
        if not _ensure_column("user", column, definition):
            return False
    if excute_sql(
        "UPDATE `user` SET account_uuid = UUID() WHERE account_uuid IS NULL OR account_uuid = ''",
        fetch=False,
    ) is None:
        return False
    _USER_ACCOUNT_COLUMNS_READY = True
    return True


def _set_session_user(user, phone):
    session_user = _user_session_payload(user, phone)
    _sync_detection_user(
        phone,
        user.get("username") or phone,
        user.get("openid", "") or phone,
        session_user.get("account_uuid"),
    )
    session.clear()
    session.permanent = True
    session["user_info"] = session_user
    return session["user_info"]


def _record_terms_acceptance(phone):
    return _append_terms_acceptance(phone, channel="v2_api_auth")


def _developer_key_hash(api_key):
    return hashlib.sha256(f"realguard-developer-api-key:{api_key}".encode("utf-8")).hexdigest()


def _idempotent_developer_api_key(user_id, operation, idempotency_key):
    if not DEVELOPER_IDEMPOTENCY_SECRET:
        return None
    digest = hmac.new(
        DEVELOPER_IDEMPOTENCY_SECRET.encode("utf-8"),
        f"developer-key:{user_id}:{operation}:{idempotency_key}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{DEVELOPER_API_KEY_PREFIX}{encoded}"


def _developer_operation_idempotency(user_id, operation):
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        return None, (
            jsonify({
                "status": "error",
                "code": "idempotency_key_required",
                "message": "创建或轮换 API Key 必须提供 Idempotency-Key",
            }),
            400,
        )
    if len(key) > 128:
        return None, (jsonify({"status": "error", "message": "Idempotency-Key 不能超过 128 个字符"}), 400)
    api_key = _idempotent_developer_api_key(user_id, operation, key)
    if not api_key:
        return None, (jsonify({"status": "error", "message": "幂等密钥服务未配置"}), 503)
    return api_key, None


def _developer_scopes(raw):
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _developer_key_preview(row):
    return f"{row.get('key_prefix') or DEVELOPER_API_KEY_PREFIX}...{row.get('key_last4') or '****'}"


def _developer_key_payload(row):
    return {
        "id": row.get("id"),
        "name": row.get("name") or "Default key",
        "preview": _developer_key_preview(row),
        "scopes": _developer_scopes(row.get("scopes")),
        "status": row.get("status") or "active",
        "createdAt": format_createtime(row.get("created_at", "")),
        "lastUsedAt": format_createtime(row.get("last_used_at", "")),
        "revokedAt": format_createtime(row.get("revoked_at", "")),
        "expiresAt": format_createtime(row.get("expires_at", "")),
        "ipAllowlist": _developer_ip_allowlist(row.get("ip_allowlist")),
    }


def _security_audit_key():
    raw = str(os.environ.get("REALGUARD_SECURITY_AUDIT_HMAC_KEY") or "").strip().lower()
    if len(raw) != 64 or any(char not in "0123456789abcdef" for char in raw):
        raise RuntimeError("dedicated security audit HMAC key must be 64 hexadecimal characters")
    return bytes.fromhex(raw)


def _append_security_audit(cursor, actor_type, actor_id, action, target, meta=None):
    event_id = str(uuid.uuid4())
    occurred_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    key_id = str(os.environ.get("REALGUARD_SECURITY_AUDIT_HMAC_KEY_ID") or "session-v1")[:64]
    metadata = json.dumps(meta or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    cursor.execute(
        "INSERT IGNORE INTO security_audit_chain_head (id, last_event_hash) VALUES (1, %s)",
        ("0" * 64,),
    )
    cursor.execute("SELECT last_event_hash FROM security_audit_chain_head WHERE id = 1 FOR UPDATE")
    head = cursor.fetchone() or {}
    previous_hash = str(head.get("last_event_hash") or "0" * 64)
    canonical = json.dumps(
        {
            "eventId": event_id,
            "occurredAt": occurred_at,
            "actorType": str(actor_type),
            "actorId": str(actor_id),
            "action": str(action),
            "target": str(target),
            "meta": json.loads(metadata),
            "previousHash": previous_hash,
            "keyId": key_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    event_hash = hmac.new(_security_audit_key(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    cursor.execute(
        """
        INSERT INTO security_audit_events (
            event_id, occurred_at, actor_type, actor_id, action, target,
            meta_json, previous_hash, event_hash, key_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event_id, occurred_at, str(actor_type)[:32], str(actor_id)[:64],
            str(action)[:96], str(target)[:191], metadata, previous_hash, event_hash, key_id,
        ),
    )
    cursor.execute(
        "UPDATE security_audit_chain_head SET last_event_hash = %s, updated_at = NOW(6) WHERE id = 1",
        (event_hash,),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("security audit chain head update failed")
    return event_id


def _security_audit_keyring():
    current_id = str(os.environ.get("REALGUARD_SECURITY_AUDIT_HMAC_KEY_ID") or "").strip()
    if not current_id:
        raise RuntimeError("security audit HMAC key id is missing")
    keys = {current_id: _security_audit_key()}
    raw = str(os.environ.get("REALGUARD_SECURITY_AUDIT_HMAC_KEYS_JSON") or "{}").strip()
    try:
        historical = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("security audit HMAC keyring is invalid") from exc
    if not isinstance(historical, dict):
        raise RuntimeError("security audit HMAC keyring must be an object")
    for key_id, value in historical.items():
        encoded = str(value or "").strip().lower()
        if len(encoded) != 64 or any(char not in "0123456789abcdef" for char in encoded):
            raise RuntimeError(f"security audit historical key is invalid: {key_id}")
        keys[str(key_id)] = bytes.fromhex(encoded)
    return keys


def _security_audit_checkpoint_hmac(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hmac.new(_security_audit_key(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_security_audit_chain(*, allow_bootstrap=False):
    """Verify the full chain and a filesystem checkpoint outside the database."""
    status_path = Path(os.environ.get(
        "REALGUARD_SECURITY_AUDIT_STATUS_FILE", "/opt/realguard-data/security-audit-status.json"
    ))
    checkpoint_path = Path(os.environ.get(
        "REALGUARD_SECURITY_AUDIT_CHECKPOINT_FILE", "/opt/realguard-audit-checkpoint/checkpoint.json"
    ))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        keys = _security_audit_keyring()
        rows = excute_sql(
            "SELECT id, event_id, occurred_at, actor_type, actor_id, action, target, meta_json, "
            "previous_hash, event_hash, key_id FROM security_audit_events ORDER BY id ASC"
        )
        heads = excute_sql("SELECT last_event_hash FROM security_audit_chain_head WHERE id = 1 LIMIT 1")
        if rows is None or heads is None:
            raise RuntimeError("security audit storage unavailable")
        previous = "0" * 64
        event_hashes = set()
        for row in rows:
            if not hmac.compare_digest(str(row.get("previous_hash") or ""), previous):
                raise RuntimeError(f"audit chain link mismatch at event {row.get('id')}")
            key = keys.get(str(row.get("key_id") or ""))
            if key is None:
                raise RuntimeError(f"unknown audit key id at event {row.get('id')}")
            try:
                meta = json.loads(row.get("meta_json") or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid audit metadata at event {row.get('id')}") from exc
            canonical = json.dumps({
                "eventId": str(row.get("event_id") or ""),
                "occurredAt": str(row.get("occurred_at") or ""),
                "actorType": str(row.get("actor_type") or ""),
                "actorId": str(row.get("actor_id") or ""),
                "action": str(row.get("action") or ""),
                "target": str(row.get("target") or ""),
                "meta": meta,
                "previousHash": previous,
                "keyId": str(row.get("key_id") or ""),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            expected = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, str(row.get("event_hash") or "")):
                raise RuntimeError(f"audit event HMAC mismatch at event {row.get('id')}")
            previous = expected
            event_hashes.add(expected)
        database_head = str((heads[0] if heads else {}).get("last_event_hash") or "0" * 64)
        if not hmac.compare_digest(database_head, previous):
            raise RuntimeError("audit chain head mismatch")
        checkpoint = {}
        if checkpoint_path.exists() and (not checkpoint_path.is_file() or checkpoint_path.is_symlink()):
            raise RuntimeError("audit checkpoint is not a safe regular file")
        if checkpoint_path.is_file():
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            signature = checkpoint.pop("hmacSha256", "")
            if not hmac.compare_digest(_security_audit_checkpoint_hmac(checkpoint), str(signature)):
                raise RuntimeError("audit checkpoint HMAC mismatch")
            checkpoint_count = int(checkpoint.get("eventCount") or 0)
            if len(rows) < checkpoint_count:
                raise RuntimeError("audit event count moved backwards")
            old_hash = str(checkpoint.get("lastEventHash") or "")
            if checkpoint_count == 0 and old_hash != "0" * 64:
                raise RuntimeError("empty audit checkpoint has an invalid chain head")
            if checkpoint_count > 0 and old_hash not in event_hashes:
                raise RuntimeError("audit checkpoint event disappeared")
        elif not allow_bootstrap:
            raise RuntimeError("audit checkpoint is missing; explicit bootstrap is required")
        checkpoint = {
            "schemaVersion": 1,
            "eventCount": len(rows),
            "lastEventId": int(rows[-1].get("id")) if rows else 0,
            "lastEventHash": previous,
            "updatedAt": now,
        }
        checkpoint["hmacSha256"] = _security_audit_checkpoint_hmac(checkpoint)
        _write_security_audit_json(checkpoint_path, checkpoint, 0o600)
        result = {"schemaVersion": 1, "state": "passed", "updatedAt": now, **checkpoint}
        result.pop("hmacSha256", None)
        _write_security_audit_json(status_path, result, 0o644)
        return result
    except Exception as exc:
        result = {
            "schemaVersion": 1,
            "state": "failed",
            "updatedAt": now,
            "lastError": str(exc)[:1000],
        }
        _write_security_audit_json(status_path, result, 0o644)
        return result


def _write_security_audit_json(path, payload, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _ensure_security_audit_tables():
    global _SECURITY_AUDIT_TABLES_READY
    if _SECURITY_AUDIT_TABLES_READY:
        return True
    statements = (
        """
        CREATE TABLE IF NOT EXISTS security_audit_chain_head (
          id TINYINT NOT NULL,
          last_event_hash CHAR(64) NOT NULL,
          updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
          PRIMARY KEY (id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS security_audit_events (
          id BIGINT NOT NULL AUTO_INCREMENT,
          event_id CHAR(36) NOT NULL,
          occurred_at VARCHAR(32) NOT NULL,
          actor_type VARCHAR(32) NOT NULL,
          actor_id VARCHAR(64) NOT NULL,
          action VARCHAR(96) NOT NULL,
          target VARCHAR(191) NOT NULL,
          meta_json LONGTEXT NOT NULL,
          previous_hash CHAR(64) NOT NULL,
          event_hash CHAR(64) NOT NULL,
          key_id VARCHAR(64) NOT NULL,
          PRIMARY KEY (id),
          UNIQUE KEY uk_security_audit_event_id (event_id),
          UNIQUE KEY uk_security_audit_event_hash (event_hash),
          KEY idx_security_audit_actor_time (actor_type, actor_id, occurred_at),
          KEY idx_security_audit_action_time (action, occurred_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    )
    _SECURITY_AUDIT_TABLES_READY = all(
        excute_sql(statement, fetch=False) is not None for statement in statements
    )
    return _SECURITY_AUDIT_TABLES_READY


def _developer_ip_allowlist(raw):
    if isinstance(raw, (list, tuple)):
        values = raw
    else:
        values = str(raw or "").replace("\r", "\n").replace(",", "\n").split("\n")
    return [str(item).strip() for item in values if str(item).strip()]


def _normalize_developer_ip_allowlist(raw):
    normalized = []
    for value in _developer_ip_allowlist(raw):
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            return None, f"IP 白名单格式无效: {value}"
        canonical = str(network.network_address) if network.num_addresses == 1 else str(network)
        if canonical not in normalized:
            normalized.append(canonical)
    if len(normalized) > 32:
        return None, "IP 白名单最多支持 32 条"
    return normalized, None


def _developer_request_ip():
    direct = str(request.remote_addr or "").strip()
    try:
        direct_ip = ipaddress.ip_address(direct)
        trusted_proxy = any(
            direct_ip in ipaddress.ip_network(value, strict=False)
            for value in DEVELOPER_TRUSTED_PROXY_CIDRS
        )
    except ValueError:
        trusted_proxy = False
    if not trusted_proxy:
        return direct

    real_ip = request.headers.get("x-real-ip", "").strip()
    try:
        if real_ip:
            ipaddress.ip_address(real_ip)
            return real_ip
    except ValueError:
        pass

    forwarded = [item.strip() for item in request.headers.get("x-forwarded-for", "").split(",") if item.strip()]
    for candidate in reversed(forwarded):
        try:
            candidate_ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not any(candidate_ip in ipaddress.ip_network(value, strict=False) for value in DEVELOPER_TRUSTED_PROXY_CIDRS):
            return candidate
    return direct


def _developer_ip_allowed(raw_allowlist):
    allowlist = _developer_ip_allowlist(raw_allowlist)
    if not allowlist:
        return True
    try:
        client_ip = ipaddress.ip_address(_developer_request_ip())
    except ValueError:
        return False
    return any(client_ip in ipaddress.ip_network(value, strict=False) for value in allowlist)


@api_blueprint.route("/analytics/pageview", methods=["POST"])
def record_analytics_pageview():
    if request.content_length and request.content_length > 2048:
        return jsonify({"status": "error", "message": "请求体过大"}), 413
    fetch_site = str(request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if fetch_site and fetch_site != "same-origin":
        return jsonify({"status": "error", "message": "仅允许同源上报"}), 403
    if request.headers.get("X-RealGuard-Browser-Event") != "1":
        return jsonify({"status": "error", "message": "无效的页面访问事件"}), 400
    payload = request.get_json(silent=True) or {}
    accepted = traffic_geo.record_confirmed_pageview(
        ip=_developer_request_ip(),
        agent=request.headers.get("User-Agent", ""),
        visitor_id=payload.get("visitorId", ""),
        event_id=payload.get("eventId", ""),
        page=payload.get("page", ""),
    )
    if not accepted:
        return jsonify({"status": "error", "message": "页面访问事件未通过校验"}), 400
    return "", 204


def _normalize_developer_expiry(raw):
    now = datetime.now()
    if raw in (None, ""):
        parsed = now + timedelta(seconds=DEVELOPER_API_KEY_DEFAULT_TTL_SECONDS)
        return parsed.strftime("%Y-%m-%d %H:%M:%S"), None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, "有效期格式无效，请使用 ISO 8601 时间"
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    if parsed <= now:
        return None, "有效期必须晚于当前时间"
    if parsed > now + timedelta(seconds=DEVELOPER_API_KEY_MAX_TTL_SECONDS):
        return None, f"API Key 有效期不能超过 {DEVELOPER_API_KEY_MAX_TTL_SECONDS // 86400} 天"
    return parsed.strftime("%Y-%m-%d %H:%M:%S"), None


def _developer_key_options(payload, *, fallback_name="Default key", fallback_scopes=None, fallback_expiry=None, fallback_ips=None):
    name = str(payload.get("name") or fallback_name).strip() or fallback_name
    if len(name) > 120:
        return None, "Key 名称不能超过 120 个字符"

    raw_scopes = payload.get("scopes", fallback_scopes or DEVELOPER_API_KEY_DEFAULT_SCOPES)
    if isinstance(raw_scopes, (list, tuple)):
        scopes = [str(item).strip() for item in raw_scopes if str(item).strip()]
    else:
        scopes = _developer_scopes(raw_scopes)
    scopes = list(dict.fromkeys(scopes))
    invalid_scopes = sorted(set(scopes) - DEVELOPER_API_KEY_ALLOWED_SCOPES)
    if invalid_scopes:
        return None, f"不支持的权限范围: {', '.join(invalid_scopes)}"
    if not any(scope in {"image:fast", "image:swarm"} for scope in scopes):
        return None, "请至少启用快速检测或 Swarm 检测权限"
    expiry_raw = payload.get("expiresAt", payload.get("expires_at", fallback_expiry))
    expires_at, expiry_error = _normalize_developer_expiry(expiry_raw)
    if expiry_error:
        return None, expiry_error

    ips_raw = payload.get("ipAllowlist", payload.get("ip_allowlist", fallback_ips))
    ip_allowlist, ip_error = _normalize_developer_ip_allowlist(ips_raw)
    if ip_error:
        return None, ip_error
    return {
        "name": name,
        "scopes": ",".join(scopes),
        "expires_at": expires_at,
        "ip_allowlist": ",".join(ip_allowlist),
    }, None


def _create_developer_key_with_limit(user_id, options):
    """Serialize the active-key limit and insert on the owning account row."""
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT Userid FROM `user` WHERE Userid = %s FOR UPDATE",
                (user_id,),
            )
            if not cursor.fetchone():
                conn.rollback()
                return None, None, "开发者账号不存在"
            api_key = options.get("_api_key_override") or f"{DEVELOPER_API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
            key_hash = _developer_key_hash(api_key)
            if options.get("_api_key_override"):
                cursor.execute(
                    """
                    SELECT id, name, key_prefix, key_last4, scopes, status, created_at, last_used_at,
                           revoked_at, expires_at, ip_allowlist
                    FROM developer_api_keys
                    WHERE user_id = %s AND key_hash = %s
                    LIMIT 1
                    """,
                    (user_id, key_hash),
                )
                existing = cursor.fetchone()
                if existing:
                    comparable = (
                        str(existing.get("name") or "") == options["name"]
                        and str(existing.get("scopes") or "") == options["scopes"]
                        and str(existing.get("ip_allowlist") or "") == options["ip_allowlist"]
                        and format_createtime(existing.get("expires_at"))
                        == format_createtime(options["expires_at"])
                    )
                    if not comparable:
                        conn.rollback()
                        return None, None, "该 Idempotency-Key 已用于其他 API Key 参数"
                    conn.commit()
                    return api_key, existing, None
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM developer_api_keys WHERE user_id = %s AND status = 'active'",
                (user_id,),
            )
            active_count = int((cursor.fetchone() or {}).get("cnt") or 0)
            if active_count >= DEVELOPER_API_KEY_MAX_ACTIVE:
                conn.rollback()
                return None, None, f"最多只能保留 {DEVELOPER_API_KEY_MAX_ACTIVE} 个 active API Key"

            cursor.execute(
                """
                INSERT INTO developer_api_keys
                    (user_id, name, key_hash, key_prefix, key_last4, scopes, status, expires_at, ip_allowlist)
                VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s)
                """,
                (
                    user_id,
                    options["name"],
                    key_hash,
                    DEVELOPER_API_KEY_PREFIX,
                    api_key[-4:],
                    options["scopes"],
                    options["expires_at"],
                    options["ip_allowlist"],
                ),
            )
            row_id = cursor.lastrowid
            cursor.execute(
                """
                SELECT id, name, key_prefix, key_last4, scopes, status, created_at, last_used_at,
                       revoked_at, expires_at, ip_allowlist
                FROM developer_api_keys
                WHERE id = %s AND user_id = %s
                LIMIT 1
                """,
                (row_id, user_id),
            )
            row = cursor.fetchone()
            if not row:
                raise RuntimeError("API Key 写入后无法读取")
            _append_security_audit(
                cursor,
                "account",
                user_id,
                "developer_api_key.create",
                f"key:{row_id}",
                {"name": options["name"], "scopes": options["scopes"], "last4": api_key[-4:]},
            )
        conn.commit()
        return api_key, row, None
    except Exception as exc:
        if conn:
            conn.rollback()
        print(f"[API KEY CREATE ERROR] {exc}")
        return None, None, "创建 API Key 失败，请稍后重试"
    finally:
        if conn:
            conn.close()


def _rotate_developer_key_atomic(user_id, key_id, payload):
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute("SELECT Userid FROM `user` WHERE Userid = %s FOR UPDATE", (user_id,))
            if not cursor.fetchone():
                conn.rollback()
                return None, None, "开发者账号不存在", 404
            cursor.execute(
                """
                SELECT id, name, scopes, status, expires_at, ip_allowlist
                FROM developer_api_keys
                WHERE id = %s AND user_id = %s
                LIMIT 1 FOR UPDATE
                """,
                (key_id, user_id),
            )
            current = cursor.fetchone()
            if not current:
                conn.rollback()
                return None, None, "API Key 不存在或已撤销", 404
            options, options_error = _developer_key_options(
                payload,
                fallback_name=current.get("name") or "Default key",
                fallback_scopes=current.get("scopes"),
                fallback_expiry=current.get("expires_at"),
                fallback_ips=current.get("ip_allowlist"),
            )
            if options_error:
                conn.rollback()
                return None, None, options_error, 400
            api_key = payload.get("_api_key_override") or f"{DEVELOPER_API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
            key_hash = _developer_key_hash(api_key)
            if payload.get("_api_key_override"):
                cursor.execute(
                    """
                    SELECT id, name, key_prefix, key_last4, scopes, status, created_at, last_used_at,
                           revoked_at, expires_at, ip_allowlist
                    FROM developer_api_keys
                    WHERE user_id = %s AND key_hash = %s
                    LIMIT 1
                    """,
                    (user_id, key_hash),
                )
                existing = cursor.fetchone()
                if existing:
                    comparable = (
                        str(existing.get("name") or "") == options["name"]
                        and str(existing.get("scopes") or "") == options["scopes"]
                        and str(existing.get("ip_allowlist") or "") == options["ip_allowlist"]
                        and format_createtime(existing.get("expires_at"))
                        == format_createtime(options["expires_at"])
                    )
                    if not comparable:
                        conn.rollback()
                        return None, None, "该 Idempotency-Key 已用于其他轮换参数", 409
                    if current.get("status") != "revoked":
                        conn.rollback()
                        return None, None, "API Key 轮换状态不一致", 409
                    conn.commit()
                    return api_key, existing, None, 200
            if current.get("status") != "active":
                conn.rollback()
                return None, None, "API Key 不存在或已撤销", 404
            cursor.execute(
                """
                INSERT INTO developer_api_keys
                    (user_id, name, key_hash, key_prefix, key_last4, scopes, status, expires_at, ip_allowlist)
                VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s)
                """,
                (
                    user_id,
                    options["name"],
                    key_hash,
                    DEVELOPER_API_KEY_PREFIX,
                    api_key[-4:],
                    options["scopes"],
                    options["expires_at"],
                    options["ip_allowlist"],
                ),
            )
            row_id = cursor.lastrowid
            cursor.execute(
                "UPDATE developer_api_keys SET status = 'revoked', revoked_at = NOW() "
                "WHERE id = %s AND user_id = %s AND status = 'active'",
                (key_id, user_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("原 API Key 状态已变化")
            cursor.execute(
                """
                SELECT id, name, key_prefix, key_last4, scopes, status, created_at, last_used_at,
                       revoked_at, expires_at, ip_allowlist
                FROM developer_api_keys WHERE id = %s AND user_id = %s LIMIT 1
                """,
                (row_id, user_id),
            )
            row = cursor.fetchone()
            _append_security_audit(
                cursor,
                "account",
                user_id,
                "developer_api_key.rotate",
                f"key:{key_id}",
                {"replacementKeyId": row_id, "scopes": options["scopes"], "last4": api_key[-4:]},
            )
        conn.commit()
        return api_key, row, None, 200
    except Exception as exc:
        if conn:
            conn.rollback()
        print(f"[API KEY ROTATE ERROR] {exc}")
        return None, None, "轮换 API Key 失败，请稍后重试", 500
    finally:
        if conn:
            conn.close()


def _revoke_developer_key_atomic(user_id, key_id):
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, name, scopes, status FROM developer_api_keys "
                "WHERE id = %s AND user_id = %s LIMIT 1 FOR UPDATE",
                (key_id, user_id),
            )
            current = cursor.fetchone()
            if not current:
                conn.rollback()
                return False, "API Key 不存在或已撤销", 404
            if current.get("status") == "revoked":
                conn.commit()
                return True, None, 200
            if current.get("status") != "active":
                conn.rollback()
                return False, "API Key 当前状态不允许撤销", 409
            cursor.execute(
                "UPDATE developer_api_keys SET status = 'revoked', revoked_at = NOW() "
                "WHERE id = %s AND user_id = %s AND status = 'active'",
                (key_id, user_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("API Key 状态已变化")
            _append_security_audit(
                cursor,
                "account",
                user_id,
                "developer_api_key.revoke",
                f"key:{key_id}",
                {"name": current.get("name") or "", "scopes": current.get("scopes") or ""},
            )
        conn.commit()
        return True, None, 200
    except Exception as exc:
        if conn:
            conn.rollback()
        print(f"[API KEY REVOKE ERROR] {exc}")
        return False, "撤销 API Key 失败，请稍后重试", 500
    finally:
        if conn:
            conn.close()


def _ensure_developer_api_key_table():
    global _DEVELOPER_KEY_TABLE_READY
    if _DEVELOPER_KEY_TABLE_READY:
        return True
    result = excute_sql(
        """
        CREATE TABLE IF NOT EXISTS developer_api_keys (
          id BIGINT NOT NULL AUTO_INCREMENT,
          user_id INT NOT NULL,
          name VARCHAR(120) NOT NULL,
          key_hash CHAR(64) NOT NULL,
          key_prefix VARCHAR(16) NOT NULL DEFAULT 'rg_sk_',
          key_last4 CHAR(4) NOT NULL,
          scopes VARCHAR(255) NOT NULL DEFAULT 'image:fast,image:swarm,reports',
          status VARCHAR(16) NOT NULL DEFAULT 'active',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          last_used_at DATETIME NULL,
          revoked_at DATETIME NULL,
          expires_at DATETIME NULL,
          ip_allowlist TEXT NULL,
          last_used_ip VARCHAR(64) NULL,
          PRIMARY KEY (id),
          UNIQUE KEY uk_developer_api_key_hash (key_hash),
          KEY idx_developer_api_keys_user_status (user_id, status),
          KEY idx_developer_api_keys_created_at (created_at),
          CONSTRAINT fk_developer_api_keys_user
            FOREIGN KEY (user_id) REFERENCES `user`(Userid)
            ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        fetch=False,
    )
    if result is not None:
        result = all((
            _ensure_column("developer_api_keys", "expires_at", "DATETIME NULL COMMENT '密钥有效期'"),
            _ensure_column("developer_api_keys", "ip_allowlist", "TEXT NULL COMMENT '逗号分隔的 IP/CIDR 白名单'"),
            _ensure_column("developer_api_keys", "last_used_ip", "VARCHAR(64) NULL COMMENT '最近调用 IP'"),
            _ensure_security_audit_tables(),
            admin_state.ensure_api_key_quota_storage(),
            admin_state.sync_api_key_quotas_to_db(),
        ))
    _DEVELOPER_KEY_TABLE_READY = result is not None and bool(result)
    return _DEVELOPER_KEY_TABLE_READY


def _ensure_developer_usage_table():
    global _DEVELOPER_USAGE_TABLE_READY
    if _DEVELOPER_USAGE_TABLE_READY:
        return True
    if not _ensure_developer_api_key_table():
        return False
    result = excute_sql(
        """
        CREATE TABLE IF NOT EXISTS developer_usage_events (
          id BIGINT NOT NULL AUTO_INCREMENT,
          task_id VARCHAR(64) NULL,
          user_id INT NOT NULL,
          key_id BIGINT NULL,
          pipeline VARCHAR(32) NOT NULL,
          endpoint VARCHAR(160) NOT NULL,
          model_version VARCHAR(120) NULL,
          status_code INT NOT NULL DEFAULT 200,
          prompt_tokens INT NOT NULL DEFAULT 0,
          completion_tokens INT NOT NULL DEFAULT 0,
          total_tokens INT NOT NULL DEFAULT 0,
          billable TINYINT(1) NOT NULL DEFAULT 0,
          decision_status VARCHAR(24) NOT NULL DEFAULT 'review_only',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (id),
          UNIQUE KEY uk_developer_usage_task (task_id),
          KEY idx_developer_usage_user_created (user_id, created_at),
          KEY idx_developer_usage_key_created (key_id, created_at),
          KEY idx_developer_usage_pipeline_created (pipeline, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        fetch=False,
    )
    if result is not None:
        result = _ensure_column(
            "developer_usage_events",
            "task_id",
            "VARCHAR(64) NULL COMMENT '可幂等恢复的开发者任务标识' AFTER id",
        )
    if result is not None:
        result = _ensure_column(
            "developer_usage_events",
            "billable",
            "TINYINT(1) NOT NULL DEFAULT 0 AFTER total_tokens",
        )
    if result is not None:
        result = _ensure_column(
            "developer_usage_events",
            "decision_status",
            "VARCHAR(24) NOT NULL DEFAULT 'review_only' AFTER billable",
        )
    if result is not None:
        result = excute_sql(
            """
            ALTER TABLE developer_usage_events
              MODIFY billable TINYINT(1) NOT NULL DEFAULT 0,
              MODIFY decision_status VARCHAR(24) NOT NULL DEFAULT 'review_only'
            """,
            fetch=False,
        ) is not None
    if result:
        indexes = excute_sql("SHOW INDEX FROM developer_usage_events WHERE Key_name = %s", ("uk_developer_usage_task",))
        if indexes == []:
            result = excute_sql(
                "ALTER TABLE developer_usage_events ADD UNIQUE INDEX uk_developer_usage_task (task_id)",
                fetch=False,
            ) is not None
        elif indexes is None:
            result = False
    _DEVELOPER_USAGE_TABLE_READY = bool(result)
    return _DEVELOPER_USAGE_TABLE_READY


def _developer_key_from_request():
    payload = request.get_json(silent=True) or {}
    bearer = request.headers.get("authorization", "").strip()
    if bearer.lower().startswith("bearer "):
        bearer = bearer[7:].strip()
    else:
        bearer = ""
    return (
        request.headers.get("x-realguard-key", "").strip()
        or request.headers.get("x-realguard-api-key", "").strip()
        or request.headers.get("x-api-key", "").strip()
        or bearer
        or str(payload.get("api_key") or payload.get("apiKey") or "").strip()
    )


def _require_internal_developer_auth():
    if not DEVELOPER_AUTH_SECRET:
        return False
    submitted = request.headers.get("x-realguard-internal-secret", "").strip()
    return hmac.compare_digest(submitted, DEVELOPER_AUTH_SECRET)


def _active_developer_key(api_key, *, touch=True):
    if not _ensure_developer_api_key_table():
        return None, "API Key 存储初始化失败"
    if not api_key.startswith(DEVELOPER_API_KEY_PREFIX):
        return None, None
    rows = excute_sql(
        """
        SELECT k.id, k.user_id, k.name, k.scopes, k.status, k.expires_at, k.ip_allowlist,
               u.account_uuid, u.phone, u.username, u.openid
        FROM developer_api_keys k
        JOIN user u ON u.Userid = k.user_id
        WHERE k.key_hash = %s
        LIMIT 1
        """,
        (_developer_key_hash(api_key),),
    )
    if rows is None:
        return None, "API Key 校验失败"
    if not rows or rows[0].get("status") != "active":
        return None, None

    row = rows[0]
    expires_at = row.get("expires_at")
    if expires_at:
        if not isinstance(expires_at, datetime):
            try:
                expires_at = datetime.fromisoformat(str(expires_at))
            except ValueError:
                return None, "API Key 有效期数据异常"
        if expires_at <= datetime.now():
            return None, None
    if not _developer_ip_allowed(row.get("ip_allowlist")):
        return None, "当前来源 IP 不在该 API Key 的白名单中"
    if touch:
        excute_sql(
            """
            UPDATE developer_api_keys
            SET last_used_at = NOW(), last_used_ip = %s
            WHERE id = %s
            """,
            (_developer_request_ip()[:64], row["id"]),
            fetch=False,
        )
    return row, None


def _quota_retry_after(now, *, daily=False):
    if daily:
        boundary = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        boundary = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    return max(1, int((boundary - now).total_seconds()) + 1)


def _consume_developer_key_request(api_key):
    """Authenticate and atomically consume the account-level request-rate quota."""
    if not _ensure_developer_api_key_table():
        return None, "API Key 存储初始化失败", "storage_unavailable", None
    if not api_key.startswith(DEVELOPER_API_KEY_PREFIX):
        return None, None, None, None

    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            # Discover the owner without taking a row lock, then use the same
            # account -> key lock order as create/rotate/revoke operations.
            cursor.execute(
                """
                SELECT k.user_id
                FROM developer_api_keys k
                WHERE k.key_hash = %s
                LIMIT 1
                """,
                (_developer_key_hash(api_key),),
            )
            owner = cursor.fetchone()
            if not owner:
                conn.rollback()
                return None, None, None, None
            cursor.execute(
                "SELECT Userid FROM `user` WHERE Userid = %s FOR UPDATE",
                (owner["user_id"],),
            )
            if not cursor.fetchone():
                conn.rollback()
                return None, "开发者账号不存在", "storage_unavailable", None

            cursor.execute(
                """
                SELECT k.id, k.user_id, k.name, k.scopes, k.status, k.expires_at,
                       k.ip_allowlist, u.account_uuid, u.phone, u.username, u.openid,
                       q.daily_limit, q.rate_limit_per_minute,
                       NOW() AS quota_now
                FROM developer_api_keys k
                JOIN user u ON u.Userid = k.user_id
                LEFT JOIN developer_api_account_quotas q ON q.user_id = k.user_id
                WHERE k.key_hash = %s AND k.user_id = %s
                LIMIT 1
                FOR UPDATE
                """,
                (_developer_key_hash(api_key), owner["user_id"]),
            )
            row = cursor.fetchone()
            if not row or row.get("status") != "active":
                conn.rollback()
                return None, None, None, None

            now = row.get("quota_now")
            if not isinstance(now, datetime):
                now = datetime.now()
            expires_at = row.get("expires_at")
            if expires_at and not isinstance(expires_at, datetime):
                try:
                    expires_at = datetime.fromisoformat(str(expires_at))
                except ValueError:
                    conn.rollback()
                    return None, "API Key 有效期数据异常", "storage_unavailable", None
            if expires_at and expires_at <= now:
                conn.rollback()
                return None, None, None, None
            if not _developer_ip_allowed(row.get("ip_allowlist")):
                conn.rollback()
                return None, "当前来源 IP 不在该 API Key 的白名单中", "ip_not_allowed", None

            # Read-only polling remains protected by the edge IP limiter, but
            # must not consume the account's detection-submission rate budget.
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                day_bucket = now.date()
                minute_bucket = now.replace(second=0, microsecond=0)
                cursor.execute(
                    """
                    SELECT day_bucket, daily_count, minute_bucket, minute_count
                    FROM developer_api_account_quota_usage
                    WHERE user_id = %s
                    FOR UPDATE
                    """,
                    (row["user_id"],),
                )
                usage = cursor.fetchone() or {}
                daily_count = int(usage.get("daily_count") or 0) if usage.get("day_bucket") == day_bucket else 0
                minute_count = int(usage.get("minute_count") or 0) if usage.get("minute_bucket") == minute_bucket else 0
                minute_limit = row.get("rate_limit_per_minute")
                if minute_limit is not None and minute_count >= int(minute_limit):
                    conn.rollback()
                    return row, "该账号请求过于频繁", "rate_limit_exceeded", _quota_retry_after(now)

                cursor.execute(
                    """
                    INSERT INTO developer_api_account_quota_usage
                        (user_id, day_bucket, daily_count, minute_bucket, minute_count)
                    VALUES (%s, %s, %s, %s, 1)
                    ON DUPLICATE KEY UPDATE
                        day_bucket = VALUES(day_bucket),
                        daily_count = %s,
                        minute_bucket = VALUES(minute_bucket),
                        minute_count = %s
                    """,
                    (
                        row["user_id"],
                        day_bucket,
                        daily_count,
                        minute_bucket,
                        daily_count,
                        minute_count + 1,
                    ),
                )
            cursor.execute(
                """
                UPDATE developer_api_keys
                SET last_used_at = NOW(), last_used_ip = %s
                WHERE id = %s AND status = 'active'
                """,
                (_developer_request_ip()[:64], row["id"]),
            )
        conn.commit()
        return row, None, None, None
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        print(f"[API KEY QUOTA ERROR] {exc}")
        return None, "API Key 配额服务暂不可用", "storage_unavailable", None
    finally:
        if conn is not None:
            conn.close()


def _change_developer_daily_detection_quota(actor, delta):
    """Reserve or release one accepted detection against the account daily limit."""
    user_id = int((actor or {}).get("user_id") or 0)
    if user_id <= 0 or delta not in {-1, 1}:
        return "开发者账号数据异常", "storage_unavailable", None
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
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
            daily_count = int(usage.get("daily_count") or 0) if usage.get("day_bucket") == day_bucket else 0
            daily_limit = account.get("daily_limit")
            if delta > 0 and daily_limit is not None and daily_count >= int(daily_limit):
                conn.rollback()
                return "该账号已达到每日调用上限", "daily_limit_exceeded", _quota_retry_after(now, daily=True)
            next_count = max(0, daily_count + delta)
            minute_bucket = now.replace(second=0, microsecond=0)
            cursor.execute(
                """
                INSERT INTO developer_api_account_quota_usage
                    (user_id, day_bucket, daily_count, minute_bucket, minute_count)
                VALUES (%s, %s, %s, %s, 0)
                ON DUPLICATE KEY UPDATE
                    day_bucket = VALUES(day_bucket),
                    daily_count = VALUES(daily_count)
                """,
                (user_id, day_bucket, next_count, minute_bucket),
            )
        conn.commit()
        return None, None, None
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        print(f"[API DAILY QUOTA ERROR] {exc}")
        return "API Key 配额服务暂不可用", "storage_unavailable", None
    finally:
        if conn is not None:
            conn.close()


def _reserve_developer_daily_detection(actor):
    error, code, retry_after = _change_developer_daily_detection_quota(actor, 1)
    if not error:
        return None
    response = jsonify({"status": "error", "code": code, "message": error})
    status = 429 if code == "daily_limit_exceeded" else 503
    if retry_after:
        response.headers["Retry-After"] = str(retry_after)
    return response, status


def _release_developer_daily_detection(actor):
    error, _, _ = _change_developer_daily_detection_quota(actor, -1)
    return error is None


def _developer_key_required():
    row, error, error_code, retry_after = _consume_developer_key_request(_developer_key_from_request())
    if error_code in {"daily_limit_exceeded", "rate_limit_exceeded"}:
        response = jsonify({"status": "error", "code": error_code, "message": error})
        response.headers["Retry-After"] = str(retry_after)
        return None, (response, 429)
    if error:
        status_code = 403 if error_code == "ip_not_allowed" else 503
        return None, (jsonify({"status": "error", "code": error_code, "message": error}), status_code)
    if not row:
        return None, (jsonify({"status": "error", "message": "API Key 缺失、无效或已撤销"}), 401)
    return row, None


def _usage_int(value):
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _usage_bucket():
    return {
        "requests": 0,
        "billableRequests": 0,
        "cacheHits": 0,
        "promptTokens": 0,
        "completionTokens": 0,
        "totalTokens": 0,
    }


def _record_developer_usage_event(
    actor,
    *,
    pipeline,
    endpoint,
    model_version,
    status_code,
    prompt_tokens=0,
    completion_tokens=0,
    total_tokens=0,
    task_id=None,
    billable=True,
    decision_status="verdict",
):
    if not actor or not _ensure_developer_usage_table():
        return False
    result = excute_sql(
        """
        INSERT IGNORE INTO developer_usage_events
          (task_id, user_id, key_id, pipeline, endpoint, model_version, status_code,
           prompt_tokens, completion_tokens, total_tokens, billable, decision_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            task_id,
            actor.get("user_id"),
            actor.get("id"),
            pipeline,
            endpoint,
            model_version,
            int(status_code),
            _usage_int(prompt_tokens),
            _usage_int(completion_tokens),
            _usage_int(total_tokens),
            1 if billable else 0,
            str(decision_status or "review_only")[:24],
        ),
        fetch=False,
    )
    return result is not None


def _developer_usage_from_v1(user_id, days):
    if not _ensure_developer_usage_table():
        raise RuntimeError("V1 调用统计存储初始化失败")
    since = datetime.now() - timedelta(days=days - 1)
    rows = excute_sql(
        """
        SELECT created_at, key_id, pipeline, endpoint, model_version, status_code,
               prompt_tokens, completion_tokens, total_tokens, billable, decision_status
        FROM developer_usage_events
        WHERE user_id = %s AND created_at >= %s
        ORDER BY created_at ASC
        """,
        (user_id, since.strftime("%Y-%m-%d %H:%M:%S")),
    )
    if rows is None:
        raise RuntimeError("读取 V1 调用统计失败")

    summary = {**_usage_bucket(), "lastEventAt": None}
    by_day = defaultdict(_usage_bucket)
    by_endpoint = defaultdict(_usage_bucket)
    by_model = defaultdict(_usage_bucket)
    by_key = defaultdict(_usage_bucket)

    for row in rows:
        prompt_tokens = _usage_int(row.get("prompt_tokens"))
        completion_tokens = _usage_int(row.get("completion_tokens"))
        total_tokens = _usage_int(row.get("total_tokens")) or prompt_tokens + completion_tokens
        created_at = row.get("created_at")
        created_text = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or "")
        day = created_text[:10]
        endpoint = str(row.get("endpoint") or "unknown")
        model = str(row.get("model_version") or "realguard-v1")
        key = str(row.get("key_id") or "unknown")

        summary["requests"] += 1
        is_billable = bool(
            int(row.get("billable") or 0) == 1
            and str(row.get("decision_status") or "review_only") == "verdict"
        )
        summary["billableRequests"] += 1 if is_billable else 0
        summary["promptTokens"] += prompt_tokens
        summary["completionTokens"] += completion_tokens
        summary["totalTokens"] += total_tokens
        summary["lastEventAt"] = created_text
        for bucket in (by_day[day], by_endpoint[endpoint], by_model[model], by_key[key]):
            bucket["requests"] += 1
            bucket["billableRequests"] += 1 if is_billable else 0
            bucket["promptTokens"] += prompt_tokens
            bucket["completionTokens"] += completion_tokens
            bucket["totalTokens"] += total_tokens

    days_list = []
    for index in range(days):
        day = (since + timedelta(days=index)).strftime("%Y-%m-%d")
        days_list.append({"date": day, **by_day[day]})

    return {
        "days": days,
        "summary": {
            "totalRequests": summary["requests"],
            "billableRequests": summary["billableRequests"],
            "cacheHits": summary["cacheHits"],
            "promptTokens": summary["promptTokens"],
            "completionTokens": summary["completionTokens"],
            "totalTokens": summary["totalTokens"],
            "lastEventAt": summary["lastEventAt"],
        },
        "byDay": days_list,
        "byEndpoint": [{"pipeline": "v1", "endpoint": key, **value} for key, value in sorted(by_endpoint.items())],
        "byModel": [{"pipeline": "v1", "modelVersion": key, **value} for key, value in sorted(by_model.items())],
        "byKey": [{"pipeline": "v1", "keyId": key, **value} for key, value in sorted(by_key.items())],
    }


def _developer_usage_from_v2(user_id, days):
    if not DEVELOPER_AUTH_SECRET or not DEVELOPER_USAGE_URL:
        raise RuntimeError("Token 用量统计服务未配置")
    with requests.Session() as sess:
        sess.trust_env = False
        response = sess.get(
            DEVELOPER_USAGE_URL,
            params={"developerUserId": str(user_id), "days": str(days)},
            headers={"X-RealGuard-Internal-Secret": DEVELOPER_AUTH_SECRET},
            timeout=8,
        )
    response.raise_for_status()
    return response.json()


def _empty_developer_usage(days):
    since = datetime.now() - timedelta(days=days - 1)
    return {
        "days": days,
        "summary": {
            "totalRequests": 0,
            "billableRequests": 0,
            "cacheHits": 0,
            "promptTokens": 0,
            "completionTokens": 0,
            "totalTokens": 0,
            "lastEventAt": None,
        },
        "byDay": [{"date": (since + timedelta(days=index)).strftime("%Y-%m-%d"), **_usage_bucket()} for index in range(days)],
        "byEndpoint": [],
        "byModel": [],
        "byKey": [],
    }


def _merge_developer_usage(v1_usage, v2_usage, days):
    v1_summary = v1_usage.get("summary") or {}
    v2_summary = v2_usage.get("summary") or {}
    v1_calls = _usage_int(v1_summary.get("totalRequests"))
    v2_calls = _usage_int(v2_summary.get("totalRequests"))
    last_events = [value for value in (v1_summary.get("lastEventAt"), v2_summary.get("lastEventAt")) if value]

    day_rows = {}
    for pipeline, payload in (("v1", v1_usage), ("v2", v2_usage)):
        for row in payload.get("byDay") or []:
            date = str(row.get("date") or "")
            bucket = day_rows.setdefault(date, {"date": date, **_usage_bucket(), "v1Calls": 0, "v2Calls": 0})
            requests_count = _usage_int(row.get("requests"))
            bucket["requests"] += requests_count
            bucket[f"{pipeline}Calls"] += requests_count
            for field in ("billableRequests", "cacheHits", "promptTokens", "completionTokens", "totalTokens"):
                bucket[field] += _usage_int(row.get(field))

    by_day = []
    since = datetime.now() - timedelta(days=days - 1)
    for index in range(days):
        date = (since + timedelta(days=index)).strftime("%Y-%m-%d")
        by_day.append(day_rows.get(date, {"date": date, **_usage_bucket(), "v1Calls": 0, "v2Calls": 0}))

    def with_pipeline(pipeline, rows):
        return [{**row, "pipeline": row.get("pipeline") or pipeline} for row in (rows or [])]

    return {
        "days": days,
        "summary": {
            "totalCalls": v1_calls + v2_calls,
            "totalRequests": v1_calls + v2_calls,
            "v1Calls": v1_calls,
            "v2Calls": v2_calls,
            "billableRequests": _usage_int(v1_summary.get("billableRequests")) + _usage_int(v2_summary.get("billableRequests")),
            "cacheHits": _usage_int(v1_summary.get("cacheHits")) + _usage_int(v2_summary.get("cacheHits")),
            "promptTokens": _usage_int(v1_summary.get("promptTokens")) + _usage_int(v2_summary.get("promptTokens")),
            "completionTokens": _usage_int(v1_summary.get("completionTokens")) + _usage_int(v2_summary.get("completionTokens")),
            "totalTokens": _usage_int(v1_summary.get("totalTokens")) + _usage_int(v2_summary.get("totalTokens")),
            "lastEventAt": max(last_events) if last_events else None,
        },
        "byDay": by_day,
        "byEndpoint": with_pipeline("v1", v1_usage.get("byEndpoint")) + with_pipeline("v2", v2_usage.get("byEndpoint")),
        "byModel": with_pipeline("v1", v1_usage.get("byModel")) + with_pipeline("v2", v2_usage.get("byModel")),
        "byKey": with_pipeline("v1", v1_usage.get("byKey")) + with_pipeline("v2", v2_usage.get("byKey")),
        "byPipeline": [
            {"pipeline": "v1", "requests": v1_calls, "totalTokens": _usage_int(v1_summary.get("totalTokens"))},
            {"pipeline": "v2", "requests": v2_calls, "totalTokens": _usage_int(v2_summary.get("totalTokens"))},
        ],
    }


def _history_identity(allow_empty=False):
    user = _current_user()
    if user:
        return {
            "mode": "user",
            "user_id": user.get("Userid") or user.get("userId") or user.get("id"),
            "account_uuid": str(user.get("account_uuid") or "").strip(),
            "phone": str(user.get("phone") or "").strip(),
            "openid": str(user.get("openid") or "").strip(),
        }, None
    guest_openid = str(session.get("guest_openid") or "").strip()
    if guest_openid:
        return {"mode": "guest", "phone": "", "openid": guest_openid}, None
    if allow_empty:
        return {"mode": "anonymous", "phone": "", "openid": ""}, None
    return None, (jsonify({"status": "error", "message": "用户未登录"}), 401)


def _history_actor_where(actor):
    phone = str((actor or {}).get("phone") or "").strip()
    openid = str((actor or {}).get("openid") or "").strip()
    account_uuid = normalize_account_uuid((actor or {}).get("account_uuid"))
    return detection_owner_where(
        phone,
        openid,
        account_uuid=account_uuid,
        require_account_uuid=(actor or {}).get("mode") == "user",
    )


def _is_guest_detection_record(item):
    return (not str((item or {}).get("phone") or "").strip()) and str((item or {}).get("openid") or "").startswith("guest-")


def _has_detection_metadata(itemid):
    rows = excute_detection_sql(
        "SELECT 1 AS ok FROM exif WHERE data_itemid = %s AND all_metadata IS NOT NULL AND all_metadata <> '' LIMIT 1",
        (itemid,),
    )
    return bool(rows)


def _history_visual_issue_count(item):
    issues = []
    in_issues = False
    for raw_line in str((item or {}).get("explantation") or (item or {}).get("explanation") or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "视觉可疑点" or line.startswith("视觉可疑点"):
            in_issues = True
            continue
        if in_issues:
            cleaned = line.lstrip("-·•*0123456789.、) ").strip()
            if cleaned:
                issues.append(cleaned)
    return len(issues)


def _thumbnail_url(item):
    itemid = item.get("itemid")
    return f"/api/media/thumbnail/image/{itemid}" if itemid else ""


def _backend_media_url(kind, item):
    filename = (item or {}).get("filename") or ""
    folder = (item or {}).get("openid") or (item or {}).get("phone") or "guest"
    if not filename:
        return ""
    return (
        f"{DETECTION_BACKEND_BASE_URL}/static/uploads/"
        f"{quote(str(folder), safe='')}/{kind}/{quote(str(filename), safe='')}"
    )


def _thumbnail_cache_path(item):
    key = "|".join(
        str(item.get(name, ""))
        for name in ("itemid", "openid", "phone", "filename", "createtime")
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return THUMBNAIL_CACHE_DIR / f"{digest}.webp"


def _history_limit(default=500):
    raw = str(request.args.get("limit", default) or default).strip()
    try:
        value = int(raw)
    except ValueError:
        return None, (jsonify({"status": "error", "message": "limit 必须为整数"}), 400)
    if value <= 0 or value > 1000:
        return None, (jsonify({"status": "error", "message": "limit 仅支持 1 到 1000"}), 400)
    return value, None


def _history_offset(default=0):
    raw = str(request.args.get("offset", default) or default).strip()
    try:
        value = int(raw)
    except ValueError:
        return None, (jsonify({"status": "error", "message": "offset 必须为整数"}), 400)
    if value < 0:
        return None, (jsonify({"status": "error", "message": "offset 不能小于 0"}), 400)
    return value, None


def _history_query():
    return str(request.args.get("query") or "").strip().lower()


def _contains_history_query(fields, query):
    if not query:
        return True
    return any(query in str(field or "").lower() for field in fields)


def _image_history_record(item, model_run=None):
    fake_pct = round(float(item.get("fake", 0) or 0), 1)
    issue_count = _history_visual_issue_count(item)
    stored_label = str(item.get("aigc") or "").strip()
    try:
        visible = evidence_manifest._structured_visible_watermark(model_run)
        decision = evidence_manifest._decision_authorization(model_run, visible)
    except Exception:
        decision = {"status": "review_only", "authority": "none"}
    stored_review = stored_label == "需人工复核"
    legacy_calibration_unknown = decision.get("status") != "verdict" and not stored_review
    review_required = stored_review or legacy_calibration_unknown
    display_fake_pct = None if review_required else fake_pct
    final_label = binary_final_label(stored_label, fake_pct)
    return {
        "itemid": item.get("itemid"),
        "filename": item.get("filename", ""),
        "image_url": f"/api/media/image/{item.get('itemid')}",
        "thumbnail_url": _thumbnail_url(item),
        "real_prob": None if display_fake_pct is None else round(100 - display_fake_pct, 1),
        "fake_prob": display_fake_pct,
        "final_label": final_label,
        "confidence": "低" if review_required else item.get("clarity", ""),
        "createtime": format_createtime(item.get("createtime", "")),
        "report_url": (
            f"/image_upload/report?itemid={item.get('itemid')}"
        ),
        "review_required": review_required,
        "decision_status": "review_only" if review_required else "verdict",
        "decision_authority": decision.get("authority") or "none",
        "legacy_calibration_unknown": legacy_calibration_unknown,
        "report_unavailable_reason": (
            f"二元结论为“{final_label}”，但缺少可验证的自动决策授权；结论置信度为低。"
            if legacy_calibration_unknown else ""
        ),
        "is_guest_record": _is_guest_detection_record(item),
        "has_metadata": _has_detection_metadata(item.get("itemid")),
        "has_visual_issues": issue_count > 0,
        "visual_issue_count": issue_count,
    }


def _image_history_search_fields(record):
    issue_count = int(record.get("visual_issue_count") or 0)
    return [
        record.get("filename", ""),
        record.get("final_label", ""),
        record.get("confidence", ""),
        record.get("createtime", ""),
        "访客" if record.get("is_guest_record") else "",
        "元数据" if record.get("has_metadata") else "",
        f"可疑点 {issue_count}" if issue_count > 0 else ("可疑点" if record.get("has_visual_issues") else ""),
        "结论",
        "置信度",
    ]


def _image_history_matches_filter(record, filter_key):
    if filter_key == "guest":
        return bool(record.get("is_guest_record"))
    if filter_key == "metadata":
        return bool(record.get("has_metadata"))
    if filter_key == "issues":
        return bool(record.get("has_visual_issues"))
    return True


def _video_history_record(item):
    final_label = binary_final_label(item.get("final_label"), item.get("fake"))
    return {
        "itemid": item.get("itemid"),
        "filename": item.get("filename", ""),
        "video_url": f"/api/media/video/{item.get('itemid')}",
        "real_percentage": None,
        "fake_percentage": None,
        "final_label": final_label,
        "confidence": "低",
        "decision_status": "review_only",
        "review_required": True,
        "createtime": format_createtime(item.get("createtime", "")),
        "report_url": f"/video_upload/report?itemid={item.get('itemid')}",
        "is_guest_record": _is_guest_detection_record(item),
    }


def _video_history_search_fields(record):
    return [
        record.get("filename", ""),
        record.get("final_label", ""),
        record.get("confidence", ""),
        record.get("createtime", ""),
        "访客" if record.get("is_guest_record") else "",
        "人工复核",
        "结论",
        "置信度",
    ]


def _video_history_matches_filter(record, filter_key):
    if filter_key == "guest":
        return bool(record.get("is_guest_record"))
    if filter_key == "review":
        return record.get("decision_status") == "review_only"
    return True


@api_blueprint.route("/me")
def me():
    user, error = _auth_required()
    if error:
        return jsonify({
            "status": "success",
            "authenticated": False,
            "user": None,
            "counters": {"image_detect": 0, "video_detect": 0},
        })

    phone = user.get("phone", "")
    actor = {
        "user_id": user.get("Userid") or user.get("userId") or user.get("id"),
        "account_uuid": user.get("account_uuid"),
        "phone": phone,
        "openid": user.get("openid", ""),
    }
    history_where, history_params = _history_actor_where(actor)
    counters = {
        "image_detect": 0,
        "video_detect": 0,
    }

    rows = excute_detection_sql(f"SELECT COUNT(*) AS cnt FROM data WHERE {history_where}", history_params)
    if rows:
        counters["image_detect"] = rows[0].get("cnt", 0)
    rows = excute_detection_sql(f"SELECT COUNT(*) AS cnt FROM video_data WHERE {history_where}", history_params)
    if rows:
        counters["video_detect"] = rows[0].get("cnt", 0)

    return jsonify({"status": "success", "authenticated": True, "user": user, "counters": counters})


@api_blueprint.route("/ready")
def ready():
    account_db = excute_sql("SELECT 1 AS ok")
    detection_db = excute_detection_sql("SELECT 1 AS ok")
    try:
        heartbeat_age = max(0.0, time.time() - DEVELOPER_WORKER_HEARTBEAT.stat().st_mtime)
        worker_health = json.loads(DEVELOPER_WORKER_HEARTBEAT.read_text(encoding="ascii"))
        worker_ready = (
            heartbeat_age <= DEVELOPER_WORKER_MAX_HEARTBEAT_AGE
            and worker_health.get("claimHealthy") is True
            and worker_health.get("maintenanceHealthy") is True
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        heartbeat_age = None
        worker_health = {}
        worker_ready = False
    detector_payload = {}
    try:
        with requests.Session() as sess:
            sess.trust_env = False
            detector_response = sess.get(
                DETECTOR_READY_URL,
                headers={"X-RealGuard-Detector-Token": DETECTOR_INTERNAL_TOKEN},
                timeout=(1, 4),
            )
        detector_payload = detector_response.json()
        detector_ready = (
            detector_response.status_code == 200
            and detector_payload.get("capabilityReady") is True
            and detector_payload.get("tokenReady") is True
        )
    except (requests.RequestException, TypeError, ValueError):
        detector_ready = False
    queue_rows = excute_sql(
        """
        SELECT COALESCE(SUM(queued), 0) AS queued,
               COALESCE(SUM(running), 0) AS running,
               COALESCE(SUM(queued + running), 0) AS pending,
               COALESCE(MAX(oldest_age_seconds), 0) AS oldest_age_seconds
        FROM (
          SELECT SUM(status IN ('preparing', 'queued')) AS queued,
                 SUM(status = 'running') AS running,
                 COALESCE(TIMESTAMPDIFF(
                   SECOND,
                   MIN(CASE WHEN status IN ('preparing', 'queued') THEN created_at END),
                   NOW()
                 ), 0) AS oldest_age_seconds
          FROM developer_detection_tasks
          WHERE status IN ('preparing', 'queued', 'running')
          UNION ALL
          SELECT SUM(status = 'queued') AS queued,
                 SUM(status = 'running') AS running,
                 COALESCE(TIMESTAMPDIFF(
                   SECOND,
                   MIN(CASE WHEN status = 'queued' THEN created_at END),
                   NOW()
                 ), 0) AS oldest_age_seconds
          FROM web_detection_tasks
          WHERE status IN ('queued', 'running')
        ) AS queues
        """
    )
    queue_pending = int((queue_rows or [{}])[0].get("pending") or 0) if queue_rows is not None else None
    queue_queued = int((queue_rows or [{}])[0].get("queued") or 0) if queue_rows is not None else None
    queue_running = int((queue_rows or [{}])[0].get("running") or 0) if queue_rows is not None else None
    queue_oldest_age = (
        int((queue_rows or [{}])[0].get("oldest_age_seconds") or 0)
        if queue_rows is not None else None
    )
    last_claim_at = float(worker_health.get("lastClaimCheckAt") or 0)
    active_tasks = int(worker_health.get("activeTasks") or 0)
    worker_capacity = max(1, int(worker_health.get("capacity") or 1))
    has_claim_capacity = active_tasks < worker_capacity
    if queue_queued and has_claim_capacity and (
        not last_claim_at or time.time() - last_claim_at > DEVELOPER_WORKER_MAX_HEARTBEAT_AGE
    ):
        worker_ready = False
    checks = {
        "accountDatabase": bool(account_db),
        "detectionDatabase": bool(detection_db),
        "developerWorker": worker_ready,
        "detectorModel": detector_ready,
        "queueState": queue_pending is not None,
    }
    remote_detector = (
        detector_payload.get("remoteInference")
        if isinstance(detector_payload.get("remoteInference"), dict)
        else {}
    )
    response = jsonify({
        "status": "ready" if all(checks.values()) else "not_ready",
        "checks": checks,
        "workerHeartbeatAgeSeconds": round(heartbeat_age, 2) if heartbeat_age is not None else None,
        "queuePending": queue_pending,
        "queueQueued": queue_queued,
        "queueRunning": queue_running,
        "queueOldestAgeSeconds": queue_oldest_age,
        "worker": {
            "claimHealthy": worker_health.get("claimHealthy") is True,
            "maintenanceHealthy": worker_health.get("maintenanceHealthy") is True,
            "activeTasks": active_tasks,
            "capacity": worker_capacity,
            "lastError": str(worker_health.get("lastError") or "")[:120],
        },
        "detector": {
            "provider": remote_detector.get("activeProvider") or detector_payload.get("activeProvider"),
            "cudaDeviceId": remote_detector.get("cudaDeviceId", detector_payload.get("cudaDeviceId")),
            "modelRevision": remote_detector.get("modelRevision"),
            "modelSha256": remote_detector.get("modelSha256"),
            "deploymentCommit": remote_detector.get("deploymentCommit"),
            "capabilityReady": detector_payload.get("capabilityReady") is True,
            "tokenReady": detector_payload.get("tokenReady") is True,
        },
    })
    response.status_code = 200 if all(checks.values()) else 503
    response.headers["Cache-Control"] = "no-store"
    return response


@api_blueprint.route("/login/password", methods=["POST"])
def login_password():
    payload = request.get_json(silent=True) or request.form
    phone = (payload.get("phone") or "").strip()
    secret = (payload.get("secret") or "").strip()
    accepted_terms = _truthy(payload.get("accepted_terms") or payload.get("acceptedTerms"))

    if not phone or not secret:
        return jsonify({"status": "error", "message": "请输入手机号和密码"}), 400
    if not accepted_terms:
        return jsonify({"status": "error", "message": "请先阅读并同意用户协议和隐私政策"}), 400
    try:
        _reserve_password_login_attempt(phone)
    except PasswordLoginRateLimitError as exc:
        response = jsonify({"status": "error", "code": "login_rate_limited", "message": str(exc)})
        response.headers["Retry-After"] = str(exc.retry_after)
        return response, 429
    except SmsStorageError as exc:
        return jsonify({"status": "error", "code": "login_protection_unavailable", "message": str(exc)}), 503

    user = _authenticate_password_user(phone, secret)
    if not user:
        return jsonify({"status": "error", "message": "手机号或密码错误"}), 401
    _clear_password_phone_attempts(phone)
    if not _record_terms_acceptance(phone):
        return jsonify({"status": "error", "message": "协议确认记录失败，请稍后重试"}), 500

    return jsonify({"status": "success", "user": _set_session_user(user, phone)})


@api_blueprint.route("/login/sms", methods=["POST"])
def login_sms():
    payload = request.get_json(silent=True) or request.form
    phone = (payload.get("phone") or "").strip()
    sms_code = (payload.get("sms_code") or "").strip()
    accepted_terms = _truthy(payload.get("accepted_terms") or payload.get("acceptedTerms"))

    if not _is_valid_phone(phone):
        return jsonify({"status": "error", "message": "请输入正确的手机号"}), 400
    if not accepted_terms:
        return jsonify({"status": "error", "message": "请先阅读并同意用户协议和隐私政策"}), 400
    ok, message = _verify_sms_code("login", phone, sms_code)
    if not ok:
        return jsonify({"status": "error", "message": message}), 400

    user = _find_user_by_phone(phone)
    if not user or not user.get("secret"):
        _begin_sms_password_setup(phone, user)
        return jsonify({
            "status": "success",
            "requiresPasswordSetup": True,
            "message": "手机号验证成功，请设置登录密码",
            "passwordSetupExpiresIn": SMS_PASSWORD_SETUP_TTL,
        })
    if not _record_terms_acceptance(phone):
        return jsonify({"status": "error", "message": "协议确认记录失败，请稍后重试"}), 500

    return jsonify({"status": "success", "user": _set_session_user(user, phone)})


@api_blueprint.route("/login/sms/complete", methods=["POST"])
def complete_sms_login():
    payload = request.get_json(silent=True) or request.form
    secret = str(payload.get("secret") or "").strip()
    secret_confirm = str(
        payload.get("secret_confirm") or payload.get("secretConfirm") or ""
    ).strip()
    ok, message, user = _complete_sms_password_setup(secret, secret_confirm)
    if not ok:
        return jsonify({
            "status": "error",
            "code": "password_setup_failed",
            "message": message,
        }), 400
    phone = str(user.get("phone") or "")
    return jsonify({
        "status": "success",
        "message": "密码设置成功",
        "user": _set_session_user(user, phone),
    })


@api_blueprint.route("/register", methods=["POST"])
def register():
    payload = request.get_json(silent=True) or request.form
    phone = (payload.get("phone") or "").strip()
    secret = (payload.get("secret") or "").strip()
    secret_confirm = str(
        payload.get("secret_confirm") or payload.get("secretConfirm") or ""
    )
    username = (payload.get("username") or "").strip() or phone
    sms_code = (payload.get("sms_code") or "").strip()
    accepted_terms = _truthy(payload.get("accepted_terms") or payload.get("acceptedTerms"))
    submitted_terms_version = str(
        payload.get("terms_version") or payload.get("termsVersion") or ""
    ).strip()

    if not _is_valid_phone(phone) or not secret:
        return jsonify({"status": "error", "message": "请输入正确的手机号和密码"}), 400
    password_error = _password_policy_error(secret)
    if password_error:
        return jsonify({"status": "error", "message": password_error}), 400
    if not secret_confirm:
        return jsonify({"status": "error", "message": "请再次输入密码"}), 400
    if not hmac.compare_digest(secret, secret_confirm):
        return jsonify({"status": "error", "message": "两次输入的密码不一致"}), 400
    if len(username) > 128:
        return jsonify({"status": "error", "message": "用户名不能超过 128 个字符"}), 400
    if not accepted_terms:
        return jsonify({"status": "error", "message": "请先阅读并同意用户协议和隐私政策"}), 400
    if submitted_terms_version != TERMS_VERSION:
        return jsonify({
            "status": "error",
            "code": "legal_documents_changed",
            "message": "用户协议或隐私政策已更新，请阅读当前版本后重新确认",
            "termsVersion": TERMS_VERSION,
        }), 428
    ok, message = _verify_sms_code("register", phone, sms_code)
    if not ok:
        return jsonify({"status": "error", "message": message}), 400
    if not _ensure_user_account_columns():
        return jsonify({"status": "error", "message": "账号表初始化失败，请稍后重试"}), 500

    rows = excute_sql("SELECT Userid FROM user WHERE phone = %s", (phone,))
    if rows:
        return jsonify({"status": "error", "message": "该手机号已注册，请直接登录"}), 409

    affected = excute_sql(
        """
        INSERT INTO user
            (account_uuid, phone, secret, username, openid, terms_version, terms_accepted_at, password_updated_at)
        VALUES (UUID(), %s, %s, %s, %s, %s, NOW(), NOW())
        """,
        (phone, _hash_password(secret), username, "", TERMS_VERSION),
        fetch=False,
    )
    if not affected:
        return jsonify({"status": "error", "message": "注册失败，请重试"}), 500
    if not _record_terms_acceptance(phone):
        return jsonify({"status": "error", "message": "协议确认记录失败，请稍后重试"}), 500

    created_user = _find_user_by_phone(phone) or {}
    _sync_detection_user(phone, username, phone, created_user.get("account_uuid"))
    return jsonify({"status": "success", "message": "注册成功，请登录"})


@api_blueprint.route("/password/reset", methods=["POST"])
def reset_password():
    payload = request.get_json(silent=True) or request.form
    phone = (payload.get("phone") or "").strip()
    secret = (payload.get("secret") or "").strip()
    sms_code = (payload.get("sms_code") or "").strip()

    if not _is_valid_phone(phone):
        return jsonify({"status": "error", "message": "请输入正确的手机号"}), 400
    password_error = _password_policy_error(secret)
    if password_error:
        return jsonify({"status": "error", "message": password_error}), 400
    ok, message = _verify_sms_code("reset", phone, sms_code)
    if not ok:
        return jsonify({"status": "error", "message": message}), 400
    if not _ensure_user_account_columns():
        return jsonify({"status": "error", "message": "账号表初始化失败，请稍后重试"}), 500

    user = _find_user_by_phone(phone)
    if not user:
        return jsonify({"status": "error", "message": "该手机号尚未注册，无法找回密码"}), 404
    affected = excute_sql(
        "UPDATE user SET secret = %s, session_version = session_version + 1, password_updated_at = NOW() WHERE phone = %s",
        (_hash_password(secret), phone),
        fetch=False,
    )
    if not affected:
        return jsonify({"status": "error", "message": "密码重置失败，请稍后重试"}), 500
    if _current_user() and str(_current_user().get("phone") or "") == phone:
        session.clear()
    return jsonify({"status": "success", "message": "密码已重置，请使用新密码登录"})


@api_blueprint.route("/logout", methods=["POST"])
def logout():
    if not revoke_current_user_sessions():
        return jsonify({"status": "error", "message": "退出失败，服务端会话尚未撤销"}), 503
    session.clear()
    return jsonify({"status": "success"})


@api_blueprint.route("/developer/keys", methods=["GET"])
def developer_api_keys():
    user, error = _auth_required()
    if error:
        return error
    if not _ensure_developer_api_key_table():
        return jsonify({"status": "error", "message": "API Key 存储初始化失败"}), 500

    rows = excute_sql(
        """
        SELECT id, name, key_prefix, key_last4, scopes, status, created_at, last_used_at,
               revoked_at, expires_at, ip_allowlist
        FROM developer_api_keys
        WHERE user_id = %s
        ORDER BY status = 'active' DESC, created_at DESC
        """,
        (user["Userid"],),
    )
    if rows is None:
        return jsonify({"status": "error", "message": "读取 API Key 失败"}), 500
    return jsonify({"status": "success", "keys": [_developer_key_payload(row) for row in rows]})


@api_blueprint.route("/developer/keys", methods=["POST"])
def create_developer_api_key():
    user, error = _auth_required()
    if error:
        return error
    if not _ensure_developer_api_key_table():
        return jsonify({"status": "error", "message": "API Key 存储初始化失败"}), 500

    payload = request.get_json(silent=True) or {}
    options, options_error = _developer_key_options(payload)
    if options_error:
        return jsonify({"status": "error", "message": options_error}), 400

    override, idempotency_error = _developer_operation_idempotency(user["Userid"], "create")
    if idempotency_error:
        return idempotency_error
    if override:
        options["_api_key_override"] = override
    api_key, row, create_error = _create_developer_key_with_limit(user["Userid"], options)
    if create_error:
        status = 409 if create_error.startswith("该 Idempotency-Key") else (400 if create_error.startswith("最多只能") else 500)
        return jsonify({"status": "error", "message": create_error}), status
    key_payload = _developer_key_payload(row or {})
    return jsonify({"status": "success", "apiKey": api_key, "key": key_payload})


@api_blueprint.route("/developer/keys/<int:key_id>/rotate", methods=["POST"])
def rotate_developer_api_key(key_id):
    user, error = _auth_required()
    if error:
        return error
    if not _ensure_developer_api_key_table():
        return jsonify({"status": "error", "message": "API Key 存储初始化失败"}), 500

    payload = dict(request.get_json(silent=True) or {})
    override, idempotency_error = _developer_operation_idempotency(
        user["Userid"], f"rotate:{key_id}"
    )
    if idempotency_error:
        return idempotency_error
    if override:
        payload["_api_key_override"] = override
    api_key, row, rotate_error, status = _rotate_developer_key_atomic(user["Userid"], key_id, payload)
    if rotate_error:
        return jsonify({"status": "error", "message": rotate_error}), status
    return jsonify({
        "status": "success",
        "apiKey": api_key,
        "key": _developer_key_payload(row or {}),
        "revoked": key_id,
    })


@api_blueprint.route("/developer/keys/<int:key_id>", methods=["DELETE"])
def revoke_developer_api_key(key_id):
    user, error = _auth_required()
    if error:
        return error
    if not _ensure_developer_api_key_table():
        return jsonify({"status": "error", "message": "API Key 存储初始化失败"}), 500

    revoked, revoke_error, status = _revoke_developer_key_atomic(user["Userid"], key_id)
    if not revoked:
        return jsonify({"status": "error", "message": revoke_error}), status
    return jsonify({"status": "success", "revoked": key_id})


@api_blueprint.route("/developer/keys/verify", methods=["POST"])
def verify_developer_api_key():
    if not _require_internal_developer_auth():
        return jsonify({"status": "error", "valid": False, "message": "内部鉴权失败"}), 403
    row, error = _active_developer_key(_developer_key_from_request(), touch=True)
    if error:
        return jsonify({"status": "error", "valid": False, "message": error}), 500
    if not row:
        return jsonify({"status": "success", "valid": False}), 200

    return jsonify({
        "status": "success",
        "valid": True,
        "keyId": row.get("id"),
        "userId": row.get("user_id"),
        "accountUuid": row.get("account_uuid"),
        "user": {
            "phone": row.get("phone") or "",
            "username": row.get("username") or "",
        },
        "scopes": _developer_scopes(row.get("scopes")),
    })


@api_blueprint.route("/developer/v1/detect", methods=["POST"])
def developer_v1_detect():
    _, error = _developer_key_required()
    if error:
        return error
    return jsonify({
        "status": "error",
        "message": "该接口已停用，请迁移到 /api/openapi/v1/image-detections",
        "migration": "/api/developer/openapi.json",
    }), 410


@api_blueprint.route("/developer/usage", methods=["GET"])
def developer_token_usage():
    user, error = _auth_required()
    if error:
        return error
    try:
        days = int(request.args.get("days", "30"))
    except ValueError:
        return jsonify({"status": "error", "message": "days 必须是整数"}), 400
    if days not in (7, 14, 30, 90):
        return jsonify({"status": "error", "message": "days 仅支持 7、14、30、90"}), 400

    try:
        v1_usage = _developer_usage_from_v1(user["Userid"], days)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 502

    v2_usage = _empty_developer_usage(days)
    try:
        v2_usage = _developer_usage_from_v2(user["Userid"], days)
    except Exception:
        pass

    usage = _merge_developer_usage(v1_usage, v2_usage, days)
    return jsonify({"status": "success", "usage": usage})


@api_blueprint.route("/history/image-detections")
def image_detection_history():
    actor, error = _history_identity(allow_empty=True)
    if error:
        return error
    limit, limit_error = _history_limit()
    if limit_error:
        return limit_error
    offset, offset_error = _history_offset()
    if offset_error:
        return offset_error
    query = _history_query()
    filter_key = str(request.args.get("filter") or "all").strip()
    if filter_key not in {"all", "guest", "metadata", "issues"}:
        return jsonify({"status": "error", "message": "filter 不受支持"}), 400

    if actor["mode"] == "guest":
        rows = excute_detection_sql(
            f"SELECT * FROM data WHERE Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s ORDER BY {HISTORY_ORDER_BY}",
            (actor["openid"],),
        )
    elif actor["mode"] == "anonymous":
        rows = []
    else:
        history_where, history_params = _history_actor_where(actor)
        rows = excute_detection_sql(
            f"SELECT * FROM data WHERE {history_where} ORDER BY {HISTORY_ORDER_BY}",
            history_params,
        )
    item_ids = [item.get("itemid") for item in (rows or []) if item.get("itemid")]
    try:
        model_runs = admin_state.model_runs_by_itemids(item_ids)
    except Exception:
        model_runs = {}
    query_records = []
    for item in rows or []:
        if not detection_record_is_publishable(item):
            continue
        record = _image_history_record(item, model_runs.get(str(item.get("itemid"))))
        if not _contains_history_query(_image_history_search_fields(record), query):
            continue
        query_records.append(record)
    filter_counts = {
        "all": len(query_records),
        "guest": sum(1 for record in query_records if record["is_guest_record"]),
        "metadata": sum(1 for record in query_records if record["has_metadata"]),
        "issues": sum(1 for record in query_records if record["has_visual_issues"]),
    }
    records = [record for record in query_records if _image_history_matches_filter(record, filter_key)]
    return jsonify({"status": "success", "records": records[offset: offset + limit], "total": len(records), "filter_counts": filter_counts})


@api_blueprint.route("/media/thumbnail/image/<int:itemid>")
def image_detection_thumbnail(itemid):
    actor, error = _history_identity()
    if error:
        return error

    if actor["mode"] == "guest":
        rows = excute_detection_sql(
            "SELECT * FROM data WHERE itemid = %s AND Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s LIMIT 1",
            (itemid, actor["openid"]),
        )
    else:
        history_where, history_params = _history_actor_where(actor)
        rows = excute_detection_sql(
            f"SELECT * FROM data WHERE itemid = %s AND ({history_where}) LIMIT 1",
            (itemid, *history_params),
        )
    if not rows:
        return jsonify({"status": "error", "message": "未找到图片记录"}), 404

    item = rows[0]
    if not detection_record_is_publishable(item):
        return jsonify({"status": "error", "message": "未找到图片记录"}), 404
    cache_path = _thumbnail_cache_path(item)
    if cache_path.exists():
        return send_file(cache_path, mimetype="image/webp", max_age=86400, conditional=True)

    source_url = _backend_media_url("image", item)
    if not source_url:
        return jsonify({"status": "error", "message": "图片地址为空"}), 404

    try:
        with requests.Session() as sess:
            sess.trust_env = False
            response = sess.get(source_url, timeout=20)
        response.raise_for_status()

        image = Image.open(io.BytesIO(response.content))
        image.thumbnail(THUMBNAIL_MAX_SIZE, Image.Resampling.LANCZOS)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")

        THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        image.save(cache_path, "WEBP", quality=THUMBNAIL_QUALITY, method=6)
        return send_file(cache_path, mimetype="image/webp", max_age=86400, conditional=True)
    except Exception:
        return _serve_owned_media("image", itemid)


def _serve_owned_media(kind, itemid):
    if kind not in {"image", "video"}:
        return jsonify({"status": "error", "message": "媒体类型不受支持"}), 404
    actor, error = _history_identity()
    if error:
        return error
    table = "data" if kind == "image" else "video_data"
    if actor["mode"] == "guest":
        rows = excute_detection_sql(
            f"SELECT * FROM {table} WHERE itemid = %s AND Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s LIMIT 1",
            (itemid, actor["openid"]),
        )
    else:
        history_where, history_params = _history_actor_where(actor)
        rows = excute_detection_sql(
            f"SELECT * FROM {table} WHERE itemid = %s AND ({history_where}) LIMIT 1",
            (itemid, *history_params),
        )
    if not rows:
        return jsonify({"status": "error", "message": "媒体不存在"}), 404

    if kind == "image" and not detection_record_is_publishable(rows[0]):
        return jsonify({"status": "error", "message": "媒体不存在"}), 404

    return _serve_detection_media_item(kind, rows[0])


def _serve_detection_media_item(kind, item):
    """Serve a database-authorized media row without re-evaluating its owner."""
    if kind not in {"image", "video"}:
        return jsonify({"status": "error", "message": "媒体类型不受支持"}), 404

    filename = str(item.get("filename") or "").strip()
    folder = str(item.get("openid") or item.get("phone") or "guest").strip()
    if not filename or not folder:
        return jsonify({"status": "error", "message": "媒体不存在"}), 404

    media_root, local_path = _local_detection_media_path(kind, item)
    try:
        local_path.relative_to(media_root)
    except ValueError:
        return jsonify({"status": "error", "message": "媒体路径无效"}), 404
    if local_path.is_file():
        response = send_file(local_path, conditional=True, max_age=0)
        response.headers["Cache-Control"] = "private, no-store"
        return response

    source_url = _backend_media_url(kind, item)
    if not source_url:
        return jsonify({"status": "error", "message": "媒体不存在"}), 404
    upstream_session = requests.Session()
    upstream_session.trust_env = False
    forwarded_headers = {}
    if request.headers.get("Range"):
        forwarded_headers["Range"] = request.headers["Range"]
    try:
        upstream = upstream_session.get(source_url, headers=forwarded_headers, timeout=30, stream=True)
    except requests.RequestException:
        upstream_session.close()
        return jsonify({"status": "error", "message": "媒体服务暂不可用"}), 502
    if upstream.status_code not in {200, 206}:
        upstream.close()
        upstream_session.close()
        return jsonify({"status": "error", "message": "媒体不存在"}), 404

    def stream():
        try:
            yield from upstream.iter_content(chunk_size=64 * 1024)
        finally:
            upstream.close()
            upstream_session.close()

    headers = {"Cache-Control": "private, no-store"}
    for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
        value = upstream.headers.get(name)
        if value:
            headers[name] = value
    return Response(
        stream_with_context(stream()),
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type") or "application/octet-stream",
        headers=headers,
    )


def _local_detection_media_path(kind, item):
    media_root = (Path(__file__).resolve().parents[1] / "static" / "uploads").resolve()
    folder = str((item or {}).get("openid") or (item or {}).get("phone") or "guest").strip()
    filename = str((item or {}).get("filename") or "").strip()
    return media_root, (media_root / folder / kind / filename).resolve()


def _ensure_privacy_erasure_table():
    global _PRIVACY_ERASURE_TABLE_READY
    if _PRIVACY_ERASURE_TABLE_READY:
        return True
    result = excute_sql(
        """
        CREATE TABLE IF NOT EXISTS privacy_erasure_jobs (
          job_id CHAR(36) NOT NULL,
          resource_kind VARCHAR(16) NOT NULL,
          resource_id BIGINT NOT NULL,
          owner_key_hash CHAR(64) NOT NULL,
          state VARCHAR(24) NOT NULL DEFAULT 'preparing',
          original_path TEXT NULL,
          staged_path TEXT NULL,
          thumbnail_original_path TEXT NULL,
          thumbnail_staged_path TEXT NULL,
          manifest_original_path TEXT NULL,
          manifest_staged_path TEXT NULL,
          attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
          last_error VARCHAR(255) NULL,
          created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
          updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
          completed_at DATETIME(6) NULL,
          PRIMARY KEY (job_id),
          KEY idx_privacy_erasure_resource (resource_kind, resource_id),
          KEY idx_privacy_erasure_state_updated (state, updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        fetch=False,
    )
    _PRIVACY_ERASURE_TABLE_READY = result is not None
    return _PRIVACY_ERASURE_TABLE_READY


def _privacy_owner_hash(actor):
    actor = actor or {}
    identity = (
        actor.get("account_uuid")
        or actor.get("openid")
        or actor.get("phone")
        or actor.get("Userid")
        or "anonymous"
    )
    return hashlib.sha256(str(identity).encode("utf-8")).hexdigest()


def _create_privacy_erasure_job(kind, itemid, actor):
    job_id = str(uuid.uuid4())
    inserted = excute_sql(
        """
        INSERT INTO privacy_erasure_jobs
            (job_id, resource_kind, resource_id, owner_key_hash, state)
        VALUES (%s, %s, %s, %s, 'preparing')
        """,
        (job_id, kind, itemid, _privacy_owner_hash(actor)),
        fetch=False,
    )
    return job_id if inserted is not None else None


def _stage_privacy_erasure_job(
    job_id,
    *,
    original_path=None,
    staged_path=None,
    thumbnail_original_path=None,
    thumbnail_staged_path=None,
    staged_manifest=None,
):
    manifest_original, manifest_staged = staged_manifest or (None, None)
    updated = excute_sql(
        """
        UPDATE privacy_erasure_jobs
        SET state = 'staged', original_path = %s, staged_path = %s,
            thumbnail_original_path = %s, thumbnail_staged_path = %s,
            manifest_original_path = %s, manifest_staged_path = %s,
            last_error = NULL
        WHERE job_id = %s AND state = 'preparing'
        """,
        (
            str(original_path) if original_path else None,
            str(staged_path) if staged_path else None,
            str(thumbnail_original_path) if thumbnail_original_path else None,
            str(thumbnail_staged_path) if thumbnail_staged_path else None,
            str(manifest_original) if manifest_original else None,
            str(manifest_staged) if manifest_staged else None,
            job_id,
        ),
        fetch=False,
    )
    return updated == 1


def _privacy_erasure_job(job_id):
    rows = excute_sql(
        """
        SELECT job_id, resource_kind, resource_id, state,
               original_path, staged_path,
               thumbnail_original_path, thumbnail_staged_path,
               manifest_original_path, manifest_staged_path,
               attempt_count, updated_at
        FROM privacy_erasure_jobs WHERE job_id = %s LIMIT 1
        """,
        (job_id,),
    )
    return rows[0] if rows else None


def _privacy_erasure_allowed_roots():
    upload_root = (Path(__file__).resolve().parents[1] / "static" / "uploads").resolve()
    return (
        upload_root,
        THUMBNAIL_CACHE_DIR.expanduser().resolve(),
        evidence_manifest._snapshot_root().resolve(),
    )


def _validated_erasure_path(raw_path, *, staged):
    if not raw_path:
        return None
    path = Path(str(raw_path)).expanduser().resolve()
    try:
        allowed = any(path.is_relative_to(root) for root in _privacy_erasure_allowed_roots())
    except AttributeError:  # pragma: no cover - Python 3.8 compatibility
        allowed = any(str(path).startswith(f"{root}{os.sep}") for root in _privacy_erasure_allowed_roots())
    if not allowed or (staged and ".deleting-" not in path.name):
        raise RuntimeError("擦除任务路径校验失败")
    return path


def _set_privacy_erasure_state(job_id, state, *, error="", scrub_paths=False):
    path_reset = (
        ", original_path = NULL, staged_path = NULL, thumbnail_original_path = NULL, "
        "thumbnail_staged_path = NULL, manifest_original_path = NULL, manifest_staged_path = NULL"
        if scrub_paths
        else ""
    )
    completed = ", completed_at = NOW(6)" if state in {"completed", "rolled_back"} else ""
    updated = excute_sql(
        f"""
        UPDATE privacy_erasure_jobs
        SET state = %s, last_error = %s, attempt_count = attempt_count + 1
            {path_reset}{completed}
        WHERE job_id = %s
        """,
        (state, str(error or "")[:255] or None, job_id),
        fetch=False,
    )
    return updated == 1


def _erase_privacy_spool_file(root, spool_name):
    name = str(spool_name or "").strip()
    root = Path(root).expanduser()
    if not root.is_absolute() or not name or Path(name).name != name or name in {".", ".."}:
        raise RuntimeError("擦除任务队列文件路径无效")
    root_stat = root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise RuntimeError("擦除任务队列目录无效")
    path = root / name
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return True
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
        raise RuntimeError("擦除任务队列文件类型或链接数异常")
    path.unlink()
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(root, directory_flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return True


def _scrub_history_replicas(kind, itemid, job_id):
    if kind != "image":
        return True
    erased_hash = hashlib.sha256(f"erased:{job_id}".encode("utf-8")).hexdigest()
    erased_account_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"realguard-erased:{job_id}"))
    conn = get_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT task_id, spool_path
                FROM developer_detection_tasks
                WHERE (result_item_id = %s OR effect_item_id = %s)
                  AND spool_path IS NOT NULL
                FOR UPDATE
                """,
                (itemid, itemid),
            )
            developer_spools = cursor.fetchall() or []
            cursor.execute(
                """
                SELECT job_id, spool_path
                FROM web_detection_tasks
                WHERE effect_item_id = %s AND spool_path IS NOT NULL
                FOR UPDATE
                """,
                (itemid,),
            )
            web_spools = cursor.fetchall() or []

            # Delete bytes before removing their durable references. If a later
            # SQL write fails, the retained path makes the retry idempotent;
            # the reverse order could strand sensitive unreferenced files.
            for row in developer_spools:
                _erase_privacy_spool_file(PRIVACY_DEVELOPER_SPOOL_ROOT, row.get("spool_path"))
            for row in web_spools:
                _erase_privacy_spool_file(PRIVACY_WEB_SPOOL_ROOT, row.get("spool_path"))

            cursor.execute(
                """
                UPDATE developer_detection_tasks
                SET user_id = 0, account_uuid = %s, key_id = 0,
                    filename = '[erased]', mime_type = 'application/octet-stream',
                    execution_filename = NULL, request_sha256 = %s,
                    spool_path = NULL, spool_size = NULL, request_context_json = NULL,
                    idempotency_key = NULL, effect_item_id = NULL,
                    effect_result_json = NULL, result_item_id = NULL,
                    result_json = NULL, error_message = NULL
                WHERE result_item_id = %s OR effect_item_id = %s
                """,
                (erased_account_uuid, erased_hash, itemid, itemid),
            )
            cursor.execute(
                """
                UPDATE web_detection_tasks
                SET filename = '[erased]', mime_type = 'application/octet-stream',
                    request_sha256 = %s, spool_path = NULL, spool_size = NULL,
                    request_context_json = NULL, owner_type = 'erased', owner_key = %s,
                    idempotency_key = NULL, effect_item_id = NULL,
                    effect_result_json = NULL, result_json = NULL, error_message = NULL
                WHERE effect_item_id = %s
                """,
                (erased_hash, erased_hash, itemid),
            )
            cursor.execute(
                """
                UPDATE admin_model_runs
                SET itemid = NULL, actor_id = NULL, actor_username = NULL,
                    actor_phone = NULL, meta_json = %s
                WHERE itemid = %s
                """,
                (json.dumps({"erased": True, "erasureJobId": job_id}), itemid),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    admin_state.scrub_detection_item(itemid, job_id)
    return True


def _restore_privacy_erasure_job(job):
    try:
        for original_key, staged_key in (
            ("original_path", "staged_path"),
            ("thumbnail_original_path", "thumbnail_staged_path"),
            ("manifest_original_path", "manifest_staged_path"),
        ):
            original = _validated_erasure_path(job.get(original_key), staged=False)
            staged = _validated_erasure_path(job.get(staged_key), staged=True)
            if staged and staged.exists() and original and not original.exists():
                staged.replace(original)
        return _set_privacy_erasure_state(job["job_id"], "rolled_back", scrub_paths=True)
    except Exception as exc:
        _set_privacy_erasure_state(job["job_id"], "restore_failed", error=type(exc).__name__)
        return False


def _finalize_privacy_erasure_job(job):
    errors = []
    for key in ("staged_path", "thumbnail_staged_path", "manifest_staged_path"):
        try:
            staged = _validated_erasure_path(job.get(key), staged=True)
            if staged:
                staged.unlink(missing_ok=True)
        except Exception as exc:
            errors.append(f"{key}:{type(exc).__name__}")
    try:
        _scrub_history_replicas(job.get("resource_kind"), int(job.get("resource_id")), job["job_id"])
    except Exception as exc:
        errors.append(f"replicas:{type(exc).__name__}")
    if errors:
        _set_privacy_erasure_state(job["job_id"], "cleanup_failed", error=",".join(errors))
        return False
    return _set_privacy_erasure_state(job["job_id"], "completed", scrub_paths=True)


def _privacy_erasure_record_exists(kind, itemid):
    table = "data" if kind == "image" else "video_data"
    rows = excute_detection_sql(f"SELECT itemid FROM {table} WHERE itemid = %s LIMIT 1", (itemid,))
    if rows is None:
        return None
    return bool(rows)


def _retry_pending_privacy_erasures(limit=50):
    if not _ensure_privacy_erasure_table():
        return {"retried": 0, "completed": 0, "restored": 0}
    try:
        batch_size = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        batch_size = 50
    rows = excute_sql(
        """
        SELECT job_id, resource_kind, resource_id, state,
               original_path, staged_path,
               thumbnail_original_path, thumbnail_staged_path,
               manifest_original_path, manifest_staged_path,
               attempt_count, updated_at
        FROM privacy_erasure_jobs
        WHERE state IN ('pending_cleanup', 'cleanup_failed', 'restore_failed')
           OR (
                state IN ('preparing', 'staged')
                AND updated_at <= DATE_SUB(NOW(6), INTERVAL %s SECOND)
           )
        ORDER BY updated_at ASC LIMIT %s
        """,
        (PRIVACY_ERASURE_PRECOMMIT_GRACE_SECONDS, batch_size),
    ) or []
    completed = 0
    restored = 0
    for job in rows:
        if job.get("state") in {"preparing", "staged", "restore_failed"}:
            exists = _privacy_erasure_record_exists(job.get("resource_kind"), job.get("resource_id"))
            if exists is None:
                continue
            if exists:
                restored += int(_restore_privacy_erasure_job(job))
                continue
        completed += int(_finalize_privacy_erasure_job(job))
    return {"retried": len(rows), "completed": completed, "restored": restored}


def _delete_owned_history_record(kind, itemid, actor):
    if kind not in {"image", "video"}:
        return False, "媒体类型不受支持", 404
    if not _ensure_privacy_erasure_table():
        return False, "擦除任务存储暂不可用", 503
    table = "data" if kind == "image" else "video_data"
    if actor.get("mode") == "guest":
        owner_where = "Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s"
        owner_params = (actor.get("openid"),)
    else:
        owner_where, owner_params = _history_actor_where(actor)

    conn = None
    quarantine_path = None
    thumbnail_quarantine_path = None
    thumbnail_path = None
    staged_manifest = None
    original_path = None
    item = None
    erasure_job_id = None
    try:
        conn = get_detection_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {table} WHERE itemid = %s AND ({owner_where}) LIMIT 1 FOR UPDATE",
                (itemid, *owner_params),
            )
            item = cursor.fetchone()
            if not item:
                conn.rollback()
                return False, "记录不存在", 404

            media_root, original_path = _local_detection_media_path(kind, item)
            try:
                original_path.relative_to(media_root)
            except ValueError:
                conn.rollback()
                return False, "媒体路径无效", 500
            erasure_job_id = _create_privacy_erasure_job(kind, itemid, actor)
            if not erasure_job_id:
                raise RuntimeError("无法创建擦除任务")
            privacy_erasure_ledger.record_tombstone(
                "realguard-v1",
                f"{kind}-history",
                itemid,
                resource_fingerprint_value=privacy_erasure_ledger.resource_fingerprint(
                    {
                        "sourceSystem": "realguard-v1",
                        "resourceKind": f"{kind}-history",
                        "itemId": itemid,
                        "filename": item.get("filename") or "",
                        "sha256": item.get("sha256") or item.get("file_sha256") or "",
                        "owner": item.get("openid") or item.get("phone") or "",
                        "mediaPath": str(original_path),
                    }
                ),
            )
            if original_path.is_file():
                quarantine_path = original_path.with_name(f".{original_path.name}.deleting-{uuid.uuid4().hex}")

            if kind == "image":
                thumbnail_path = _thumbnail_cache_path(item)
                if thumbnail_path.is_file():
                    thumbnail_quarantine_path = thumbnail_path.with_name(
                        f".{thumbnail_path.name}.deleting-{uuid.uuid4().hex}"
                    )
                staged_manifest = evidence_manifest.plan_signed_image_manifest_deletion(itemid)

            # Persist every future rename before performing it. If the process
            # dies after any filesystem mutation, the retry worker can now
            # restore or finish the exact paths instead of leaving an orphan.
            if not _stage_privacy_erasure_job(
                erasure_job_id,
                original_path=original_path if quarantine_path else None,
                staged_path=quarantine_path,
                thumbnail_original_path=thumbnail_path if thumbnail_quarantine_path else None,
                thumbnail_staged_path=thumbnail_quarantine_path,
                staged_manifest=staged_manifest,
            ):
                raise RuntimeError("无法持久化擦除任务")

            if quarantine_path:
                original_path.replace(quarantine_path)
            if thumbnail_quarantine_path:
                thumbnail_path.replace(thumbnail_quarantine_path)
            if staged_manifest:
                staged_manifest = evidence_manifest.stage_signed_image_manifest_deletion(
                    itemid,
                    planned_deletion=staged_manifest,
                )

            if kind == "image":
                cursor.execute("DELETE FROM exif WHERE data_itemid = %s", (itemid,))
            cursor.execute(
                f"DELETE FROM {table} WHERE itemid = %s AND ({owner_where})",
                (itemid, *owner_params),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("删除记录时属主状态发生变化")
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        if quarantine_path and quarantine_path.exists() and original_path and not original_path.exists():
            try:
                quarantine_path.replace(original_path)
            except OSError:
                pass
        if (
            thumbnail_quarantine_path
            and thumbnail_quarantine_path.exists()
            and thumbnail_path
            and not thumbnail_path.exists()
        ):
            try:
                thumbnail_quarantine_path.replace(thumbnail_path)
            except OSError:
                pass
        try:
            evidence_manifest.restore_staged_image_manifest_deletion(staged_manifest)
        except evidence_manifest.EvidenceManifestError:
            pass
        if erasure_job_id:
            job = _privacy_erasure_job(erasure_job_id)
            if job:
                _restore_privacy_erasure_job(job)
        return False, "删除失败，请稍后重试", 500
    finally:
        if conn:
            conn.close()

    _set_privacy_erasure_state(erasure_job_id, "pending_cleanup")
    job = _privacy_erasure_job(erasure_job_id)
    if job and _finalize_privacy_erasure_job(job):
        return True, "", 204
    return True, erasure_job_id, 202


@api_blueprint.route("/media/image/<int:itemid>")
def image_detection_media(itemid):
    return _serve_owned_media("image", itemid)


@api_blueprint.route("/media/video/<int:itemid>")
def video_detection_media(itemid):
    return _serve_owned_media("video", itemid)


@api_blueprint.route("/history/image-detections/<int:itemid>", methods=["DELETE"])
def delete_image_detection_history(itemid):
    actor, error = _history_identity()
    if error:
        return error
    deleted, message, status = _delete_owned_history_record("image", itemid, actor)
    if not deleted:
        return jsonify({"status": "error", "message": message}), status
    if status == 202:
        return jsonify({
            "status": "pending",
            "message": "记录已从账户中移除，物理擦除仍在后台重试",
            "erasureRequestId": message,
        }), status
    return "", status


@api_blueprint.route("/history/video-detections/<int:itemid>", methods=["DELETE"])
def delete_video_detection_history(itemid):
    actor, error = _history_identity()
    if error:
        return error
    deleted, message, status = _delete_owned_history_record("video", itemid, actor)
    if not deleted:
        return jsonify({"status": "error", "message": message}), status
    if status == 202:
        return jsonify({
            "status": "pending",
            "message": "记录已从账户中移除，物理擦除仍在后台重试",
            "erasureRequestId": message,
        }), status
    return "", status


@api_blueprint.route("/history/video-detections")
def video_detection_history():
    actor, error = _history_identity(allow_empty=True)
    if error:
        return error
    limit, limit_error = _history_limit()
    if limit_error:
        return limit_error
    offset, offset_error = _history_offset()
    if offset_error:
        return offset_error
    query = _history_query()
    filter_key = str(request.args.get("filter") or "all").strip()
    if filter_key not in {"all", "guest", "review"}:
        return jsonify({"status": "error", "message": "filter 不受支持"}), 400

    if actor["mode"] == "guest":
        rows = excute_detection_sql(
            f"SELECT * FROM video_data WHERE Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s ORDER BY {HISTORY_ORDER_BY}",
            (actor["openid"],),
        )
    elif actor["mode"] == "anonymous":
        rows = []
    else:
        history_where, history_params = _history_actor_where(actor)
        rows = excute_detection_sql(
            f"SELECT * FROM video_data WHERE {history_where} ORDER BY {HISTORY_ORDER_BY}",
            history_params,
        )
    query_records = []
    for item in rows or []:
        record = _video_history_record(item)
        if not _contains_history_query(_video_history_search_fields(record), query):
            continue
        query_records.append(record)
    filter_counts = {
        "all": len(query_records),
        "guest": sum(1 for record in query_records if record["is_guest_record"]),
        "review": sum(1 for record in query_records if record.get("decision_status") == "review_only"),
    }
    records = [record for record in query_records if _video_history_matches_filter(record, filter_key)]
    return jsonify({"status": "success", "records": records[offset: offset + limit], "total": len(records), "filter_counts": filter_counts})
