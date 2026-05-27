import os
import json
import uuid
import requests
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, session, jsonify
from werkzeug.utils import secure_filename

from imagedetection.views.utils import (
    excute_detection_sql
)

image_upload_blueprint = Blueprint('image_upload_blueprint', __name__, static_folder='static')

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'bmp', 'gif'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'flv', 'wmv', 'webm'}
DETECTION_BACKEND_BASE_URL = os.environ.get(
    'REALGUARD_DETECTION_BACKEND_URL',
    'http://127.0.0.1:15000'
).rstrip('/')
DETECTION_PUBLIC_STATIC_PREFIX = os.environ.get(
    'REALGUARD_DETECTION_PUBLIC_STATIC_PREFIX',
    '/detection-static'
).rstrip('/')
IMAGE_DETECT_API = f"{DETECTION_BACKEND_BASE_URL}/image"
VIDEO_DETECT_API = f"{DETECTION_BACKEND_BASE_URL}/video"
VIDEO_DETECT_TIMEOUT_NORMAL = 120
IMAGE_DETECT_TIMEOUT = 180
GUEST_DETECTION_SESSION_KEY = 'guest_detection_count'
GUEST_DETECTION_LIMIT = int(os.environ.get('REALGUARD_GUEST_DETECTION_LIMIT', '1'))


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_video_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS


def _to_user_explanation(final_label='', confidence='', has_metadata=False):
    """
    生成面向用户的简洁说明，不展示 Agent 详细推理过程。
    """
    is_ai = str(final_label or '').strip() == 'AI生成图像'
    if is_ai:
        lines = [
            "综合检测结果显示该图像更偏向 AI 生成。",
        ]
        if has_metadata:
            lines.append("已结合元数据进行辅助研判。")
        else:
            lines.append("图像缺少可验证的相机元数据。")
    else:
        lines = [
            "综合检测结果显示该图像更偏向真实拍摄。",
        ]
        if has_metadata:
            lines.append("元数据信息可为真实拍摄提供辅助支撑。")
        else:
            lines.append("虽缺少元数据支撑，但检测器结果仍偏向真实。")

    if confidence:
        lines.append(f"当前置信度：{confidence}。")
    return "\n".join(lines)


def _normalize_visual_issues(value, final_label=''):
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
    else:
        items = []
        for line in str(value or '').splitlines():
            line = line.strip().lstrip("-·•*0123456789.、) ").strip()
            if line:
                items.append(line)
    if str(final_label or '').strip() == '真实图像':
        return items or ['无明显视觉可疑点。']
    return (items or ['暂未提取到明确的视觉可疑点。'])[:6]


