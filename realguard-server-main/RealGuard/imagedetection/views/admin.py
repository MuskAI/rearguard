import base64
import csv
import hashlib
import hmac
import io
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from flask import Blueprint, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from imagedetection.views import admin_state, aliyun_green, model_registry
from imagedetection.views.utils import detection_owner_where, excute_detection_sql, excute_sql, format_createtime


admin_blueprint = Blueprint("admin_blueprint", __name__)
ADMIN_SESSION_KEY = "admin_user"
ADMIN_LOGIN_ATTEMPTS_KEY = "admin_login_attempts"
ADMIN_CSRF_SESSION_KEY = "admin_csrf_token"
ADMIN_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")
ADMIN_PASSWORD_MIN_LENGTH = int(os.environ.get("REALGUARD_ADMIN_PASSWORD_MIN_LENGTH", "10"))
ADMIN_LOGIN_MAX_ATTEMPTS = int(os.environ.get("REALGUARD_ADMIN_LOGIN_MAX_ATTEMPTS", "5"))
ADMIN_LOGIN_LOCK_SECONDS = int(os.environ.get("REALGUARD_ADMIN_LOGIN_LOCK_SECONDS", "600"))
ADMIN_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS admin_accounts (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(64) NOT NULL UNIQUE,
        phone VARCHAR(20) NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(32) NOT NULL DEFAULT 'admin',
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_login_at DATETIME NULL,
        last_login_ip VARCHAR(64) NULL,
        KEY idx_admin_accounts_status (status),
        KEY idx_admin_accounts_phone (phone)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_audit_logs (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actor_id VARCHAR(64) NULL,
        actor_username VARCHAR(64) NULL,
        actor_phone VARCHAR(20) NULL,
        action VARCHAR(96) NOT NULL,
        target VARCHAR(191) NOT NULL,
        before_json LONGTEXT NULL,
        after_json LONGTEXT NULL,
        meta_json LONGTEXT NULL,
        KEY idx_admin_audit_created (created_at),
        KEY idx_admin_audit_action (action),
        KEY idx_admin_audit_target (target)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_model_runs (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        run_id VARCHAR(64) NOT NULL UNIQUE,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        itemid BIGINT NULL,
        route VARCHAR(32) NOT NULL DEFAULT 'primary',
        status VARCHAR(32) NOT NULL DEFAULT 'success',
        model_id VARCHAR(96) NULL,
        model_name VARCHAR(191) NULL,
        model_runtime VARCHAR(96) NULL,
        model_endpoint VARCHAR(512) NULL,
        model_version VARCHAR(96) NULL,
        actor_id VARCHAR(64) NULL,
        actor_username VARCHAR(64) NULL,
        actor_phone VARCHAR(20) NULL,
        meta_json LONGTEXT NULL,
        KEY idx_admin_model_runs_itemid (itemid),
        KEY idx_admin_model_runs_created (created_at),
        KEY idx_admin_model_runs_model (model_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_login_attempts (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        identity_hash CHAR(64) NOT NULL,
        ip_hash CHAR(64) NOT NULL,
        failure_count INT NOT NULL DEFAULT 0,
        locked_until_epoch BIGINT NOT NULL DEFAULT 0,
        last_failed_at DATETIME NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uniq_admin_login_attempts (identity_hash, ip_hash),
        KEY idx_admin_login_attempts_locked (locked_until_epoch)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)
ADMIN_ROLE_LABELS = {
    "super_admin": "超级管理员",
    "admin": "管理员",
    "operator": "运维",
    "reviewer": "复核员",
    "readonly": "只读",
}
ALL_ADMIN_PERMISSIONS = {
    "view",
    "admin.manage",
    "model.manage",
    "model.probe",
    "routing.manage",
    "alerts.manage",
    "detection.review",
    "api_key.manage",
    "data.export",
}
ADMIN_ROLE_PERMISSIONS = {
    "super_admin": {"*"},
    "admin": {"*"},
    "operator": {
        "view",
        "model.probe",
        "alerts.manage",
        "routing.manage",
        "detection.review",
        "data.export",
    },
    "reviewer": {"view", "detection.review", "data.export"},
    "readonly": {"view"},
}
BIG_SCREEN_CACHE_TTL_SECONDS = int(os.environ.get("REALGUARD_BIG_SCREEN_CACHE_SECONDS", "15"))
_BIG_SCREEN_CACHE = {"expires": 0, "payload": None}
PROBE_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def _current_user():
    user = session.get("user_info")
    return user if isinstance(user, dict) else None


def _current_admin_user():
    user = session.get(ADMIN_SESSION_KEY)
    return user if isinstance(user, dict) else None


def _admin_phone_set():
    return {
        item.strip()
        for item in os.environ.get("REALGUARD_ADMIN_PHONES", "").split(",")
        if item.strip()
    }


def _admin_user_id_set():
    return {
        item.strip()
        for item in os.environ.get("REALGUARD_ADMIN_USER_IDS", "").split(",")
        if item.strip()
    }


def _is_legacy_admin(user):
    if not user:
        return False
    phones = _admin_phone_set()
    user_ids = _admin_user_id_set()
    if not phones and not user_ids:
        return bool(os.environ.get("REALGUARD_ADMIN_ALLOW_ANY_LOGIN", "0") == "1")
    return str(user.get("phone") or "") in phones or str(user.get("Userid") or "") in user_ids


def _normalize_admin_role(role):
    role = str(role or "readonly").strip()
    return role if role in ADMIN_ROLE_PERMISSIONS else "readonly"


def _admin_permissions(role):
    role = _normalize_admin_role(role)
    permissions = ADMIN_ROLE_PERMISSIONS.get(role, set())
    if "*" in permissions:
        return sorted(ALL_ADMIN_PERMISSIONS)
    return sorted(permissions)


def _has_admin_permission(user, permission="view"):
    if not user:
        return False
    role = _normalize_admin_role(user.get("role") or ("super_admin" if user.get("authType") == "legacy_whitelist" else "admin"))
    permissions = ADMIN_ROLE_PERMISSIONS.get(role, set())
    return "*" in permissions or permission in permissions or permission == "view"


def _legacy_admin_payload(user):
    if not user:
        return None
    return {
        "Userid": user.get("Userid"),
        "adminId": None,
        "username": user.get("username") or "",
        "phone": user.get("phone") or "",
        "role": "super_admin",
        "authType": "legacy_whitelist",
    }


def _is_admin(user):
    return bool(_current_admin_user() or _is_legacy_admin(user))


def _permission_error(permission):
    return jsonify({
        "status": "error",
        "message": f"当前管理员角色缺少权限：{permission}",
    }), 403


def _admin_required(permission="view"):
    admin_user = _current_admin_user()
    if admin_user:
        admin_user["role"] = _normalize_admin_role(admin_user.get("role") or "admin")
        if not _has_admin_permission(admin_user, permission):
            return None, _permission_error(permission)
        return admin_user, None
    user = _current_user()
    if not user:
        return None, (jsonify({"status": "error", "message": "管理员未登录"}), 401)
    if not _is_legacy_admin(user):
        return None, (jsonify({"status": "error", "message": "无后台管理权限"}), 403)
    legacy = _legacy_admin_payload(user)
    if not _has_admin_permission(legacy, permission):
        return None, _permission_error(permission)
    return legacy, None


def _csrf_token():
    token = session.get(ADMIN_CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[ADMIN_CSRF_SESSION_KEY] = token
        session.modified = True
    return token


def _csrf_error_response():
    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "message": "CSRF 校验失败，请刷新后台页面后重试"}), 403
    return render_template(
        "admin_auth.html",
        **_admin_auth_context("login", error="CSRF 校验失败，请刷新页面后重试"),
    ), 403


def _csrf_valid():
    expected = session.get(ADMIN_CSRF_SESSION_KEY)
    provided = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    return bool(expected and provided and hmac.compare_digest(str(expected), str(provided)))


def _screen_token_from_request():
    return (
        request.headers.get("X-RealGuard-Screen-Token")
        or request.args.get("screenToken")
        or request.args.get("token")
        or ""
    ).strip()


def _screen_token_valid():
    token = _screen_token_from_request()
    if not token:
        return False
    plain = (os.environ.get("REALGUARD_BIG_SCREEN_TOKEN") or "").strip()
    digest = (os.environ.get("REALGUARD_BIG_SCREEN_TOKEN_SHA256") or "").strip().lower()
    if plain and hmac.compare_digest(token, plain):
        return True
    if digest:
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(token_digest, digest)
    return False


def _screen_token_user():
    return {
        "Userid": "screen:readonly",
        "adminId": None,
        "username": "运营大屏",
        "phone": "",
        "role": "readonly",
        "authType": "screen_token",
    }


def _client_ip():
    forwarded = str(request.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    return (forwarded or request.remote_addr or "")[:128]


def _security_hash(value):
    salt = os.environ.get("SECRET_KEY") or os.environ.get("REALGUARD_SECRET_KEY") or "realguard-admin"
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()


@admin_blueprint.before_request
def _admin_csrf_protect():
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return None
    if not _csrf_valid():
        return _csrf_error_response()
    return None


def _admin_timezone():
    name = os.environ.get("REALGUARD_ADMIN_TIMEZONE", "Asia/Shanghai")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def _day_bounds(offset_days=0):
    now = datetime.now(_admin_timezone()) + timedelta(days=int(offset_days))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def _today_bounds():
    return _day_bounds(0)


def _days_ago_start(days):
    now = datetime.now(_admin_timezone())
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=max(1, int(days)) - 1)
    return start.strftime("%Y-%m-%d %H:%M:%S")


def _hours_window(hours=24):
    now = datetime.now(_admin_timezone()).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=max(1, int(hours)) - 1)
    return start.strftime("%Y-%m-%d %H:%M:%S"), [(start + timedelta(hours=i)) for i in range(max(1, int(hours)))]


def _scalar(sql, params=None, detection=False, default=0):
    rows = (excute_detection_sql if detection else excute_sql)(sql, params)
    if not rows:
        return default
    row = rows[0]
    if not row:
        return default
    return next(iter(row.values()), default)


def apply_admin_schema():
    messages = []
    ok = True
    for sql in ADMIN_SCHEMA_SQL:
        result = excute_sql(sql, fetch=False)
        table_match = re.search(r"CREATE TABLE IF NOT EXISTS\s+([a-zA-Z0-9_]+)", sql)
        table = table_match.group(1) if table_match else "admin table"
        if result is None:
            ok = False
            messages.append(f"{table}: failed")
        else:
            messages.append(f"{table}: ready")
    return ok, messages


def _table_exists(table_name):
    rows = excute_sql("SHOW TABLES LIKE %s", (table_name,)) or []
    return bool(rows)


def _ensure_admin_account_table():
    return _table_exists("admin_accounts")


def _admin_account_count():
    if not _ensure_admin_account_table():
        return 0
    return int(_scalar("SELECT COUNT(*) AS count FROM admin_accounts", default=0) or 0)


def _login_attempt_table_ready():
    return _table_exists("admin_login_attempts")


def _login_attempt_keys(identity):
    normalized = str(identity or "").strip().lower()
    return _security_hash(normalized or "empty"), _security_hash(_client_ip() or "unknown")


def _find_admin_account(identity):
    if not identity or not _ensure_admin_account_table():
        return None
    rows = excute_sql(
        """
        SELECT id, username, phone, password_hash, role, status, created_at, last_login_at, last_login_ip
        FROM admin_accounts
        WHERE username = %s OR phone = %s
        LIMIT 1
        """,
        (identity, identity),
    ) or []
    return rows[0] if rows else None


def _admin_password_error(password):
    value = str(password or "")
    if len(value) < ADMIN_PASSWORD_MIN_LENGTH:
        return f"管理员密码至少需要 {ADMIN_PASSWORD_MIN_LENGTH} 位"
    if len(value) > 128:
        return "管理员密码不能超过 128 位"
    if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
        return "管理员密码需同时包含字母和数字"
    return ""


def _admin_registration_allowed():
    admin_user = _current_admin_user()
    return bool((admin_user and _has_admin_permission(admin_user, "admin.manage")) or _is_legacy_admin(_current_user()))


def _create_admin_account(username, phone, password, role="admin"):
    if not _ensure_admin_account_table():
        return False, "管理员账号表未初始化，请先执行 sql/patch_admin_security.sql 或 flask admin-db-upgrade"
    username = str(username or "").strip()
    phone = str(phone or "").strip()
    requested_role = str(role or "admin").strip()
    if requested_role not in ADMIN_ROLE_LABELS:
        return False, "管理员角色无效"
    role = requested_role
    if not ADMIN_USERNAME_RE.match(username):
        return False, "管理员账号只能包含字母、数字、下划线、点和短横线，长度 3-64"
    password_error = _admin_password_error(password)
    if password_error:
        return False, password_error
    if _find_admin_account(username) or (phone and _find_admin_account(phone)):
        return False, "管理员账号或手机号已存在"
    result = excute_sql(
        """
        INSERT INTO admin_accounts (username, phone, password_hash, role, status)
        VALUES (%s, %s, %s, %s, 'active')
        """,
        (username, phone or None, generate_password_hash(password), role),
        fetch=False,
    )
    if result is None:
        return False, "管理员账号创建失败"
    return True, ""


def _admin_session_payload(account):
    return {
        "Userid": f"admin:{account.get('id')}",
        "adminId": account.get("id"),
        "username": account.get("username") or "",
        "phone": account.get("phone") or "",
        "role": account.get("role") or "admin",
        "authType": "admin_account",
    }


def _update_admin_login(account):
    excute_sql(
        "UPDATE admin_accounts SET last_login_at = NOW(), last_login_ip = %s WHERE id = %s",
        (request.headers.get("X-Forwarded-For", request.remote_addr or "")[:64], account.get("id")),
        fetch=False,
    )


def _session_login_lock_seconds():
    attempts = session.get(ADMIN_LOGIN_ATTEMPTS_KEY)
    if not isinstance(attempts, dict):
        return 0
    locked_until = int(attempts.get("locked_until") or 0)
    return max(0, locked_until - int(time.time()))


def _admin_login_lock_seconds(identity=None):
    if identity and _login_attempt_table_ready():
        identity_hash, ip_hash = _login_attempt_keys(identity)
        rows = excute_sql(
            """
            SELECT locked_until_epoch
            FROM admin_login_attempts
            WHERE identity_hash = %s AND ip_hash = %s
            LIMIT 1
            """,
            (identity_hash, ip_hash),
        ) or []
        if rows:
            return max(0, int(rows[0].get("locked_until_epoch") or 0) - int(time.time()))
    return _session_login_lock_seconds()


def _record_session_login_failure():
    attempts = session.get(ADMIN_LOGIN_ATTEMPTS_KEY)
    if not isinstance(attempts, dict):
        attempts = {"count": 0, "locked_until": 0}
    attempts["count"] = int(attempts.get("count") or 0) + 1
    if attempts["count"] >= ADMIN_LOGIN_MAX_ATTEMPTS:
        attempts["locked_until"] = int(time.time()) + ADMIN_LOGIN_LOCK_SECONDS
    session[ADMIN_LOGIN_ATTEMPTS_KEY] = attempts
    session.modified = True


def _record_admin_login_failure(identity=None):
    if identity and _login_attempt_table_ready():
        identity_hash, ip_hash = _login_attempt_keys(identity)
        now = int(time.time())
        rows = excute_sql(
            """
            SELECT failure_count, locked_until_epoch
            FROM admin_login_attempts
            WHERE identity_hash = %s AND ip_hash = %s
            LIMIT 1
            """,
            (identity_hash, ip_hash),
        ) or []
        if rows:
            row = rows[0]
            previous_lock = int(row.get("locked_until_epoch") or 0)
            previous_count = int(row.get("failure_count") or 0)
            count = 1 if previous_lock and previous_lock <= now else previous_count + 1
        else:
            count = 1
        locked_until = now + ADMIN_LOGIN_LOCK_SECONDS if count >= ADMIN_LOGIN_MAX_ATTEMPTS else 0
        excute_sql(
            """
            INSERT INTO admin_login_attempts (identity_hash, ip_hash, failure_count, locked_until_epoch, last_failed_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                failure_count = VALUES(failure_count),
                locked_until_epoch = VALUES(locked_until_epoch),
                last_failed_at = NOW()
            """,
            (identity_hash, ip_hash, count, locked_until),
            fetch=False,
        )
        return
    _record_session_login_failure()


def _clear_admin_login_failures(identity=None):
    if identity and _login_attempt_table_ready():
        identity_hash, ip_hash = _login_attempt_keys(identity)
        excute_sql(
            "DELETE FROM admin_login_attempts WHERE identity_hash = %s AND ip_hash = %s",
            (identity_hash, ip_hash),
            fetch=False,
        )
    session.pop(ADMIN_LOGIN_ATTEMPTS_KEY, None)


def _admin_auth_context(mode="login", error="", message=""):
    can_register = _admin_registration_allowed()
    return {
        "mode": mode if mode != "register" or can_register else "login",
        "error": error,
        "message": message,
        "csrf_token": _csrf_token(),
        "legacy_admin_allowed": bool(_is_legacy_admin(_current_user())),
        "can_register": can_register,
        "admin_account_count": _admin_account_count(),
        "role_options": ADMIN_ROLE_LABELS,
    }


def _admin_account_payload(row):
    role = _normalize_admin_role(row.get("role") or "readonly")
    return {
        "id": row.get("id"),
        "username": row.get("username") or "",
        "phone": row.get("phone") or "",
        "role": role,
        "roleLabel": ADMIN_ROLE_LABELS.get(role, role),
        "status": row.get("status") or "",
        "createdAt": format_createtime(row.get("created_at")),
        "lastLoginAt": format_createtime(row.get("last_login_at")),
        "lastLoginIp": row.get("last_login_ip") or "",
    }


def _detection_data_columns():
    rows = excute_detection_sql("SHOW COLUMNS FROM data") or []
    return {row.get("Field") for row in rows if isinstance(row, dict) and row.get("Field")}


def _detection_data_select_clause():
    columns = _detection_data_columns()
    select_columns = [
        "itemid",
        "createtime",
        "filename",
        "fake",
        "detector_probability" if "detector_probability" in columns else "NULL AS detector_probability",
        "phone",
        "aigc",
        "clarity",
        "feedback" if "feedback" in columns else "NULL AS feedback",
    ]
    return ", ".join(select_columns)


def _limit_arg(default=50, maximum=200):
    try:
        return min(max(int(request.args.get("limit", str(default)) or default), 1), maximum)
    except ValueError:
        return default


def _search_term():
    return str(request.args.get("q") or "").strip()


def _audit(actor, action, target, before=None, after=None, meta=None):
    try:
        return admin_state.append_audit(actor, action, target, before=before, after=after, meta=meta)
    except Exception as exc:
        print(f"[ADMIN AUDIT ERROR] {exc}")
        return None


def _csv_response(filename, headers, rows):
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return Response(
        buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _probe_model(model):
    if aliyun_green.is_aliyun_model(model):
        start = time.time()
        service = aliyun_green.service_from_model(model)
        try:
            result = aliyun_green.detect_image_bytes(service, PROBE_PNG_BYTES, "realguard-admin-probe.png")
            return {
                "ok": True,
                "httpStatus": None,
                "latencyMs": result.get("latencyMs", int((time.time() - start) * 1000)),
                "message": "probe ok",
                "payload": result,
            }
        except Exception as exc:
            return {
                "ok": False,
                "httpStatus": None,
                "latencyMs": int((time.time() - start) * 1000),
                "message": str(exc),
                "payload": None,
            }

    endpoint = str(model.get("endpoint") or "").strip()
    if not endpoint:
        return {"ok": False, "message": "endpoint not configured"}
    start = time.time()
    timeout = min(max(int(model.get("timeoutSeconds") or 30), 1), 60)
    headers = {}
    files = {"image_file": ("realguard-admin-probe.png", io.BytesIO(PROBE_PNG_BYTES), "image/png")}
    data = {"openid": "admin-probe", "phone": "admin-probe"}
    if "/api/detect" in endpoint or "dashscope" in str(model.get("runtime") or ""):
        token = (
            os.environ.get("REALGUARD_V2_INTERNAL_TOKEN")
            or os.environ.get("JIANZHEN_ACCESS_TOKEN")
            or ""
        ).strip()
        if token:
            headers["X-Jianzhen-Token"] = token
        files = {"file": ("realguard-admin-probe.png", io.BytesIO(PROBE_PNG_BYTES), "image/png")}
        data = {"fileType": "image"}
    try:
        with requests.Session() as sess:
            sess.trust_env = False
            resp = sess.post(endpoint, headers=headers, files=files, data=data, timeout=timeout)
        latency = int((time.time() - start) * 1000)
        payload = None
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        return {
            "ok": 200 <= resp.status_code < 300,
            "httpStatus": resp.status_code,
            "latencyMs": latency,
            "message": "probe ok" if 200 <= resp.status_code < 300 else resp.text[:200],
            "payload": payload,
        }
    except Exception as exc:
        return {
            "ok": False,
            "httpStatus": None,
            "latencyMs": int((time.time() - start) * 1000),
            "message": str(exc),
            "payload": None,
        }


def _v1_assurance():
    registry = model_registry.load_registry()
    routing = registry.get("routing", {})
    models = [_model_payload(model, include_health=True) for model in registry.get("models", [])]
    by_id = {model.get("id"): model for model in models}
    primary = by_id.get(routing.get("imagePrimary")) or {}
    v1_models = [model for model in models if str(model.get("id") or "").startswith("v1-")]
    blockers = []
    recommendations = []
    primary_id = primary.get("id") or routing.get("imagePrimary") or ""
    primary_health = primary.get("health") or {}
    if not primary_id.startswith("v1-"):
        blockers.append("当前主图像模型不是 V1，线上检测能力已偏离 V1 路由。")
    if primary and primary.get("enabled") is False:
        blockers.append(f"主模型 {primary_id} 已被禁用。")
    if primary_id.startswith("v1-") and not primary_health.get("ok"):
        blockers.append(f"主模型 {primary_id} 未达到可用状态：{primary_health.get('message') or 'unknown'}")
    for model in v1_models:
        health = model.get("health") or {}
        if model.get("id") == "v1-onnx-mil" and health.get("artifactReady") is False:
            recommendations.append("补齐 model_deploy.onnx.data 后，可把主路由从 legacy tunnel 迁回本机托管 V1。")
        if model.get("id") == "v1-legacy-tunnel" and not health.get("serviceOk"):
            recommendations.append("恢复 legacy V1 隧道源端，让服务器重新监听 127.0.0.1:15000。")
    if routing.get("fallbackEnabled"):
        recommendations.append("当前已开启自动兜底；如需严格保持 V1 结果一致性，应关闭自动兜底。")
    return {
        "online": bool(primary_id.startswith("v1-") and primary_health.get("ok") and primary.get("enabled") is not False),
        "routing": routing,
        "primary": primary,
        "v1Models": v1_models,
        "blockers": blockers,
        "recommendations": recommendations,
        "alerts": admin_state.alerts(),
    }


def _dashboard_metrics():
    today_start, today_end = _today_bounds()
    yesterday_start, yesterday_end = _day_bounds(-1)
    last7_start = _days_ago_start(7)
    today_user_count = _scalar(
        "SELECT COUNT(*) AS count FROM user WHERE created_at >= %s AND created_at < %s",
        (today_start, today_end),
    )
    today_image_count = _scalar(
        "SELECT COUNT(*) AS count FROM data WHERE createtime >= %s AND createtime < %s",
        (today_start, today_end),
        detection=True,
    )
    today_video_count = _scalar(
        "SELECT COUNT(*) AS count FROM video_data WHERE createtime >= %s AND createtime < %s",
        (today_start, today_end),
        detection=True,
    )
    yesterday_image_count = _scalar(
        "SELECT COUNT(*) AS count FROM data WHERE createtime >= %s AND createtime < %s",
        (yesterday_start, yesterday_end),
        detection=True,
    )
    yesterday_video_count = _scalar(
        "SELECT COUNT(*) AS count FROM video_data WHERE createtime >= %s AND createtime < %s",
        (yesterday_start, yesterday_end),
        detection=True,
    )
    last7_image_count = _scalar(
        "SELECT COUNT(*) AS count FROM data WHERE createtime >= %s AND createtime < %s",
        (last7_start, today_end),
        detection=True,
    )
    last7_video_count = _scalar(
        "SELECT COUNT(*) AS count FROM video_data WHERE createtime >= %s AND createtime < %s",
        (last7_start, today_end),
        detection=True,
    )
    last_image_at = _scalar("SELECT createtime AS latest FROM data ORDER BY itemid DESC LIMIT 1", detection=True, default="")
    last_video_at = _scalar("SELECT createtime AS latest FROM video_data ORDER BY itemid DESC LIMIT 1", detection=True, default="")
    return {
        "users": {
            "total": _scalar("SELECT COUNT(*) AS count FROM user"),
            "today": today_user_count,
            "todayNew": today_user_count,
            "apiKeys": _scalar("SELECT COUNT(*) AS count FROM developer_api_keys WHERE status = 'active'"),
        },
        "detections": {
            "images": _scalar("SELECT COUNT(*) AS count FROM data", detection=True),
            "videos": _scalar("SELECT COUNT(*) AS count FROM video_data", detection=True),
            "today": today_image_count + today_video_count,
            "todayImages": today_image_count,
            "todayVideos": today_video_count,
            "yesterday": yesterday_image_count + yesterday_video_count,
            "yesterdayImages": yesterday_image_count,
            "yesterdayVideos": yesterday_video_count,
            "last7Days": last7_image_count + last7_video_count,
            "last7Images": last7_image_count,
            "last7Videos": last7_video_count,
            "lastImageAt": format_createtime(last_image_at),
            "lastVideoAt": format_createtime(last_video_at),
            "feedbackPositive": _scalar("SELECT COUNT(*) AS count FROM data WHERE feedback IN (1, '1', '满意')", detection=True),
            "feedbackNegative": _scalar("SELECT COUNT(*) AS count FROM data WHERE feedback IN (-1, '-1', '不满意')", detection=True),
        },
        "todayWindow": {
            "start": today_start,
            "end": today_end,
            "timezone": os.environ.get("REALGUARD_ADMIN_TIMEZONE", "Asia/Shanghai"),
            "generatedAt": datetime.now(_admin_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
        },
    }


def _hourly_detection_series(hours=24):
    start, buckets = _hours_window(hours)
    image_rows = excute_detection_sql(
        """
        SELECT DATE_FORMAT(createtime, '%%Y-%%m-%%d %%H:00:00') AS bucket, COUNT(*) AS count
        FROM data
        WHERE createtime >= %s
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        (start,),
    ) or []
    video_rows = excute_detection_sql(
        """
        SELECT DATE_FORMAT(createtime, '%%Y-%%m-%%d %%H:00:00') AS bucket, COUNT(*) AS count
        FROM video_data
        WHERE createtime >= %s
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        (start,),
    ) or []
    image_map = {str(row.get("bucket")): int(row.get("count") or 0) for row in image_rows}
    video_map = {str(row.get("bucket")): int(row.get("count") or 0) for row in video_rows}
    keys = [bucket.strftime("%Y-%m-%d %H:00:00") for bucket in buckets]
    return {
        "labels": [bucket.strftime("%H:00") for bucket in buckets],
        "buckets": keys,
        "images": [image_map.get(key, 0) for key in keys],
        "videos": [video_map.get(key, 0) for key in keys],
    }


def _label_distribution():
    rows = excute_detection_sql(
        """
        SELECT COALESCE(NULLIF(aigc, ''), '未标注') AS label, COUNT(*) AS count
        FROM data
        GROUP BY label
        ORDER BY count DESC
        LIMIT 8
        """
    ) or []
    return [{"label": row.get("label"), "count": int(row.get("count") or 0)} for row in rows]


def _feedback_distribution():
    if "feedback" not in _detection_data_columns():
        return [{"label": "未反馈", "count": _scalar("SELECT COUNT(*) AS count FROM data", detection=True)}]
    rows = excute_detection_sql(
        """
        SELECT COALESCE(NULLIF(feedback, ''), '未反馈') AS label, COUNT(*) AS count
        FROM data
        GROUP BY label
        ORDER BY count DESC
        """
    ) or []
    return [{"label": row.get("label"), "count": int(row.get("count") or 0)} for row in rows]


def _recent_detection_items(limit=12):
    select_clause = _detection_data_select_clause()
    rows = excute_detection_sql(
        f"""
        SELECT {select_clause}
        FROM data
        ORDER BY itemid DESC
        LIMIT %s
        """,
        (limit,),
    ) or []
    route_runs = admin_state.model_runs_by_itemids([row.get("itemid") for row in rows])
    return [
        {
            "id": row.get("itemid"),
            "createdAt": format_createtime(row.get("createtime")),
            "filename": row.get("filename"),
            "phone": row.get("phone"),
            "label": row.get("aigc"),
            "probability": row.get("fake"),
            "confidence": row.get("clarity"),
            "feedback": row.get("feedback"),
            "modelRoute": route_runs.get(str(row.get("itemid"))) or None,
        }
        for row in rows
    ]


def _route_distribution(limit=6):
    counts = {}
    for item in admin_state.list_model_runs(1000):
        model = (item.get("model") or {}).get("id") or "unknown"
        counts[model] = counts.get(model, 0) + 1
    return [
        {"model": model, "count": count}
        for model, count in sorted(counts.items(), key=lambda pair: pair[1], reverse=True)[:limit]
    ]


def _big_screen_anomalies(metrics, models, assurance):
    anomalies = []
    for blocker in assurance.get("blockers") or []:
        anomalies.append({"level": "critical", "title": "V1 阻断", "message": blocker})
    for model in models:
        health = model.get("health") or {}
        if not health.get("ok"):
            level = "warning" if health.get("serviceOk") else "critical"
            anomalies.append({
                "level": level,
                "title": model.get("name") or model.get("id") or "模型异常",
                "message": health.get("message") or "模型健康检查未通过",
            })
    routing = assurance.get("routing") or {}
    if routing.get("fallbackEnabled"):
        anomalies.append({
            "level": "warning",
            "title": "自动兜底已开启",
            "message": "V1 失败后会自动调用兜底模型，请确认业务口径允许模型替换。",
        })
    detections = metrics.get("detections") or {}
    positive = int(detections.get("feedbackPositive") or 0)
    negative = int(detections.get("feedbackNegative") or 0)
    if negative >= 5 and negative >= positive:
        anomalies.append({
            "level": "warning",
            "title": "负反馈偏高",
            "message": f"当前负反馈 {negative} 条，建议抽检最近检测记录。",
        })
    return anomalies[:10]


def _big_screen_payload():
    registry = model_registry.load_registry()
    models = [_model_payload(model, include_health=True) for model in registry.get("models", [])]
    metrics = _dashboard_metrics()
    assurance = _v1_assurance()
    return {
        "generatedAt": datetime.now(_admin_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": metrics,
        "routing": registry.get("routing", {}),
        "models": models,
        "series": _hourly_detection_series(24),
        "labels": _label_distribution(),
        "feedback": _feedback_distribution(),
        "routes": _route_distribution(),
        "recent": _recent_detection_items(12),
        "assurance": assurance,
        "anomalies": _big_screen_anomalies(metrics, models, assurance),
    }


def _clear_big_screen_cache():
    _BIG_SCREEN_CACHE["expires"] = 0
    _BIG_SCREEN_CACHE["payload"] = None


def _cached_big_screen_payload():
    now = time.time()
    payload = _BIG_SCREEN_CACHE.get("payload")
    if payload and now < float(_BIG_SCREEN_CACHE.get("expires") or 0):
        return payload
    payload = _big_screen_payload()
    _BIG_SCREEN_CACHE["payload"] = payload
    _BIG_SCREEN_CACHE["expires"] = now + max(1, BIG_SCREEN_CACHE_TTL_SECONDS)
    return payload


def _model_payload(model, include_health=False):
    payload = dict(model)
    payload["artifacts"] = model_registry.artifact_status(model)
    if include_health:
        payload["health"] = model_registry.check_model_health(model)
    return payload


def _service_health():
    registry = model_registry.load_registry()
    models = [_model_payload(model, include_health=True) for model in registry.get("models", [])]
    return {
        "models": models,
        "services": [
            {
                "id": model.get("id"),
                "name": model.get("name"),
                "runtime": model.get("runtime"),
                "endpoint": model.get("endpoint"),
                "healthUrl": model.get("healthUrl"),
                "health": model.get("health"),
            }
            for model in models
        ],
        "environment": {
            "detectorBackendUrl": os.environ.get("REALGUARD_DETECTION_BACKEND_URL", ""),
            "adminConfigured": bool(_admin_phone_set() or _admin_user_id_set()),
            "adminAllowAnyLogin": os.environ.get("REALGUARD_ADMIN_ALLOW_ANY_LOGIN", "0") == "1",
            "adminAccounts": _admin_account_count(),
        },
    }


@admin_blueprint.route("/admin")
def admin_console():
    user = _current_user()
    admin_user = _current_admin_user()
    if not admin_user and not user:
        return redirect(url_for("admin_blueprint.admin_login"))
    active_user = admin_user or _legacy_admin_payload(user) or user
    role = _normalize_admin_role((active_user.get("role") if isinstance(active_user, dict) else "") or "admin")
    return render_template(
        "admin.html",
        admin_allowed=bool(admin_user or _is_legacy_admin(user)),
        admin_user=active_user,
        admin_role_label=ADMIN_ROLE_LABELS.get(role, role),
        admin_permissions=_admin_permissions(role),
        can_manage_admins=_has_admin_permission(active_user, "admin.manage"),
        csrf_token=_csrf_token(),
        role_options=ADMIN_ROLE_LABELS,
        admin_configured=bool(_admin_phone_set() or _admin_user_id_set()),
        admin_account_count=_admin_account_count(),
    )


@admin_blueprint.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        identity = str(request.form.get("identity") or "").strip()
        password = str(request.form.get("password") or "")
        locked = _admin_login_lock_seconds(identity)
        if locked > 0:
            return render_template(
                "admin_auth.html",
                **_admin_auth_context("login", error=f"登录尝试过多，请 {locked} 秒后再试"),
            ), 429
        account = _find_admin_account(identity)
        if not account or account.get("status") != "active" or not check_password_hash(str(account.get("password_hash") or ""), password):
            _record_admin_login_failure(identity)
            return render_template("admin_auth.html", **_admin_auth_context("login", error="管理员账号或密码错误")), 401
        _clear_admin_login_failures(identity)
        _update_admin_login(account)
        session.permanent = True
        session[ADMIN_SESSION_KEY] = _admin_session_payload(account)
        return redirect(url_for("admin_blueprint.admin_console"))
    return render_template("admin_auth.html", **_admin_auth_context("login"))


@admin_blueprint.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    user, permission_error = _admin_required("admin.manage")
    if permission_error:
        return render_template(
            "admin_auth.html",
            **_admin_auth_context("login", error="管理员注册需要已有管理员或后台白名单账号授权"),
        ), 403
    if request.method == "POST":
        username = request.form.get("username") or ""
        phone = request.form.get("phone") or ""
        password = request.form.get("password") or ""
        password_confirm = request.form.get("password_confirm") or ""
        role = request.form.get("role") or "operator"
        if password != password_confirm:
            return render_template("admin_auth.html", **_admin_auth_context("register", error="两次输入的密码不一致")), 400
        ok, message = _create_admin_account(username, phone, password, role=role)
        if not ok:
            return render_template("admin_auth.html", **_admin_auth_context("register", error=message)), 400
        account = _find_admin_account(username)
        if account:
            session.permanent = True
            session[ADMIN_SESSION_KEY] = _admin_session_payload(account)
            _audit(user, "admin_account.create", username, after={"username": username, "phone": phone, "role": role})
        return redirect(url_for("admin_blueprint.admin_console"))
    return render_template("admin_auth.html", **_admin_auth_context("register"))


@admin_blueprint.route("/admin/logout")
def admin_logout():
    session.pop(ADMIN_SESSION_KEY, None)
    session.pop(ADMIN_LOGIN_ATTEMPTS_KEY, None)
    return redirect(url_for("admin_blueprint.admin_login"))


@admin_blueprint.route("/admin/screen")
def admin_screen():
    if _screen_token_valid():
        return render_template("admin_screen.html", admin_user=_screen_token_user(), screen_token=_screen_token_from_request())
    user, error = _admin_required("view")
    if error:
        return redirect(url_for("admin_blueprint.admin_login"))
    return render_template("admin_screen.html", admin_user=user, screen_token="")


@admin_blueprint.route("/api/admin/overview")
def admin_overview():
    _, error = _admin_required("view")
    if error:
        return error
    registry = model_registry.load_registry()
    models = [_model_payload(model, include_health=True) for model in registry.get("models", [])]
    return jsonify({
        "status": "success",
        "routing": registry.get("routing", {}),
        "routingHistory": model_registry.routing_history(10),
        "models": models,
        "metrics": _dashboard_metrics(),
    })


@admin_blueprint.route("/api/admin/big-screen")
def admin_big_screen():
    if _screen_token_valid():
        return jsonify({"status": "success", **_cached_big_screen_payload()})
    _, error = _admin_required("view")
    if error:
        return error
    return jsonify({"status": "success", **_cached_big_screen_payload()})


@admin_blueprint.route("/api/admin/system")
def admin_system():
    _, error = _admin_required("view")
    if error:
        return error
    return jsonify({"status": "success", **_service_health()})


@admin_blueprint.route("/api/admin/models", methods=["GET", "POST"])
def admin_models():
    permission = "model.manage" if request.method == "POST" else "view"
    user, error = _admin_required(permission)
    if error:
        return error
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        _, model, message = model_registry.create_model(payload)
        if not model:
            return jsonify({"status": "error", "message": message or "模型创建失败"}), 400
        _audit(user, "model.create", model.get("id"), before=None, after=model)
        return jsonify({"status": "success", "model": _model_payload(model, include_health=True)}), 201
    return jsonify({
        "status": "success",
        "models": [_model_payload(model) for model in model_registry.list_models()],
        "routing": model_registry.get_routing(),
        "routingHistory": model_registry.routing_history(10),
    })


@admin_blueprint.route("/api/admin/models/<model_id>", methods=["PATCH", "POST"])
def admin_update_model(model_id):
    user, error = _admin_required("model.manage")
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    before = model_registry.get_model(model_id)
    _, model = model_registry.update_model(model_id, payload)
    if not model:
        return jsonify({"status": "error", "message": "模型不存在"}), 404
    _audit(user, "model.update", model_id, before=before, after=model)
    return jsonify({"status": "success", "model": _model_payload(model, include_health=True)})


@admin_blueprint.route("/api/admin/models/<model_id>", methods=["DELETE"])
def admin_delete_model(model_id):
    user, error = _admin_required("model.manage")
    if error:
        return error
    before = model_registry.get_model(model_id)
    registry, message = model_registry.delete_model(model_id)
    if not registry:
        return jsonify({"status": "error", "message": message or "模型删除失败"}), 400
    _audit(user, "model.delete", model_id, before=before, after=None)
    return jsonify({"status": "success", "models": [_model_payload(model) for model in registry.get("models", [])]})


@admin_blueprint.route("/api/admin/models/<model_id>/health", methods=["POST"])
def admin_model_health(model_id):
    _, error = _admin_required("model.probe")
    if error:
        return error
    model = model_registry.get_model(model_id)
    if not model:
        return jsonify({"status": "error", "message": "模型不存在"}), 404
    return jsonify({"status": "success", "health": model_registry.check_model_health(model)})


@admin_blueprint.route("/api/admin/models/<model_id>/probe", methods=["POST"])
def admin_model_probe(model_id):
    user, error = _admin_required("model.probe")
    if error:
        return error
    model = model_registry.get_model(model_id)
    if not model:
        return jsonify({"status": "error", "message": "模型不存在"}), 404
    result = _probe_model(model)
    _audit(user, "model.probe", model_id, meta={"ok": result.get("ok"), "message": result.get("message")})
    return jsonify({"status": "success", "probe": result})


@admin_blueprint.route("/api/admin/routing", methods=["PATCH", "POST"])
def admin_update_routing():
    user, error = _admin_required("routing.manage")
    if error:
        return error
    before = model_registry.get_routing()
    routing = model_registry.update_routing(request.get_json(silent=True) or {})
    _clear_big_screen_cache()
    _audit(user, "routing.update", "image", before=before, after=routing)
    return jsonify({"status": "success", "routing": routing})


@admin_blueprint.route("/api/admin/routing/rollback", methods=["POST"])
def admin_rollback_routing():
    user, error = _admin_required("routing.manage")
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    before = model_registry.get_routing()
    routing, snapshot, message = model_registry.rollback_routing(payload.get("snapshotId"))
    if not routing:
        return jsonify({"status": "error", "message": message or "没有可回滚的路由快照"}), 400
    _clear_big_screen_cache()
    _audit(user, "routing.rollback", "image", before=before, after=routing, meta={"snapshot": snapshot})
    return jsonify({"status": "success", "routing": routing, "snapshot": snapshot})


@admin_blueprint.route("/api/admin/swarm", methods=["GET", "PATCH", "POST"])
def admin_swarm_config():
    permission = "view" if request.method == "GET" else "routing.manage"
    user, error = _admin_required(permission)
    if error:
        return error
    if request.method == "GET":
        return jsonify({"status": "success", "swarm": model_registry.get_swarm_config()})
    before = model_registry.get_swarm_config()
    swarm = model_registry.update_swarm_config(request.get_json(silent=True) or {})
    _clear_big_screen_cache()
    _audit(user, "swarm.update", "swarm", before=before, after=swarm)
    return jsonify({"status": "success", "swarm": swarm})


@admin_blueprint.route("/api/admin/assurance")
def admin_assurance():
    _, error = _admin_required("view")
    if error:
        return error
    return jsonify({"status": "success", "assurance": _v1_assurance()})


@admin_blueprint.route("/api/admin/alerts", methods=["GET", "PATCH", "POST"])
def admin_alerts():
    permission = "view" if request.method == "GET" else "alerts.manage"
    user, error = _admin_required(permission)
    if error:
        return error
    if request.method == "GET":
        return jsonify({"status": "success", "alerts": admin_state.alerts()})
    before = admin_state.alerts()
    alerts = admin_state.update_alerts(request.get_json(silent=True) or {})
    _audit(user, "alerts.update", "alerts", before=before, after=alerts)
    return jsonify({"status": "success", "alerts": alerts})


@admin_blueprint.route("/api/admin/audit")
def admin_audit():
    _, error = _admin_required("view")
    if error:
        return error
    return jsonify({"status": "success", "audit": admin_state.list_audit(_limit_arg(80, 500))})


@admin_blueprint.route("/api/admin/admins")
def admin_accounts():
    _, error = _admin_required("admin.manage")
    if error:
        return error
    if not _ensure_admin_account_table():
        return jsonify({"status": "success", "admins": [], "message": "管理员账号表尚未初始化"})
    limit = _limit_arg(80, 200)
    query = _search_term()
    filters = []
    params = []
    if query:
        like = f"%{query}%"
        filters.append("(username LIKE %s OR phone LIKE %s OR role LIKE %s OR status LIKE %s)")
        params.extend([like, like, like, like])
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)
    rows = excute_sql(
        f"""
        SELECT id, username, phone, role, status, created_at, last_login_at, last_login_ip
        FROM admin_accounts
        {where}
        ORDER BY status = 'active' DESC, id DESC
        LIMIT %s
        """,
        tuple(params),
    ) or []
    return jsonify({
        "status": "success",
        "admins": [_admin_account_payload(row) for row in rows],
        "roles": ADMIN_ROLE_LABELS,
        "currentAdminId": (_current_admin_user() or {}).get("adminId"),
    })


@admin_blueprint.route("/api/admin/admins/<int:admin_id>", methods=["PATCH", "POST"])
def admin_update_account(admin_id):
    actor, error = _admin_required("admin.manage")
    if error:
        return error
    if not _ensure_admin_account_table():
        return jsonify({"status": "error", "message": "管理员账号表尚未初始化"}), 400
    before_rows = excute_sql(
        """
        SELECT id, username, phone, role, status, created_at, last_login_at, last_login_ip
        FROM admin_accounts
        WHERE id = %s
        LIMIT 1
        """,
        (admin_id,),
    ) or []
    if not before_rows:
        return jsonify({"status": "error", "message": "管理员账号不存在"}), 404
    before = before_rows[0]
    payload = request.get_json(silent=True) or {}
    role = payload.get("role", before.get("role"))
    status = payload.get("status", before.get("status"))
    role = _normalize_admin_role(role)
    status = str(status or "active").strip()
    if status not in ("active", "disabled"):
        return jsonify({"status": "error", "message": "管理员状态只能为 active 或 disabled"}), 400
    if actor.get("adminId") == admin_id and (status != "active" or role not in ("admin", "super_admin")):
        return jsonify({"status": "error", "message": "不能停用或降级当前登录管理员"}), 400
    updated = excute_sql(
        "UPDATE admin_accounts SET role = %s, status = %s WHERE id = %s",
        (role, status, admin_id),
        fetch=False,
    )
    if updated is None:
        return jsonify({"status": "error", "message": "管理员账号更新失败"}), 500
    after_rows = excute_sql(
        """
        SELECT id, username, phone, role, status, created_at, last_login_at, last_login_ip
        FROM admin_accounts
        WHERE id = %s
        LIMIT 1
        """,
        (admin_id,),
    ) or []
    after = after_rows[0] if after_rows else before
    _audit(actor, "admin_account.update", str(admin_id), before=_admin_account_payload(before), after=_admin_account_payload(after))
    return jsonify({"status": "success", "admin": _admin_account_payload(after)})


@admin_blueprint.route("/api/admin/admins/<int:admin_id>/password", methods=["POST"])
def admin_reset_account_password(admin_id):
    actor, error = _admin_required("admin.manage")
    if error:
        return error
    if not _ensure_admin_account_table():
        return jsonify({"status": "error", "message": "管理员账号表尚未初始化"}), 400
    payload = request.get_json(silent=True) or {}
    password = str(payload.get("password") or "")
    password_error = _admin_password_error(password)
    if password_error:
        return jsonify({"status": "error", "message": password_error}), 400
    rows = excute_sql(
        """
        SELECT id, username, phone, role, status, created_at, last_login_at, last_login_ip
        FROM admin_accounts
        WHERE id = %s
        LIMIT 1
        """,
        (admin_id,),
    ) or []
    if not rows:
        return jsonify({"status": "error", "message": "管理员账号不存在"}), 404
    updated = excute_sql(
        "UPDATE admin_accounts SET password_hash = %s WHERE id = %s",
        (generate_password_hash(password), admin_id),
        fetch=False,
    )
    if updated is None:
        return jsonify({"status": "error", "message": "管理员密码重置失败"}), 500
    _audit(actor, "admin_account.password_reset", str(admin_id), before=_admin_account_payload(rows[0]), after={"id": admin_id, "passwordReset": True})
    return jsonify({"status": "success"})


@admin_blueprint.route("/api/admin/users")
def admin_users():
    _, error = _admin_required("view")
    if error:
        return error
    limit = _limit_arg(50, 200)
    query = _search_term()
    where = ""
    params = []
    if query:
        like = f"%{query}%"
        where = "WHERE phone LIKE %s OR username LIKE %s OR openid LIKE %s"
        params.extend([like, like, like])
    params.append(limit)
    rows = excute_sql(
        f"""
        SELECT Userid, phone, username, openid, created_at, terms_version, terms_accepted_at
        FROM user
        {where}
        ORDER BY Userid DESC
        LIMIT %s
        """,
        tuple(params),
    ) or []
    users = []
    for row in rows:
        users.append({
            "id": row.get("Userid"),
            "phone": row.get("phone"),
            "username": row.get("username"),
            "openid": row.get("openid"),
            "createdAt": format_createtime(row.get("created_at")),
            "termsVersion": row.get("terms_version"),
            "termsAcceptedAt": format_createtime(row.get("terms_accepted_at")),
        })
    return jsonify({"status": "success", "users": users})


@admin_blueprint.route("/api/admin/users/<int:user_id>")
def admin_user_detail(user_id):
    _, error = _admin_required("view")
    if error:
        return error
    rows = excute_sql(
        """
        SELECT Userid, phone, username, openid, created_at, terms_version, terms_accepted_at
        FROM user
        WHERE Userid = %s
        LIMIT 1
        """,
        (user_id,),
    ) or []
    if not rows:
        return jsonify({"status": "error", "message": "用户不存在"}), 404
    row = rows[0]
    phone = row.get("phone") or ""
    openid = row.get("openid") or ""
    history_where, history_params = detection_owner_where(phone, openid)
    select_clause = _detection_data_select_clause()
    detection_rows = excute_detection_sql(
        f"""
        SELECT {select_clause}
        FROM data
        WHERE {history_where}
        ORDER BY itemid DESC
        LIMIT 20
        """,
        history_params,
    ) or []
    key_rows = excute_sql(
        """
        SELECT id, name, key_prefix, key_last4, scopes, status, created_at, last_used_at, last_used_ip
        FROM developer_api_keys
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 20
        """,
        (user_id,),
    ) or []
    image_count = _scalar(f"SELECT COUNT(*) AS count FROM data WHERE {history_where}", history_params, detection=True)
    video_count = _scalar(f"SELECT COUNT(*) AS count FROM video_data WHERE {history_where}", history_params, detection=True)
    route_runs = admin_state.model_runs_by_itemids([item.get("itemid") for item in detection_rows])
    user = {
        "id": row.get("Userid"),
        "phone": phone,
        "username": row.get("username"),
        "openid": openid,
        "createdAt": format_createtime(row.get("created_at")),
        "termsVersion": row.get("terms_version"),
        "termsAcceptedAt": format_createtime(row.get("terms_accepted_at")),
    }
    return jsonify({
        "status": "success",
        "user": user,
        "stats": {"imageDetections": image_count, "videoDetections": video_count, "apiKeys": len(key_rows)},
        "detections": [
            {
                "id": item.get("itemid"),
                "createdAt": format_createtime(item.get("createtime")),
                "filename": item.get("filename"),
                "phone": item.get("phone"),
                "label": item.get("aigc"),
                "probability": item.get("fake"),
                "detectorProbability": item.get("detector_probability"),
                "confidence": item.get("clarity"),
                "feedback": item.get("feedback"),
                "modelRoute": route_runs.get(str(item.get("itemid"))) or None,
            }
            for item in detection_rows
        ],
        "keys": [
            {
                "id": key.get("id"),
                "name": key.get("name"),
                "masked": f"{key.get('key_prefix') or 'rg_sk_'}...{key.get('key_last4') or '****'}",
                "scopes": key.get("scopes") or "",
                "status": key.get("status") or "",
                "createdAt": format_createtime(key.get("created_at")),
                "lastUsedAt": format_createtime(key.get("last_used_at")),
                "lastUsedIp": key.get("last_used_ip") or "",
            }
            for key in key_rows
        ],
    })


@admin_blueprint.route("/api/admin/users/export")
def admin_users_export():
    _, error = _admin_required("data.export")
    if error:
        return error
    rows = excute_sql(
        """
        SELECT Userid, phone, username, openid, created_at, terms_version, terms_accepted_at
        FROM user
        ORDER BY Userid DESC
        LIMIT 5000
        """
    ) or []
    return _csv_response(
        "realguard-users.csv",
        ["ID", "Phone", "Username", "OpenID", "Created At", "Terms Version", "Terms Accepted At"],
        [
            [
                row.get("Userid"),
                row.get("phone"),
                row.get("username"),
                row.get("openid"),
                format_createtime(row.get("created_at")),
                row.get("terms_version"),
                format_createtime(row.get("terms_accepted_at")),
            ]
            for row in rows
        ],
    )


@admin_blueprint.route("/api/admin/detections")
def admin_detections():
    _, error = _admin_required("view")
    if error:
        return error
    limit = _limit_arg(80, 200)
    query = _search_term()
    label = str(request.args.get("label") or "").strip()
    filters = []
    params = []
    if query:
        like = f"%{query}%"
        filters.append("(phone LIKE %s OR filename LIKE %s OR aigc LIKE %s)")
        params.extend([like, like, like])
    if label:
        filters.append("aigc = %s")
        params.append(label)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)
    select_clause = _detection_data_select_clause()
    rows = excute_detection_sql(
        f"""
        SELECT {select_clause}
        FROM data
        {where}
        ORDER BY itemid DESC
        LIMIT %s
        """,
        tuple(params),
    ) or []
    route_runs = admin_state.model_runs_by_itemids([row.get("itemid") for row in rows])
    items = []
    for row in rows:
        items.append({
            "id": row.get("itemid"),
            "createdAt": format_createtime(row.get("createtime")),
            "filename": row.get("filename"),
            "phone": row.get("phone"),
            "label": row.get("aigc"),
            "probability": row.get("fake"),
            "detectorProbability": row.get("detector_probability"),
            "confidence": row.get("clarity"),
            "feedback": row.get("feedback"),
            "modelRoute": route_runs.get(str(row.get("itemid"))) or None,
        })
    return jsonify({"status": "success", "detections": items})


@admin_blueprint.route("/api/admin/detections/<int:itemid>")
def admin_detection_detail(itemid):
    _, error = _admin_required("view")
    if error:
        return error
    select_clause = _detection_data_select_clause()
    rows = excute_detection_sql(
        f"""
        SELECT {select_clause}, explantation, Userid, openid
        FROM data
        WHERE itemid = %s
        LIMIT 1
        """,
        (itemid,),
    ) or []
    if not rows:
        return jsonify({"status": "error", "message": "检测记录不存在"}), 404
    row = rows[0]
    metadata_rows = excute_detection_sql(
        "SELECT all_metadata FROM exif WHERE data_itemid = %s LIMIT 1",
        (itemid,),
    ) or []
    metadata = {}
    if metadata_rows and metadata_rows[0].get("all_metadata"):
        try:
            import json
            metadata = json.loads(metadata_rows[0].get("all_metadata") or "{}")
        except Exception:
            metadata = {}
    route_runs = admin_state.model_runs_by_itemids([itemid])
    item = {
        "id": row.get("itemid"),
        "userId": row.get("Userid"),
        "openid": row.get("openid"),
        "createdAt": format_createtime(row.get("createtime")),
        "filename": row.get("filename"),
        "phone": row.get("phone"),
        "label": row.get("aigc"),
        "probability": row.get("fake"),
        "detectorProbability": row.get("detector_probability"),
        "confidence": row.get("clarity"),
        "feedback": row.get("feedback"),
        "explanation": row.get("explantation") or "",
        "metadata": metadata,
        "modelRoute": route_runs.get(str(itemid)) or None,
    }
    return jsonify({"status": "success", "detection": item})


@admin_blueprint.route("/api/admin/detections/export")
def admin_detections_export():
    _, error = _admin_required("data.export")
    if error:
        return error
    select_clause = _detection_data_select_clause()
    rows = excute_detection_sql(
        f"""
        SELECT {select_clause}
        FROM data
        ORDER BY itemid DESC
        LIMIT 5000
        """
    ) or []
    route_runs = admin_state.model_runs_by_itemids([row.get("itemid") for row in rows])
    return _csv_response(
        "realguard-detections.csv",
        ["ID", "Created At", "Phone", "Filename", "Label", "Probability", "Detector Probability", "Confidence", "Feedback", "Model Route"],
        [
            [
                row.get("itemid"),
                format_createtime(row.get("createtime")),
                row.get("phone"),
                row.get("filename"),
                row.get("aigc"),
                row.get("fake"),
                row.get("detector_probability"),
                row.get("clarity"),
                row.get("feedback"),
                (route_runs.get(str(row.get("itemid"))) or {}).get("model", {}).get("id", ""),
            ]
            for row in rows
        ],
    )


@admin_blueprint.route("/api/admin/detections/<int:itemid>/review", methods=["PATCH", "POST"])
def admin_review_detection(itemid):
    user, error = _admin_required("detection.review")
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    feedback = payload.get("feedback")
    try:
        feedback = None if feedback in (None, "") else int(feedback)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "feedback 参数无效"}), 400
    if feedback not in (1, -1, 0, None):
        return jsonify({"status": "error", "message": "feedback 只能为 1、-1 或 0"}), 400
    before = excute_detection_sql("SELECT itemid, feedback FROM data WHERE itemid = %s LIMIT 1", (itemid,)) or []
    db_value = None
    if feedback == 1:
        db_value = "满意"
    elif feedback == -1:
        db_value = "不满意"
    updated = excute_detection_sql(
        "UPDATE data SET feedback = %s WHERE itemid = %s",
        (db_value, itemid),
        fetch=False,
    )
    if updated is None:
        return jsonify({"status": "error", "message": "检测记录更新失败"}), 500
    if updated == 0:
        return jsonify({"status": "error", "message": "检测记录不存在"}), 404
    after = {"itemid": itemid, "feedback": db_value}
    _audit(user, "detection.review", str(itemid), before=before[0] if before else None, after=after)
    return jsonify({"status": "success", "review": after})


@admin_blueprint.route("/api/admin/api-keys")
def admin_api_keys():
    _, error = _admin_required("view")
    if error:
        return error
    limit = _limit_arg(80, 200)
    query = _search_term()
    filters = []
    params = []
    if query:
        like = f"%{query}%"
        filters.append("(k.name LIKE %s OR u.phone LIKE %s OR u.username LIKE %s OR k.key_last4 LIKE %s)")
        params.extend([like, like, like, like])
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)
    rows = excute_sql(
        f"""
        SELECT
            k.id, k.user_id, k.name, k.key_prefix, k.key_last4, k.scopes, k.status,
            k.created_at, k.last_used_at, k.revoked_at, k.last_used_ip,
            u.phone, u.username
        FROM developer_api_keys k
        LEFT JOIN user u ON u.Userid = k.user_id
        {where}
        ORDER BY k.status = 'active' DESC, k.created_at DESC
        LIMIT %s
        """,
        tuple(params),
    )
    if rows is None:
        return jsonify({"status": "success", "keys": [], "message": "API Key 表尚未初始化"})
    keys = []
    for row in rows:
        keys.append({
            "id": row.get("id"),
            "userId": row.get("user_id"),
            "owner": row.get("username") or row.get("phone") or "",
            "phone": row.get("phone") or "",
            "name": row.get("name"),
            "masked": f"{row.get('key_prefix') or 'rg_sk_'}...{row.get('key_last4') or '****'}",
            "scopes": row.get("scopes") or "",
            "status": row.get("status") or "",
            "createdAt": format_createtime(row.get("created_at")),
            "lastUsedAt": format_createtime(row.get("last_used_at")),
            "revokedAt": format_createtime(row.get("revoked_at")),
            "lastUsedIp": row.get("last_used_ip") or "",
            "quota": admin_state.get_api_key_quota(row.get("id")),
        })
    return jsonify({"status": "success", "keys": keys})


@admin_blueprint.route("/api/admin/api-keys/<int:key_id>/quota", methods=["PATCH", "POST"])
def admin_api_key_quota(key_id):
    user, error = _admin_required("api_key.manage")
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    normalized = {}
    for field in ("dailyLimit", "rateLimitPerMinute"):
        if field in payload:
            value = payload.get(field)
            if value in (None, ""):
                normalized[field] = None
            else:
                try:
                    normalized[field] = max(0, int(value))
                except (TypeError, ValueError):
                    return jsonify({"status": "error", "message": f"{field} 必须为数字"}), 400
    for field in ("scopes", "notes"):
        if field in payload:
            normalized[field] = str(payload.get(field) or "").strip()
    before = admin_state.get_api_key_quota(key_id)
    quota = admin_state.set_api_key_quota(key_id, normalized)
    _audit(user, "api_key.quota.update", str(key_id), before=before, after=quota)
    return jsonify({"status": "success", "quota": quota})
