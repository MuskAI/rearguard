import hashlib
import hmac
import io
import os
import secrets
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image
from flask import Blueprint, Response, jsonify, make_response, request, send_file, session, stream_with_context

from imagedetection.views.detection import image_detect_for_actor
from imagedetection.views.historical_record import DETECTION_BACKEND_BASE_URL
from imagedetection.views.login import (
    _authenticate_password_user,
    _find_user_by_phone,
    _hash_password,
    _is_valid_phone,
    _sync_detection_user,
    _verify_sms_code,
)
from imagedetection.views.utils import excute_detection_sql, excute_sql, excute_sql_lastid, format_createtime


api_blueprint = Blueprint("api_blueprint", __name__, url_prefix="/api")
THUMBNAIL_CACHE_DIR = Path(os.environ.get("REALGUARD_THUMBNAIL_CACHE_DIR", "/tmp/realguard-thumbnails"))
THUMBNAIL_MAX_SIZE = (
    int(os.environ.get("REALGUARD_THUMBNAIL_MAX_WIDTH", "220")),
    int(os.environ.get("REALGUARD_THUMBNAIL_MAX_HEIGHT", "165")),
)
THUMBNAIL_QUALITY = int(os.environ.get("REALGUARD_THUMBNAIL_QUALITY", "45"))
DEVELOPER_API_KEY_PREFIX = "rg_sk_"
DEVELOPER_API_KEY_MAX_ACTIVE = int(os.environ.get("REALGUARD_DEVELOPER_API_KEY_MAX_ACTIVE", "5"))
DEVELOPER_API_KEY_DEFAULT_SCOPES = "detect,forensics,provenance,reports"
DEVELOPER_AUTH_SECRET = os.environ.get("REALGUARD_DEVELOPER_AUTH_SECRET", "").strip()
DEVELOPER_USAGE_URL = os.environ.get(
    "REALGUARD_DEVELOPER_USAGE_URL",
    "http://127.0.0.1:8848/api/developer/token-usage",
).strip()
TERMS_VERSION = os.environ.get("REALGUARD_TERMS_VERSION", "2026-06-03")
PASSWORD_MIN_LENGTH = int(os.environ.get("REALGUARD_PASSWORD_MIN_LENGTH", "8"))
_DEVELOPER_KEY_TABLE_READY = False
_DEVELOPER_USAGE_TABLE_READY = False
_USER_ACCOUNT_COLUMNS_READY = False


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
        ("created_at", "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'"),
        ("terms_version", "VARCHAR(32) NULL COMMENT '用户协议版本'"),
        ("terms_accepted_at", "DATETIME NULL COMMENT '用户协议同意时间'"),
        ("password_updated_at", "DATETIME NULL COMMENT '密码更新时间'"),
    ]
    for column, definition in columns:
        if not _ensure_column("user", column, definition):
            return False
    _USER_ACCOUNT_COLUMNS_READY = True
    return True


def _set_session_user(user, phone):
    _sync_detection_user(phone, user.get("username") or phone, user.get("openid", "") or phone)
    session.clear()
    session.permanent = True
    session["user_info"] = {
        "Userid": user["Userid"],
        "username": user.get("username") or phone,
        "phone": phone,
        "openid": user.get("openid", ""),
    }
    return session["user_info"]


def _record_terms_acceptance(phone):
    if not _ensure_user_account_columns():
        return False
    result = excute_sql(
        "UPDATE user SET terms_version = %s, terms_accepted_at = NOW() WHERE phone = %s",
        (TERMS_VERSION, phone),
        fetch=False,
    )
    return result is not None


