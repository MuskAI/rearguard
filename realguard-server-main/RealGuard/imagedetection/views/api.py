import hashlib
import hmac
import io
import json
import os
import secrets
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image
from flask import Blueprint, jsonify, redirect, request, send_file, session

from imagedetection.views.historical_record import DETECTION_BACKEND_BASE_URL, _detection_static_url
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
_DEVELOPER_KEY_TABLE_READY = False


def _current_user():
    user = session.get("user_info")
    return user if isinstance(user, dict) else None


def _auth_required():
    user = _current_user()
    if not user:
        return None, (jsonify({"status": "error", "message": "用户未登录"}), 401)
    return user, None


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


def _history_identity(allow_empty=False):
    user = _current_user()
    if user:
        return {
            "mode": "user",
            "phone": str(user.get("phone") or "").strip(),
            "openid": str(user.get("openid") or "").strip(),
        }, None
    guest_openid = str(session.get("guest_openid") or "").strip()
    if guest_openid:
        return {"mode": "guest", "phone": "", "openid": guest_openid}, None
    if allow_empty:
        return {"mode": "anonymous", "phone": "", "openid": ""}, None
    return None, (jsonify({"status": "error", "message": "用户未登录"}), 401)


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
        "image_url": _detection_static_url("image", item),
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
        "video_url": _detection_static_url("video", item),
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


def _retrieval_history_record(item, phone, search_type):
    result_count = int(item.get("result_count") or 0)
    results = json.loads(item.get("results_json") or "[]")
    top_result = results[0] if results else {}
    top_result_id = str(top_result.get("id") or "")
    top_result_library = top_result_id.split("/", 1)[0] if "/" in top_result_id else ""
    return {
        "itemid": item.get("itemid"),
        "filename": item.get("filename", ""),
        "file_url": f"/static/uploads/{phone}/retrieve/{item.get('filename', '')}",
        "search_type": search_type,
        "result_count": result_count,
        "top_k": item.get("top_k", 10),
        "file_size": item.get("file_size", ""),
        "createtime": format_createtime(item.get("createtime", "")),
        "results": results,
        "report_url": f"/history_retrieve/report?itemid={item.get('itemid')}",
        "has_hits": result_count > 0,
        "top_result_id": top_result_id,
        "top_result_library": top_result_library,
        "top_result_score": round(float(top_result.get("score", 0) or 0), 4) if top_result else 0,
    }


def _retrieval_history_search_fields(record):
    return [
        record.get("filename", ""),
        record.get("createtime", ""),
        record.get("result_count", ""),
        record.get("top_k", ""),
        record.get("search_type", ""),
        record.get("top_result_id", ""),
        record.get("top_result_library", ""),
        record.get("top_result_score", ""),
        "有命中" if record.get("has_hits") else "无命中",
        "图像检索" if record.get("search_type") == "image" else "视频检索",
        f"命中库 {record.get('top_result_library', '')}" if record.get("top_result_library") else "",
        f"检索库 {record.get('top_result_library', '')}" if record.get("top_result_library") else "",
        "首个命中" if record.get("top_result_id") else "",
        "命中库" if record.get("top_result_library") else "",
        "最高分" if record.get("top_result_id") else "",
        "数量",
        "Top-K",
    ]


def _retrieval_history_matches_filter(record, filter_key):
    result_count = int(record.get("result_count") or 0)
    if filter_key == "hits":
        return result_count > 0
    if filter_key == "empty":
        return result_count <= 0
    return True


@api_blueprint.route("/me")
def me():
    user, error = _auth_required()
    if error:
        return error

    phone = user.get("phone", "")
    counters = {
        "image_detect": 0,
        "video_detect": 0,
        "image_retrieve": 0,
        "video_retrieve": 0,
    }

    rows = excute_detection_sql("SELECT COUNT(*) AS cnt FROM data WHERE phone = %s", (phone,))
    if rows:
        counters["image_detect"] = rows[0].get("cnt", 0)
    rows = excute_detection_sql("SELECT COUNT(*) AS cnt FROM video_data WHERE phone = %s", (phone,))
    if rows:
        counters["video_detect"] = rows[0].get("cnt", 0)
    rows = excute_sql(
        "SELECT search_type, COUNT(*) AS cnt FROM retrieve_data WHERE phone = %s GROUP BY search_type",
        (phone,),
    )
    for row in rows or []:
        if row.get("search_type") == "image":
            counters["image_retrieve"] = row.get("cnt", 0)
        elif row.get("search_type") == "video":
            counters["video_retrieve"] = row.get("cnt", 0)

    return jsonify({"status": "success", "user": user, "counters": counters})


