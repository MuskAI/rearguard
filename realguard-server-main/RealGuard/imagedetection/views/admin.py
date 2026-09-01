import base64
import binascii
import csv
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import stat
import threading
import time
from datetime import datetime, timedelta, timezone
from http.client import HTTPResponse
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import quote, unquote, urlparse

import requests
from flask import Blueprint, Response, g, jsonify, redirect, render_template, request, send_file, session, stream_with_context, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from imagedetection.decision_labels import binary_final_label
from imagedetection.views import (
    admin_state,
    aliyun_green,
    evidence_manifest,
    internal_testing,
    model_registry,
    traffic_geo,
)
from imagedetection.views.utils import (
    LegacyGovernanceError,
    approve_legacy_record_claim,
    detection_owner_where,
    excute_detection_sql,
    excute_sql,
    format_createtime,
    get_db_connection,
    legacy_record_preview,
    list_legacy_record_claims,
    normalize_account_uuid,
    reject_legacy_record_claim,
    request_legacy_record_claim,
)


admin_blueprint = Blueprint("admin_blueprint", __name__)
ADMIN_SESSION_KEY = "admin_user"
ADMIN_LOGIN_ATTEMPTS_KEY = "admin_login_attempts"
ADMIN_CSRF_SESSION_KEY = "admin_csrf_token"
ADMIN_SCREEN_SESSION_KEY = "admin_screen_access_digest"
ADMIN_SCREEN_SESSION_ISSUED_KEY = "admin_screen_access_issued_at"
ADMIN_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")
INTERNAL_TEST_OWNER_SQL = "COALESCE(openid, '') <> '__realguard_internal_test__'"
ADMIN_PASSWORD_MIN_LENGTH = int(os.environ.get("REALGUARD_ADMIN_PASSWORD_MIN_LENGTH", "10"))
ADMIN_LOGIN_MAX_ATTEMPTS = int(os.environ.get("REALGUARD_ADMIN_LOGIN_MAX_ATTEMPTS", "5"))
ADMIN_LOGIN_IP_MAX_ATTEMPTS = int(os.environ.get("REALGUARD_ADMIN_LOGIN_IP_MAX_ATTEMPTS", "20"))
ADMIN_LOGIN_WINDOW_SECONDS = int(os.environ.get("REALGUARD_ADMIN_LOGIN_WINDOW_SECONDS", "900"))
ADMIN_LOGIN_LOCK_SECONDS = int(os.environ.get("REALGUARD_ADMIN_LOGIN_LOCK_SECONDS", "600"))
ADMIN_SESSION_MAX_AGE_SECONDS = int(os.environ.get("REALGUARD_ADMIN_SESSION_MAX_AGE_SECONDS", "28800"))
ADMIN_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS admin_accounts (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(64) NOT NULL UNIQUE,
        phone VARCHAR(20) NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(32) NOT NULL DEFAULT 'admin',
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        session_version INT NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_login_at DATETIME NULL,
        last_login_ip VARCHAR(64) NULL,
        KEY idx_admin_accounts_status (status),
        KEY idx_admin_accounts_role_status (role, status),
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
    "audit.view",
    "admin.manage",
    "user.view",
    "user.read_pii",
    "conversation.view",
    "conversation.read_content",
    "conversation.read_media",
    "detection.view",
    "detection.read_pii",
    "api_key.view",
    "billing.view",
    "billing.adjust",
    "billing.pricing",
    "quota.manage",
    "topology.view",
    "model.manage",
    "model.probe",
    "testing.view",
    "testing.run",
    "testing.load",
    "routing.manage",
    "alerts.manage",
    "detection.review",
    "api_key.manage",
    "data.export",
    "legacy_history.view",
    "legacy_history.request",
    "legacy_history.approve",
}
ADMIN_ROLE_PERMISSIONS = {
    "super_admin": ALL_ADMIN_PERMISSIONS - {
        "legacy_history.request", "legacy_history.approve",
    },
    "admin": ALL_ADMIN_PERMISSIONS - {
        "admin.manage", "billing.adjust", "billing.pricing", "quota.manage",
        "legacy_history.request", "legacy_history.approve",
    },
    "operator": {
        "view",
        "detection.view",
        "detection.read_pii",
        "api_key.view",
        "topology.view",
        "model.probe",
        "testing.view",
        "testing.run",
        "testing.load",
        "alerts.manage",
        "routing.manage",
        "detection.review",
        "data.export",
        "legacy_history.view",
        "legacy_history.request",
    },
    "reviewer": {
        "view", "detection.view", "detection.review", "data.export",
        "testing.view", "conversation.view", "conversation.read_content",
        "conversation.read_media",
        "legacy_history.view", "legacy_history.approve",
    },
    "readonly": {"view"},
}
BIG_SCREEN_CACHE_TTL_SECONDS = int(os.environ.get("REALGUARD_BIG_SCREEN_CACHE_SECONDS", "15"))
_BIG_SCREEN_CACHE = {"expires": 0, "payload": None}
DASHBOARD_METRICS_CACHE_TTL_SECONDS = int(os.environ.get("REALGUARD_DASHBOARD_METRICS_CACHE_SECONDS", "15"))
_DASHBOARD_METRICS_CACHE = {"expires": 0, "payload": None}
_TRAFFIC_SUMMARY_CACHE = {"expires": 0, "payload": None}
_REGISTERED_USER_CACHE_TTL_SECONDS = 60
_REGISTERED_USER_CACHE = {"expires": 0.0, "rows": None}
_REGISTERED_USER_CACHE_LOCK = threading.Lock()
_PROCESS_STARTED_MONOTONIC = time.monotonic()
_ALERT_WORKER_LOCK = threading.Lock()
_ALERT_WORKER_THREAD = None
PROBE_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
BACKUP_STATUS_MAX_BYTES = 16384
ASSURANCE_STATUS_MAX_BYTES = 16384


def _current_user():
    user = session.get("user_info")
    return user if isinstance(user, dict) else None


def _current_admin_user():
    user = session.get(ADMIN_SESSION_KEY)
    if not isinstance(user, dict):
        return None
    if hasattr(g, "_realguard_admin_user"):
        return g._realguard_admin_user
    refreshed = _refresh_admin_session(user)
    if not refreshed:
        # Keep the reason available for the remainder of this request. The
        # normal website session may still be valid, but an expired/revoked
        # administrator session must be treated as an authentication failure
        # instead of a generic role denial.
        g._realguard_admin_session_invalid = True
        session.pop(ADMIN_SESSION_KEY, None)
        session.pop(ADMIN_CSRF_SESSION_KEY, None)
        session.modified = True
        g._realguard_admin_user = None
        return None
    if refreshed != user:
        session[ADMIN_SESSION_KEY] = refreshed
        session.modified = True
    g._realguard_admin_user = refreshed
    return refreshed


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
    # A missing allowlist must fail closed. The first administrator is created
    # through `flask create-admin`, never by an arbitrary logged-in account.
    if not phones and not user_ids:
        return False
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
    role = _normalize_admin_role(user.get("role") or "readonly")
    permissions = ADMIN_ROLE_PERMISSIONS.get(role, set())
    return "*" in permissions or permission in permissions


def _legacy_admin_payload(user):
    if not user:
        return None
    return {
        "Userid": user.get("Userid"),
        "adminId": None,
        "username": user.get("username") or "",
        "phone": user.get("phone") or "",
        "role": "readonly",
        "authType": "legacy_whitelist",
    }


def _is_admin(user):
    return bool(_current_admin_user() or _is_legacy_admin(user))


def _permission_error(permission):
    return jsonify({
        "status": "error",
        "code": "admin_permission_denied",
        "message": f"当前管理员角色缺少权限：{permission}",
    }), 403


def _admin_auth_error(message="管理员未登录", code="admin_session_required"):
    return jsonify({
        "status": "error",
        "code": code,
        "message": message,
    }), 401


def _legacy_governance_error(exc):
    status = {
        "not_found": 404,
        "account_not_found": 404,
        "already_owned": 409,
        "already_processed": 409,
        "duplicate_request": 409,
        "duplicate_approval": 409,
        "media_mismatch": 409,
        "evidence_mismatch": 409,
        "account_changed": 409,
        "exif_owner_conflict": 409,
        "audit_integrity_failed": 409,
        "fingerprint_mismatch": 409,
        "version_mismatch": 409,
        "write_conflict": 409,
        "separation_of_duties": 403,
        "named_admin_required": 403,
        "verified_identity_required": 403,
        "audit_unavailable": 503,
        "media_unverifiable": 422,
        "evidence_unverifiable": 422,
    }.get(getattr(exc, "code", ""), 400)
    return jsonify({"status": "error", "code": getattr(exc, "code", "invalid_request"), "message": str(exc)}), status


def _named_admin_actor(actor):
    try:
        admin_id = int((actor or {}).get("adminId") or 0)
    except (TypeError, ValueError):
        admin_id = 0
    if admin_id <= 0 or (actor or {}).get("authType") != "admin_account":
        raise LegacyGovernanceError("遗留数据治理必须使用实名后台管理员账号", "named_admin_required")
    raw_phone = re.sub(r"[\s()-]", "", str((actor or {}).get("phone") or "").strip())
    if raw_phone.startswith("+86"):
        raw_phone = raw_phone[3:]
    elif raw_phone.startswith("0086"):
        raw_phone = raw_phone[4:]
    if not re.fullmatch(r"1[3-9]\d{9}", raw_phone):
        raise LegacyGovernanceError("遗留数据治理管理员必须绑定已验证的中国大陆手机号", "verified_identity_required")
    identities = excute_sql(
        "SELECT account_uuid FROM `user` WHERE phone = %s LIMIT 1",
        (raw_phone,),
    ) or []
    identity = str((identities[0] if identities else {}).get("account_uuid") or "")
    if not identity:
        raise LegacyGovernanceError("管理员手机号尚未绑定已验证用户身份", "verified_identity_required")
    return admin_id, str((actor or {}).get("username") or ""), identity


def _admin_required(permission="view"):
    admin_user = _current_admin_user()
    if admin_user:
        admin_user["role"] = _normalize_admin_role(admin_user.get("role") or "admin")
        if not _has_admin_permission(admin_user, permission):
            return None, _permission_error(permission)
        return admin_user, None
    if getattr(g, "_realguard_admin_session_invalid", False):
        return None, _admin_auth_error("后台会话已过期或已被撤销，请重新登录", "admin_session_expired")
    user = _current_user()
    if not user:
        return None, _admin_auth_error()
    if not _is_legacy_admin(user):
        return None, (jsonify({
            "status": "error",
            "code": "admin_access_denied",
            "message": "无后台管理权限",
        }), 403)
    # The website-account allowlist only exists to bootstrap the first named
    # administrator. Once a dedicated administrator account exists, silently
    # falling back to this readonly identity leaves an expired admin tab looking
    # signed in while every privileged panel fails.
    if _admin_account_count() > 0:
        return None, _admin_auth_error(
            "请重新登录管理员账号后继续",
            "admin_session_required",
        )
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
        if request.path.startswith("/api/admin/"):
            active_admin = _current_admin_user()
            if not active_admin:
                if getattr(g, "_realguard_admin_session_invalid", False):
                    return _admin_auth_error("后台会话已过期或已被撤销，请重新登录", "admin_session_expired")
                if not _current_user():
                    return _admin_auth_error()
        return jsonify({
            "status": "error",
            "code": "csrf_failed",
            "message": "CSRF 校验失败，请刷新后台页面后重试",
        }), 403
    return render_template(
        "admin_auth.html",
        **_admin_auth_context("login", error="CSRF 校验失败，请刷新页面后重试"),
    ), 403


def _csrf_valid():
    expected = session.get(ADMIN_CSRF_SESSION_KEY)
    provided = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    return bool(expected and provided and hmac.compare_digest(str(expected), str(provided)))


def _screen_token_from_request():
    header_token = request.headers.get("X-RealGuard-Screen-Token") or ""
    return header_token.strip()


def _configured_screen_token_digest():
    plain = (os.environ.get("REALGUARD_BIG_SCREEN_TOKEN") or "").strip()
    digest = (os.environ.get("REALGUARD_BIG_SCREEN_TOKEN_SHA256") or "").strip().lower()
    if digest:
        return digest
    return hashlib.sha256(plain.encode("utf-8")).hexdigest() if plain else ""


def _screen_token_matches(token):
    token = str(token or "").strip()
    if not token:
        return False
    plain = (os.environ.get("REALGUARD_BIG_SCREEN_TOKEN") or "").strip()
    digest = (os.environ.get("REALGUARD_BIG_SCREEN_TOKEN_SHA256") or "").strip().lower()
    if plain and hmac.compare_digest(token, plain):
        return True
    if digest:
        return hmac.compare_digest(hashlib.sha256(token.encode("utf-8")).hexdigest(), digest)
    return False


def _screen_session_valid():
    expected = _configured_screen_token_digest()
    claim = str(session.get(ADMIN_SCREEN_SESSION_KEY) or "")
    try:
        issued_at = int(session.get(ADMIN_SCREEN_SESSION_ISSUED_KEY) or 0)
    except (TypeError, ValueError):
        issued_at = 0
    max_age = max(300, int(os.environ.get("REALGUARD_BIG_SCREEN_SESSION_SECONDS", "14400")))
    valid_age = issued_at > 0 and 0 <= int(time.time()) - issued_at <= max_age
    return bool(expected and claim and valid_age and hmac.compare_digest(claim, expected))


