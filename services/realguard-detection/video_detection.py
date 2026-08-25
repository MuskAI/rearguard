"""
视频检测 API 蓝图
路由：
    POST /video          上传视频文件或传入视频 URL，返回 D3 检测结果
    POST /video/feedback 提交用户对视频检测结果的反馈
    GET  /video          健康检查
    POST /video/api      纯 API 接口（无用户验证、无入库）
"""

import os
import uuid
import shutil
import tempfile
import requests
import traceback

import cv2
from flask import Blueprint, request, session, jsonify

from imagedetection import db
from imagedetection.models import VideoData, User
from .utils import get_current_time_string
from .security import current_identity, download_public_url, upload_directory, check_daily_limit, get_quota_info
from .video_text import public_video_explanation

video_blueprint = Blueprint('video_blueprint', __name__, static_folder='static')

# 允许上传的视频扩展名
ALLOWED_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv'}
# 最大文件大小：200MB
MAX_FILE_SIZE = 200 * 1024 * 1024

current_dir = os.path.dirname(os.path.abspath(__file__))


def _allowed_video(filename: str) -> bool:
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS


def _get_video_meta(video_path: str) -> dict:
    """读取视频基础元信息（时长、分辨率、格式）。"""
    meta = {"duration": None, "resolution": "", "video_format": ""}
    try:
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS) or 1
            total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            meta["duration"] = round(total / fps, 2)
            meta["resolution"] = f"{w}x{h}" if w and h else ""
            cap.release()
        size_bytes = os.path.getsize(video_path)
        if size_bytes < 1024 * 1024:
            meta["file_size"] = f"{size_bytes / 1024:.1f} KB"
        else:
            meta["file_size"] = f"{size_bytes / 1024 / 1024:.1f} MB"
        meta["video_format"] = os.path.splitext(video_path)[1].lstrip('.').upper()
    except Exception as e:
        meta["file_size"] = ""
    return meta


@video_blueprint.route('/video', methods=['GET'])
def video_health():
    return jsonify({"code": 200, "msg": "Video Detection API Ready", "data": None})