def _developer_key_hash(api_key):
    return hashlib.sha256(f"realguard-developer-api-key:{api_key}".encode("utf-8")).hexdigest()


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
    }


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
          scopes VARCHAR(255) NOT NULL DEFAULT 'detect,forensics,provenance,reports',
          status VARCHAR(16) NOT NULL DEFAULT 'active',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          last_used_at DATETIME NULL,
          revoked_at DATETIME NULL,
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
    _DEVELOPER_KEY_TABLE_READY = result is not None
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
          user_id INT NOT NULL,
          key_id BIGINT NULL,
          pipeline VARCHAR(32) NOT NULL,
          endpoint VARCHAR(160) NOT NULL,
          model_version VARCHAR(120) NULL,
          status_code INT NOT NULL DEFAULT 200,
          prompt_tokens INT NOT NULL DEFAULT 0,
          completion_tokens INT NOT NULL DEFAULT 0,
          total_tokens INT NOT NULL DEFAULT 0,
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (id),
          KEY idx_developer_usage_user_created (user_id, created_at),
          KEY idx_developer_usage_key_created (key_id, created_at),
          KEY idx_developer_usage_pipeline_created (pipeline, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        fetch=False,
    )
    _DEVELOPER_USAGE_TABLE_READY = result is not None
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
        SELECT k.id, k.user_id, k.name, k.scopes, k.status, u.phone, u.username, u.openid
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
    if touch:
        excute_sql(
            """
            UPDATE developer_api_keys
            SET last_used_at = NOW(), last_used_ip = %s
            WHERE id = %s
            """,
            (request.headers.get("x-forwarded-for", request.remote_addr or "")[:64], row["id"]),
            fetch=False,
        )
    return row, None


