import os
import json
import uuid
import io
import requests
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, session, jsonify, Response
from werkzeug.utils import secure_filename

from imagedetection.views import reporting
from imagedetection.views.utils import (
    create_folder,
    excute_detection_sql,
    excute_detection_sql_lastid,
    get_file_size_str,
    get_image_info,
    safe_truncate,
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
V2_DETECT_API = os.environ.get(
    'REALGUARD_V2_INTERNAL_DETECT_URL',
    'http://127.0.0.1:8848/api/detect'
).strip()
V2_INTERNAL_TOKEN = (
    os.environ.get('REALGUARD_V2_INTERNAL_TOKEN')
    or os.environ.get('JIANZHEN_ACCESS_TOKEN')
    or ''
).strip()
IMAGE_DETECT_FALLBACK = os.environ.get('REALGUARD_IMAGE_DETECT_FALLBACK', 'v2').strip().lower()
VIDEO_DETECT_TIMEOUT_NORMAL = 120
IMAGE_DETECT_TIMEOUT = 180
V2_DETECT_TIMEOUT = int(os.environ.get('REALGUARD_V2_DETECT_TIMEOUT', '180'))
GUEST_DETECTION_SESSION_KEY = 'guest_detection_count'
GUEST_DETECTION_LIMIT = int(os.environ.get('REALGUARD_GUEST_DETECTION_LIMIT', '1'))
STATIC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'static'))


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


def _detection_owner():
    user_info = session.get('user_info')
    if isinstance(user_info, dict) and user_info:
        phone = str(user_info.get('phone') or '').strip()
        openid = str(user_info.get('openid') or '').strip()
        return phone, openid, False
    guest_openid = str(session.get('guest_openid') or '').strip()
    return '', guest_openid, True


def _load_detection_record(table, itemid):
    phone, openid, is_guest = _detection_owner()
    if is_guest:
        if not openid:
            return None
        sql = f"SELECT * FROM {table} WHERE itemid = %s AND openid = %s LIMIT 1"
        rows = excute_detection_sql(sql, (itemid, openid))
    else:
        sql = f"SELECT * FROM {table} WHERE itemid = %s AND (phone = %s OR openid = %s) LIMIT 1"
        rows = excute_detection_sql(sql, (itemid, phone, openid))
    return rows[0] if rows else None


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
    local_path = os.path.join(STATIC_ROOT, 'uploads', folder, kind, filename)
    if os.path.exists(local_path):
        return f"/static/uploads/{folder}/{kind}/{filename}"
    return f"{DETECTION_PUBLIC_STATIC_PREFIX}/uploads/{folder}/{kind}/{filename}"


def _public_backend_static_url(value):
    """Rewrite private detection-backend static URLs to the public Nginx proxy path."""
    if not value:
        return ''
    if str(value).startswith('/static/'):
        return str(value)
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


def _v2_fallback_enabled():
    return IMAGE_DETECT_FALLBACK in ('v2', 'auto', '1', 'true', 'yes') and bool(V2_DETECT_API and V2_INTERNAL_TOKEN)


def _save_local_upload(image_bytes, folder, filename):
    upload_dir = os.path.join(STATIC_ROOT, 'uploads', folder, 'image')
    create_folder(upload_dir)
    safe_name = secure_filename(filename) or f"{uuid.uuid4().hex}.png"
    stored_name = f"{uuid.uuid4().hex[:12]}-{safe_name}"
    file_path = os.path.join(upload_dir, stored_name)
    with open(file_path, 'wb') as out:
        out.write(image_bytes)
    return stored_name, file_path


def _extract_v2_visual_issues(payload):
    issues = []
    for region in payload.get('regions') or []:
        label = str(region.get('label') or '').strip()
        score = _to_float(region.get('score'), 0.0)
        if label:
            issues.append(f"{label}（{round(score * 100, 1)}%）" if score else label)
    for dim in payload.get('dimensions') or []:
        label = str(dim.get('label') or dim.get('key') or '').strip()
        result = str(dim.get('result') or '').strip()
        score = _to_float(dim.get('score'), 0.0)
        if label and result:
            suffix = f"（{round(score * 100, 1)}%）" if score else ""
            issues.append(f"{label}: {result}{suffix}")
    return issues[:6]