@video_blueprint.route('/video', methods=['POST'])
def detect_video_api():
    # ── 1. 获取用户身份 ──────────────────────────────────────────────────
    openid, phone = current_identity()

    # 每日额度检查
    allowed, current_count, limit = check_daily_limit(openid, 'video')
    if not allowed:
        return jsonify({
            "code": 429,
            "msg": f"今日视频检测次数已用完（{current_count}/{limit}），请明天再试",
            "data": None
        })

    static_upload_dir, folder_name = upload_directory(
        os.path.join(current_dir, '..', 'static', 'uploads'), openid, 'video'
    )

    target_save_path = ""
    filename = ""


    # ── 2. 接收视频（文件 或 URL）────────────────────────────────────────
    if 'video_file' in request.files and request.files['video_file'].filename != '':
        video_file = request.files['video_file']
        original_name = video_file.filename

        if not _allowed_video(original_name):
            return jsonify({"code": 400, "msg": f"不支持的视频格式，请上传 {'/'.join(ALLOWED_EXTENSIONS)}", "data": None})

        ext = os.path.splitext(original_name)[1].lower()
        filename = f"{uuid.uuid4().hex}{ext}"
        target_save_path = os.path.join(static_upload_dir, filename)
        video_file.save(target_save_path)

        if os.path.getsize(target_save_path) > MAX_FILE_SIZE:
            os.remove(target_save_path)
            return jsonify({"code": 400, "msg": "视频文件过大，请上传 200MB 以内的视频", "data": None})

    elif 'video_url' in request.form and request.form['video_url'].strip():
        video_url = request.form['video_url'].strip()
        ext = '.mp4'
        for e in ALLOWED_EXTENSIONS:
            if video_url.lower().endswith(e):
                ext = e
                break
        filename = f"url_video_{uuid.uuid4().hex[:8]}{ext}"
        target_save_path = os.path.join(static_upload_dir, filename)
        try:
            download_public_url(video_url, target_save_path, MAX_FILE_SIZE, timeout=30)
        except Exception as e:
            return jsonify({"code": 400, "msg": f"视频下载失败: {str(e)}", "data": None})
    else:
        return jsonify({"code": 400, "msg": "请上传视频文件或提供视频 URL", "data": None})

    # ── 3. D3 检测 ────────────────────────────────────────────────────────
    try:
        from imagedetection.video_detector import detect_video
        result = detect_video(target_save_path)
    except Exception as e:
        traceback.print_exc()
        # 检测失败时删除已保存的文件，避免占用磁盘
        if os.path.exists(target_save_path):
            os.remove(target_save_path)
        return jsonify({"code": 500, "msg": f"视频检测失败: {str(e)}", "data": None})

    # ── 4. 读取视频元信息 ─────────────────────────────────────────────────
    meta = _get_video_meta(target_save_path)

    result["explanation"] = public_video_explanation(
        result.get("explanation"),
        result.get("final_label"),
        result.get("confidence"),
        result.get("fake_score"),
        result.get("real_score"),
    )

    # ── 5. 入库 ───────────────────────────────────────────────────────────
    try:
        create_time = get_current_time_string()
        user = User.query.filter_by(openid=openid).order_by(User.Userid.desc()).first() if openid else None
        user_id = user.Userid if user else None

        record = VideoData(
            createtime=create_time,
            filename=filename,
            openid=openid,
            phone=phone,
            Userid=user_id,
            fake=result["fake_score"],
            d3_std=result["d3_std"],
            final_label=result["final_label"],
            confidence=result["confidence"],
            encoder=result["encoder"],
            frame_count=result["frame_count"],
            explanation=result["explanation"][:500],
            file_size=meta.get("file_size", ""),
            duration=meta.get("duration"),
            resolution=meta.get("resolution", ""),
            video_format=meta.get("video_format", ""),
        )
        db.session.add(record)
        db.session.commit()
        data_itemid = record.itemid
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        data_itemid = None

    # ── 6. 返回结果 ───────────────────────────────────────────────────────
    return jsonify({
        "code": 200,
        "msg": "success",
        "data": {
            "data_itemid": data_itemid,
            "filename": filename,
            "video_url": (
                f"{request.host_url.rstrip('/')}/static/uploads/"
                f"{folder_name}/video/{filename}"
            ),
            "fake_percentage": result["fake_score"],
            "real_percentage": result["real_score"],
            "final_label": result["final_label"],
            "confidence": result["confidence"],
            # 同时返回两种拼写，兼容前端
            "explanation": result["explanation"],
            "explantation": result["explanation"],
            "d3_std": result["d3_std"],
            "encoder": result["encoder"],
            "frame_count": result["frame_count"],
            "meta": {
                "file_size": meta.get("file_size", ""),
                "duration": meta.get("duration"),
                "resolution": meta.get("resolution", ""),
                "video_format": meta.get("video_format", ""),
                # 前端 finishDetect 会自己补 width/height/size，这里留空兼容
                "width": 0,
                "height": 0,
                "size": 0,
            },
            "quota": get_quota_info(openid),
        }
    })


@video_blueprint.route('/video/feedback', methods=['POST'])
def video_feedback():
    """
    视频检测用户反馈接口。
    请求体（JSON）：
        data_itemid : int   检测记录 ID
        feedback    : str   反馈内容（如 "正确" / "错误"）
    """
    data = request.get_json(silent=True) or {}
    data_itemid = data.get('data_itemid')
    feedback = data.get('feedback', '')

    if not data_itemid:
        return jsonify({"code": 400, "msg": "缺少 data_itemid 参数", "data": None})
    if not feedback:
        return jsonify({"code": 400, "msg": "缺少 feedback 参数", "data": None})

    try:
        openid, _ = current_identity()
        if not openid:
            return jsonify({"code": 401, "msg": "请先登录", "data": None}), 401
        record = VideoData.query.filter_by(itemid=int(data_itemid), openid=openid).first()
        if not record:
            return jsonify({"code": 404, "msg": "未找到对应的视频检测记录", "data": None})

        record.feedback = str(feedback)[:20]
        db.session.commit()
        return jsonify({
            "code": 200,
            "msg": "反馈提交成功",
            "data": {"data_itemid": record.itemid, "feedback": record.feedback}
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"code": 500, "msg": f"反馈提交失败: {str(e)}", "data": None})