def _developer_key_required():
    row, error = _active_developer_key(_developer_key_from_request())
    if error:
        return None, (jsonify({"status": "error", "message": error}), 500)
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
):
    if not actor or not _ensure_developer_usage_table():
        return False
    result = excute_sql(
        """
        INSERT INTO developer_usage_events
          (user_id, key_id, pipeline, endpoint, model_version, status_code,
           prompt_tokens, completion_tokens, total_tokens)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            actor.get("user_id"),
            actor.get("id"),
            pipeline,
            endpoint,
            model_version,
            int(status_code),
            _usage_int(prompt_tokens),
            _usage_int(completion_tokens),
            _usage_int(total_tokens),
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
               prompt_tokens, completion_tokens, total_tokens
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
        summary["billableRequests"] += 1
        summary["promptTokens"] += prompt_tokens
        summary["completionTokens"] += completion_tokens
        summary["totalTokens"] += total_tokens
        summary["lastEventAt"] = created_text
        for bucket in (by_day[day], by_endpoint[endpoint], by_model[model], by_key[key]):
            bucket["requests"] += 1
            bucket["billableRequests"] += 1
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
    clauses = []
    params = []
    user_id = (actor or {}).get("user_id")
    if user_id not in (None, ""):
        clauses.append("(Userid = %s)")
        params.append(user_id)
    phone = str((actor or {}).get("phone") or "").strip()
    if phone:
        clauses.append("(Userid IS NULL AND phone = %s)")
        params.append(phone)
    openid = str((actor or {}).get("openid") or "").strip()
    if openid:
        clauses.append("(Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s)")
        params.append(openid)
    if not clauses:
        return "1 = 0", ()
    return " OR ".join(clauses), tuple(params)


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


def _image_history_record(item):
    fake_pct = round(float(item.get("fake", 0) or 0), 1)
    issue_count = _history_visual_issue_count(item)
    return {
        "itemid": item.get("itemid"),
        "filename": item.get("filename", ""),
        "image_url": f"/api/media/image/{item.get('itemid')}",
        "thumbnail_url": _thumbnail_url(item),
        "real_prob": round(100 - fake_pct, 1),
        "fake_prob": fake_pct,
        "final_label": "AI生成图像" if fake_pct >= 50 else "真实图像",
        "confidence": item.get("clarity", ""),
        "createtime": format_createtime(item.get("createtime", "")),
        "report_url": f"/image_upload/report?itemid={item.get('itemid')}",
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
    fake_pct = round(float(item.get("fake") or item.get("fake_percentage") or 0), 1)
    return {
        "itemid": item.get("itemid"),
        "filename": item.get("filename", ""),
        "video_url": f"/api/media/video/{item.get('itemid')}",
        "real_percentage": round(100 - fake_pct, 1),
        "fake_percentage": fake_pct,
        "final_label": item.get("final_label", ""),
        "confidence": item.get("confidence") or item.get("confidence_level", ""),
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
        "AI结论" if "AI" in str(record.get("final_label") or "") else "",
        "真实结论" if "真实" in str(record.get("final_label") or "") else "",
        "结论",
        "置信度",
    ]


def _video_history_matches_filter(record, filter_key):
    if filter_key == "guest":
        return bool(record.get("is_guest_record"))
    if filter_key == "ai":
        return "AI" in str(record.get("final_label") or "")
    if filter_key == "real":
        return "真实" in str(record.get("final_label") or "")
    return True


@api_blueprint.route("/me")
def me():
    user, error = _auth_required()
    if error:
        return error

    phone = user.get("phone", "")
    actor = {
        "user_id": user.get("Userid") or user.get("userId") or user.get("id"),
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

    return jsonify({"status": "success", "user": user, "counters": counters})


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

    user = _authenticate_password_user(phone, secret)
    if not user:
        return jsonify({"status": "error", "message": "手机号或密码错误"}), 401
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
    if not user:
        return jsonify({"status": "error", "message": "该手机号尚未注册，请先注册"}), 404
    if not _record_terms_acceptance(phone):
        return jsonify({"status": "error", "message": "协议确认记录失败，请稍后重试"}), 500

    return jsonify({"status": "success", "user": _set_session_user(user, phone)})


@api_blueprint.route("/register", methods=["POST"])
def register():
    payload = request.get_json(silent=True) or request.form
    phone = (payload.get("phone") or "").strip()
    secret = (payload.get("secret") or "").strip()
    username = (payload.get("username") or "").strip() or phone
    sms_code = (payload.get("sms_code") or "").strip()
    accepted_terms = _truthy(payload.get("accepted_terms") or payload.get("acceptedTerms"))

    if not _is_valid_phone(phone) or not secret:
        return jsonify({"status": "error", "message": "请输入正确的手机号和密码"}), 400
    password_error = _password_policy_error(secret)
    if password_error:
        return jsonify({"status": "error", "message": password_error}), 400
    if len(username) > 128:
        return jsonify({"status": "error", "message": "用户名不能超过 128 个字符"}), 400
    if not accepted_terms:
        return jsonify({"status": "error", "message": "请先阅读并同意用户协议和隐私政策"}), 400
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
            (phone, secret, username, openid, terms_version, terms_accepted_at, password_updated_at)
        VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
        """,
        (phone, _hash_password(secret), username, "", TERMS_VERSION),
        fetch=False,
    )
    if not affected:
        return jsonify({"status": "error", "message": "注册失败，请重试"}), 500

    _sync_detection_user(phone, username, phone)
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
        "UPDATE user SET secret = %s, password_updated_at = NOW() WHERE phone = %s",
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
        SELECT id, name, key_prefix, key_last4, scopes, status, created_at, last_used_at, revoked_at
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
    name = str(payload.get("name") or "").strip() or "Default key"
    if len(name) > 120:
        return jsonify({"status": "error", "message": "Key 名称不能超过 120 个字符"}), 400

    rows = excute_sql(
        "SELECT COUNT(*) AS cnt FROM developer_api_keys WHERE user_id = %s AND status = 'active'",
        (user["Userid"],),
    )
    active_count = int((rows or [{}])[0].get("cnt") or 0)
    if active_count >= DEVELOPER_API_KEY_MAX_ACTIVE:
        return jsonify({"status": "error", "message": f"最多只能保留 {DEVELOPER_API_KEY_MAX_ACTIVE} 个 active API Key"}), 400

    api_key = f"{DEVELOPER_API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
    row_id = excute_sql_lastid(
        """
        INSERT INTO developer_api_keys
            (user_id, name, key_hash, key_prefix, key_last4, scopes, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'active')
        """,
        (
            user["Userid"],
            name,
            _developer_key_hash(api_key),
            DEVELOPER_API_KEY_PREFIX,
            api_key[-4:],
            DEVELOPER_API_KEY_DEFAULT_SCOPES,
        ),
    )
    if not row_id:
        return jsonify({"status": "error", "message": "创建 API Key 失败，请稍后重试"}), 500

    rows = excute_sql(
        """
        SELECT id, name, key_prefix, key_last4, scopes, status, created_at, last_used_at, revoked_at
        FROM developer_api_keys
        WHERE id = %s AND user_id = %s
        LIMIT 1
        """,
        (row_id, user["Userid"]),
    )
    key_payload = _developer_key_payload((rows or [{}])[0])
    return jsonify({"status": "success", "apiKey": api_key, "key": key_payload})