def _split_reasoning_sections(text):
    summary = []
    issues = []
    in_issues = False
    for raw_line in str(text or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == '视觉可疑点' or line.startswith('视觉可疑点'):
            in_issues = True
            continue
        if in_issues:
            cleaned = line.lstrip("-·•*0123456789.、) ").strip()
            if cleaned:
                issues.append(cleaned)
        else:
            summary.append(line)
    return "\n".join(summary).strip(), issues


def _backend_identity(user_info):
    phone = str((user_info or {}).get('phone') or '').strip()
    openid = str((user_info or {}).get('openid') or '').strip()
    return openid or phone or 'guest', phone


def _detection_actor():
    user_info = session.get('user_info')
    if isinstance(user_info, dict) and user_info:
        return user_info, False, None

    guest_count = int(session.get(GUEST_DETECTION_SESSION_KEY, 0) or 0)
    if guest_count >= GUEST_DETECTION_LIMIT:
        return None, True, (
            jsonify({'status': 'error', 'message': '访客免费检测次数已用完，请登录后继续检测'}),
            401
        )

    guest_id = session.get('guest_openid')
    if not guest_id:
        guest_id = f"guest-{uuid.uuid4().hex[:16]}"
        session['guest_openid'] = guest_id
        session.modified = True
    return {'Userid': None, 'username': '访客', 'phone': '', 'openid': guest_id}, True, None


def _mark_guest_detection_used(is_guest):
    if not is_guest:
        return
    session[GUEST_DETECTION_SESSION_KEY] = int(session.get(GUEST_DETECTION_SESSION_KEY, 0) or 0) + 1
    session.modified = True


def _backend_static_url(kind, record):
    filename = (record or {}).get('filename') or ''
    folder = (record or {}).get('openid') or (record or {}).get('phone') or 'guest'
    if not filename:
        return ''
    return f"{DETECTION_PUBLIC_STATIC_PREFIX}/uploads/{folder}/{kind}/{filename}"


def _public_backend_static_url(value):
    """Rewrite private detection-backend static URLs to the public Nginx proxy path."""
    if not value:
        return ''
    parsed = urlparse(str(value))
    path = parsed.path if parsed.scheme and parsed.netloc else str(value)
    marker = '/static/'
    if marker not in path:
        return str(value)
    static_path = path.split(marker, 1)[1].lstrip('/')
    return f"{DETECTION_PUBLIC_STATIC_PREFIX}/{static_path}"


def _metadata_for_item(itemid):
    rows = excute_detection_sql(
        "SELECT all_metadata FROM exif WHERE data_itemid = %s LIMIT 1",
        (itemid,)
    )
    if rows and rows[0].get('all_metadata'):
        try:
            return json.loads(rows[0]['all_metadata'])
        except Exception:
            return {}
    return {}


def _prob01_from_percent(value):
    return max(0.0, min(1.0, _to_float(value, 0.0) / 100.0))


def _backend_post(url, **kwargs):
    # 服务器内部调用本机鉴伪后端时必须直连，避免被 HTTP_PROXY/HTTPS_PROXY 劫持。
    with requests.Session() as sess:
        sess.trust_env = False
        return sess.post(url, **kwargs)


def _conf_level_from_score(score):
    """置信度分级：离 0.5 越远，置信度越高。"""
    try:
        p = max(0.0, min(1.0, float(score)))
    except Exception:
        p = 0.5
    d = abs(p - 0.5)
    if d >= 0.35:
        return "高"
    if d >= 0.18:
        return "中"
    return "低"


def _to_float(val, default=0.0):
    """兼容 '72.5', '72.5%', None 等输入。"""
    if val is None:
        return float(default)
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace('%', '')
    if not s:
        return float(default)
    try:
        return float(s)
    except Exception:
        return float(default)


def _conf_score_from_api(conf_value, fake_pct):
    """
    API 的 confidence 可能是数字(0~1)或中文等级(高/中/低)。
    """
    if isinstance(conf_value, (int, float)):
        v = float(conf_value)
        return v if v <= 1 else (v / 100.0)
    s = str(conf_value or '').strip().lower()
    if s in ('高', 'high'):
        return 0.9
    if s in ('中', 'medium'):
        return 0.7
    if s in ('低', 'low'):
        return 0.55
    return max(0.0, min(1.0, _to_float(fake_pct, 0.0) / 100.0))


def _to_public_static_url(host_url, relative_static_path):
    """将 static 相对路径转换为可被外部服务访问的 URL。"""
    host = (host_url or '').rstrip('/')
    rel = (relative_static_path or '').lstrip('/')
    if not host:
        return ''
    return f"{host}/static/{rel}"


@image_upload_blueprint.route('/image_upload')
def image_upload_page():
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    return render_template('image_detection_upload.html')


@image_upload_blueprint.route('/image_result')
def image_result_page():
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    return render_template('image_detection_result.html')


@image_upload_blueprint.route('/video_upload')
def video_upload_page():
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    return render_template('video_detection_upload.html')


@image_upload_blueprint.route('/video')
def video_upload_alias_page():
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    return render_template('video_detection_upload.html')


@image_upload_blueprint.route('/video_result')
def video_result_page():
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    return render_template('video_detection_result.html')


@image_upload_blueprint.route('/image_upload/detect', methods=['POST'])
def image_detect():
    user_info, is_guest, auth_error = _detection_actor()
    if auth_error:
        return auth_error

    if 'image' not in request.files or request.files['image'].filename == '':
        return jsonify({'status': 'error', 'message': '请上传图片文件'}), 400

    file = request.files['image']
    filename = file.filename

    if not allowed_file(filename):
        return jsonify({'status': 'error', 'message': '不支持的文件格式'}), 400

    backend_openid, phone = _backend_identity(user_info)
    safe_name = secure_filename(filename) or filename

    try:
        file.stream.seek(0)
        api_resp = _backend_post(
            IMAGE_DETECT_API,
            files={'image_file': (safe_name, file.stream, file.mimetype or 'application/octet-stream')},
            data={'openid': backend_openid, 'phone': phone},
            timeout=IMAGE_DETECT_TIMEOUT,
        )
        api_resp.raise_for_status()
        api_json = api_resp.json()
    except requests.RequestException as e:
        return jsonify({'status': 'error', 'message': f'调用图像鉴伪后端失败: {str(e)}'}), 502
    except ValueError:
        return jsonify({'status': 'error', 'message': '图像鉴伪后端返回了非 JSON 数据'}), 502

    if api_json.get('code') != 200:
        return jsonify({'status': 'error', 'message': api_json.get('msg', '图像鉴伪失败')}), 400

    try:
        data = api_json.get('data') or {}
        data_itemid = data.get('data_itemid')
        fake_pct = _to_float(data.get('fake_percentage', 0), 0.0)
        probability = _prob01_from_percent(fake_pct)
        final_label = data.get('final_label') or ('AI生成图像' if fake_pct >= 50 else '真实图像')
        confidence = data.get('confidence') or data.get('clarity') or ''
        metadata = _metadata_for_item(data_itemid) if data_itemid else {}
        if not metadata and isinstance(data.get('full_exif_info'), dict):
            metadata = data.get('full_exif_info') or {}
        explanation = data.get('explanation') or data.get('explantation') or _to_user_explanation(
            final_label, confidence, has_metadata=bool(metadata)
        )
        split_explanation, split_issues = _split_reasoning_sections(explanation)
        if split_explanation:
            explanation = split_explanation
        visual_issues_source = data.get('visual_issues') or split_issues
        visual_issues = _normalize_visual_issues(visual_issues_source, final_label=final_label)
        agent_reasoning = data.get('agent_reasoning') or ''

        _mark_guest_detection_used(is_guest)
        return jsonify({
            'status': 'success',
            'result': {
                'itemid': data_itemid,
                'final_label': final_label,
                'probability': probability,
                'detector_probability': probability,
                'p_visual': None,
                'p_metadata': None,
                'confidence': confidence,
                'explanation': explanation,
                'agent_reasoning': agent_reasoning,
                'llm_used': bool(agent_reasoning),
                'visual_issues': visual_issues,
                'image_url': _public_backend_static_url(data.get('image_url')) or _backend_static_url('image', data),
                'filename': data.get('filename') or safe_name,
                'file_size': data.get('file_size') or (data.get('meta') or {}).get('file_size', ''),
                'img_format': data.get('img_format') or (data.get('meta') or {}).get('img_format', ''),
                'resolution': data.get('resolution') or (data.get('meta') or {}).get('resolution', ''),
                'all_metadata': metadata,
                'feedback': None,
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'status': 'error', 'message': f'检测失败: {str(e)}'}), 500


@image_upload_blueprint.route('/image_upload/feedback', methods=['POST'])
def image_detection_feedback():
    """用户对检测结果点赞/点踩/取消，写入 data.feedback（1 / -1 / NULL）"""
    if 'user_info' not in session or session['user_info'] is None:
        return jsonify({'status': 'error', 'message': '用户未登录'}), 401

    payload = request.get_json(silent=True) or {}
    itemid = payload.get('itemid')
    feedback = payload.get('feedback')

    try:
        itemid = int(itemid)
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': '无效的记录 id'}), 400

    try:
        feedback = None if feedback is None else int(feedback)
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'feedback 参数无效'}), 400

    # 1=满意, -1=不满意, 0/None=取消
    if feedback not in (1, -1, 0, None):
        return jsonify({'status': 'error', 'message': 'feedback 须为 1（满意）、-1（不满意）或 0（取消）'}), 400
    db_feedback = None if feedback in (0, None) else feedback

    phone = session['user_info'].get('phone', '')
    db_text = None
    if db_feedback == 1:
        db_text = '满意'
    elif db_feedback == -1:
        db_text = '不满意'
    sql = "UPDATE data SET feedback = %s WHERE itemid = %s AND phone = %s"
    n = excute_detection_sql(sql, (db_text, itemid, phone), fetch=False)
    if n is None:
        return jsonify({'status': 'error', 'message': '数据库更新失败，请确认已执行 feedback 字段迁移'}), 500
    if n == 0:
        return jsonify({'status': 'error', 'message': '未找到记录或无权限'}), 404

    msg = '已取消反馈' if db_feedback is None else '已记录反馈'
    return jsonify({'status': 'success', 'message': msg, 'feedback': db_feedback})