def _screen_token_valid():
    return _screen_token_matches(_screen_token_from_request()) or _screen_session_valid()


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
    remote = str(request.remote_addr or "").strip()
    trusted = {
        item.strip()
        for item in os.environ.get("REALGUARD_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
        if item.strip()
    }
    candidate = remote
    if remote in trusted:
        forwarded = str(request.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
        candidate = forwarded or str(request.headers.get("X-Real-IP") or "").strip() or remote
    try:
        return str(ipaddress.ip_address(candidate))[:128]
    except ValueError:
        return remote[:128]


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


def _internal_testing_remote_url():
    value = str(os.environ.get("REALGUARD_INTERNAL_TESTING_REMOTE_URL") or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("内部评测远程地址必须使用本机回环隧道")
    return value


def _internal_testing_remote_required():
    value = str(
        os.environ.get("REALGUARD_INTERNAL_TESTING_REMOTE_REQUIRED") or ""
    ).strip().lower()
    environment = str(os.environ.get("REALGUARD_ENV") or "").strip().lower()
    return value in {"1", "true", "yes", "on"} or environment in {
        "prod", "production",
    }


def _internal_testing_remote_model():
    path = request.path.rstrip("/")
    model_id = ""
    if request.method == "POST" and path == "/api/admin/testing/runs":
        model_id = str((request.get_json(silent=True) or {}).get("modelId") or "")
    elif request.method == "POST" and path == "/api/admin/testing/dataset-imports":
        payload = request.get_json(silent=True) or {}
        if payload.get("streamEvaluation"):
            model_id = str(payload.get("modelId") or "")
    elif request.method == "POST" and path == "/api/admin/testing/load-tests":
        model_id = str(request.headers.get("X-RealGuard-Testing-Model-Id") or "")
        if not model_id:
            raise ValueError("后台页面版本过旧，请刷新页面后重新启动压测")
    if not model_id:
        return None
    model, message = _internal_testing_model(model_id)
    if message:
        raise ValueError(message)
    return model


def _internal_testing_remote_audit(user, status_code):
    if status_code >= 400 or request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    path = request.path.rstrip("/")
    action = "testing.remote.mutate"
    target = path.rsplit("/", 1)[-1] or "internal-testing"
    if path == "/api/admin/testing/dataset-imports":
        action, target = "testing.dataset_import.create", "dataset-import"
    elif path.endswith("/finalize"):
        action, target = "testing.dataset_import.finalize", path.split("/")[-2]
    elif "/dataset-imports/" in path and request.method == "DELETE":
        action = "testing.dataset_import.delete"
    elif path == "/api/admin/testing/runs":
        action, target = "testing.evaluation.start", "evaluation"
    elif path == "/api/admin/testing/load-tests":
        action, target = "testing.load.start", "load-test"
    elif path.endswith("/cancel"):
        action, target = "testing.run.cancel", path.split("/")[-2]
    elif "/datasets/" in path and request.method == "DELETE":
        action = "testing.dataset.delete"
    elif "/samples/" in path:
        action = "testing.sample.label"
    try:
        _audit(user, action, target, meta={"dataHost": "10.1.20.66"})
    except Exception:
        # The remote mutation has already committed. Audit availability must not
        # turn a successful upload into a browser retry and duplicate the chunk.
        pass


@admin_blueprint.before_request
def _proxy_remote_internal_testing():
    if not request.path.startswith("/api/admin/testing/"):
        return None
    try:
        remote_url = _internal_testing_remote_url()
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 503
    if not remote_url:
        if _internal_testing_remote_required():
            return jsonify({
                "status": "error",
                "message": "内部评测仅允许使用 66 服务器存储，远程存储隧道尚未配置",
            }), 503
        return None
    permission = (
        "testing.load"
        if request.path.rstrip("/") == "/api/admin/testing/load-tests"
        else "testing.view" if request.method in {"GET", "HEAD", "OPTIONS"}
        else "testing.run"
    )
    user, error = _admin_required(permission)
    if error:
        return error
    token = str(os.environ.get("REALGUARD_INTERNAL_TESTING_TOKEN") or "").strip()
    if not token:
        return jsonify({
            "status": "error",
            "message": "内部评测远程存储尚未完成配置",
        }), 503
    try:
        model = _internal_testing_remote_model()
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    actor_id = str(user.get("adminId") or user.get("Userid") or "")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-RealGuard-Actor-Id": actor_id,
        "X-RealGuard-Actor-Name": str(user.get("username") or ""),
    }
    for name, value in request.headers.items():
        lowered = name.lower()
        if lowered.startswith("x-upload-") or lowered in {
            "content-type", "content-length", "range", "if-modified-since", "if-none-match",
        }:
            headers[name] = value
    if model:
        encoded = base64.urlsafe_b64encode(
            json.dumps(model, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("=")
        headers["X-RealGuard-Testing-Model"] = encoded

    body = None
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.is_json:
            body = request.get_data(cache=True)
        else:
            body = request.stream
    remote = None
    remote_session = requests.Session()
    remote_session.trust_env = False
    try:
        remote = remote_session.request(
            request.method,
            f"{remote_url}{request.path}",
            params=list(request.args.items(multi=True)),
            headers=headers,
            data=body,
            timeout=(5, 900),
            allow_redirects=False,
            stream=True,
        )
    except requests.RequestException:
        remote_session.close()
        return jsonify({
            "status": "error",
            "message": "66 服务器的内部评测服务暂时不可达，上传断点已保留",
        }), 503

    content_type = str(remote.headers.get("Content-Type") or "")
    if "application/json" in content_type:
        content = remote.content
        status_code = remote.status_code
        response_headers = {
            key: value for key, value in remote.headers.items()
            if key.lower() in {"content-type", "retry-after"}
        }
        remote.close()
        remote_session.close()
        if request.path.rstrip("/") == "/api/admin/testing/overview" and status_code < 400:
            try:
                payload = json.loads(content.decode("utf-8"))
                payload["system"] = _testing_system_snapshot()
                payload["system"]["internalTestingStorage"] = payload.get("storage") or {}
                content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
                pass
        _internal_testing_remote_audit(user, status_code)
        return Response(content, status=status_code, headers=response_headers)

    passthrough_headers = {
        key: value for key, value in remote.headers.items()
        if key.lower() in {
            "content-type", "content-length", "content-disposition", "etag",
            "last-modified", "accept-ranges", "content-range",
        }
    }

    @stream_with_context
    def generate():
        try:
            yield from remote.iter_content(chunk_size=64 * 1024)
        finally:
            remote.close()
            remote_session.close()

    _internal_testing_remote_audit(user, remote.status_code)
    return Response(generate(), status=remote.status_code, headers=passthrough_headers)


@admin_blueprint.after_request
def _admin_security_headers(response):
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


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
    columns = excute_sql("SHOW COLUMNS FROM admin_accounts") or []
    if columns and "session_version" not in {row.get("Field") for row in columns}:
        result = excute_sql(
            "ALTER TABLE admin_accounts ADD COLUMN session_version INT NOT NULL DEFAULT 1 AFTER status",
            fetch=False,
        )
        if result is None:
            ok = False
            messages.append("admin_accounts.session_version: failed")
        else:
            messages.append("admin_accounts.session_version: ready")
    detection_columns = _detection_data_columns()
    if detection_columns and "admin_review" not in detection_columns:
        result = excute_detection_sql(
            "ALTER TABLE data ADD COLUMN admin_review TINYINT NULL DEFAULT NULL "
            "COMMENT '管理员复核：1=正确 -1=误判' AFTER feedback",
            fetch=False,
        )
        if result is None:
            ok = False
            messages.append("data.admin_review: failed")
        else:
            messages.append("data.admin_review: ready")
    admin_indexes = excute_sql("SHOW INDEX FROM admin_accounts") or []
    if admin_indexes and "idx_admin_accounts_role_status" not in {
        row.get("Key_name") for row in admin_indexes
    }:
        result = excute_sql(
            "CREATE INDEX idx_admin_accounts_role_status ON admin_accounts (role, status)",
            fetch=False,
        )
        if result is None:
            ok = False
            messages.append("admin_accounts.idx_admin_accounts_role_status: failed")
        else:
            messages.append("admin_accounts.idx_admin_accounts_role_status: ready")
    index_specs = {
        "data": {
            "idx_data_createtime": "createtime",
            "idx_data_phone": "phone",
            "idx_data_aigc": "aigc",
            "idx_data_feedback": "feedback",
        },
        "video_data": {
            "idx_video_data_createtime": "createtime",
            "idx_video_data_phone": "phone",
        },
    }
    for table, indexes in index_specs.items():
        existing_rows = excute_detection_sql(f"SHOW INDEX FROM `{table}`")
        if existing_rows is None:
            messages.append(f"{table} indexes: skipped")
            continue
        existing = {row.get("Key_name") for row in existing_rows}
        for name, column in indexes.items():
            if name in existing:
                continue
            result = excute_detection_sql(
                f"CREATE INDEX `{name}` ON `{table}` (`{column}`)",
                fetch=False,
            )
            messages.append(f"{table}.{name}: {'ready' if result is not None else 'failed'}")
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


def _login_attempt_buckets(identity):
    identity_hash, ip_hash = _login_attempt_keys(identity)
    wildcard = _security_hash("*")
    return (
        (identity_hash, ip_hash, ADMIN_LOGIN_MAX_ATTEMPTS),
        (identity_hash, wildcard, ADMIN_LOGIN_MAX_ATTEMPTS),
        (wildcard, ip_hash, ADMIN_LOGIN_IP_MAX_ATTEMPTS),
    )


def _find_admin_account(identity):
    if not identity or not _ensure_admin_account_table():
        return None
    rows = excute_sql(
        """
        SELECT id, username, phone, password_hash, role, status, session_version,
               created_at, last_login_at, last_login_ip
        FROM admin_accounts
        WHERE username = %s OR phone = %s
        LIMIT 1
        """,
        (identity, identity),
    ) or []
    return rows[0] if rows else None


def _find_admin_account_by_id(admin_id):
    if not admin_id or not _ensure_admin_account_table():
        return None
    rows = excute_sql(
        """
        SELECT id, username, phone, password_hash, role, status, session_version,
               created_at, last_login_at, last_login_ip
        FROM admin_accounts
        WHERE id = %s
        LIMIT 1
        """,
        (admin_id,),
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
    legacy_bootstrap = _is_legacy_admin(_current_user()) and _admin_account_count() == 0
    return bool((admin_user and _has_admin_permission(admin_user, "admin.manage")) or legacy_bootstrap)


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


def _admin_session_payload(account, issued_at=None):
    return {
        "Userid": f"admin:{account.get('id')}",
        "adminId": account.get("id"),
        "username": account.get("username") or "",
        "phone": account.get("phone") or "",
        "role": account.get("role") or "admin",
        "authType": "admin_account",
        "sessionVersion": int(account.get("session_version") or 1),
        "issuedAt": int(issued_at or time.time()),
    }


def _refresh_admin_session(user):
    """Refresh the signed cookie snapshot from the authoritative account row."""
    if not isinstance(user, dict) or user.get("authType") != "admin_account":
        return None
    try:
        issued_at = int(user.get("issuedAt") or 0)
        session_version = int(user.get("sessionVersion") or 0)
        admin_id = int(user.get("adminId") or 0)
    except (TypeError, ValueError):
        return None
    now = int(time.time())
    if issued_at <= 0 or now - issued_at > max(300, ADMIN_SESSION_MAX_AGE_SECONDS) or issued_at > now + 60:
        return None
    account = _find_admin_account_by_id(admin_id)
    if not account or account.get("status") != "active":
        return None
    if int(account.get("session_version") or 1) != session_version:
        return None
    return _admin_session_payload(account, issued_at=issued_at)


def _update_admin_login(account):
    excute_sql(
        "UPDATE admin_accounts SET last_login_at = NOW(), last_login_ip = %s WHERE id = %s",
        (_client_ip()[:64], account.get("id")),
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
        locks = []
        for identity_hash, ip_hash, _limit in _login_attempt_buckets(identity):
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
                locks.append(int(rows[0].get("locked_until_epoch") or 0))
        if locks:
            return max(0, max(locks) - int(time.time()))
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
        now = int(time.time())
        conn = None
        try:
            conn = get_db_connection()
            conn.begin()
            with conn.cursor() as cursor:
                for identity_hash, ip_hash, limit in _login_attempt_buckets(identity):
                    cursor.execute(
                        """
                        SELECT failure_count, locked_until_epoch,
                               UNIX_TIMESTAMP(last_failed_at) AS last_failed_epoch
                        FROM admin_login_attempts
                        WHERE identity_hash = %s AND ip_hash = %s
                        LIMIT 1 FOR UPDATE
                        """,
                        (identity_hash, ip_hash),
                    )
                    row = cursor.fetchone()
                    if row:
                        previous_lock = int(row.get("locked_until_epoch") or 0)
                        previous_count = int(row.get("failure_count") or 0)
                        last_failed = int(row.get("last_failed_epoch") or 0)
                        expired = (
                            (previous_lock and previous_lock <= now)
                            or not last_failed
                            or now - last_failed > ADMIN_LOGIN_WINDOW_SECONDS
                        )
                        count = 1 if expired else previous_count + 1
                    else:
                        count = 1
                    locked_until = now + ADMIN_LOGIN_LOCK_SECONDS if count >= limit else 0
                    cursor.execute(
                        """
                        INSERT INTO admin_login_attempts
                            (identity_hash, ip_hash, failure_count, locked_until_epoch, last_failed_at)
                        VALUES (%s, %s, %s, %s, NOW())
                        ON DUPLICATE KEY UPDATE
                            failure_count = VALUES(failure_count),
                            locked_until_epoch = VALUES(locked_until_epoch),
                            last_failed_at = NOW()
                        """,
                        (identity_hash, ip_hash, count, locked_until),
                    )
            conn.commit()
            return
        except Exception as exc:
            print(f"[ADMIN LOGIN RATE LIMIT ERROR] {exc}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()
    _record_session_login_failure()


def _clear_admin_login_failures(identity=None):
    if identity and _login_attempt_table_ready():
        identity_hash, ip_hash = _login_attempt_keys(identity)
        wildcard = _security_hash("*")
        excute_sql(
            "DELETE FROM admin_login_attempts WHERE identity_hash = %s AND ip_hash IN (%s, %s)",
            (identity_hash, ip_hash, wildcard),
            fetch=False,
        )
    session.pop(ADMIN_LOGIN_ATTEMPTS_KEY, None)


def _safe_admin_next(value):
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return "/admin"
    return raw if raw in {"/admin", "/admin/testing", "/admin/screen"} else "/admin"


def _admin_auth_context(mode="login", error="", message="", next_path="/admin"):
    can_register = _admin_registration_allowed()
    return {
        "mode": mode if mode != "register" or can_register else "login",
        "error": error,
        "message": message,
        "csrf_token": _csrf_token(),
        "legacy_admin_allowed": bool(_is_legacy_admin(_current_user())),
        "can_register": can_register,
        "admin_account_count": _admin_account_count() if can_register else None,
        "role_options": ADMIN_ROLE_LABELS,
        "next_path": _safe_admin_next(next_path),
    }


def _clear_registered_user_cache():
    with _REGISTERED_USER_CACHE_LOCK:
        _REGISTERED_USER_CACHE.update({"expires": 0.0, "rows": None})


def _registered_user_rows():
    now = time.monotonic()
    with _REGISTERED_USER_CACHE_LOCK:
        if _REGISTERED_USER_CACHE["rows"] is not None and now < _REGISTERED_USER_CACHE["expires"]:
            return _REGISTERED_USER_CACHE["rows"]
        rows = excute_sql(
            """
            SELECT Userid, account_uuid, phone, username, created_at
            FROM user
            WHERE account_uuid IS NOT NULL AND account_uuid <> ''
            """
        )
        if rows is None:
            return None
        normalized_rows = {
            normalized: row
            for row in rows
            if (normalized := normalize_account_uuid(row.get("account_uuid")))
        }
        _REGISTERED_USER_CACHE.update({
            "expires": now + _REGISTERED_USER_CACHE_TTL_SECONDS,
            "rows": normalized_rows,
        })
        return normalized_rows


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


def _update_admin_account_atomic(admin_id, role, status, actor_admin_id=None):
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM admin_accounts
                WHERE role = 'super_admin' AND status = 'active'
                ORDER BY id
                FOR UPDATE
                """
            )
            active_super_ids = {int(row.get("id")) for row in (cursor.fetchall() or [])}
            cursor.execute(
                """
                SELECT id, username, phone, role, status, session_version,
                       created_at, last_login_at, last_login_ip
                FROM admin_accounts
                WHERE id = %s
                LIMIT 1
                FOR UPDATE
                """,
                (admin_id,),
            )
            before = cursor.fetchone()
            if not before:
                conn.rollback()
                return None, None, "not_found"
            effective_role = _normalize_admin_role(role if role is not None else before.get("role"))
            effective_status = str(status if status is not None else before.get("status") or "active").strip()
            if int(actor_admin_id or 0) == int(admin_id) and (
                effective_status != "active" or effective_role not in ("admin", "super_admin")
            ):
                conn.rollback()
                return before, None, "self_downgrade"
            removes_active_super = (
                int(admin_id) in active_super_ids
                and (effective_role != "super_admin" or effective_status != "active")
            )
            if removes_active_super and len(active_super_ids) <= 1:
                conn.rollback()
                return before, None, "last_super_admin"
            cursor.execute(
                """
                UPDATE admin_accounts
                SET role = %s, status = %s, session_version = session_version + 1
                WHERE id = %s
                """,
                (effective_role, effective_status, admin_id),
            )
            cursor.execute(
                """
                SELECT id, username, phone, role, status, session_version,
                       created_at, last_login_at, last_login_ip
                FROM admin_accounts
                WHERE id = %s
                LIMIT 1
                """,
                (admin_id,),
            )
            after = cursor.fetchone() or {
                **before,
                "role": effective_role,
                "status": effective_status,
                "session_version": int(before.get("session_version") or 1) + 1,
            }
        conn.commit()
        return before, after, ""
    except Exception as exc:
        print(f"[ADMIN ACCOUNT UPDATE ERROR] {exc}")
        if conn:
            conn.rollback()
        return None, None, "database_error"
    finally:
        if conn:
            conn.close()


def _detection_data_columns():
    rows = excute_detection_sql("SHOW COLUMNS FROM data") or []
    return {row.get("Field") for row in rows if isinstance(row, dict) and row.get("Field")}


_POSITIVE_FEEDBACK_VALUES = {"1", "满意", "right", "helpful", "positive", "correct"}
_NEGATIVE_FEEDBACK_VALUES = {"-1", "不满意", "wrong", "unhelpful", "negative", "incorrect"}


def _normalize_detection_feedback(value):
    text = str(value or "").strip().lower()
    if text in _POSITIVE_FEEDBACK_VALUES:
        return 1
    if text in _NEGATIVE_FEEDBACK_VALUES:
        return -1
    return None


def _detection_feedback_label(value):
    normalized = _normalize_detection_feedback(value)
    if normalized == 1:
        return "有帮助"
    if normalized == -1:
        return "没帮助"
    return "未评价"


def _admin_review_label(value):
    normalized = _normalize_detection_feedback(value)
    if normalized == 1:
        return "判定正确"
    if normalized == -1:
        return "判定误判"
    return "未复核"


def _detection_feedback_counts():
    counts = {"positive": 0, "negative": 0, "unrated": 0, "unknown": 0}
    if "feedback" not in _detection_data_columns():
        counts["unrated"] = _scalar(
            f"SELECT COUNT(*) AS count FROM data WHERE {INTERNAL_TEST_OWNER_SQL}",
            detection=True,
        )
        return counts
    rows = excute_detection_sql(
        f"""
        SELECT feedback, COUNT(*) AS count
        FROM data
        WHERE {INTERNAL_TEST_OWNER_SQL}
        GROUP BY feedback
        """
    ) or []
    for row in rows:
        raw = row.get("feedback")
        count = int(row.get("count") or 0)
        normalized = _normalize_detection_feedback(raw)
        if normalized == 1:
            counts["positive"] += count
        elif normalized == -1:
            counts["negative"] += count
        elif raw in (None, "", 0, "0"):
            counts["unrated"] += count
        else:
            counts["unknown"] += count
    return counts


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
        "admin_review" if "admin_review" in columns else "NULL AS admin_review",
    ]
    return ", ".join(select_columns)


def _screen_detection_select_clause():
    columns = _detection_data_columns()
    select_columns = [
        "itemid",
        "createtime",
        "fake",
        "aigc",
        "clarity",
        "feedback" if "feedback" in columns else "NULL AS feedback",
        "admin_review" if "admin_review" in columns else "NULL AS admin_review",
    ]
    return ", ".join(select_columns)


def _limit_arg(default=50, maximum=200):
    try:
        return min(max(int(request.args.get("limit", str(default)) or default), 1), maximum)
    except ValueError:
        return default


def _cursor_arg():
    raw = str(request.args.get("cursor") or "").strip()
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def _page_payload(items, limit, id_field="id"):
    has_more = len(items) > limit
    visible = items[:limit]
    next_cursor = visible[-1].get(id_field) if has_more and visible else None
    return visible, {"hasMore": has_more, "nextCursor": str(next_cursor) if next_cursor is not None else None}


def _search_term():
    return str(request.args.get("q") or "").strip()


def _v2_conversation_db_path():
    configured = str(os.environ.get("REALGUARD_V2_DB_PATH") or "").strip()
    return Path(configured or "/opt/jianzhen-v2/data/jianzhen-v2.sqlite3").expanduser().resolve()


def _v2_conversation_connection():
    database = _v2_conversation_db_path()
    if not database.is_file():
        raise FileNotFoundError(database)
    encoded = quote(database.as_posix(), safe="/")
    connection = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=3000")
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_qa_turns'"
    ).fetchone()
    if not table:
        connection.close()
        raise sqlite3.OperationalError("report_qa_turns table is unavailable")
    return connection


def _conversation_time(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(_admin_timezone()).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return format_createtime(text)


def _conversation_json_list(value, limit):
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = []
    return [str(item) for item in parsed if str(item).strip()][:limit] if isinstance(parsed, list) else []


def _conversation_json_object(value):
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    sources = []
    for item in parsed.get("sources") or []:
        if not isinstance(item, dict):
            continue
        raw_url = str(item.get("url") or "").strip()
        try:
            url = urlparse(raw_url)
            hostname = url.hostname
        except ValueError:
            continue
        if (
            url.scheme not in {"http", "https"}
            or not hostname
            or url.username
            or url.password
        ):
            continue
        sources.append({
            "index": len(sources) + 1,
            "title": str(item.get("title") or "公开来源")[:240],
            "url": raw_url[:2000],
            "siteName": str(item.get("siteName") or hostname)[:100],
            "quality": str(item.get("quality") or "other")[:20],
            "matchLevel": (
                str(item.get("matchLevel") or "weak")[:16]
                if str(item.get("matchLevel") or "weak") in {"direct", "context", "weak"}
                else "weak"
            ),
            "contentStatus": str(item.get("contentStatus") or "")[:24],
            "evidenceRole": str(item.get("evidenceRole") or "irrelevant")[:32],
            "evidenceQuote": str(item.get("evidenceQuote") or "")[:360],
            "evidenceReason": str(item.get("evidenceReason") or "")[:220],
            "evidenceBasis": (
                item.get("evidenceBasis")
                if item.get("evidenceBasis") in {"page", "platform_metadata"}
                else "none"
            ),
        })
        if len(sources) >= 10:
            break
    source_refs = []
    for value in parsed.get("sourceRefs") or []:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= len(sources) and number not in source_refs:
            source_refs.append(number)
    try:
        matched_source_count = max(0, int(parsed.get("matchedSourceCount") or 0))
    except (TypeError, ValueError):
        matched_source_count = 0
    try:
        direct_source_count = max(0, int(parsed.get("directSourceCount") or 0))
    except (TypeError, ValueError):
        direct_source_count = 0
    try:
        candidate_source_count = max(0, int(parsed.get("candidateSourceCount") or 0))
    except (TypeError, ValueError):
        candidate_source_count = 0
    try:
        verified_source_count = max(0, int(parsed.get("verifiedSourceCount") or 0))
    except (TypeError, ValueError):
        verified_source_count = 0
    try:
        background_source_count = max(0, int(parsed.get("backgroundSourceCount") or 0))
    except (TypeError, ValueError):
        background_source_count = 0
    return {
        "attempted": bool(parsed.get("attempted")),
        "used": bool(parsed.get("used") and sources),
        "status": str(parsed.get("status") or "not_requested")[:32],
        "claim": str(parsed.get("claim") or "")[:320],
        "strategy": str(parsed.get("strategy") or "")[:24],
        "candidateSourceCount": candidate_source_count,
        "verifiedSourceCount": verified_source_count,
        "matchedSourceCount": matched_source_count,
        "directSourceCount": direct_source_count,
        "backgroundSourceCount": background_source_count,
        "contentVerdict": str(parsed.get("contentVerdict") or "not_applicable")[:32],
        "sourceRefs": source_refs[:5],
        "sources": sources,
    }


def _conversation_optional_column(row, column, default=None):
    return row[column] if column in row.keys() else default


def _conversation_user_directory(rows):
    account_uuids = sorted({
        str(row["developer_account_uuid"] or "").strip()
        for row in rows
        if str(row["developer_account_uuid"] or "").strip()
    })
    user_ids = sorted({
        int(row["developer_user_id"])
        for row in rows
        if str(row["developer_user_id"] or "").isdigit()
    })
    filters = []
    params = []
    if account_uuids:
        filters.append(f"account_uuid IN ({', '.join(['%s'] * len(account_uuids))})")
        params.extend(account_uuids)
    if user_ids:
        filters.append(f"Userid IN ({', '.join(['%s'] * len(user_ids))})")
        params.extend(user_ids)
    if not filters:
        return {}
    users = excute_sql(
        f"""
        SELECT Userid, account_uuid, username, phone, openid
        FROM `user`
        WHERE {' OR '.join(filters)}
        """,
        tuple(params),
    ) or []
    directory = {}
    for user in users:
        account_uuid = str(user.get("account_uuid") or "").strip()
        user_id = str(user.get("Userid") or "").strip()
        if account_uuid:
            directory[f"account:{account_uuid}"] = user
        if user_id:
            directory[f"user:{user_id}"] = user
    return directory


def _conversation_user_for_row(row, directory):
    account_uuid = str(row["developer_account_uuid"] or "").strip()
    user_id = str(row["developer_user_id"] or "").strip()
    return directory.get(f"account:{account_uuid}") or directory.get(f"user:{user_id}") or {}


def _conversation_user_payload(row, directory, actor):
    user = _conversation_user_for_row(row, directory)
    include_pii = _has_admin_permission(actor, "user.read_pii")
    user_id = user.get("Userid") or row["developer_user_id"]
    username = str(user.get("username") or "").strip()
    phone = str(user.get("phone") or "").strip()
    return {
        "id": user_id,
        "username": username or (f"用户 #{user_id}" if user_id else "未识别用户"),
        "phone": phone if include_pii else _mask_phone(phone),
        "actorMode": row["actor_mode"],
    }


def _audit(actor, action, target, before=None, after=None, meta=None):
    try:
        return admin_state.append_audit(actor, action, target, before=before, after=after, meta=meta)
    except Exception as exc:
        print(f"[ADMIN AUDIT ERROR] {exc}")
        return None


def _csv_response(filename, headers, rows):
    def safe_cell(value):
        if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
            return f"'{value}"
        return value

    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow([safe_cell(value) for value in headers])
    writer.writerows([[safe_cell(value) for value in row] for row in rows])
    return Response(
        buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _mask_phone(value):
    text = str(value or "")
    if len(text) < 7:
        return "***" if text else ""
    return f"{text[:3]}****{text[-4:]}"


def _mask_identifier(value):
    text = str(value or "")
    if len(text) <= 8:
        return "***" if text else ""
    return f"{text[:4]}...{text[-4:]}"


def _api_key_owner_label(username, phone, include_pii=False):
    username = str(username or "").strip()
    phone = str(phone or "").strip()
    if include_pii:
        return username or phone
    if username:
        compact = re.sub(r"[\s()+.-]", "", username)
        if username == phone or (compact.isdigit() and 7 <= len(compact) <= 15):
            return _mask_phone(username)
        return _mask_identifier(username)
    return _mask_phone(phone)


def _redact_metadata(value):
    sensitive_tokens = ("gps", "latitude", "longitude", "serial", "owner", "artist", "copyright", "location")
    if isinstance(value, dict):
        return {
            key: _redact_metadata(item)
            for key, item in value.items()
            if not any(token in str(key).lower() for token in sensitive_tokens)
        }
    if isinstance(value, list):
        return [_redact_metadata(item) for item in value]
    return value


def _safe_model_payload(model):
    payload = dict(model)
    for field in ("endpoint", "healthUrl", "artifactPath", "externalDataPath", "notes", "headers", "env"):
        payload.pop(field, None)
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        payload["artifacts"] = {
            key: {"exists": bool(item.get("exists"))} if isinstance(item, dict) else item
            for key, item in artifacts.items()
        }
    return payload


def _safe_model_run_payload(run, include_topology=False, include_pii=False):
    if not isinstance(run, dict):
        return None
    model = run.get("model") if isinstance(run.get("model"), dict) else {}
    actor = run.get("actor") if isinstance(run.get("actor"), dict) else {}
    meta = run.get("meta") if isinstance(run.get("meta"), dict) else {}
    safe_model = {
        "id": model.get("id") or "",
        "name": model.get("name") or model.get("id") or "",
        "version": model.get("version") or "",
    }
    if include_topology:
        safe_model.update({
            "runtime": model.get("runtime") or "",
            "endpoint": model.get("endpoint") or "",
        })
    safe_actor = {"id": actor.get("id")}
    if include_pii:
        safe_actor.update({
            "username": actor.get("username") or "",
            "phone": actor.get("phone") or "",
        })
    safe_meta = {
        key: meta.get(key)
        for key in ("provider", "service", "latencyMs", "fallback")
        if key in meta
    }
    if include_topology and include_pii:
        safe_meta = dict(meta)
    return {
        "id": run.get("id") or "",
        "createdAt": run.get("createdAt") or "",
        "itemid": run.get("itemid"),
        "route": run.get("route") or "",
        "status": run.get("status") or "",
        "model": safe_model,
        "actor": safe_actor,
        "meta": safe_meta,
    }


def _model_run_for_admin(run, actor, pii_permission="detection.read_pii"):
    return _safe_model_run_payload(
        run,
        include_topology=_has_admin_permission(actor, "topology.view"),
        include_pii=_has_admin_permission(actor, pii_permission),
    )


def _resolve_public_webhook_addresses(host, port):
    try:
        resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return [], f"Webhook 域名解析失败：{exc}"
    addresses = []
    for item in resolved:
        address = str(item[4][0] or "").split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if not parsed.is_global:
            return [], "Webhook 域名解析到了内网、本机或保留地址"
        normalized = str(parsed)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        return [], "Webhook 域名没有可用的公网地址"
    return addresses, ""


def _validate_webhook_url(value, resolve=False):
    url = str(value or "").strip()
    if not url:
        return "", ""
    if any(ord(char) <= 32 or ord(char) == 127 for char in url):
        return "", "Webhook 地址包含非法控制字符"
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return "", "Webhook 必须使用不含账号信息的 HTTPS 地址"
    host = parsed.hostname
    try:
        port = parsed.port or 443
    except ValueError:
        return "", "Webhook 端口无效"
    try:
        literal = ipaddress.ip_address(host)
        if not literal.is_global:
            return "", "Webhook 不能指向内网、本机或保留地址"
    except ValueError:
        if resolve:
            _, error = _resolve_public_webhook_addresses(host, port)
            if error:
                return "", error
    return url, ""


def _post_webhook_payload(url, payload, address, timeout=5):
    parsed = urlparse(url)
    host = parsed.hostname.encode("idna").decode("ascii")
    port = parsed.port or 443
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    host_header = f"[{host}]" if ":" in host else host
    if port != 443:
        host_header = f"{host_header}:{port}"
    headers = (
        f"POST {target} HTTP/1.1\r\n"
        f"Host: {host_header}\r\n"
        "User-Agent: RealGuard-Alerting/1.0\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    tls_context = ssl.create_default_context()
    with socket.create_connection((address, port), timeout=timeout) as raw_socket:
        with tls_context.wrap_socket(raw_socket, server_hostname=host) as tls_socket:
            tls_socket.settimeout(timeout)
            tls_socket.sendall(headers + body)
            response = HTTPResponse(tls_socket)
            response.begin()
            response_text = response.read(161).decode("utf-8", errors="replace")
            return response.status, response_text


def _deliver_alert_claim(claim, webhook_url):
    url, error = _validate_webhook_url(webhook_url)
    if error:
        return admin_state.record_alert_delivery(claim, False, error=error, attempts=1)
    parsed = urlparse(url)
    addresses, error = _resolve_public_webhook_addresses(parsed.hostname, parsed.port or 443)
    if error:
        return admin_state.record_alert_delivery(claim, False, error=error, attempts=1)
    payload = {
        "source": "慧鉴AI",
        "eventId": claim.get("eventId"),
        "kind": claim.get("kind"),
        "level": claim.get("level"),
        "title": claim.get("title"),
        "message": claim.get("message"),
        "occurredAt": claim.get("createdAt"),
    }
    last_error = ""
    status_code = None
    for attempt in range(1, 4):
        try:
            address = addresses[(attempt - 1) % len(addresses)]
            status_code, response_text = _post_webhook_payload(url, payload, address, timeout=5)
            if 200 <= status_code < 300:
                return admin_state.record_alert_delivery(claim, True, status_code=status_code, attempts=attempt)
            last_error = f"HTTP {status_code}: {response_text[:160]}"
        except Exception as exc:
            last_error = str(exc)
        if attempt < 3:
            time.sleep(0.5 * attempt)
    return admin_state.record_alert_delivery(
        claim,
        False,
        status_code=status_code,
        error=last_error,
        attempts=3,
    )


def _parse_status_timestamp(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _backup_assurance(now=None):
    path = os.environ.get(
        "REALGUARD_BACKUP_STATUS_FILE",
        "/opt/realguard-data/backup-status.json",
    )
    max_age_seconds = max(
        3600,
        int(os.environ.get("REALGUARD_BACKUP_MAX_AGE_SECONDS", "129600")),
    )
    payload = {}
    read_error = ""
    try:
        file_stat = os.lstat(path)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise ValueError("backup status must be a regular single-link file")
        if file_stat.st_size > BACKUP_STATUS_MAX_BYTES:
            raise ValueError("backup status exceeds size limit")
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
            raise ValueError("unsupported backup status schema")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        payload = {}
        read_error = str(exc)

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    def age_seconds(value):
        parsed = _parse_status_timestamp(value)
        if parsed is None:
            return None
        return max(0, int((current - parsed).total_seconds()))

    state = str(payload.get("state") or "missing")
    last_success_at = str(payload.get("lastSuccessAt") or "")
    last_offsite_success_at = str(payload.get("lastOffsiteSuccessAt") or "")
    local_age = age_seconds(last_success_at)
    offsite_age = age_seconds(last_offsite_success_at)
    offsite_configured = payload.get("offsiteConfigured") is True
    offsite_verified = payload.get("offsiteVerified") is True
    return {
        "state": state,
        "updatedAt": str(payload.get("updatedAt") or ""),
        "snapshot": str(payload.get("snapshot") or ""),
        "lastSuccessAt": last_success_at,
        "lastOffsiteSuccessAt": last_offsite_success_at,
        "localAgeSeconds": local_age,
        "offsiteAgeSeconds": offsite_age,
        "maxAgeSeconds": max_age_seconds,
        "localFresh": bool(local_age is not None and local_age <= max_age_seconds),
        "offsiteConfigured": offsite_configured,
        "offsiteVerified": offsite_verified,
        "offsiteFresh": bool(
            offsite_configured
            and offsite_verified
            and offsite_age is not None
            and offsite_age <= max_age_seconds
        ),
        "readError": read_error,
    }


def _read_assurance_status(path, schema_version=1):
    try:
        file_stat = os.lstat(path)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise ValueError("status must be a regular single-link file")
        if file_stat.st_size > ASSURANCE_STATUS_MAX_BYTES:
            raise ValueError("status exceeds size limit")
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or payload.get("schemaVersion") != schema_version:
            raise ValueError("unsupported status schema")
        return payload, ""
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {}, str(exc)


def write_alert_worker_heartbeat(state="running", error=""):
    path = os.environ.get(
        "REALGUARD_ALERT_HEARTBEAT_FILE",
        "/opt/realguard-data/alert-worker-heartbeat.json",
    )
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o755, exist_ok=True)
    payload = {
        "schemaVersion": 1,
        "state": str(state or "running"),
        "updatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "pid": os.getpid(),
        "lastError": str(error or "")[:1000],
    }
    temporary = os.path.join(parent, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
    return payload


def _alert_worker_assurance(now=None):
    path = os.environ.get(
        "REALGUARD_ALERT_HEARTBEAT_FILE",
        "/opt/realguard-data/alert-worker-heartbeat.json",
    )
    max_age = max(30, int(os.environ.get("REALGUARD_ALERT_HEARTBEAT_MAX_AGE_SECONDS", "120")))
    payload, read_error = _read_assurance_status(path)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    updated = _parse_status_timestamp(payload.get("updatedAt"))
    age = max(0, int((current.astimezone(timezone.utc) - updated).total_seconds())) if updated else None
    return {
        "state": str(payload.get("state") or "missing"),
        "updatedAt": str(payload.get("updatedAt") or ""),
        "ageSeconds": age,
        "maxAgeSeconds": max_age,
        "fresh": bool(updated and age <= max_age and payload.get("state") == "running"),
        "lastError": str(payload.get("lastError") or ""),
        "readError": read_error,
    }


def _restore_drill_assurance(now=None):
    path = os.environ.get(
        "REALGUARD_RESTORE_STATUS_FILE",
        "/opt/realguard-data/restore-drill-status.json",
    )
    max_age = max(86400, int(os.environ.get("REALGUARD_RESTORE_MAX_AGE_SECONDS", "691200")))
    payload, read_error = _read_assurance_status(path)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    last_success = _parse_status_timestamp(payload.get("lastSuccessAt"))
    age = max(0, int((current.astimezone(timezone.utc) - last_success).total_seconds())) if last_success else None
    return {
        "state": str(payload.get("state") or "missing"),
        "updatedAt": str(payload.get("updatedAt") or ""),
        "lastSuccessAt": str(payload.get("lastSuccessAt") or ""),
        "ageSeconds": age,
        "maxAgeSeconds": max_age,
        "fresh": bool(last_success and age <= max_age and payload.get("state") != "failed"),
        "snapshot": str(payload.get("snapshot") or ""),
        "lastError": str(payload.get("lastError") or ""),
        "readError": read_error,
    }


def _security_audit_assurance(now=None):
    path = os.environ.get(
        "REALGUARD_SECURITY_AUDIT_STATUS_FILE",
        "/opt/realguard-data/security-audit-status.json",
    )
    max_age = max(120, int(os.environ.get("REALGUARD_SECURITY_AUDIT_MAX_AGE_SECONDS", "600")))
    payload, read_error = _read_assurance_status(path)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    updated = _parse_status_timestamp(payload.get("updatedAt"))
    age = max(0, int((current.astimezone(timezone.utc) - updated).total_seconds())) if updated else None
    return {
        "state": str(payload.get("state") or "missing"),
        "updatedAt": str(payload.get("updatedAt") or ""),
        "eventCount": int(payload.get("eventCount") or 0),
        "ageSeconds": age,
        "maxAgeSeconds": max_age,
        "fresh": bool(updated and age <= max_age and payload.get("state") == "passed"),
        "lastError": str(payload.get("lastError") or ""),
        "readError": read_error,
    }


def _alert_conditions(
    registry, models, assurance, backup=None, alert_worker=None,
    restore_drill=None, security_audit=None,
):
    routing = registry.get("routing") or {}
    backup = backup or _backup_assurance()
    alert_worker = alert_worker or _alert_worker_assurance()
    restore_drill = restore_drill or _restore_drill_assurance()
    security_audit = security_audit or _security_audit_assurance()
    enabled_models = [model for model in models if model.get("enabled") is not False]
    artifact_missing = any(
        model.get("id") == "v1-onnx-mil" and (model.get("health") or {}).get("artifactReady") is False
        for model in models
    )
    probe_failed = any(not (model.get("health") or {}).get("serviceOk") for model in enabled_models)
    primary_id = str(routing.get("imagePrimary") or "")
    primary_model = next((model for model in enabled_models if str(model.get("id") or "") == primary_id), {})
    primary_health = primary_model.get("health") if isinstance(primary_model.get("health"), dict) else {}
    primary_telemetry = primary_health.get("telemetry") if isinstance(primary_health.get("telemetry"), dict) else {}
    return {
        "v1Offline": {
            "active": not bool(assurance.get("online")),
            "level": "critical",
            "title": "主鉴伪链路离线",
            "message": "主图像模型未达到可用状态，请登录管理后台查看模型健康详情。",
        },
        "artifactMissing": {
            "active": artifact_missing,
            "level": "critical",
            "title": "模型文件缺失",
            "message": "V1 ONNX 模型文件不完整，模型能力无法正常加载。",
        },
        "fallbackEnabled": {
            "active": bool(routing.get("fallbackEnabled")),
            "level": "warning",
            "title": "自动兜底已启用",
            "message": "主模型失败后会切换模型，请确认当前业务口径允许自动替换。",
        },
        "probeFailed": {
            "active": probe_failed,
            "level": "warning",
            "title": "模型健康探测失败",
            "message": "至少一个已启用模型的健康接口不可达或返回失败状态。",
        },
        "verdictNotReady": {
            "active": bool(primary_model) and primary_telemetry.get("verdictReady") is not True,
            "level": "warning",
            "title": "自动真假结论未授权",
            "message": "主模型运行时在线，但独立校准门禁未通过；系统当前仅允许人工复核结论。",
        },
        "backupRunFailed": {
            "active": backup.get("state") == "failed",
            "level": "critical",
            "title": "最近一次备份失败",
            "message": "最近一次生产备份未完成，请检查备份服务日志并执行恢复校验。",
        },
        "backupStale": {
            "active": not bool(backup.get("localFresh")),
            "level": "critical",
            "title": "生产备份已过期",
            "message": "没有发现时效范围内且完成校验的生产备份。",
        },
        "offsiteBackupMissing": {
            "active": not bool(backup.get("offsiteFresh")),
            "level": "critical",
            "title": "异地备份未验证",
            "message": "异地备份未配置、未通过一致性校验或已经超过允许时效。",
        },
        "alertWorkerStale": {
            "active": not bool(alert_worker.get("fresh")),
            "level": "critical",
            "title": "独立告警进程心跳异常",
            "message": "告警工作进程没有在允许时间内更新心跳，请检查 realguard-alert-worker.service。",
        },
        "restoreDrillStale": {
            "active": not bool(restore_drill.get("fresh")),
            "level": "critical",
            "title": "备份恢复演练未通过",
            "message": "没有发现时效范围内通过的隔离恢复演练，当前备份可恢复性无法证明。",
        },
        "securityAuditInvalid": {
            "active": not bool(security_audit.get("fresh")),
            "level": "critical",
            "title": "安全审计链验证失败",
            "message": "安全审计链完整性验证失败、状态过期或事件计数发生回退。",
        },
    }


def run_alert_cycle():
    config = admin_state.alerts()
    if not config.get("enabled") or not str(config.get("webhookUrl") or "").strip():
        return []
    registry = model_registry.load_registry()
    models = _models_payload_with_health(registry.get("models", []))
    assurance = _v1_assurance(registry=registry, models=models)
    conditions = _alert_conditions(registry, models, assurance, backup=_backup_assurance())
    deliveries = []
    rules = config.get("rules") or {}
    for event_id, condition in conditions.items():
        if not rules.get(event_id, True):
            admin_state.suppress_alert_event(event_id)
            continue
        active = bool(condition.get("active"))
        claim = admin_state.claim_alert_event(
            event_id,
            active,
            condition.get("title"),
            condition.get("message"),
            condition.get("level"),
        )
        if claim:
            deliveries.append(_deliver_alert_claim(claim, config.get("webhookUrl")))
    return deliveries


def run_alert_watchdog_cycle():
    """Deliver the alert-worker dead-man signal from a separate timer process."""
    config = admin_state.alerts()
    if not config.get("enabled") or not str(config.get("webhookUrl") or "").strip():
        return []
    assurance = _alert_worker_assurance()
    claim = admin_state.claim_alert_event(
        "alertWorkerStale",
        not bool(assurance.get("fresh")),
        "独立告警进程心跳异常",
        "告警工作进程没有在允许时间内更新心跳，请检查 realguard-alert-worker.service。",
        "critical",
    )
    return [_deliver_alert_claim(claim, config.get("webhookUrl"))] if claim else []


def ensure_alert_worker(app):
    global _ALERT_WORKER_THREAD
    if app.config.get("TESTING") or os.environ.get("REALGUARD_ALERT_WORKER_ENABLED", "1").lower() in ("0", "false", "off"):
        return
    with _ALERT_WORKER_LOCK:
        if _ALERT_WORKER_THREAD and _ALERT_WORKER_THREAD.is_alive():
            return

        def loop():
            interval = max(15, int(os.environ.get("REALGUARD_ALERT_INTERVAL_SECONDS", "30")))
            while True:
                try:
                    with app.app_context():
                        run_alert_cycle()
                except Exception as exc:
                    print(f"[ADMIN ALERT WORKER ERROR] {exc}")
                time.sleep(interval)

        _ALERT_WORKER_THREAD = threading.Thread(target=loop, name="realguard-alert-worker", daemon=True)
        _ALERT_WORKER_THREAD.start()


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
    endpoint_error = model_registry.validate_model_url(endpoint, allow_internal=True)
    if endpoint_error:
        return {"ok": False, "message": endpoint_error}
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
            resp = sess.post(
                endpoint,
                headers=headers,
                files=files,
                data=data,
                timeout=timeout,
                allow_redirects=False,
            )
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


def _v1_assurance(registry=None, models=None):
    registry = registry or model_registry.load_registry()
    routing = registry.get("routing", {})
    if models is None:
        models = _models_payload_with_health(registry.get("models", []))
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
        "backup": _backup_assurance(),
        "alertWorker": _alert_worker_assurance(),
        "restoreDrill": _restore_drill_assurance(),
        "securityAudit": _security_audit_assurance(),
    }


def _traffic_metrics_payload(traffic):
    homepage = traffic.get("homepage") if isinstance(traffic.get("homepage"), dict) else {}
    site = traffic.get("site") if isinstance(traffic.get("site"), dict) else {}
    cumulative = traffic.get("cumulative") if isinstance(traffic.get("cumulative"), dict) else {}
    cumulative_homepage = cumulative.get("homepage") if isinstance(cumulative.get("homepage"), dict) else {}
    cumulative_site = cumulative.get("site") if isinstance(cumulative.get("site"), dict) else {}
    ready = bool(traffic.get("ready"))
    cumulative_ready = bool(cumulative.get("ready"))
    payload = {
        "ready": ready,
        "windowHours": int(traffic.get("windowHours") or 24),
        "homepagePageViews": int(homepage.get("pageViews") or 0) if ready else None,
        "homepageUniqueVisitors": int(homepage.get("uniqueVisitors") or 0) if ready else None,
        "sitePageViews": int(site.get("pageViews", traffic.get("requests")) or 0) if ready else None,
        "siteUniqueVisitors": int(site.get("uniqueVisitors", traffic.get("uniqueVisitors")) or 0) if ready else None,
        "onlineVisitors": int(traffic.get("onlineVisitors") or 0) if ready else None,
        "onlineWindowMinutes": int(traffic.get("onlineWindowMinutes") or 5),
        "cumulativeReady": cumulative_ready,
        "cumulativeSince": str(cumulative.get("since") or "--") if cumulative_ready else None,
        "cumulativeHomepagePageViews": int(cumulative_homepage.get("pageViews") or 0) if cumulative_ready else None,
        "cumulativeHomepageUniqueVisitors": int(cumulative_homepage.get("uniqueVisitors") or 0) if cumulative_ready else None,
        "cumulativeSitePageViews": int(cumulative_site.get("pageViews", cumulative.get("requests")) or 0) if cumulative_ready else None,
        "cumulativeSiteUniqueVisitors": int(cumulative_site.get("uniqueVisitors", cumulative.get("uniqueVisitors")) or 0) if cumulative_ready else None,
    }
    source = traffic.get("source")
    tracking_status = traffic.get("trackingStatus")
    if isinstance(source, dict) and source:
        payload["source"] = source
    if tracking_status and tracking_status != "unknown":
        payload["trackingStatus"] = tracking_status
    return payload


def _cached_traffic_summary():
    now = time.time()
    payload = _TRAFFIC_SUMMARY_CACHE.get("payload")
    if payload and now < float(_TRAFFIC_SUMMARY_CACHE.get("expires") or 0):
        return payload
    payload = traffic_geo.traffic_summary()
    _TRAFFIC_SUMMARY_CACHE["payload"] = payload
    _TRAFFIC_SUMMARY_CACHE["expires"] = now + max(1, DASHBOARD_METRICS_CACHE_TTL_SECONDS)
    return payload


def _dashboard_metrics():
    today_start, today_end = _today_bounds()
    yesterday_start, yesterday_end = _day_bounds(-1)
    last7_start = _days_ago_start(7)
    today_user_count = _scalar(
        "SELECT COUNT(*) AS count FROM user WHERE created_at >= %s AND created_at < %s",
        (today_start, today_end),
    )
    today_image_count = _scalar(
        f"SELECT COUNT(*) AS count FROM data WHERE createtime >= %s AND createtime < %s AND {INTERNAL_TEST_OWNER_SQL}",
        (today_start, today_end),
        detection=True,
    )
    today_video_count = _scalar(
        "SELECT COUNT(*) AS count FROM video_data WHERE createtime >= %s AND createtime < %s",
        (today_start, today_end),
        detection=True,
    )
    yesterday_image_count = _scalar(
        f"SELECT COUNT(*) AS count FROM data WHERE createtime >= %s AND createtime < %s AND {INTERNAL_TEST_OWNER_SQL}",
        (yesterday_start, yesterday_end),
        detection=True,
    )
    yesterday_video_count = _scalar(
        "SELECT COUNT(*) AS count FROM video_data WHERE createtime >= %s AND createtime < %s",
        (yesterday_start, yesterday_end),
        detection=True,
    )
    last7_image_count = _scalar(
        f"SELECT COUNT(*) AS count FROM data WHERE createtime >= %s AND createtime < %s AND {INTERNAL_TEST_OWNER_SQL}",
        (last7_start, today_end),
        detection=True,
    )
    last7_video_count = _scalar(
        "SELECT COUNT(*) AS count FROM video_data WHERE createtime >= %s AND createtime < %s",
        (last7_start, today_end),
        detection=True,
    )
    last_image_at = _scalar(
        f"SELECT createtime AS latest FROM data WHERE {INTERNAL_TEST_OWNER_SQL} ORDER BY itemid DESC LIMIT 1",
        detection=True,
        default="",
    )
    last_video_at = _scalar("SELECT createtime AS latest FROM video_data ORDER BY itemid DESC LIMIT 1", detection=True, default="")
    traffic = _cached_traffic_summary()
    feedback_counts = _detection_feedback_counts()
    return {
        "users": {
            "total": _scalar("SELECT COUNT(*) AS count FROM user"),
            "today": today_user_count,
            "todayNew": today_user_count,
            "apiKeys": _scalar("SELECT COUNT(*) AS count FROM developer_api_keys WHERE status = 'active'"),
        },
        "detections": {
            "images": _scalar(
                f"SELECT COUNT(*) AS count FROM data WHERE {INTERNAL_TEST_OWNER_SQL}",
                detection=True,
            ),
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
            "feedbackPositive": feedback_counts["positive"],
            "feedbackNegative": feedback_counts["negative"],
            "feedbackUnrated": feedback_counts["unrated"],
            "feedbackUnknown": feedback_counts["unknown"],
        },
        "traffic": _traffic_metrics_payload(traffic),
        "todayWindow": {
            "start": today_start,
            "end": today_end,
            "timezone": os.environ.get("REALGUARD_ADMIN_TIMEZONE", "Asia/Shanghai"),
            "generatedAt": datetime.now(_admin_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
        },
    }


def _cached_dashboard_metrics():
    now = time.time()
    payload = _DASHBOARD_METRICS_CACHE.get("payload")
    if payload and now < float(_DASHBOARD_METRICS_CACHE.get("expires") or 0):
        return payload
    payload = _dashboard_metrics()
    _DASHBOARD_METRICS_CACHE["payload"] = payload
    _DASHBOARD_METRICS_CACHE["expires"] = now + max(1, DASHBOARD_METRICS_CACHE_TTL_SECONDS)
    return payload


def _clear_dashboard_metrics_cache():
    _DASHBOARD_METRICS_CACHE["expires"] = 0
    _DASHBOARD_METRICS_CACHE["payload"] = None
    _TRAFFIC_SUMMARY_CACHE["expires"] = 0
    _TRAFFIC_SUMMARY_CACHE["payload"] = None


def _hourly_detection_series(hours=24):
    start, buckets = _hours_window(hours)
    image_rows = excute_detection_sql(
        f"""
        SELECT DATE_FORMAT(createtime, '%%Y-%%m-%%d %%H:00:00') AS bucket, COUNT(*) AS count
        FROM data
        WHERE createtime >= %s AND {INTERNAL_TEST_OWNER_SQL}
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
        f"""
        SELECT itemid, aigc
        FROM data
        WHERE {INTERNAL_TEST_OWNER_SQL}
        ORDER BY itemid DESC
        LIMIT 1000
        """
    ) or []
    route_runs = admin_state.model_runs_by_itemids([row.get("itemid") for row in rows])
    counts = {}
    for row in rows:
        view = _authorized_detection_view(
            row,
            route_runs.get(str(row.get("itemid"))),
        )
        label = view["label"]
        counts[label] = counts.get(label, 0) + 1
    return [
        {"label": label, "count": count}
        for label, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8]
    ]


def _feedback_distribution():
    counts = _detection_feedback_counts()
    result = [
        {"label": "有帮助", "count": counts["positive"]},
        {"label": "没帮助", "count": counts["negative"]},
        {"label": "未评价", "count": counts["unrated"]},
    ]
    if counts["unknown"]:
        result.append({"label": "历史未知", "count": counts["unknown"]})
    return result


def _screen_model_run(run):
    if not isinstance(run, dict):
        return None
    model = run.get("model") if isinstance(run.get("model"), dict) else {}
    return {
        "route": run.get("route") or "",
        "status": run.get("status") or "",
        "model": {
            "id": model.get("id") or "",
            "name": model.get("name") or "",
            "runtime": model.get("runtime") or "",
            "version": model.get("version") or "",
        },
    }


def _authorized_detection_view(row, model_run):
    """Normalize every admin surface to the signed decision authorization."""
    try:
        visible = evidence_manifest._structured_visible_watermark(model_run)
        decision = evidence_manifest._decision_authorization(model_run, visible)
    except Exception:
        decision = {"status": "review_only", "authority": "none"}
    authorized = decision.get("status") == "verdict"
    return {
        "label": binary_final_label(row.get("aigc"), row.get("fake")),
        "probability": row.get("fake") if authorized else None,
        "detectorProbability": row.get("detector_probability") if authorized else None,
        "confidence": row.get("clarity") if authorized else "低",
        "decisionStatus": "verdict" if authorized else "review_only",
        "decisionAuthority": decision.get("authority") if authorized else "none",
    }


def _recent_detection_items(limit=12):
    select_clause = _screen_detection_select_clause()
    rows = excute_detection_sql(
        f"""
        SELECT {select_clause}
        FROM data
        WHERE {INTERNAL_TEST_OWNER_SQL}
        ORDER BY itemid DESC
        LIMIT %s
        """,
        (limit,),
    ) or []
    route_runs = admin_state.model_runs_by_itemids([row.get("itemid") for row in rows])
    items = []
    for row in rows:
        run = route_runs.get(str(row.get("itemid")))
        view = _authorized_detection_view(row, run)
        items.append({
            "id": row.get("itemid"),
            "createdAt": format_createtime(row.get("createtime")),
            "feedback": _normalize_detection_feedback(row.get("feedback")),
            "feedbackLabel": _detection_feedback_label(row.get("feedback")),
            "adminReview": _normalize_detection_feedback(row.get("admin_review")),
            "adminReviewLabel": _admin_review_label(row.get("admin_review")),
            "modelRoute": _screen_model_run(run),
            **view,
        })
    return items


def _route_distribution(limit=6):
    counts = {}
    for item in admin_state.list_model_runs(1000):
        model = (item.get("model") or {}).get("id") or "unknown"
        counts[model] = counts.get(model, 0) + 1
    return [
        {"model": model, "count": count}
        for model, count in sorted(counts.items(), key=lambda pair: pair[1], reverse=True)[:limit]
    ]


def _read_proc_text(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _host_telemetry():
    cpu_count = max(1, int(os.cpu_count() or 1))
    try:
        load_1, load_5, load_15 = os.getloadavg()
    except (AttributeError, OSError):
        load_1 = load_5 = load_15 = None

    meminfo = {}
    for line in _read_proc_text("/proc/meminfo").splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        try:
            meminfo[key] = int(value.strip().split()[0]) * 1024
        except (IndexError, TypeError, ValueError):
            continue
    memory_total = int(meminfo.get("MemTotal") or 0)
    memory_available = int(meminfo.get("MemAvailable") or meminfo.get("MemFree") or 0)
    memory_used = max(0, memory_total - memory_available)
    memory_percent = round(memory_used * 100 / memory_total, 1) if memory_total else None

    try:
        disk = shutil.disk_usage("/")
        disk_total = int(disk.total)
        disk_free = int(disk.free)
        disk_used = int(disk.used)
        disk_percent = round(disk_used * 100 / disk_total, 1) if disk_total else None
    except OSError:
        disk_total = disk_free = disk_used = 0
        disk_percent = None

    uptime_text = _read_proc_text("/proc/uptime").split()
    try:
        uptime_seconds = int(float(uptime_text[0])) if uptime_text else None
    except (TypeError, ValueError):
        uptime_seconds = None
    load_percent = round(load_1 * 100 / cpu_count, 1) if load_1 is not None else None

    status = "unknown"
    if any(value is not None for value in (load_percent, memory_percent, disk_percent)):
        if (
            (load_percent is not None and load_percent >= 150)
            or (memory_percent is not None and memory_percent >= 95)
            or (disk_percent is not None and disk_percent >= 95)
        ):
            status = "critical"
        elif (
            (load_percent is not None and load_percent >= 100)
            or (memory_percent is not None and memory_percent >= 85)
            or (disk_percent is not None and disk_percent >= 85)
        ):
            status = "warning"
        else:
            status = "healthy"
    return {
        "status": status,
        "cpu": {
            "cores": cpu_count,
            "load1": round(load_1, 2) if load_1 is not None else None,
            "load5": round(load_5, 2) if load_5 is not None else None,
            "load15": round(load_15, 2) if load_15 is not None else None,
            "loadPercent": load_percent,
        },
        "memory": {
            "totalBytes": memory_total,
            "usedBytes": memory_used,
            "availableBytes": memory_available,
            "usedPercent": memory_percent,
        },
        "disk": {
            "totalBytes": disk_total,
            "usedBytes": disk_used,
            "freeBytes": disk_free,
            "usedPercent": disk_percent,
        },
        "uptimeSeconds": uptime_seconds,
        "processUptimeSeconds": max(0, int(time.monotonic() - _PROCESS_STARTED_MONOTONIC)),
    }


def _screen_model_payload(model):
    health = model.get("health") if isinstance(model.get("health"), dict) else {}
    telemetry = health.get("telemetry") if isinstance(health.get("telemetry"), dict) else {}
    if health.get("ok") and telemetry.get("verdictReady") is False:
        message = "运行正常，自动结论未授权"
    elif health.get("ok"):
        message = "运行正常"
    elif health.get("serviceOk"):
        message = "服务在线，模型能力未就绪"
    else:
        message = "服务不可达"
    return {
        "id": model.get("id") or "",
        "name": model.get("name") or model.get("id") or "未命名模型",
        "runtime": model.get("runtime") or "",
        "enabled": model.get("enabled") is not False,
        "health": {
            "ok": bool(health.get("ok")),
            "serviceOk": bool(health.get("serviceOk")),
            "artifactReady": health.get("artifactReady"),
            "latencyMs": health.get("latencyMs"),
            "message": message,
            "telemetry": health.get("telemetry") if isinstance(health.get("telemetry"), dict) else {},
        },
    }


def _model_run_performance(limit=1000):
    runs = admin_state.list_model_runs(limit)
    groups = {}
    all_latencies = []
    success = 0
    for run in runs:
        model = run.get("model") if isinstance(run.get("model"), dict) else {}
        model_id = str(model.get("id") or "unknown")
        group = groups.setdefault(model_id, {"modelId": model_id, "count": 0, "success": 0, "latencies": []})
        group["count"] += 1
        if str(run.get("status") or "").lower() in ("success", "ok", "completed"):
            group["success"] += 1
            success += 1
        meta = run.get("meta") if isinstance(run.get("meta"), dict) else {}
        try:
            latency = float(meta.get("latencyMs"))
        except (TypeError, ValueError):
            latency = None
        if latency is not None and latency >= 0:
            group["latencies"].append(latency)
            all_latencies.append(latency)

    def percentile(values, fraction):
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
        return round(ordered[index], 1)

    models = []
    for group in groups.values():
        count = group.pop("count")
        successful = group.pop("success")
        latencies = group.pop("latencies")
        models.append({
            **group,
            "count": count,
            "successRate": round(successful * 100 / count, 1) if count else None,
            "p50LatencyMs": percentile(latencies, 0.5),
            "p95LatencyMs": percentile(latencies, 0.95),
        })
    models.sort(key=lambda item: item.get("count") or 0, reverse=True)
    total = len(runs)
    return {
        "sampleSize": total,
        "successRate": round(success * 100 / total, 1) if total else None,
        "p50LatencyMs": percentile(all_latencies, 0.5),
        "p95LatencyMs": percentile(all_latencies, 0.95),
        "models": models[:12],
    }


def _service_summary(models):
    enabled = [model for model in models if model.get("enabled") is not False]
    online = sum(1 for model in enabled if (model.get("health") or {}).get("ok"))
    degraded = sum(
        1
        for model in enabled
        if not (model.get("health") or {}).get("ok") and (model.get("health") or {}).get("serviceOk")
    )
    return {
        "total": len(enabled),
        "online": online,
        "degraded": degraded,
        "offline": max(0, len(enabled) - online - degraded),
    }


def _screen_routing_payload(routing):
    return {
        "imagePrimary": routing.get("imagePrimary") or "",
        "imageFallback": routing.get("imageFallback") or "",
        "fallbackEnabled": bool(routing.get("fallbackEnabled")),
    }


def _screen_algorithm_server_payload(models, routing):
    primary_id = str((routing or {}).get("imagePrimary") or "")
    primary = next((model for model in models if str(model.get("id") or "") == primary_id), None)
    if primary is None:
        primary = next((model for model in models if model.get("enabled") is not False), None)
    if primary is None:
        return {
            "status": "unknown",
            "overallStatus": "unknown",
            "transportStatus": "unavailable",
            "inferenceStatus": "unavailable",
            "verdictStatus": "unavailable",
            "reasons": ["未配置主模型"],
            "serviceReady": False,
            "modelReady": False,
            "modelId": "",
            "modelName": "未配置主模型",
            "inferenceMode": "",
            "provider": "",
            "cudaDeviceId": None,
            "latencyMs": None,
            "queueDepth": None,
        }

    health = primary.get("health") if isinstance(primary.get("health"), dict) else {}
    telemetry = health.get("telemetry") if isinstance(health.get("telemetry"), dict) else {}
    service_ready = bool(health.get("serviceOk"))
    remote_ready = telemetry.get("remoteReady")
    provider = str(telemetry.get("activeProvider") or "")
    remote_model_ready = remote_ready is True and provider == "CUDAExecutionProvider"
    model_ready = bool(health.get("ok")) or remote_model_ready
    verdict_ready = telemetry.get("verdictReady") is True
    accelerator_ready = remote_ready is not False and (
        not provider or provider == "CUDAExecutionProvider"
    )
    if not service_ready:
        status = "offline"
    elif model_ready and accelerator_ready and verdict_ready:
        status = "healthy"
    else:
        status = "degraded"

    transport_status = "online" if service_ready else "offline"
    inference_status = "ready" if model_ready and accelerator_ready else ("degraded" if service_ready else "unavailable")
    verdict_status = "ready" if verdict_ready else ("review_only" if service_ready else "unavailable")
    reasons = list(telemetry.get("decisionGateReasons") or [])
    if not service_ready:
        reasons.append(health.get("message") or "模型服务不可达")
    elif not model_ready:
        reasons.append("主模型尚未达到可推理状态")
    if not accelerator_ready:
        reasons.append("GPU 推理状态未就绪")
    if not verdict_ready:
        reasons.append("自动结论门禁尚未通过")
    reasons = list(dict.fromkeys(str(reason) for reason in reasons if reason))

    latency = telemetry.get("remoteLatencyMs")
    if latency is None:
        latency = health.get("latencyMs")
    return {
        "status": status,
        "overallStatus": status,
        "transportStatus": transport_status,
        "inferenceStatus": inference_status,
        "verdictStatus": verdict_status,
        "reasons": reasons,
        "serviceReady": service_ready,
        "modelReady": model_ready,
        "verdictReady": verdict_ready,
        "decisionMode": telemetry.get("decisionMode") or "review_only",
        "decisionGateReasons": list(telemetry.get("decisionGateReasons") or []),
        "modelId": primary.get("id") or "",
        "modelName": primary.get("name") or primary.get("id") or "主鉴伪模型",
        "inferenceMode": telemetry.get("inferenceMode") or "",
        "provider": provider,
        "cudaDeviceId": telemetry.get("cudaDeviceId"),
        "latencyMs": latency,
        "queueDepth": telemetry.get("queueDepth"),
    }


def _screen_assurance_payload(assurance):
    return {
        "online": bool(assurance.get("online")),
        "blockerCount": len(assurance.get("blockers") or []),
        "recommendationCount": len(assurance.get("recommendations") or []),
    }


def _big_screen_anomalies(metrics, models, assurance, host, algorithm_server=None):
    anomalies = []
    algorithm_server = algorithm_server or {}
    algorithm_status = algorithm_server.get("overallStatus") or algorithm_server.get("status")
    if algorithm_status != "healthy":
        anomalies.append({
            "level": "critical" if algorithm_status in ("offline", "unknown", None) else "warning",
            "title": "主检测链路未就绪",
            "message": "；".join(algorithm_server.get("reasons") or []) or "主模型或依赖服务未达到可用状态，请登录管理后台查看阻断详情。",
        })
    primary_id = str(algorithm_server.get("modelId") or "")
    for model in models:
        if primary_id and str(model.get("id") or "") == primary_id:
            continue
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
    if host.get("status") in ("warning", "critical"):
        cpu = (host.get("cpu") or {}).get("loadPercent")
        memory = (host.get("memory") or {}).get("usedPercent")
        disk = (host.get("disk") or {}).get("usedPercent")
        anomalies.append({
            "level": host.get("status"),
            "title": "主机资源压力偏高",
            "message": f"CPU 负载 {cpu if cpu is not None else '--'}%，内存 {memory if memory is not None else '--'}%，磁盘 {disk if disk is not None else '--'}%。",
        })
    return anomalies[:10]


def _big_screen_payload():
    registry = model_registry.load_registry()
    raw_models = _models_payload_with_health(registry.get("models", []))
    models = [_screen_model_payload(model) for model in raw_models]
    metrics = dict(_cached_dashboard_metrics())
    traffic = _cached_traffic_summary()
    metrics["traffic"] = _traffic_metrics_payload(traffic)
    assurance_detail = _v1_assurance(registry=registry, models=raw_models)
    assurance = _screen_assurance_payload(assurance_detail)
    host = _host_telemetry()
    routing = _screen_routing_payload(registry.get("routing", {}))
    algorithm_server = _screen_algorithm_server_payload(models, routing)
    now = datetime.now(_admin_timezone())
    return {
        "generatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "generatedAtIso": now.isoformat(),
        "metrics": metrics,
        "routing": routing,
        "models": models,
        "services": _service_summary(models),
        "host": host,
        "algorithmServer": algorithm_server,
        "series": _hourly_detection_series(24),
        "labels": _label_distribution(),
        "feedback": _feedback_distribution(),
        "routes": _route_distribution(),
        "performance": _model_run_performance(1000),
        "traffic": traffic,
        "recent": _recent_detection_items(12),
        "assurance": assurance,
        "anomalies": _big_screen_anomalies(metrics, models, assurance_detail, host, algorithm_server),
        "privacy": {"piiIncluded": False, "internalEndpointsIncluded": False, "rawIpsIncluded": False},
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


def _models_payload_with_health(models, force=False):
    models = list(models or [])
    health_by_id = model_registry.check_models_health(models, force=force)
    payloads = []
    for model in models:
        payload = _model_payload(model)
        payload["health"] = health_by_id.get(str(model.get("id") or ""), {
            "ok": False,
            "serviceOk": False,
            "message": "health result unavailable",
        })
        payloads.append(payload)
    return payloads


def _service_health():
    registry = model_registry.load_registry()
    models = _models_payload_with_health(registry.get("models", []))
    screen_models = [_screen_model_payload(model) for model in models]
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
        "host": _host_telemetry(),
        "serviceSummary": _service_summary(screen_models),
        "performance": _model_run_performance(1000),
        "environment": {
            "detectorBackendUrl": os.environ.get("REALGUARD_DETECTION_BACKEND_URL", ""),
            "adminConfigured": bool(_admin_phone_set() or _admin_user_id_set()),
            # Kept for old dashboard clients; the legacy bypass is intentionally
            # disabled and is no longer a meaningful runtime capability.
            "adminAllowAnyLogin": False,
            "adminAccounts": _admin_account_count(),
        },
    }


def _render_admin_console(initial_route="dashboard"):
    active_user, permission_error = _admin_required("view")
    if permission_error:
        if permission_error[1] == 401:
            response = permission_error[0]
            payload = response.get_json(silent=True) if hasattr(response, "get_json") else {}
            reason = "expired" if (payload or {}).get("code") == "admin_session_expired" else "required"
            return redirect(url_for(
                "admin_blueprint.admin_login",
                next=request.path,
                reason=reason,
            ))
        return permission_error
    if not active_user:
        return redirect(url_for("admin_blueprint.admin_login", next=request.path))
    role = _normalize_admin_role((active_user.get("role") if isinstance(active_user, dict) else "") or "admin")
    return render_template(
        "admin.html",
        admin_allowed=True,
        admin_user=active_user,
        admin_role_label=ADMIN_ROLE_LABELS.get(role, role),
        admin_permissions=_admin_permissions(role),
        can_manage_admins=_has_admin_permission(active_user, "admin.manage"),
        csrf_token=_csrf_token(),
        role_options=ADMIN_ROLE_LABELS,
        admin_configured=bool(_admin_phone_set() or _admin_user_id_set()),
        admin_account_count=_admin_account_count(),
        initial_admin_route=initial_route,
    )


@admin_blueprint.route("/admin")
def admin_console():
    return _render_admin_console("dashboard")


@admin_blueprint.route("/admin/testing")
def admin_testing_console():
    return _render_admin_console("testing")


@admin_blueprint.route("/api/admin/session")
def admin_session_status():
    active_user, error = _admin_required("view")
    if error:
        return error
    issued_at = None
    expires_at = None
    if (active_user or {}).get("authType") == "admin_account":
        try:
            issued_at = int(active_user.get("issuedAt") or 0)
        except (TypeError, ValueError):
            issued_at = 0
        if issued_at > 0:
            expires_at = issued_at + max(300, ADMIN_SESSION_MAX_AGE_SECONDS)
    response = jsonify({
        "status": "success",
        "authenticated": True,
        "serverTime": int(time.time()),
        "issuedAt": issued_at,
        "expiresAt": expires_at,
        "admin": {
            "id": active_user.get("adminId"),
            "username": active_user.get("username") or "",
            "role": _normalize_admin_role(active_user.get("role") or "readonly"),
            "authType": active_user.get("authType") or "",
        },
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@admin_blueprint.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    next_path = _safe_admin_next(request.form.get("next") if request.method == "POST" else request.args.get("next"))
    if request.method == "POST":
        identity = str(request.form.get("identity") or "").strip()
        password = str(request.form.get("password") or "")
        locked = _admin_login_lock_seconds(identity)
        if locked > 0:
            return render_template(
                "admin_auth.html",
                **_admin_auth_context("login", error=f"登录尝试过多，请 {locked} 秒后再试", next_path=next_path),
            ), 429
        account = _find_admin_account(identity)
        if not account or account.get("status") != "active" or not check_password_hash(str(account.get("password_hash") or ""), password):
            _record_admin_login_failure(identity)
            return render_template(
                "admin_auth.html",
                **_admin_auth_context("login", error="管理员账号或密码错误", next_path=next_path),
            ), 401
        _clear_admin_login_failures(identity)
        _update_admin_login(account)
        admin_user = _admin_session_payload(account)
        session.permanent = True
        session[ADMIN_SESSION_KEY] = admin_user
        session[ADMIN_CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
        session.pop(ADMIN_SCREEN_SESSION_KEY, None)
        session.pop(ADMIN_SCREEN_SESSION_ISSUED_KEY, None)
        _audit(admin_user, "admin.login", str(account.get("id") or identity), meta={"ip": _client_ip()})
        return redirect(next_path)
    reason = str(request.args.get("reason") or "").strip()
    message = {
        "expired": "后台会话已过期，请重新登录管理员账号。",
        "required": "请登录管理员账号后继续访问后台。",
    }.get(reason, "")
    return render_template(
        "admin_auth.html",
        **_admin_auth_context("login", message=message, next_path=next_path),
    )


@admin_blueprint.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    user, permission_error = _admin_required("admin.manage")
    if permission_error and _is_legacy_admin(_current_user()) and _admin_account_count() == 0:
        user = _legacy_admin_payload(_current_user())
        permission_error = None
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
            _audit(user, "admin_account.create", username, after={"username": username, "phone": phone, "role": role})
        return redirect(url_for("admin_blueprint.admin_console"))
    return render_template("admin_auth.html", **_admin_auth_context("register"))


@admin_blueprint.route("/admin/logout", methods=["POST"])
def admin_logout():
    admin_user = _current_admin_user()
    legacy_user = _current_user()
    actor = admin_user or _legacy_admin_payload(legacy_user)
    if admin_user:
        try:
            admin_id = int(admin_user.get("adminId") or 0)
            session_version = int(admin_user.get("sessionVersion") or 0)
        except (TypeError, ValueError):
            admin_id = 0
            session_version = 0
        revoked = excute_sql(
            """
            UPDATE admin_accounts
            SET session_version = session_version + 1
            WHERE id = %s AND session_version = %s
            """,
            (admin_id, session_version),
            fetch=False,
        )
        if revoked != 1:
            return render_template(
                "admin_auth.html",
                **_admin_auth_context("login", error="退出失败，服务端管理员会话尚未撤销"),
            ), 503
    elif _is_legacy_admin(legacy_user):
        account_uuid = str((legacy_user or {}).get("account_uuid") or "").strip()
        try:
            user_id = int((legacy_user or {}).get("Userid") or 0)
            session_version = int((legacy_user or {}).get("session_version") or 0)
        except (TypeError, ValueError):
            user_id = 0
            session_version = 0
        revoked = excute_sql(
            """
            UPDATE user
            SET session_version = session_version + 1
            WHERE Userid = %s AND account_uuid = %s AND session_version = %s
            """,
            (user_id, account_uuid, session_version),
            fetch=False,
        )
        if revoked != 1:
            return render_template(
                "admin_auth.html",
                **_admin_auth_context("login", error="退出失败，服务端会话尚未撤销"),
            ), 503
    if actor:
        _audit(actor, "admin.logout", str(actor.get("adminId") or actor.get("Userid") or "session"), meta={"ip": _client_ip()})
    session.pop(ADMIN_SESSION_KEY, None)
    session.pop(ADMIN_LOGIN_ATTEMPTS_KEY, None)
    session.pop(ADMIN_CSRF_SESSION_KEY, None)
    session.pop(ADMIN_SCREEN_SESSION_KEY, None)
    session.pop(ADMIN_SCREEN_SESSION_ISSUED_KEY, None)
    if not admin_user and _is_legacy_admin(legacy_user):
        session.pop("user_info", None)
    return redirect(url_for("admin_blueprint.admin_login"))


@admin_blueprint.route("/admin/screen")
def admin_screen():
    request_token = _screen_token_from_request()
    admin_user = _current_admin_user()
    if admin_user and _has_admin_permission(admin_user, "view"):
        return render_template(
            "admin_screen.html",
            admin_user=admin_user,
            screen_token="",
            can_view_registered_users=_has_admin_permission(admin_user, "user.view"),
        )
    if _screen_token_matches(request_token):
        session[ADMIN_SCREEN_SESSION_KEY] = _configured_screen_token_digest()
        session[ADMIN_SCREEN_SESSION_ISSUED_KEY] = int(time.time())
        session.modified = True
        return render_template(
            "admin_screen.html",
            admin_user=_screen_token_user(),
            screen_token="",
            can_view_registered_users=False,
        )
    if _screen_session_valid():
        return render_template(
            "admin_screen.html",
            admin_user=_screen_token_user(),
            screen_token="",
            can_view_registered_users=False,
        )
    user, error = _admin_required("view")
    if error:
        return redirect(url_for("admin_blueprint.admin_login", next="/admin/screen"))
    return render_template(
        "admin_screen.html",
        admin_user=user,
        screen_token="",
        can_view_registered_users=_has_admin_permission(user, "user.view"),
    )


@admin_blueprint.route("/api/admin/overview")
def admin_overview():
    user, error = _admin_required("view")
    if error:
        return error
    registry = model_registry.load_registry()
    models = _models_payload_with_health(registry.get("models", []))
    if not _has_admin_permission(user, "topology.view"):
        models = [_safe_model_payload(model) for model in models]
    return jsonify({
        "status": "success",
        "routing": registry.get("routing", {}),
        "routingHistory": model_registry.routing_history(10),
        "models": models,
        "metrics": _cached_dashboard_metrics(),
    })


@admin_blueprint.route("/api/admin/big-screen")
def admin_big_screen():
    if _screen_token_valid():
        return jsonify({"status": "success", **_cached_big_screen_payload()})
    _, error = _admin_required("view")
    if error:
        return error
    return jsonify({"status": "success", **_cached_big_screen_payload()})


@admin_blueprint.route("/api/admin/traffic/registered-users")
def admin_registered_users_by_province():
    actor, error = _admin_required("user.view")
    if error:
        return error
    province = traffic_geo.normalize_province(request.args.get("province", ""))
    scope = str(request.args.get("scope") or "recent").strip().lower()
    if not province:
        return jsonify({"status": "error", "message": "请选择省份"}), 400
    if scope not in {"recent", "cumulative"}:
        return jsonify({"status": "error", "message": "统计范围无效"}), 400
    limit = _limit_arg(50, 100)
    by_account = _registered_user_rows()
    if by_account is None:
        return jsonify({"status": "error", "message": "用户目录暂时无法读取"}), 503
    activity = traffic_geo.registered_account_activity(
        by_account.keys(),
        province=province,
        scope=scope,
        limit=limit,
    )
    if not activity.get("ready"):
        return jsonify({
            "status": "error",
            "message": activity.get("message") or "注册用户地域统计暂不可用",
        }), 503
    include_pii = _has_admin_permission(actor, "user.read_pii")
    users = []
    for item in activity.get("accounts", []):
        row = by_account.get(str(item.get("accountUuid") or ""))
        if not row:
            continue
        users.append({
            "id": row.get("Userid"),
            "username": row.get("username") or "未命名用户",
            "phone": row.get("phone") if include_pii else _mask_phone(row.get("phone")),
            "createdAt": format_createtime(row.get("created_at")),
            "activity": {
                "requests": item.get("requests", 0),
                "pages": item.get("pages", 0),
                "firstSeen": item.get("firstSeen"),
                "lastSeen": item.get("lastSeen"),
                "city": item.get("city") or "省内位置未细分",
                "network": item.get("network") or "未知网络",
                "device": item.get("device") or "未知设备",
                "browser": item.get("browser") or "未知浏览器",
            },
        })
    return jsonify({
        "status": "success",
        "province": province,
        "scope": scope,
        "total": activity.get("total", len(users)),
        "hasMore": bool(activity.get("hasMore")),
        "users": users,
        "privacy": {"rawIpsIncluded": False, "accountUuidIncluded": False},
    })


@admin_blueprint.route("/api/admin/system")
def admin_system():
    _, error = _admin_required("topology.view")
    if error:
        return error
    return jsonify({"status": "success", **_service_health()})


@admin_blueprint.route("/api/admin/models", methods=["GET", "POST"])
def admin_models():
    permission = "model.manage" if request.method == "POST" else "topology.view"
    user, error = _admin_required(permission)
    if error:
        return error
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        _, model, message = model_registry.create_model(payload)
        if not model:
            return jsonify({"status": "error", "message": message or "模型创建失败"}), 400
        model_registry.clear_health_cache()
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
    confirmed_primary_runtime_change = payload.pop("confirmPrimaryRuntimeChange", False) is True
    before = model_registry.get_model(model_id)
    if not before:
        return jsonify({"status": "error", "message": "模型不存在"}), 404
    routing = model_registry.get_routing()
    if str(routing.get("imagePrimary") or "") == str(model_id):
        if "enabled" in payload:
            enabled = payload.get("enabled") if isinstance(payload.get("enabled"), bool) else str(payload.get("enabled") or "").strip().lower() in ("1", "true", "yes", "on")
            if not enabled:
                return jsonify({
                    "status": "error",
                    "code": "primary_model_disable_forbidden",
                    "message": "当前主模型不能直接停用，请先将主路由切换到其他可用模型。",
                }), 409
        changed_runtime_fields = [
            field for field in ("endpoint", "healthUrl")
            if field in payload and str(payload.get(field) or "").strip() != str(before.get(field) or "").strip()
        ]
        if changed_runtime_fields and not confirmed_primary_runtime_change:
            return jsonify({
                "status": "error",
                "code": "primary_model_confirmation_required",
                "message": "修改当前主模型的服务地址需要明确确认。",
                "changedFields": changed_runtime_fields,
            }), 409
    _, model, message = model_registry.update_model(model_id, payload)
    if not model:
        status = 404 if message == "模型不存在" else 400
        return jsonify({"status": "error", "message": message or "模型更新失败"}), status
    model_registry.clear_health_cache()
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
    model_registry.clear_health_cache()
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
    model_registry.clear_health_cache()
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


def _internal_testing_model(model_id):
    model = model_registry.get_model(str(model_id or "").strip())
    if not model:
        return None, "模型不存在"
    if model.get("enabled") is False:
        return None, "模型已停用"
    modality = str(model.get("modality") or "image").strip().lower()
    if modality not in ("image", "vision", "multimodal"):
        return None, "内部测试平台一期仅支持图像模型"
    endpoint = str(model.get("endpoint") or "").strip()
    endpoint_error = model_registry.validate_model_url(endpoint, allow_internal=True)
    if endpoint_error:
        return None, endpoint_error
    return model, ""


def _testing_system_snapshot():
    registry = model_registry.load_registry()
    raw_models = _models_payload_with_health(registry.get("models", []))
    screen_models = [_screen_model_payload(model) for model in raw_models]
    routing = _screen_routing_payload(registry.get("routing", {}))
    return {
        "host": _host_telemetry(),
        "algorithmServer": _screen_algorithm_server_payload(screen_models, routing),
        "services": _service_summary(screen_models),
        "models": [
            {
                "id": model.get("id"),
                "name": model.get("name") or model.get("id"),
                "runtime": model.get("runtime"),
                "modality": model.get("modality") or "image",
                "enabled": model.get("enabled") is not False,
                "health": model.get("health"),
            }
            for model in raw_models
        ],
    }


@admin_blueprint.route("/api/admin/testing/overview")
def admin_testing_overview():
    user, error = _admin_required("testing.view")
    if error:
        return error
    payload = internal_testing.overview(actor=user)
    payload["system"] = _testing_system_snapshot()
    return jsonify({"status": "success", **payload})


def _owned_testing_import(import_id, actor):
    import_session = internal_testing.get_import_session(import_id, actor=actor)
    if import_session:
        return import_session, None
    return None, (
        jsonify({"status": "error", "message": "数据集上传会话不存在"}),
        404,
    )


@admin_blueprint.route("/api/admin/testing/datasets", methods=["POST"])
def admin_testing_create_dataset():
    user, error = _admin_required("testing.run")
    if error:
        return error
    content_length = max(0, int(request.content_length or 0))
    if content_length and content_length > internal_testing.available_storage_bytes():
        return jsonify({
            "status": "error",
            "message": "服务器磁盘剩余空间不足，无法接收该数据集",
        }), 507
    uploads = []
    for uploaded in request.files.getlist("files"):
        if not uploaded or not uploaded.filename:
            continue
        uploads.append((uploaded.filename, uploaded.stream))
    labels = {}
    raw_labels = str(request.form.get("labels") or "").strip()
    if raw_labels:
        try:
            labels = json.loads(raw_labels)
        except json.JSONDecodeError:
            return jsonify({"status": "error", "message": "标签映射必须是 JSON 对象"}), 400
        if not isinstance(labels, dict):
            return jsonify({"status": "error", "message": "标签映射格式或数量无效"}), 400
    try:
        dataset = internal_testing.create_dataset(
            uploads,
            source_url=request.form.get("sourceUrl") or "",
            name=request.form.get("name") or "",
            default_label=request.form.get("defaultLabel") or "unlabeled",
            labels=labels,
            actor=user,
        )
    except (ValueError, requests.RequestException) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    _audit(
        user,
        "testing.dataset.create",
        dataset.get("id") or "dataset",
        meta={
            "sampleCount": dataset.get("sample_count"),
            "sourceType": dataset.get("source_type"),
        },
    )
    return jsonify({"status": "success", "dataset": dataset}), 201


@admin_blueprint.route("/api/admin/testing/dataset-imports", methods=["POST"])
def admin_testing_create_import():
    user, error = _admin_required("testing.run")
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    stream_evaluation = bool(payload.get("streamEvaluation"))
    model = None
    if stream_evaluation:
        model, model_error = _internal_testing_model(payload.get("modelId"))
        if model_error:
            return jsonify({"status": "error", "message": model_error}), 400
    try:
        import_session = internal_testing.create_import_session(
            name=payload.get("name") or "",
            default_label=payload.get("defaultLabel") or "unlabeled",
            source_url=payload.get("sourceUrl") or "",
            expected_files=payload.get("expectedFiles") or 0,
            expected_bytes=payload.get("expectedBytes") or 0,
            stream_evaluation=stream_evaluation,
            model=model,
            concurrency=payload.get("concurrency") or 1,
            actor=user,
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    _audit(
        user,
        "testing.dataset_import.create",
        import_session.get("id") or "dataset-import",
        meta={
            "expectedFiles": import_session.get("expectedFiles"),
            "expectedBytes": import_session.get("expectedBytes"),
            "streamEvaluation": import_session.get("streamEvaluation"),
            "modelId": import_session.get("modelId"),
        },
    )
    return jsonify({"status": "success", "importSession": import_session}), 201


@admin_blueprint.route("/api/admin/testing/dataset-imports/<import_id>")
def admin_testing_import(import_id):
    user, error = _admin_required("testing.view")
    if error:
        return error
    import_session, ownership_error = _owned_testing_import(import_id, user)
    if ownership_error:
        return ownership_error
    return jsonify({"status": "success", "importSession": import_session})


@admin_blueprint.route("/api/admin/testing/dataset-imports/<import_id>", methods=["DELETE"])
def admin_testing_delete_import(import_id):
    user, error = _admin_required("testing.run")
    if error:
        return error
    _, ownership_error = _owned_testing_import(import_id, user)
    if ownership_error:
        return ownership_error
    try:
        deleted = internal_testing.delete_import_session(import_id)
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 409
    if not deleted:
        return jsonify({"status": "error", "message": "数据集上传会话不存在"}), 404
    _audit(user, "testing.dataset_import.delete", import_id)
    return jsonify({"status": "success", "deleted": import_id})


@admin_blueprint.route("/api/admin/testing/dataset-imports/<import_id>/files", methods=["POST"])
def admin_testing_upload_import_files(import_id):
    user, error = _admin_required("testing.run")
    if error:
        return error
    _, ownership_error = _owned_testing_import(import_id, user)
    if ownership_error:
        return ownership_error
    if int(request.content_length or 0) > internal_testing.available_storage_bytes():
        return jsonify({"status": "error", "message": "服务器磁盘剩余空间不足"}), 507
    uploads = [
        (uploaded.filename, uploaded.stream)
        for uploaded in request.files.getlist("files")
        if uploaded and uploaded.filename
    ]
    if not uploads:
        return jsonify({
            "status": "error",
            "code": "empty_upload_batch",
            "message": "浏览器未提交本批次文件，系统将重建请求后重试",
        }), 422
    try:
        import_session = internal_testing.add_import_files(import_id, uploads)
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    return jsonify({"status": "success", "importSession": import_session})


@admin_blueprint.route("/api/admin/testing/dataset-imports/<import_id>/chunks", methods=["POST"])
def admin_testing_upload_import_chunk(import_id):
    user, error = _admin_required("testing.run")
    if error:
        return error
    _, ownership_error = _owned_testing_import(import_id, user)
    if ownership_error:
        return ownership_error
    is_raw_chunk = request.mimetype == "application/octet-stream"
    uploaded = None if is_raw_chunk else request.files.get("chunk")
    chunk = request.stream if is_raw_chunk else (uploaded.stream if uploaded else None)
    if chunk is None:
        return jsonify({"status": "error", "message": "缺少文件分块"}), 400
    values = request.headers if is_raw_chunk else request.form
    prefix = "X-Upload-" if is_raw_chunk else ""
    upload_id = values.get(f"{prefix}Id") or ""
    relative_path = values.get(f"{prefix}Relative-Path") or ""
    if is_raw_chunk:
        relative_path = unquote(relative_path)
    try:
        import_session = internal_testing.add_import_chunk(
            import_id,
            upload_id=upload_id,
            relative_path=relative_path or (uploaded.filename if uploaded else "archive.zip"),
            chunk_index=int(values.get(f"{prefix}Chunk-Index" if is_raw_chunk else "chunkIndex") or 0),
            total_chunks=int(values.get(f"{prefix}Total-Chunks" if is_raw_chunk else "totalChunks") or 0),
            expected_bytes=(
                int(values.get(f"{prefix}Expected-Bytes" if is_raw_chunk else "expectedBytes"))
                if values.get(f"{prefix}Expected-Bytes" if is_raw_chunk else "expectedBytes") not in (None, "")
                else None
            ),
            chunk=chunk,
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    return jsonify({"status": "success", "importSession": import_session})


@admin_blueprint.route("/api/admin/testing/dataset-imports/<import_id>/resume", methods=["POST"])
def admin_testing_resume_import(import_id):
    user, error = _admin_required("testing.run")
    if error:
        return error
    _, ownership_error = _owned_testing_import(import_id, user)
    if ownership_error:
        return ownership_error
    payload = request.get_json(silent=True) or {}
    files = payload.get("files") or []
    if not isinstance(files, list):
        return jsonify({"status": "error", "message": "文件清单格式无效"}), 400
    try:
        resume_state = internal_testing.get_import_resume_state(import_id, files)
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    return jsonify({"status": "success", "resumeState": resume_state})


@admin_blueprint.route("/api/admin/testing/dataset-imports/<import_id>/finalize", methods=["POST"])
def admin_testing_finalize_import(import_id):
    user, error = _admin_required("testing.run")
    if error:
        return error
    _, ownership_error = _owned_testing_import(import_id, user)
    if ownership_error:
        return ownership_error
    try:
        import_session = internal_testing.finalize_import(import_id)
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    _audit(
        user,
        "testing.dataset_import.finalize",
        import_id,
        meta={"validatedFiles": import_session.get("validatedFiles")},
    )
    return jsonify({"status": "success", "importSession": import_session}), 202


@admin_blueprint.route("/api/admin/testing/datasets/<dataset_id>")
def admin_testing_dataset(dataset_id):
    _, error = _admin_required("testing.view")
    if error:
        return error
    dataset = internal_testing.get_dataset(dataset_id, include_samples=True)
    if not dataset:
        return jsonify({"status": "error", "message": "测试数据集不存在"}), 404
    return jsonify({"status": "success", "dataset": dataset})


@admin_blueprint.route("/api/admin/testing/datasets/<dataset_id>", methods=["DELETE"])
def admin_testing_delete_dataset(dataset_id):
    user, error = _admin_required("testing.run")
    if error:
        return error
    try:
        deleted = internal_testing.delete_dataset(dataset_id)
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 409
    if not deleted:
        return jsonify({"status": "error", "message": "测试数据集不存在"}), 404
    _audit(user, "testing.dataset.delete", dataset_id)
    return jsonify({"status": "success", "deleted": dataset_id})


@admin_blueprint.route("/api/admin/testing/samples/<sample_id>/image")
def admin_testing_sample_image(sample_id):
    _, error = _admin_required("testing.view")
    if error:
        return error
    item = internal_testing.sample_path(sample_id)
    if not item:
        return jsonify({"status": "error", "message": "测试样本不存在"}), 404
    path, mime_type, name = item
    return send_file(path, mimetype=mime_type, download_name=name, conditional=True, max_age=0)


@admin_blueprint.route("/api/admin/testing/samples/<sample_id>", methods=["PATCH", "POST"])
def admin_testing_update_sample(sample_id):
    user, error = _admin_required("testing.run")
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    label = str(payload.get("groundTruth") or "").strip().lower()
    if label not in internal_testing.ALLOWED_LABELS:
        return jsonify({"status": "error", "message": "标签必须为 real、fake 或 unlabeled"}), 400
    sample = internal_testing.update_sample_label(sample_id, label)
    if not sample:
        return jsonify({"status": "error", "message": "测试样本不存在"}), 404
    _audit(user, "testing.sample.label", sample_id, after={"groundTruth": label})
    return jsonify({"status": "success", "sample": sample})


@admin_blueprint.route("/api/admin/testing/runs", methods=["POST"])
def admin_testing_create_run():
    user, error = _admin_required("testing.run")
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    model, model_error = _internal_testing_model(payload.get("modelId"))
    if model_error:
        return jsonify({"status": "error", "message": model_error}), 400
    try:
        run = internal_testing.create_evaluation(
            str(payload.get("datasetId") or ""),
            model,
            concurrency=payload.get("concurrency") or 1,
            actor=user,
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    _audit(
        user,
        "testing.evaluation.start",
        run.get("id") or "evaluation",
        meta={
            "datasetId": payload.get("datasetId"),
            "modelId": payload.get("modelId"),
            "concurrency": (run.get("configuration") or {}).get("concurrency"),
        },
    )
    return jsonify({"status": "success", "run": run}), 202


@admin_blueprint.route("/api/admin/testing/load-tests", methods=["POST"])
def admin_testing_create_load_test():
    user, error = _admin_required("testing.load")
    if error:
        return error
    if request.form.get("confirmation") != "INTERNAL_LOAD_TEST":
        return jsonify({"status": "error", "message": "请确认这是受控内部压测"}), 400
    active_load = [
        run for run in internal_testing.list_runs(100)
        if run.get("kind") == "load_test"
        and run.get("status") in {"queued", "running", "cancel_requested"}
    ]
    if active_load:
        return jsonify({"status": "error", "message": "已有压测任务运行中，请等待或先停止"}), 409
    model, model_error = _internal_testing_model(request.form.get("modelId"))
    if model_error:
        return jsonify({"status": "error", "message": model_error}), 400
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"status": "error", "message": "压测必须上传固定测试图片"}), 400
    image = uploaded.stream.read(internal_testing.MAX_UPLOAD_BYTES + 1)
    if len(image) > internal_testing.MAX_UPLOAD_BYTES:
        return jsonify({"status": "error", "message": "压测图片超过 24 MB"}), 413
    try:
        run = internal_testing.create_load_test(
            model,
            image,
            uploaded.filename,
            uploaded.mimetype or "application/octet-stream",
            concurrency=request.form.get("concurrency") or 1,
            request_count=request.form.get("requestCount") or 20,
            duration_seconds=request.form.get("durationSeconds") or 30,
            actor=user,
        )
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    _audit(
        user,
        "testing.load.start",
        run.get("id") or "load-test",
        meta=run.get("configuration"),
    )
    return jsonify({"status": "success", "run": run}), 202


@admin_blueprint.route("/api/admin/testing/runs/<run_id>")
def admin_testing_run(run_id):
    _, error = _admin_required("testing.view")
    if error:
        return error
    run = internal_testing.get_run(run_id, include_results=True, result_limit=200)
    if not run:
        return jsonify({"status": "error", "message": "测试任务不存在"}), 404
    return jsonify({"status": "success", "run": run})


@admin_blueprint.route("/api/admin/testing/runs/<run_id>/cancel", methods=["POST"])
def admin_testing_cancel_run(run_id):
    user, error = _admin_required("testing.run")
    if error:
        return error
    run = internal_testing.cancel_run(run_id)
    if not run:
        return jsonify({"status": "error", "message": "测试任务不存在"}), 404
    _audit(user, "testing.run.cancel", run_id, meta={"status": run.get("status")})
    return jsonify({"status": "success", "run": run})


@admin_blueprint.route("/api/admin/testing/runs/<run_id>/export")
def admin_testing_export_run(run_id):
    _, error = _admin_required("testing.view")
    if error:
        return error
    run = internal_testing.get_run(run_id, include_results=True, result_limit=None)
    if not run:
        return jsonify({"status": "error", "message": "测试任务不存在"}), 404
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "sample_id", "sample_name", "relative_path", "class_path", "group_id",
        "subclasses", "ground_truth", "predicted_label", "score", "status",
        "latency_ms", "http_status", "error",
    ])
    for item in run.get("results") or []:
        writer.writerow([
            item.get("sample_id"), item.get("sample_name"), item.get("relative_path"),
            item.get("class_path"), item.get("group_id"),
            json.dumps(item.get("subclasses") or {}, ensure_ascii=False),
            item.get("ground_truth"), item.get("predicted_label"), item.get("score"), item.get("status"),
            item.get("latency_ms"), item.get("http_status"), item.get("error"),
        ])
    response = Response(output.getvalue(), content_type="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="{run_id}.csv"'
    return response


@admin_blueprint.route("/api/admin/routing", methods=["PATCH", "POST"])
def admin_update_routing():
    user, error = _admin_required("routing.manage")
    if error:
        return error
    before = model_registry.get_routing()
    try:
        routing = model_registry.update_routing(request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
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
    permission = "topology.view" if request.method == "GET" else "routing.manage"
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
    _, error = _admin_required("topology.view")
    if error:
        return error
    return jsonify({"status": "success", "assurance": _v1_assurance()})


@admin_blueprint.route("/api/admin/alerts", methods=["GET", "PATCH", "POST"])
def admin_alerts():
    permission = "alerts.manage"
    user, error = _admin_required(permission)
    if error:
        return error
    if request.method == "GET":
        alerts = admin_state.alerts()
        return jsonify({
            "status": "success",
            "alerts": alerts,
            "deliveryHistory": admin_state.alert_delivery_history(50),
        })
    before = admin_state.alerts()
    payload = request.get_json(silent=True) or {}
    normalized = dict(payload)
    if "enabled" in normalized:
        normalized["enabled"] = str(normalized.get("enabled") or "").strip().lower() in ("1", "true", "yes", "on") if not isinstance(normalized.get("enabled"), bool) else normalized.get("enabled")
    if "webhookUrl" in normalized:
        webhook_url, validation_error = _validate_webhook_url(normalized.get("webhookUrl"))
        if validation_error:
            return jsonify({"status": "error", "message": validation_error}), 400
        normalized["webhookUrl"] = webhook_url
    effective_url = normalized.get("webhookUrl", before.get("webhookUrl"))
    if normalized.get("enabled", before.get("enabled")) and not effective_url:
        return jsonify({"status": "error", "message": "启用告警前请配置 HTTPS Webhook"}), 400
    if "cooldownSeconds" in normalized:
        try:
            normalized["cooldownSeconds"] = max(60, min(int(normalized.get("cooldownSeconds")), 86400))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "告警冷却时间必须为 60-86400 秒"}), 400
    alerts = admin_state.update_alerts(normalized)
    _audit(user, "alerts.update", "alerts", before=before, after=alerts)
    return jsonify({"status": "success", "alerts": alerts})


@admin_blueprint.route("/api/admin/alerts/test", methods=["POST"])
def admin_test_alert():
    user, error = _admin_required("alerts.manage")
    if error:
        return error
    config = admin_state.alerts()
    webhook_url = str(config.get("webhookUrl") or "").strip()
    if not webhook_url:
        return jsonify({"status": "error", "message": "请先保存 HTTPS Webhook"}), 400
    claim = admin_state.claim_alert_event(
        "manualTest",
        False,
        "慧鉴AI告警测试",
        "这是一条由管理员触发的测试通知。",
        "info",
        force=True,
    )
    delivery = _deliver_alert_claim(claim, webhook_url)
    _audit(user, "alerts.test", "webhook", meta={"ok": delivery.get("ok"), "statusCode": delivery.get("statusCode")})
    status_code = 200 if delivery.get("ok") else 502
    return jsonify({"status": "success" if delivery.get("ok") else "error", "delivery": delivery}), status_code


@admin_blueprint.route("/api/admin/audit")
def admin_audit():
    _, error = _admin_required("audit.view")
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
    cursor = _cursor_arg()
    query = _search_term()
    filters = []
    params = []
    if query:
        like = f"%{query}%"
        filters.append("(username LIKE %s OR phone LIKE %s OR role LIKE %s OR status LIKE %s)")
        params.extend([like, like, like, like])
    if cursor:
        filters.append("id < %s")
        params.append(cursor)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit + 1)
    rows = excute_sql(
        f"""
        SELECT id, username, phone, role, status, session_version,
               created_at, last_login_at, last_login_ip
        FROM admin_accounts
        {where}
        ORDER BY id DESC
        LIMIT %s
        """,
        tuple(params),
    ) or []
    items, page = _page_payload([_admin_account_payload(row) for row in rows], limit)
    return jsonify({
        "status": "success",
        "admins": items,
        "page": page,
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
    payload = request.get_json(silent=True) or {}
    role = payload.get("role")
    status = payload.get("status")
    if role is not None and str(role).strip() not in ADMIN_ROLE_LABELS:
        return jsonify({"status": "error", "message": "管理员角色无效"}), 400
    if status is not None and str(status).strip() not in ("active", "disabled"):
        return jsonify({"status": "error", "message": "管理员状态只能为 active 或 disabled"}), 400
    before, after, update_error = _update_admin_account_atomic(
        admin_id,
        str(role).strip() if role is not None else None,
        str(status).strip() if status is not None else None,
        actor_admin_id=actor.get("adminId"),
    )
    if update_error == "not_found":
        return jsonify({"status": "error", "message": "管理员账号不存在"}), 404
    if update_error == "self_downgrade":
        return jsonify({"status": "error", "message": "不能停用或降级当前登录管理员"}), 400
    if update_error == "last_super_admin":
        return jsonify({"status": "error", "message": "必须至少保留一个启用的超级管理员"}), 400
    if update_error:
        return jsonify({"status": "error", "message": "管理员账号更新失败"}), 500
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
        SELECT id, username, phone, role, status, session_version,
               created_at, last_login_at, last_login_ip
        FROM admin_accounts
        WHERE id = %s
        LIMIT 1
        """,
        (admin_id,),
    ) or []
    if not rows:
        return jsonify({"status": "error", "message": "管理员账号不存在"}), 404
    updated = excute_sql(
        "UPDATE admin_accounts SET password_hash = %s, session_version = session_version + 1 WHERE id = %s",
        (generate_password_hash(password), admin_id),
        fetch=False,
    )
    if updated is None:
        return jsonify({"status": "error", "message": "管理员密码重置失败"}), 500
    _audit(actor, "admin_account.password_reset", str(admin_id), before=_admin_account_payload(rows[0]), after={"id": admin_id, "passwordReset": True})
    return jsonify({"status": "success"})


@admin_blueprint.route("/api/admin/users")
def admin_users():
    actor, error = _admin_required("user.view")
    if error:
        return error
    limit = _limit_arg(50, 200)
    cursor = _cursor_arg()
    query = _search_term()
    filters = []
    params = []
    if query:
        like = f"%{query}%"
        filters.append("(phone LIKE %s OR username LIKE %s OR openid LIKE %s)")
        params.extend([like, like, like])
    if cursor:
        filters.append("Userid < %s")
        params.append(cursor)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit + 1)
    rows = excute_sql(
        f"""
        SELECT Userid, account_uuid, phone, username, openid, created_at, terms_version, terms_accepted_at
        FROM user
        {where}
        ORDER BY Userid DESC
        LIMIT %s
        """,
        tuple(params),
    ) or []
    include_pii = _has_admin_permission(actor, "user.read_pii")
    include_account_identity = _has_admin_permission(actor, "legacy_history.view")
    users = []
    for row in rows:
        users.append({
            "id": row.get("Userid"),
            **({"accountUuid": row.get("account_uuid")} if include_account_identity else {}),
            "phone": row.get("phone") if include_pii else _mask_phone(row.get("phone")),
            "username": row.get("username"),
            "openid": row.get("openid") if include_pii else _mask_identifier(row.get("openid")),
            "createdAt": format_createtime(row.get("created_at")),
            "termsVersion": row.get("terms_version"),
            "termsAcceptedAt": format_createtime(row.get("terms_accepted_at")),
        })
    users, page = _page_payload(users, limit)
    return jsonify({"status": "success", "users": users, "page": page})


@admin_blueprint.route("/api/admin/users/<int:user_id>")
def admin_user_detail(user_id):
    actor, error = _admin_required("user.view")
    if error:
        return error
    rows = excute_sql(
        """
        SELECT Userid, account_uuid, phone, username, openid, created_at, terms_version, terms_accepted_at
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
    history_where, history_params = detection_owner_where(
        phone,
        openid,
        account_uuid=row.get("account_uuid"),
        require_account_uuid=True,
    )
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
    include_pii = _has_admin_permission(actor, "user.read_pii")
    user = {
        "id": row.get("Userid"),
        **({"accountUuid": row.get("account_uuid")} if _has_admin_permission(actor, "legacy_history.view") else {}),
        "phone": phone if include_pii else _mask_phone(phone),
        "username": row.get("username"),
        "openid": openid if include_pii else _mask_identifier(openid),
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
                "phone": item.get("phone") if include_pii else _mask_phone(item.get("phone")),
                "feedback": _normalize_detection_feedback(item.get("feedback")),
                "feedbackLabel": _detection_feedback_label(item.get("feedback")),
                "adminReview": _normalize_detection_feedback(item.get("admin_review")),
                "adminReviewLabel": _admin_review_label(item.get("admin_review")),
                "modelRoute": _model_run_for_admin(
                    route_runs.get(str(item.get("itemid"))), actor, "user.read_pii"
                ),
                **_authorized_detection_view(
                    item,
                    route_runs.get(str(item.get("itemid"))),
                ),
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
                "lastUsedIp": (key.get("last_used_ip") or "") if include_pii else _mask_identifier(key.get("last_used_ip")),
            }
            for key in key_rows
        ],
    })


@admin_blueprint.route("/api/admin/users/export")
def admin_users_export():
    actor, error = _admin_required("data.export")
    if error:
        return error
    if not _has_admin_permission(actor, "user.view"):
        return _permission_error("user.view")
    rows = excute_sql(
        """
        SELECT Userid, phone, username, openid, created_at, terms_version, terms_accepted_at
        FROM user
        ORDER BY Userid DESC
        LIMIT 5000
        """
    ) or []
    include_pii = _has_admin_permission(actor, "user.read_pii")
    _audit(actor, "users.export", "users", meta={"count": len(rows), "piiIncluded": include_pii})
    return _csv_response(
        "realguard-users.csv",
        ["ID", "Phone", "Username", "OpenID", "Created At", "Terms Version", "Terms Accepted At"],
        [
            [
                row.get("Userid"),
                row.get("phone") if include_pii else _mask_phone(row.get("phone")),
                row.get("username"),
                row.get("openid") if include_pii else _mask_identifier(row.get("openid")),
                format_createtime(row.get("created_at")),
                row.get("terms_version"),
                format_createtime(row.get("terms_accepted_at")),
            ]
            for row in rows
        ],
    )


@admin_blueprint.route("/api/admin/conversations")
def admin_conversations():
    actor, error = _admin_required("conversation.view")
    if error:
        return error
    limit = _limit_arg(40, 100)
    offset = _cursor_arg() or 0
    query = _search_term()[:120]
    search_clause = ""
    search_params = []
    if query:
        like = f"%{query}%"
        search_clause = """
        WHERE EXISTS (
            SELECT 1
            FROM report_qa_turns search_turn
            WHERE search_turn.conversation_id = turn.conversation_id
              AND (
                search_turn.question LIKE ?
                OR search_turn.answer LIKE ?
                OR COALESCE(search_turn.file_name, '') LIKE ?
              )
        )
        """
        search_params = [like, like, like]
    try:
        with _v2_conversation_connection() as connection:
            rows = connection.execute(
                f"""
                WITH ranked AS (
                    SELECT turn.*,
                           COUNT(*) OVER (PARTITION BY turn.conversation_id) AS turn_count,
                           MIN(turn.created_at) OVER (PARTITION BY turn.conversation_id) AS started_at,
                           MAX(turn.completed_at) OVER (PARTITION BY turn.conversation_id) AS updated_at,
                           SUM(turn.total_tokens) OVER (PARTITION BY turn.conversation_id) AS conversation_tokens,
                           ROW_NUMBER() OVER (
                               PARTITION BY turn.conversation_id
                               ORDER BY turn.completed_at DESC, turn.rowid DESC
                           ) AS row_rank
                    FROM report_qa_turns turn
                    {search_clause}
                )
                SELECT * FROM ranked
                WHERE row_rank = 1
                ORDER BY updated_at DESC, turn_id DESC
                LIMIT ? OFFSET ?
                """,
                (*search_params, limit + 1, offset),
            ).fetchall()
            total_row = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM (
                    SELECT turn.conversation_id
                    FROM report_qa_turns turn
                    {search_clause}
                    GROUP BY turn.conversation_id
                ) grouped
                """,
                tuple(search_params),
            ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        print(f"[ADMIN CONVERSATION READ ERROR] {type(exc).__name__}: {exc}")
        return jsonify({
            "status": "error",
            "code": "conversation_store_unavailable",
            "message": "用户对话存储暂时无法读取",
        }), 503
    has_more = len(rows) > limit
    visible = rows[:limit]
    directory = _conversation_user_directory(visible)
    include_content = _has_admin_permission(actor, "conversation.read_content")
    include_media = _has_admin_permission(actor, "conversation.read_media")
    items = []
    for row in visible:
        media_available = bool(row["task_id"] or row["legacy_detection_id"])
        items.append({
            "id": row["conversation_id"],
            "user": _conversation_user_payload(row, directory, actor),
            "startedAt": _conversation_time(row["started_at"]),
            "updatedAt": _conversation_time(row["updated_at"]),
            "turnCount": int(row["turn_count"] or 0),
            "totalTokens": int(row["conversation_tokens"] or 0),
            "fileName": row["file_name"] or "",
            "mediaType": row["media_type"] or "",
            "mediaAvailable": media_available,
            "mediaUrl": (
                f"/api/admin/conversations/{quote(row['conversation_id'], safe='')}/media"
                if include_media and media_available else ""
            ),
            "lastQuestion": row["question"][:240] if include_content else "内容受限",
            "lastAnswer": row["answer"][:320] if include_content else "内容受限",
        })
    return jsonify({
        "status": "success",
        "conversations": items,
        "total": int(total_row["count"] if total_row else 0),
        "page": {
            "hasMore": has_more,
            "nextCursor": str(offset + limit) if has_more else None,
        },
    })


@admin_blueprint.route("/api/admin/conversations/<conversation_id>")
def admin_conversation_detail(conversation_id):
    actor, error = _admin_required("conversation.read_content")
    if error:
        return error
    if len(conversation_id) > 128:
        return jsonify({"status": "error", "message": "对话编号无效"}), 400
    try:
        with _v2_conversation_connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM report_qa_turns
                WHERE conversation_id = ?
                ORDER BY created_at ASC, completed_at ASC, turn_id ASC
                LIMIT 1000
                """,
                (conversation_id,),
            ).fetchall()
    except (OSError, sqlite3.Error) as exc:
        print(f"[ADMIN CONVERSATION DETAIL ERROR] {type(exc).__name__}: {exc}")
        return jsonify({"status": "error", "message": "用户对话存储暂时无法读取"}), 503
    if not rows:
        return jsonify({"status": "error", "message": "对话不存在"}), 404
    directory = _conversation_user_directory(rows)
    first = rows[0]
    media_available = bool(first["task_id"] or first["legacy_detection_id"])
    include_media = _has_admin_permission(actor, "conversation.read_media")
    turns = [{
        "id": row["turn_id"],
        "createdAt": _conversation_time(row["created_at"]),
        "completedAt": _conversation_time(row["completed_at"]),
        "question": row["question"],
        "answer": row["answer"],
        "evidenceRefs": _conversation_json_list(row["evidence_refs_json"], 5),
        "suggestedQuestions": _conversation_json_list(row["suggested_questions_json"], 3),
        "webSearch": _conversation_json_object(
            _conversation_optional_column(row, "web_search_json", "{}")
        ),
        "totalTokens": int(row["total_tokens"] or 0),
    } for row in rows]
    _audit(
        actor,
        "conversation.detail.read",
        conversation_id,
        meta={"turnCount": len(turns), "mediaAvailable": media_available},
    )
    return jsonify({
        "status": "success",
        "conversation": {
            "id": conversation_id,
            "user": _conversation_user_payload(first, directory, actor),
            "startedAt": _conversation_time(first["created_at"]),
            "updatedAt": _conversation_time(rows[-1]["completed_at"]),
            "fileName": first["file_name"] or "",
            "mediaType": first["media_type"] or "",
            "mediaAvailable": media_available,
            "mediaUrl": (
                f"/api/admin/conversations/{quote(conversation_id, safe='')}/media"
                if include_media and media_available else ""
            ),
            "turns": turns,
        },
    })


@admin_blueprint.route("/api/admin/conversations/<conversation_id>/media")
def admin_conversation_media(conversation_id):
    actor, error = _admin_required("conversation.read_media")
    if error:
        return error
    if len(conversation_id) > 128:
        return jsonify({"status": "error", "message": "对话编号无效"}), 400
    try:
        with _v2_conversation_connection() as connection:
            binding = connection.execute(
                """
                SELECT conversation_id, developer_user_id, developer_account_uuid,
                       task_id, report_id, legacy_detection_id, media_type
                FROM report_qa_turns
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            history = None
            if binding and binding["task_id"]:
                history = connection.execute(
                    """
                    SELECT task_id, report_id, file_type, result_json, thumbnail,
                           developer_user_id, developer_account_uuid
                    FROM history
                    WHERE task_id = ? OR report_id = ?
                    LIMIT 1
                    """,
                    (binding["task_id"], binding["report_id"] or ""),
                ).fetchone()
    except (OSError, sqlite3.Error) as exc:
        print(f"[ADMIN CONVERSATION MEDIA ERROR] {type(exc).__name__}: {exc}")
        return jsonify({"status": "error", "message": "对话媒体暂时无法读取"}), 503
    if not binding:
        return jsonify({"status": "error", "message": "对话不存在"}), 404

    if history:
        account_uuid = str(binding["developer_account_uuid"] or "")
        user_id = str(binding["developer_user_id"] or "")
        if account_uuid and account_uuid != str(history["developer_account_uuid"] or ""):
            return jsonify({"status": "error", "message": "图片不存在"}), 404
        if not account_uuid and user_id and user_id != str(history["developer_user_id"] or ""):
            return jsonify({"status": "error", "message": "图片不存在"}), 404
        try:
            result = json.loads(history["result_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            result = {}
        file_meta = result.get("fileMeta") if isinstance(result.get("fileMeta"), dict) else {}
        data_uri = str(file_meta.get("preview") or file_meta.get("thumbnail") or history["thumbnail"] or "")
        match = re.fullmatch(
            r"data:(image/(?:png|jpeg|webp|gif|avif));base64,([A-Za-z0-9+/=\r\n]+)",
            data_uri,
            re.IGNORECASE,
        )
        if match and len(match.group(2)) <= 16 * 1024 * 1024:
            try:
                content = base64.b64decode(match.group(2), validate=True)
            except (ValueError, binascii.Error):
                content = b""
            if content:
                _audit(actor, "conversation.media.read", conversation_id, meta={"source": "v2_preview"})
                return Response(content, mimetype=match.group(1), headers={"Cache-Control": "private, no-store"})

    legacy_detection_id = binding["legacy_detection_id"]
    media_type = str(binding["media_type"] or "").lower()
    if not legacy_detection_id or media_type not in {"image", "video"}:
        return jsonify({"status": "error", "message": "图片不存在"}), 404
    directory = _conversation_user_directory([binding])
    owner = _conversation_user_for_row(binding, directory)
    history_where, history_params = detection_owner_where(
        owner.get("phone"),
        owner.get("openid"),
        account_uuid=owner.get("account_uuid"),
        require_account_uuid=True,
    )
    table = "data" if media_type == "image" else "video_data"
    rows = excute_detection_sql(
        f"SELECT * FROM {table} WHERE itemid = %s AND ({history_where}) LIMIT 1",
        (legacy_detection_id, *history_params),
    ) or []
    if not rows:
        return jsonify({"status": "error", "message": "图片不存在"}), 404
    from imagedetection.views.api import _serve_detection_media_item
    _audit(actor, "conversation.media.read", conversation_id, meta={"source": "legacy_detection"})
    return _serve_detection_media_item(media_type, rows[0])


@admin_blueprint.route("/api/admin/detections")
def admin_detections():
    actor, error = _admin_required("detection.view")
    if error:
        return error
    limit = _limit_arg(80, 200)
    cursor = _cursor_arg()
    query = _search_term()
    label = str(request.args.get("label") or "").strip()
    feedback_filter = str(request.args.get("feedback") or "").strip().lower()
    filters = [INTERNAL_TEST_OWNER_SQL]
    params = []
    if query:
        like = f"%{query}%"
        filters.append("(phone LIKE %s OR filename LIKE %s OR aigc LIKE %s)")
        params.extend([like, like, like])
    if label:
        filters.append("aigc = %s")
        params.append(label)
    if feedback_filter == "positive":
        values = sorted(_POSITIVE_FEEDBACK_VALUES)
        filters.append(f"LOWER(TRIM(CAST(feedback AS CHAR))) IN ({','.join(['%s'] * len(values))})")
        params.extend(values)
    elif feedback_filter == "negative":
        values = sorted(_NEGATIVE_FEEDBACK_VALUES)
        filters.append(f"LOWER(TRIM(CAST(feedback AS CHAR))) IN ({','.join(['%s'] * len(values))})")
        params.extend(values)
    elif feedback_filter == "unrated":
        filters.append("(feedback IS NULL OR TRIM(CAST(feedback AS CHAR)) IN ('', '0'))")
    if cursor:
        filters.append("itemid < %s")
        params.append(cursor)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit + 1)
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
    include_pii = _has_admin_permission(actor, "detection.read_pii")
    items = []
    for row in rows:
        run = route_runs.get(str(row.get("itemid")))
        items.append({
            "id": row.get("itemid"),
            "createdAt": format_createtime(row.get("createtime")),
            "filename": row.get("filename"),
            "phone": row.get("phone") if include_pii else _mask_phone(row.get("phone")),
            "feedback": _normalize_detection_feedback(row.get("feedback")),
            "feedbackLabel": _detection_feedback_label(row.get("feedback")),
            "adminReview": _normalize_detection_feedback(row.get("admin_review")),
            "adminReviewLabel": _admin_review_label(row.get("admin_review")),
            "modelRoute": _model_run_for_admin(run, actor),
            **_authorized_detection_view(row, run),
        })
    items, page = _page_payload(items, limit)
    return jsonify({"status": "success", "detections": items, "page": page})


@admin_blueprint.route("/api/admin/legacy-history/<record_type>/<int:itemid>")
def admin_legacy_history_preview(record_type, itemid):
    actor, error = _admin_required("legacy_history.view")
    if error:
        return error
    try:
        preview = legacy_record_preview(record_type, itemid)
    except LegacyGovernanceError as exc:
        return _legacy_governance_error(exc)
    _audit(
        actor,
        "legacy_history.preview",
        f"{record_type}:{itemid}",
        meta={"fingerprint": preview.get("fingerprint"), "unowned": preview.get("unowned")},
    )
    return jsonify({"status": "success", "record": preview})


@admin_blueprint.route("/api/admin/legacy-history/target-account/<int:user_id>")
def admin_legacy_target_account(user_id):
    actor, error = _admin_required("legacy_history.view")
    if error:
        return error
    rows = excute_sql(
        "SELECT Userid, account_uuid, username, phone FROM `user` WHERE Userid = %s LIMIT 1",
        (user_id,),
    ) or []
    if not rows:
        return jsonify({"status": "error", "message": "目标账号不存在"}), 404
    row = rows[0]
    return jsonify({
        "status": "success",
        "account": {
            "id": row.get("Userid"),
            "accountUuid": row.get("account_uuid"),
            "username": row.get("username") or "",
            "phone": _mask_phone(row.get("phone")),
        },
    })


@admin_blueprint.route("/api/admin/legacy-history/claims", methods=["GET", "POST"])
def admin_legacy_history_claims():
    permission = "legacy_history.request" if request.method == "POST" else "legacy_history.view"
    actor, error = _admin_required(permission)
    if error:
        return error
    try:
        if request.method == "GET":
            claims = list_legacy_record_claims(
                request.args.get("status") or "claim_pending",
                _limit_arg(50, 200),
            )
            return jsonify({"status": "success", "claims": claims})

        admin_id, username, identity = _named_admin_actor(actor)
        payload = request.get_json(silent=True) or {}
        claim = request_legacy_record_claim(
            payload.get("recordType"),
            payload.get("itemid"),
            payload.get("accountUuid"),
            payload.get("expectedFingerprint"),
            payload.get("expectedMediaSha256"),
            payload.get("evidenceReference"),
            payload.get("evidenceSha256"),
            payload.get("reason"),
            payload.get("operationId"),
            admin_id,
            username,
            identity,
        )
    except LegacyGovernanceError as exc:
        return _legacy_governance_error(exc)
    _audit(
        actor,
        "legacy_history.claim.request",
        f"{claim.get('recordType')}:{claim.get('recordId')}",
        after={"claimId": claim.get("id"), "status": claim.get("status"), "version": claim.get("version")},
        meta={"evidenceSha256": claim.get("evidenceSha256")},
    )
    return jsonify({"status": "success", "claim": claim}), 201


@admin_blueprint.route("/api/admin/legacy-history/claims/<int:claim_id>/approve", methods=["POST"])
def admin_legacy_history_claim_approve(claim_id):
    actor, error = _admin_required("legacy_history.approve")
    if error:
        return error
    try:
        admin_id, username, identity = _named_admin_actor(actor)
        payload = request.get_json(silent=True) or {}
        claim = approve_legacy_record_claim(
            claim_id,
            payload.get("approvalOperationId"),
            payload.get("expectedVersion"),
            admin_id,
            username,
            identity,
        )
    except LegacyGovernanceError as exc:
        return _legacy_governance_error(exc)
    _audit(
        actor,
        "legacy_history.claim.approve",
        f"{claim.get('recordType')}:{claim.get('recordId')}",
        after={"claimId": claim.get("id"), "status": claim.get("status"), "version": claim.get("version")},
    )
    return jsonify({"status": "success", "claim": claim})


@admin_blueprint.route("/api/admin/legacy-history/claims/<int:claim_id>/reject", methods=["POST"])
def admin_legacy_history_claim_reject(claim_id):
    actor, error = _admin_required("legacy_history.approve")
    if error:
        return error
    try:
        admin_id, username, identity = _named_admin_actor(actor)
        payload = request.get_json(silent=True) or {}
        claim = reject_legacy_record_claim(
            claim_id,
            payload.get("rejectionOperationId"),
            payload.get("expectedVersion"),
            payload.get("reason"),
            admin_id,
            username,
            identity,
        )
    except LegacyGovernanceError as exc:
        return _legacy_governance_error(exc)
    _audit(
        actor,
        "legacy_history.claim.reject",
        f"{claim.get('recordType')}:{claim.get('recordId')}",
        after={"claimId": claim.get("id"), "status": claim.get("status"), "version": claim.get("version")},
    )
    return jsonify({"status": "success", "claim": claim})


@admin_blueprint.route("/api/admin/detections/<int:itemid>")
def admin_detection_detail(itemid):
    actor, error = _admin_required("detection.view")
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
    run = route_runs.get(str(itemid))
    include_pii = _has_admin_permission(actor, "detection.read_pii")
    item = {
        "id": row.get("itemid"),
        "userId": row.get("Userid"),
        "openid": row.get("openid") if include_pii else _mask_identifier(row.get("openid")),
        "createdAt": format_createtime(row.get("createtime")),
        "filename": row.get("filename"),
        "phone": row.get("phone") if include_pii else _mask_phone(row.get("phone")),
        "feedback": _normalize_detection_feedback(row.get("feedback")),
        "feedbackLabel": _detection_feedback_label(row.get("feedback")),
        "adminReview": _normalize_detection_feedback(row.get("admin_review")),
        "adminReviewLabel": _admin_review_label(row.get("admin_review")),
        "explanation": row.get("explantation") or "",
        "metadata": metadata if include_pii else _redact_metadata(metadata),
        "modelRoute": _model_run_for_admin(run, actor),
        **_authorized_detection_view(row, run),
    }
    _audit(actor, "detection.detail.read", str(itemid), meta={"piiIncluded": include_pii})
    return jsonify({"status": "success", "detection": item})


@admin_blueprint.route("/api/admin/detections/export")
def admin_detections_export():
    actor, error = _admin_required("data.export")
    if error:
        return error
    if not _has_admin_permission(actor, "detection.view"):
        return _permission_error("detection.view")
    select_clause = _detection_data_select_clause()
    rows = excute_detection_sql(
        f"""
        SELECT {select_clause}
        FROM data
        WHERE {INTERNAL_TEST_OWNER_SQL}
        ORDER BY itemid DESC
        LIMIT 5000
        """
    ) or []
    route_runs = admin_state.model_runs_by_itemids([row.get("itemid") for row in rows])
    include_pii = _has_admin_permission(actor, "detection.read_pii")
    _audit(actor, "detections.export", "detections", meta={"count": len(rows), "piiIncluded": include_pii})
    export_rows = []
    for row in rows:
        run = route_runs.get(str(row.get("itemid")))
        view = _authorized_detection_view(row, run)
        export_rows.append([
            row.get("itemid"),
            format_createtime(row.get("createtime")),
            row.get("phone") if include_pii else _mask_phone(row.get("phone")),
            row.get("filename"),
            view["label"],
            view["probability"],
            view["detectorProbability"],
            view["confidence"],
            _detection_feedback_label(row.get("feedback")),
            _admin_review_label(row.get("admin_review")),
            (run or {}).get("model", {}).get("id", ""),
        ])
    return _csv_response(
        "realguard-detections.csv",
        ["ID", "Created At", "Phone", "Filename", "Label", "Probability", "Detector Probability", "Confidence", "User Feedback", "Admin Review", "Model Route"],
        export_rows,
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
    if "admin_review" not in _detection_data_columns():
        return jsonify({"status": "error", "message": "管理员复核字段尚未完成数据库迁移"}), 503
    before = excute_detection_sql("SELECT itemid, admin_review FROM data WHERE itemid = %s LIMIT 1", (itemid,)) or []
    db_value = feedback
    updated = excute_detection_sql(
        "UPDATE data SET admin_review = %s WHERE itemid = %s",
        (db_value, itemid),
        fetch=False,
    )
    if updated is None:
        return jsonify({"status": "error", "message": "检测记录更新失败"}), 500
    if updated == 0:
        return jsonify({"status": "error", "message": "检测记录不存在"}), 404
    after = {"itemid": itemid, "adminReview": db_value}
    _audit(user, "detection.review", str(itemid), before=before[0] if before else None, after=after)
    return jsonify({"status": "success", "review": after})


@admin_blueprint.route("/api/admin/api-keys")
def admin_api_keys():
    actor, error = _admin_required("api_key.view")
    if error:
        return error
    include_pii = _has_admin_permission(actor, "user.read_pii")
    limit = _limit_arg(80, 200)
    cursor = _cursor_arg()
    query = _search_term()
    filters = []
    params = []
    if query:
        like = f"%{query}%"
        query_fields = ["k.name LIKE %s", "k.key_last4 LIKE %s"]
        params.extend([like, like])
        if include_pii:
            query_fields.extend(["u.username LIKE %s", "u.phone LIKE %s"])
            params.extend([like, like])
        filters.append(f"({' OR '.join(query_fields)})")
    if cursor:
        filters.append("k.id < %s")
        params.append(cursor)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit + 1)
    rows = excute_sql(
        f"""
        SELECT
            k.id, k.user_id, k.name, k.key_prefix, k.key_last4, k.scopes, k.status,
            k.created_at, k.last_used_at, k.revoked_at, k.last_used_ip,
            u.phone, u.username
        FROM developer_api_keys k
        LEFT JOIN user u ON u.Userid = k.user_id
        {where}
        ORDER BY k.id DESC
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
            "owner": _api_key_owner_label(row.get("username"), row.get("phone"), include_pii),
            "phone": (row.get("phone") or "") if include_pii else _mask_phone(row.get("phone")),
            "name": row.get("name"),
            "masked": f"{row.get('key_prefix') or 'rg_sk_'}...{row.get('key_last4') or '****'}",
            "scopes": row.get("scopes") or "",
            "status": row.get("status") or "",
            "createdAt": format_createtime(row.get("created_at")),
            "lastUsedAt": format_createtime(row.get("last_used_at")),
            "revokedAt": format_createtime(row.get("revoked_at")),
            "lastUsedIp": (row.get("last_used_ip") or "") if include_pii else _mask_identifier(row.get("last_used_ip")),
            "quota": admin_state.get_api_key_quota(row.get("id")),
        })
    keys, page = _page_payload(keys, limit)
    return jsonify({"status": "success", "keys": keys, "page": page})


@admin_blueprint.route("/api/admin/api-keys/<int:key_id>/quota", methods=["PATCH", "POST"])
def admin_api_key_quota(key_id):
    # Imported lazily to avoid the admin/developer blueprint import cycle.
    from imagedetection.views.developer_platform import admin_update_request_quota

    return admin_update_request_quota(key_id)