@api_blueprint.route("/developer/keys/<int:key_id>", methods=["DELETE"])
def revoke_developer_api_key(key_id):
    user, error = _auth_required()
    if error:
        return error
    if not _ensure_developer_api_key_table():
        return jsonify({"status": "error", "message": "API Key 存储初始化失败"}), 500

    affected = excute_sql(
        """
        UPDATE developer_api_keys
        SET status = 'revoked', revoked_at = NOW()
        WHERE id = %s AND user_id = %s AND status = 'active'
        """,
        (key_id, user["Userid"]),
        fetch=False,
    )
    if affected is None:
        return jsonify({"status": "error", "message": "撤销 API Key 失败"}), 500
    if affected == 0:
        return jsonify({"status": "error", "message": "API Key 不存在或已撤销"}), 404
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
        "user": {
            "phone": row.get("phone") or "",
            "username": row.get("username") or "",
        },
        "scopes": _developer_scopes(row.get("scopes")),
    })


@api_blueprint.route("/developer/v1/detect", methods=["POST"])
def developer_v1_detect():
    actor, error = _developer_key_required()
    if error:
        return error
    user_info = {
        "Userid": actor.get("user_id"),
        "username": actor.get("username") or actor.get("phone") or "developer",
        "phone": actor.get("phone") or "",
        "openid": actor.get("openid") or actor.get("phone") or f"developer-{actor.get('user_id')}",
    }
    response = make_response(image_detect_for_actor(user_info, is_guest=False))
    _record_developer_usage_event(
        actor,
        pipeline="v1",
        endpoint="/api/developer/v1/detect",
        model_version="realguard-v1-image",
        status_code=response.status_code,
    )
    return response


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
            "SELECT * FROM data WHERE Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s ORDER BY createtime DESC",
            (actor["openid"],),
        )
    elif actor["mode"] == "anonymous":
        rows = []
    else:
        history_where, history_params = _history_actor_where(actor)
        rows = excute_detection_sql(
            f"SELECT * FROM data WHERE {history_where} ORDER BY createtime DESC",
            history_params,
        )
    query_records = []
    for item in rows or []:
        record = _image_history_record(item)
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

    item = rows[0]
    filename = str(item.get("filename") or "").strip()
    folder = str(item.get("openid") or item.get("phone") or "guest").strip()
    if not filename or not folder:
        return jsonify({"status": "error", "message": "媒体不存在"}), 404

    media_root = (Path(__file__).resolve().parents[1] / "static" / "uploads").resolve()
    local_path = (media_root / folder / kind / filename).resolve()
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


@api_blueprint.route("/media/image/<int:itemid>")
def image_detection_media(itemid):
    return _serve_owned_media("image", itemid)


@api_blueprint.route("/media/video/<int:itemid>")
def video_detection_media(itemid):
    return _serve_owned_media("video", itemid)


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
    if filter_key not in {"all", "guest", "ai", "real"}:
        return jsonify({"status": "error", "message": "filter 不受支持"}), 400

    if actor["mode"] == "guest":
        rows = excute_detection_sql(
            "SELECT * FROM video_data WHERE Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s ORDER BY createtime DESC",
            (actor["openid"],),
        )
    elif actor["mode"] == "anonymous":
        rows = []
    else:
        history_where, history_params = _history_actor_where(actor)
        rows = excute_detection_sql(
            f"SELECT * FROM video_data WHERE {history_where} ORDER BY createtime DESC",
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
        "ai": sum(1 for record in query_records if "AI" in str(record["final_label"] or "")),
        "real": sum(1 for record in query_records if "真实" in str(record["final_label"] or "")),
    }
    records = [record for record in query_records if _video_history_matches_filter(record, filter_key)]
    return jsonify({"status": "success", "records": records[offset: offset + limit], "total": len(records), "filter_counts": filter_counts})