@image_upload_blueprint.route('/image_upload/result')
def image_result_api():
    """根据 itemid 获取历史检测结果"""
    if 'user_info' not in session or session['user_info'] is None:
        return jsonify({'status': 'error', 'message': '用户未登录'}), 401

    itemid = request.args.get('itemid')
    phone = session['user_info'].get('phone', '')
    if not itemid:
        return jsonify({'status': 'error', 'message': '缺少参数'}), 400

    data_sql = "SELECT * FROM data WHERE itemid = %s AND phone = %s"
    data_result = excute_detection_sql(data_sql, (itemid, phone))
    if not data_result or len(data_result) == 0:
        return jsonify({'status': 'error', 'message': '未找到该检测记录'}), 404

    item = data_result[0]
    filename = item.get('filename', '')
    fake_pct = _to_float(item.get('fake', 0), 0.0)
    fake = _prob01_from_percent(fake_pct)
    image_url = _backend_static_url('image', item)

    all_metadata = _metadata_for_item(itemid)

    final_label = 'AI生成图像' if fake_pct >= 50 else '真实图像'
    feedback_raw = item.get('feedback')
    feedback = 1 if feedback_raw == '满意' else (-1 if feedback_raw == '不满意' else None)

    explanation = item.get('explantation') or _to_user_explanation(final_label, item.get('clarity', ''), has_metadata=bool(all_metadata))
    split_explanation, split_issues = _split_reasoning_sections(explanation)
    if split_explanation:
        explanation = split_explanation
    visual_issues = _normalize_visual_issues(split_issues, final_label=final_label)

    return jsonify({
        'status': 'success',
        'result': {
            'itemid': item.get('itemid'),
            'final_label': final_label,
            'probability': fake,
            'detector_probability': fake,
            'confidence': item.get('clarity', ''),
            'explanation': explanation,
            'agent_reasoning': '',
            'visual_issues': visual_issues,
            'image_url': image_url,
            'filename': filename,
            'file_size': item.get('file_size', ''),
            'img_format': item.get('img_format', ''),
            'resolution': item.get('resolution', ''),
            'all_metadata': all_metadata,
            'feedback': feedback,
            '_model': 'aigc',
        }
    })


