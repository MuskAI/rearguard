import hashlib
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
from imagedetection.views.utils import excute_detection_sql, excute_sql, format_createtime


api_blueprint = Blueprint("api_blueprint", __name__, url_prefix="/api")
THUMBNAIL_CACHE_DIR = Path(os.environ.get("REALGUARD_THUMBNAIL_CACHE_DIR", "/tmp/realguard-thumbnails"))
THUMBNAIL_MAX_SIZE = (
    int(os.environ.get("REALGUARD_THUMBNAIL_MAX_WIDTH", "220")),
    int(os.environ.get("REALGUARD_THUMBNAIL_MAX_HEIGHT", "165")),
)
THUMBNAIL_QUALITY = int(os.environ.get("REALGUARD_THUMBNAIL_QUALITY", "45"))


def _current_user():
    user = session.get("user_info")
    return user if isinstance(user, dict) else None


def _auth_required():
    user = _current_user()
    if not user:
        return None, (jsonify({"status": "error", "message": "用户未登录"}), 401)
    return user, None


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


@api_blueprint.route("/history/image-detections")
def image_detection_history():
    user, error = _auth_required()
    if error:
        return error

    phone = user.get("phone", "")
    rows = excute_detection_sql("SELECT * FROM data WHERE phone = %s ORDER BY createtime DESC", (phone,))
    records = []
    for item in rows or []:
        fake_pct = round(float(item.get("fake", 0) or 0), 1)
        records.append(
            {
                "itemid": item.get("itemid"),
                "filename": item.get("filename", ""),
                "image_url": _detection_static_url("image", item),
                "thumbnail_url": _thumbnail_url(item),
                "real_prob": round(100 - fake_pct, 1),
                "fake_prob": fake_pct,
                "final_label": "AI生成图像" if fake_pct >= 50 else "真实图像",
                "confidence": item.get("clarity", ""),
                "createtime": format_createtime(item.get("createtime", "")),
            }
        )
    return jsonify({"status": "success", "records": records})


@api_blueprint.route("/media/thumbnail/image/<int:itemid>")
def image_detection_thumbnail(itemid):
    user, error = _auth_required()
    if error:
        return error

    phone = user.get("phone", "")
    rows = excute_detection_sql(
        "SELECT * FROM data WHERE itemid = %s AND phone = %s LIMIT 1",
        (itemid, phone),
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
    user, error = _auth_required()
    if error:
        return error

    phone = user.get("phone", "")
    rows = excute_detection_sql("SELECT * FROM video_data WHERE phone = %s ORDER BY createtime DESC", (phone,))
    records = []
    for item in rows or []:
        fake_pct = round(float(item.get("fake") or item.get("fake_percentage") or 0), 1)
        records.append(
            {
                "itemid": item.get("itemid"),
                "filename": item.get("filename", ""),
                "video_url": _detection_static_url("video", item),
                "real_percentage": round(100 - fake_pct, 1),
                "fake_percentage": fake_pct,
                "final_label": item.get("final_label", ""),
                "confidence": item.get("confidence") or item.get("confidence_level", ""),
                "createtime": format_createtime(item.get("createtime", "")),
            }
        )
    return jsonify({"status": "success", "records": records})


@api_blueprint.route("/history/retrievals")
def retrieval_history():
    user, error = _auth_required()
    if error:
        return error

    search_type = request.args.get("search_type", "image")
    if search_type not in ("image", "video"):
        return jsonify({"status": "error", "message": "search_type 必须为 image 或 video"}), 400

    phone = user.get("phone", "")
    rows = excute_sql(
        "SELECT * FROM retrieve_data WHERE phone = %s AND search_type = %s ORDER BY createtime DESC",
        (phone, search_type),
    )
    records = []
    for item in rows or []:
        records.append(
            {
                "itemid": item.get("itemid"),
                "filename": item.get("filename", ""),
                "file_url": f"/static/uploads/{phone}/retrieve/{item.get('filename', '')}",
                "search_type": search_type,
                "result_count": item.get("result_count", 0),
                "top_k": item.get("top_k", 10),
                "file_size": item.get("file_size", ""),
                "createtime": format_createtime(item.get("createtime", "")),
                "results": json.loads(item.get("results_json") or "[]"),
            }
        )
    return jsonify({"status": "success", "records": records})