@api_blueprint.route("/login/password", methods=["POST"])
def login_password():
    payload = request.get_json(silent=True) or request.form
    phone = (payload.get("phone") or "").strip()
    secret = (payload.get("secret") or "").strip()

    if not phone or not secret:
        return jsonify({"status": "error", "message": "请输入手机号和密码"}), 400

    user = _authenticate_password_user(phone, secret)
    if not user:
        return jsonify({"status": "error", "message": "手机号或密码错误"}), 401

    _sync_detection_user(phone, user.get("username") or phone, user.get("openid", "") or phone)
    session.permanent = True
    session["user_info"] = {
        "Userid": user["Userid"],
        "username": user.get("username") or phone,
        "phone": phone,
        "openid": user.get("openid", ""),
    }
    return jsonify({"status": "success", "user": session["user_info"]})


@api_blueprint.route("/login/sms", methods=["POST"])
def login_sms():
    payload = request.get_json(silent=True) or request.form
    phone = (payload.get("phone") or "").strip()
    sms_code = (payload.get("sms_code") or "").strip()

    if not _is_valid_phone(phone):
        return jsonify({"status": "error", "message": "请输入正确的手机号"}), 400
    ok, message = _verify_sms_code("login", phone, sms_code)
    if not ok:
        return jsonify({"status": "error", "message": message}), 400

    user = _find_user_by_phone(phone)
    if not user:
        affected = excute_sql(
            "INSERT INTO user (phone, secret, username, openid) VALUES (%s, %s, %s, %s)",
            (phone, _hash_password(secrets.token_urlsafe(24)), phone, ""),
            fetch=False,
        )
        if not affected:
            return jsonify({"status": "error", "message": "自动创建账号失败，请稍后重试"}), 500
        user = _find_user_by_phone(phone)
        if not user:
            return jsonify({"status": "error", "message": "自动创建账号失败，请稍后重试"}), 500

    _sync_detection_user(phone, user.get("username") or phone, user.get("openid", "") or phone)
    session.permanent = True
    session["user_info"] = {
        "Userid": user["Userid"],
        "username": user.get("username") or phone,
        "phone": phone,
        "openid": user.get("openid", ""),
    }
    return jsonify({"status": "success", "user": session["user_info"]})