@image_upload_blueprint.route('/video_upload/detect', methods=['POST'])
def video_detect():
    user_info, is_guest, auth_error = _detection_actor()
    if auth_error:
        return auth_error

    backend_openid, phone = _backend_identity(user_info)

    video_url = (request.form.get('video_url') or '').strip()
    fast_mode = str(request.form.get('fast_mode', '1')).strip().lower() not in ('0', 'false', 'no')
    file = request.files.get('video_file')

    if not file and not video_url:
        return jsonify({'status': 'error', 'message': '请上传视频文件或填写视频 URL'}), 400

    try:
        form_data = {
            'openid': backend_openid,
            'phone': phone,
            'fast_mode': int(fast_mode),
        }
        files = None
        if file and file.filename:
            if not allowed_video_file(file.filename):
                return jsonify({'status': 'error', 'message': '不支持的视频格式'}), 400
            safe_name = secure_filename(file.filename) or file.filename
            file.stream.seek(0)
            files = {'video_file': (safe_name, file.stream, file.mimetype or 'application/octet-stream')}
        else:
            form_data['video_url'] = video_url

        api_resp = _backend_post(
            VIDEO_DETECT_API,
            data=form_data,
            files=files,
            timeout=VIDEO_DETECT_TIMEOUT_NORMAL,
        )
        api_resp.raise_for_status()
        api_json = api_resp.json()
    except requests.RequestException as e:
        return jsonify({'status': 'error', 'message': f'调用视频鉴伪服务失败: {str(e)}'}), 502
    except ValueError:
        return jsonify({'status': 'error', 'message': '视频鉴伪服务返回了非 JSON 数据'}), 502

    if api_json.get('code') != 200:
        return jsonify({'status': 'error', 'message': api_json.get('msg', '视频鉴伪失败')}), 400

    data = api_json.get('data') or {}
    itemid = data.get('data_itemid')
    fake_pct = _to_float(data.get('fake_percentage', 0), 0.0)
    real_pct = _to_float(data.get('real_percentage', max(0, 100 - fake_pct)), max(0, 100 - fake_pct))
    conf_score = _conf_score_from_api(data.get('confidence'), fake_pct)
    final_label_raw = str(data.get('final_label', '') or '').strip().lower()
    if final_label_raw in ('fake', 'ai', 'ai生成视频'):
        final_label = 'AI生成视频'
    elif final_label_raw in ('real', '真实', '真实视频'):
        final_label = '真实视频'
    else:
        final_label = 'AI生成视频' if fake_pct >= 50 else '真实视频'
    explanation = data.get('explanation', '')
    conf_level = _conf_level_from_score(conf_score)
    meta = data.get('meta') or {}

    duration = meta.get('duration', '')
    resolution = meta.get('resolution', '')
    video_format = meta.get('video_format', '')
    frame_count = data.get('frame_count', 0)
    d3_std = data.get('d3_std', None)
    encoder = data.get('encoder', '')
    record = None
    if itemid:
        rows = excute_detection_sql("SELECT * FROM video_data WHERE itemid = %s", (itemid,))
        if rows:
            record = rows[0]
    video_file_url = _backend_static_url('video', record or {'openid': backend_openid, 'filename': ''})

    _mark_guest_detection_used(is_guest)
    return jsonify({
        'status': 'success',
        'result': {
            'itemid': itemid,
            'filename': (record or {}).get('filename', ''),
            'video_url': video_file_url,
            'fake_percentage': fake_pct,
            'real_percentage': real_pct,
            'final_label': final_label,
            'confidence_score': conf_score,
            'confidence': conf_level,
            'explanation': explanation,
            'd3_std': d3_std,
            'encoder': encoder,
            'frame_count': frame_count,
            'meta': {
                'file_size': meta.get('file_size', ''),
                'duration': duration,
                'resolution': resolution,
                'video_format': video_format,
            }
        }
    })