# ══════════════════════════════════════════════════════════════════════════════
# v2 纯 API 接口：无用户验证、无入库、临时文件自动清理
# ══════════════════════════════════════════════════════════════════════════════
@video_blueprint.route('/video/api', methods=['POST'])
def detect_video_api_v2():
    target_save_path = ""
    filename = ""
    tmp_dir = None

    try:
        # ── 1. 创建临时目录 ──────────────────────────────────────────────
        tmp_dir = tempfile.mkdtemp(prefix="d3_video_")

        # ── 2. 接收视频（文件 或 URL）────────────────────────────────────
        if 'video_file' in request.files and request.files['video_file'].filename != '':
            video_file = request.files['video_file']
            original_name = video_file.filename

            if not _allowed_video(original_name):
                return jsonify({"code": 400, "msg": f"不支持的视频格式，请上传 {'/'.join(ALLOWED_EXTENSIONS)}", "data": None})

            ext = os.path.splitext(original_name)[1].lower()
            filename = f"{uuid.uuid4().hex}{ext}"
            target_save_path = os.path.join(tmp_dir, filename)
            video_file.save(target_save_path)

            if os.path.getsize(target_save_path) > MAX_FILE_SIZE:
                return jsonify({"code": 400, "msg": "视频文件过大，请上传 200MB 以内的视频", "data": None})

        elif 'video_url' in request.form and request.form['video_url'].strip():
            video_url = request.form['video_url'].strip()
            ext = '.mp4'
            for e in ALLOWED_EXTENSIONS:
                if video_url.lower().endswith(e):
                    ext = e
                    break
            filename = f"url_video_{uuid.uuid4().hex[:8]}{ext}"
            target_save_path = os.path.join(tmp_dir, filename)
            try:
                download_public_url(video_url, target_save_path, MAX_FILE_SIZE, timeout=30)
            except Exception as e:
                return jsonify({"code": 400, "msg": f"视频下载失败: {str(e)}", "data": None})
        else:
            return jsonify({"code": 400, "msg": "请上传视频文件或提供视频 URL", "data": None})

        # ── 3. D3 检测 ───────────────────────────────────────────────────
        try:
            from imagedetection.video_detector import detect_video
            result = detect_video(target_save_path)
        except Exception as e:
            traceback.print_exc()
            return jsonify({"code": 500, "msg": f"视频检测失败: {str(e)}", "data": None})
        result["explanation"] = public_video_explanation(
            result.get("explanation"),
            result.get("final_label"),
            result.get("confidence"),
            result.get("fake_score"),
            result.get("real_score"),
        )

        # ── 4. 读取视频元信息 ────────────────────────────────────────────
        meta = _get_video_meta(target_save_path)

        # ── 5. 返回结果（不入库）─────────────────────────────────────────
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": {
                "fake_percentage": result["fake_score"],
                "real_percentage": result["real_score"],
                "final_label": result["final_label"],
                "confidence": result["confidence"],
                "explanation": result["explanation"],
                "d3_std": result["d3_std"],
                "encoder": result["encoder"],
                "frame_count": result["frame_count"],
                "meta": {
                    "file_size": meta.get("file_size", ""),
                    "duration": meta.get("duration"),
                    "resolution": meta.get("resolution", ""),
                    "video_format": meta.get("video_format", ""),
                }
            }
        })

    finally:
        # ── 6. 清理临时文件 ──────────────────────────────────────────────
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