def _fake_percentage_from_v2(payload):
    verdict = str(payload.get('verdict') or '').strip().lower()
    confidence = _to_float(payload.get('confidence'), 0.5)
    if confidence > 1:
        confidence = confidence / 100.0
    confidence = max(0.0, min(1.0, confidence))
    if verdict == 'real':
        return round((1.0 - confidence) * 100, 1)
    if verdict in ('suspected_fake', 'highly_suspected_fake', 'fake', 'ai', 'likely_ai_generated'):
        return round(confidence * 100, 1)
    return 50.0


def _insert_v2_fallback_record(payload, image_bytes, filename, backend_openid, phone, user_info):
    stored_name, file_path = _save_local_upload(image_bytes, backend_openid or phone or 'guest', filename)
    img_format, resolution = get_image_info(file_path)
    file_size = (payload.get('fileMeta') or {}).get('size') or get_file_size_str(file_path)
    fake_pct = _fake_percentage_from_v2(payload)
    final_label = 'AI生成图像' if fake_pct >= 50 else '真实图像'
    confidence_level = _conf_level_from_score(_conf_score_from_api(payload.get('confidence'), fake_pct))
    explanation = str(payload.get('explanation') or '').strip() or _to_user_explanation(final_label, confidence_level)
    visual_issues = _extract_v2_visual_issues(payload)
    if visual_issues:
        explanation = f"{explanation}\n视觉可疑点\n" + "\n".join(f"- {item}" for item in visual_issues)

    itemid = excute_detection_sql_lastid(
        """
        INSERT INTO data
            (createtime, filename, fake, detector_probability, openid, phone, aigc,
             file_size, img_format, resolution, clarity, explantation, Userid)
        VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            stored_name,
            fake_pct,
            fake_pct / 100.0,
            backend_openid,
            phone,
            final_label,
            file_size,
            img_format,
            resolution,
            confidence_level,
            safe_truncate(explanation, 500),
            (user_info or {}).get('Userid'),
        ),
    )
    if not itemid:
        raise RuntimeError('检测结果写入失败')

    return {
        'code': 200,
        'msg': 'success',
        'data': {
            'data_itemid': itemid,
            'fake_percentage': fake_pct,
            'final_label': final_label,
            'confidence': confidence_level,
            'image_url': f"/static/uploads/{backend_openid or phone or 'guest'}/image/{stored_name}",
            'filename': stored_name,
            'file_size': file_size,
            'img_format': img_format,
            'resolution': resolution,
            'explanation': explanation,
            'visual_issues': visual_issues,
            'agent_reasoning': json.dumps({
                'fallback': 'jianzhen-v2',
                'taskId': payload.get('taskId'),
                'reportId': payload.get('reportId'),
                'modelVersion': payload.get('modelVersion'),
                'source': payload.get('source'),
                'tokenUsage': payload.get('tokenUsage'),
            }, ensure_ascii=False),
            'meta': {
                'file_size': file_size,
                'img_format': img_format,
                'resolution': resolution,
            },
        },
    }


def _detect_with_v2_fallback(image_bytes, safe_name, mimetype, backend_openid, phone, user_info):
    if not _v2_fallback_enabled():
        return None
    response = _backend_post(
        V2_DETECT_API,
        headers={'X-Jianzhen-Token': V2_INTERNAL_TOKEN},
        files={'file': (safe_name, io.BytesIO(image_bytes), mimetype or 'application/octet-stream')},
        data={'fileType': 'image'},
        timeout=V2_DETECT_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return _insert_v2_fallback_record(payload, image_bytes, safe_name, backend_openid, phone, user_info)


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
    return image_detect_for_actor(user_info, is_guest=is_guest)


def image_detect_for_actor(user_info, *, is_guest=False):
    file = request.files.get('image') or request.files.get('file')
    if not file or file.filename == '':
        return jsonify({'status': 'error', 'message': '请上传图片文件'}), 400

    filename = file.filename

    if not allowed_file(filename):
        return jsonify({'status': 'error', 'message': '不支持的文件格式'}), 400

    backend_openid, phone = _backend_identity(user_info)
    safe_name = secure_filename(filename) or filename
    mimetype = file.mimetype or 'application/octet-stream'
    file.stream.seek(0)
    image_bytes = file.stream.read()
    if not image_bytes:
        return jsonify({'status': 'error', 'message': '请上传非空图片文件'}), 400

    try:
        api_resp = _backend_post(
            IMAGE_DETECT_API,
            files={'image_file': (safe_name, io.BytesIO(image_bytes), mimetype)},
            data={'openid': backend_openid, 'phone': phone},
            timeout=IMAGE_DETECT_TIMEOUT,
        )
        api_resp.raise_for_status()
        api_json = api_resp.json()
    except requests.RequestException as e:
        try:
            api_json = _detect_with_v2_fallback(image_bytes, safe_name, mimetype, backend_openid, phone, user_info)
        except Exception as fallback_error:
            return jsonify({
                'status': 'error',
                'message': (
                    '图像鉴伪服务暂不可用：V1 模型服务未监听 127.0.0.1:15000，'
                    f'且 V2 兜底调用失败: {str(fallback_error)}'
                )
            }), 502
        if not api_json:
            return jsonify({
                'status': 'error',
                'message': (
                    '图像鉴伪服务暂不可用：V1 模型服务未监听 127.0.0.1:15000，'
                    '且未配置 V2 兜底检测令牌 REALGUARD_V2_INTERNAL_TOKEN'
                )
            }), 502
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
    itemid = request.args.get('itemid')
    if not itemid:
        return jsonify({'status': 'error', 'message': '缺少参数'}), 400

    item = _load_detection_record('data', itemid)
    if not item:
        return jsonify({'status': 'error', 'message': '未找到该检测记录'}), 404
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


@image_upload_blueprint.route('/image_upload/report')
def image_report_api():
    itemid = request.args.get('itemid')
    if not itemid:
        return jsonify({'status': 'error', 'message': '缺少参数'}), 400

    item = _load_detection_record('data', itemid)
    if not item:
        return jsonify({'status': 'error', 'message': '未找到该检测记录'}), 404

    fake_pct = _to_float(item.get('fake', 0), 0.0)
    result = {
        'itemid': item.get('itemid'),
        'final_label': 'AI生成图像' if fake_pct >= 50 else '真实图像',
        'probability': _prob01_from_percent(fake_pct),
        'confidence': item.get('clarity', ''),
        'explanation': item.get('explantation') or _to_user_explanation(
            'AI生成图像' if fake_pct >= 50 else '真实图像',
            item.get('clarity', ''),
            has_metadata=bool(_metadata_for_item(itemid)),
        ),
        'image_url': _backend_static_url('image', item),
        'filename': item.get('filename', ''),
        'file_size': item.get('file_size', ''),
        'img_format': item.get('img_format', ''),
        'resolution': item.get('resolution', ''),
        'visual_issues': _normalize_visual_issues([], final_label='AI生成图像' if fake_pct >= 50 else '真实图像'),
        'all_metadata': _metadata_for_item(itemid),
    }
    html = reporting.image_report_content(item, result)
    return Response(
        html,
        mimetype='text/html',
        headers={'Content-Disposition': reporting.attachment_header(reporting.image_report_filename(itemid))},
    )


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
    itemid = request.args.get('itemid')
    if not itemid:
        return jsonify({'status': 'error', 'message': '缺少参数'}), 400

    item = _load_detection_record('video_data', itemid)
    if not item:
        return jsonify({'status': 'error', 'message': '未找到该视频检测记录'}), 404
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


@image_upload_blueprint.route('/video_upload/report')
def video_report_api():
    itemid = request.args.get('itemid')
    if not itemid:
        return jsonify({'status': 'error', 'message': '缺少参数'}), 400

    item = _load_detection_record('video_data', itemid)
    if not item:
        return jsonify({'status': 'error', 'message': '未找到该视频检测记录'}), 404

    fake_pct = float(item.get('fake', 0) or 0)
    result = {
        'itemid': item.get('itemid'),
        'filename': item.get('filename', ''),
        'video_url': _backend_static_url('video', item),
        'fake_percentage': fake_pct,
        'real_percentage': max(0.0, 100.0 - fake_pct),
        'final_label': item.get('final_label', ''),
        'confidence': item.get('confidence', ''),
        'explanation': item.get('explanation', ''),
        'frame_count': item.get('frame_count', 0),
        'encoder': item.get('encoder', ''),
        'meta': {
            'file_size': item.get('file_size', ''),
            'duration': item.get('duration', ''),
            'resolution': item.get('resolution', ''),
            'video_format': item.get('video_format', ''),
        },
    }
    html = reporting.video_report_content(item, result)
    return Response(
        html,
        mimetype='text/html',
        headers={'Content-Disposition': reporting.attachment_header(reporting.video_report_filename(itemid))},
    )