@image_upload_blueprint.route('/video_upload/result')
def video_result_api():
    if 'user_info' not in session or session['user_info'] is None:
        return jsonify({'status': 'error', 'message': '用户未登录'}), 401

    itemid = request.args.get('itemid')
    phone = session['user_info'].get('phone', '')
    if not itemid:
        return jsonify({'status': 'error', 'message': '缺少参数'}), 400

    sql = "SELECT * FROM video_data WHERE itemid = %s AND phone = %s"
    rows = excute_detection_sql(sql, (itemid, phone))
    if not rows:
        return jsonify({'status': 'error', 'message': '未找到该视频检测记录'}), 404

    item = rows[0]
    fake_pct = float(item.get('fake', 0) or 0)
    return jsonify({
        'status': 'success',
        'result': {
            'itemid': item.get('itemid'),
            'filename': item.get('filename', ''),
            'video_url': _backend_static_url('video', item),
            'fake_percentage': fake_pct,
            'real_percentage': max(0.0, 100.0 - fake_pct),
            'final_label': item.get('final_label', ''),
            'confidence_score': _conf_score_from_api(item.get('confidence'), fake_pct),
            'confidence': item.get('confidence', ''),
            'explanation': item.get('explanation', ''),
            'd3_std': item.get('d3_std'),
            'encoder': item.get('encoder', ''),
            'frame_count': item.get('frame_count', 0),
            'meta': {
                'file_size': item.get('file_size', ''),
                'duration': item.get('duration', ''),
                'resolution': item.get('resolution', ''),
                'video_format': item.get('video_format', ''),
            }
        }
    })