@api_blueprint.route("/register", methods=["POST"])
def register():
    payload = request.get_json(silent=True) or request.form
    phone = (payload.get("phone") or "").strip()
    secret = (payload.get("secret") or "").strip()
    username = (payload.get("username") or "").strip() or phone
    sms_code = (payload.get("sms_code") or "").strip()

    if not _is_valid_phone(phone) or not secret:
        return jsonify({"status": "error", "message": "请输入正确的手机号和密码"}), 400
    ok, message = _verify_sms_code("register", phone, sms_code)
    if not ok:
        return jsonify({"status": "error", "message": message}), 400

    rows = excute_sql("SELECT Userid FROM user WHERE phone = %s", (phone,))
    if rows:
        return jsonify({"status": "error", "message": "该手机号已注册，请直接登录"}), 409

    affected = excute_sql(
        "INSERT INTO user (phone, secret, username, openid) VALUES (%s, %s, %s, %s)",
        (phone, _hash_password(secret), username, ""),
        fetch=False,
    )
    if not affected:
        return jsonify({"status": "error", "message": "注册失败，请重试"}), 500

    _sync_detection_user(phone, username, phone)
    return jsonify({"status": "success", "message": "注册成功，请登录"})


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
    if not _ensure_developer_api_key_table():
        return jsonify({"status": "error", "valid": False, "message": "API Key 存储初始化失败"}), 500

    api_key = _developer_key_from_request()
    if not api_key.startswith(DEVELOPER_API_KEY_PREFIX):
        return jsonify({"status": "success", "valid": False}), 200

    rows = excute_sql(
        """
        SELECT k.id, k.user_id, k.name, k.scopes, k.status, u.phone, u.username
        FROM developer_api_keys k
        JOIN user u ON u.Userid = k.user_id
        WHERE k.key_hash = %s
        LIMIT 1
        """,
        (_developer_key_hash(api_key),),
    )
    if rows is None:
        return jsonify({"status": "error", "valid": False, "message": "API Key 校验失败"}), 500
    if not rows or rows[0].get("status") != "active":
        return jsonify({"status": "success", "valid": False}), 200

    row = rows[0]
    excute_sql(
        """
        UPDATE developer_api_keys
        SET last_used_at = NOW(), last_used_ip = %s
        WHERE id = %s
        """,
        (request.headers.get("x-forwarded-for", request.remote_addr or "")[:64], row["id"]),
        fetch=False,
    )
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
        usage = _developer_usage_from_v2(user["Userid"], days)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        if status >= 500:
            status = 502
        return jsonify({"status": "error", "message": "Token 用量统计服务返回异常"}), status
    except Exception:
        return jsonify({"status": "error", "message": "Token 用量统计服务暂不可用"}), 502

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
        rows = excute_detection_sql("SELECT * FROM data WHERE openid = %s ORDER BY createtime DESC", (actor["openid"],))
    elif actor["mode"] == "anonymous":
        rows = []
    else:
        rows = excute_detection_sql(
            "SELECT * FROM data WHERE phone = %s OR openid = %s ORDER BY createtime DESC",
            (actor["phone"], actor["openid"]),
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
            "SELECT * FROM data WHERE itemid = %s AND openid = %s LIMIT 1",
            (itemid, actor["openid"]),
        )
    else:
        rows = excute_detection_sql(
            "SELECT * FROM data WHERE itemid = %s AND (phone = %s OR openid = %s) LIMIT 1",
            (itemid, actor["phone"], actor["openid"]),
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
        return redirect(_detection_static_url("image", item), code=302)


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
        rows = excute_detection_sql("SELECT * FROM video_data WHERE openid = %s ORDER BY createtime DESC", (actor["openid"],))
    elif actor["mode"] == "anonymous":
        rows = []
    else:
        rows = excute_detection_sql(
            "SELECT * FROM video_data WHERE phone = %s OR openid = %s ORDER BY createtime DESC",
            (actor["phone"], actor["openid"]),
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


@api_blueprint.route("/history/retrievals")
def retrieval_history():
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
    if filter_key not in {"all", "hits", "empty"}:
        return jsonify({"status": "error", "message": "filter 不受支持"}), 400

    search_type = request.args.get("search_type", "image")
    if search_type not in ("image", "video"):
        return jsonify({"status": "error", "message": "search_type 必须为 image 或 video"}), 400

    if actor["mode"] != "user":
        rows = []
        phone = ""
    else:
        phone = actor["phone"]
        rows = excute_sql(
            "SELECT * FROM retrieve_data WHERE phone = %s AND search_type = %s ORDER BY createtime DESC",
            (phone, search_type),
        )
    records = []
    for item in rows or []:
        record = _retrieval_history_record(item, phone, search_type)
        if not _contains_history_query(_retrieval_history_search_fields(record), query):
            continue
        records.append(record)
    filter_counts = {
        "all": len(records),
        "hits": sum(1 for record in records if int(record.get("result_count") or 0) > 0),
        "empty": sum(1 for record in records if int(record.get("result_count") or 0) <= 0),
    }
    filtered_records = [record for record in records if _retrieval_history_matches_filter(record, filter_key)]
    return jsonify({
        "status": "success",
        "records": filtered_records[offset: offset + limit],
        "total": len(filtered_records),
        "filter_counts": filter_counts,
    })
