import os
import json
import uuid
import io
import copy
import hashlib
import ipaddress
import requests
import socket
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, session, jsonify, Response, redirect
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

from model_decision_contract import validate_inference_audit, validate_model_decision

from imagedetection.views import (
    admin_state,
    aliyun_green,
    capture_evidence,
    model_registry,
    probability_fusion,
    reporting,
    swarm_c2pa_expert,
    swarm_visible_watermark_expert,
    swarm_wam_expert,
    swarm_watermark_expert,
    watermark_verdict,
)
from imagedetection.views.utils import (
    claim_detection_record_owner,
    create_folder,
    detection_record_is_publishable,
    detection_owner_where,
    excute_detection_sql,
    excute_detection_sql_lastid,
    get_file_size_str,
    get_image_info,
    normalize_account_uuid,
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
DETECTOR_INTERNAL_TOKEN = os.environ.get('REALGUARD_DETECTOR_INTERNAL_TOKEN', '').strip()
IMAGE_DETECT_FALLBACK = os.environ.get('REALGUARD_IMAGE_DETECT_FALLBACK', '0').strip().lower()
VIDEO_DETECT_TIMEOUT_NORMAL = 120
IMAGE_DETECT_TIMEOUT = 180
V2_DETECT_TIMEOUT = int(os.environ.get('REALGUARD_V2_DETECT_TIMEOUT', '180'))
ALLOW_REMOTE_VIDEO_URLS = str(os.environ.get('REALGUARD_ALLOW_REMOTE_VIDEO_URLS', '0')).strip().lower() in ('1', 'true', 'yes')
GUEST_DETECTION_SESSION_KEY = 'guest_detection_count'
GUEST_DETECTION_LIMIT = int(os.environ.get('REALGUARD_GUEST_DETECTION_LIMIT', '1'))
SWARM_PARALLEL_WORKERS = max(1, min(12, int(os.environ.get('REALGUARD_SWARM_PARALLEL_WORKERS', '6'))))
SWARM_V2_STAGGER_BYTES_PER_SECOND = max(
    1,
    int(os.environ.get('REALGUARD_SWARM_V2_STAGGER_BYTES_PER_SECOND', '800000')),
)
SWARM_V2_MAX_STAGGER_SECONDS = max(
    0.0,
    float(os.environ.get('REALGUARD_SWARM_V2_MAX_STAGGER_SECONDS', '8')),
)
MAX_IMAGE_UPLOAD_BYTES = max(
    1024,
    int(os.environ.get('REALGUARD_MAX_IMAGE_UPLOAD_BYTES', str(25 * 1024 * 1024))),
)
MAX_IMAGE_SOURCE_PIXELS = max(
    1,
    int(os.environ.get('REALGUARD_MAX_IMAGE_SOURCE_PIXELS', '24000000')),
)
MAX_VIDEO_UPLOAD_BYTES = max(
    1024,
    int(os.environ.get('REALGUARD_MAX_VIDEO_UPLOAD_BYTES', str(256 * 1024 * 1024))),
)
BACKGROUND_JOB_CAPACITY = max(
    1,
    int(os.environ.get('REALGUARD_BACKGROUND_JOB_CAPACITY', '2')),
)
SWARM_EXPERT_EXECUTOR = ThreadPoolExecutor(
    max_workers=SWARM_PARALLEL_WORKERS,
    thread_name_prefix='swarm-expert',
)
BACKGROUND_THREAD_CLASS = threading.Thread
BACKGROUND_JOB_SLOTS = threading.BoundedSemaphore(BACKGROUND_JOB_CAPACITY)
STATIC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'static'))
SWARM_EXPERT_SPECS = [
    {
        'id': 'primary',
        'name': '主路由鉴伪专家',
        'role': '主检测',
        'provider': 'internal',
        'weight': 0.34,
    },
    {
        'id': 'metadata',
        'name': '元数据取证专家',
        'role': '元数据',
        'provider': 'local',
        'weight': 0.08,
    },
    {
        'id': 'v2',
        'name': 'V2视觉语言复核专家',
        'role': '语义复核',
        'provider': 'internal',
        'weight': 0.18,
    },
    {
        'id': 'aliyun_pro',
        'name': 'AIGC专业版专家',
        'role': '生成检测',
        'provider': 'aliyun',
        'weight': 0.16,
        'modelId': 'aliyun-aigc-pro',
    },
    {
        'id': 'aliyun_full',
        'name': '隐式标识专家',
        'role': '标识复核',
        'provider': 'aliyun',
        'weight': 0.08,
        'modelId': 'aliyun-aigc-full',
    },
    {
        'id': 'aliyun_ultra',
        'name': '局部编辑专家',
        'role': '局部伪造',
        'provider': 'aliyun',
        'weight': 0.09,
        'modelId': 'aliyun-aigc-ultra',
    },
    {
        'id': 'aliyun_ps',
        'name': '篡改痕迹专家',
        'role': 'PS篡改',
        'provider': 'aliyun',
        'weight': 0.05,
        'modelId': 'aliyun-ps-detector',
    },
    {
        'id': 'aliyun_recap',
        'name': '翻拍风险专家',
        'role': '翻拍检测',
        'provider': 'aliyun',
        'weight': 0.02,
        'modelId': 'aliyun-recap-detector',
    },
    {
        'id': 'c2pa',
        'name': 'C2PA 内容凭证专家',
        'role': '内容凭证',
        'provider': 'c2pa',
        'weight': 0.10,
    },
    {
        'id': 'watermark',
        'name': '隐式水印专家',
        'role': '生成水印',
        'provider': 'watermark',
        'weight': 0.10,
    },
    {
        'id': 'visible_watermark',
        'name': 'AI 平台水印识别专家',
        'role': '平台水印复核',
        'provider': 'hybrid',
        'weight': 0.0,
    },
    {
        'id': 'wam',
        'name': 'WAM 通用水印专家',
        'role': '通用水印',
        'provider': 'wam',
        'weight': 0.08,
    },
]


def _read_image_upload(file):
    file.stream.seek(0)
    image_bytes = file.stream.read(MAX_IMAGE_UPLOAD_BYTES + 1)
    if not image_bytes:
        return None, (jsonify({'status': 'error', 'message': '请上传非空图片文件'}), 400)
    if len(image_bytes) > MAX_IMAGE_UPLOAD_BYTES:
        limit_mb = max(1, MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024))
        return None, (jsonify({
            'status': 'error',
            'code': 'image_too_large',
            'message': f'图片不能超过 {limit_mb} MB',
        }), 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_bytes)) as image:
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise ValueError('invalid image dimensions')
                if bool(getattr(image, 'is_animated', False)) and int(getattr(image, 'n_frames', 1)) > 1:
                    return None, (jsonify({
                        'status': 'error',
                        'code': 'unsupported_animated_image',
                        'message': '暂不支持多帧 GIF 或动态 WebP，请上传静态图片',
                    }), 415)
                if width * height > MAX_IMAGE_SOURCE_PIXELS:
                    return None, (jsonify({
                        'status': 'error',
                        'code': 'image_pixel_limit_exceeded',
                        'message': '图片像素尺寸过大，请先缩小后重试',
                        'details': {
                            'width': width,
                            'height': height,
                            'maxPixels': MAX_IMAGE_SOURCE_PIXELS,
                        },
                    }), 413)
                image.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None, (jsonify({
            'status': 'error',
            'code': 'image_pixel_limit_exceeded',
            'message': '图片像素尺寸过大，请先缩小后重试',
        }), 413)
    except (UnidentifiedImageError, OSError, ValueError):
        return None, (jsonify({
            'status': 'error',
            'code': 'invalid_image',
            'message': '无法解析图片，请上传有效的 JPG、PNG、WebP、BMP 或 GIF 文件',
        }), 400)
    return image_bytes, None


def _start_background_job(target, args=()):
    if not BACKGROUND_JOB_SLOTS.acquire(blocking=False):
        return False

    def run_with_slot():
        try:
            target(*args)
        finally:
            BACKGROUND_JOB_SLOTS.release()

    try:
        thread = BACKGROUND_THREAD_CLASS(target=run_with_slot, daemon=True)
        thread.start()
    except Exception:
        BACKGROUND_JOB_SLOTS.release()
        raise
    return True


def _busy_response():
    response = jsonify({
        'status': 'error',
        'code': 'server_busy',
        'message': '当前检测任务较多，请稍后重试',
    })
    response.headers['Retry-After'] = '5'
    return response, 429


def _guest_capacity_subject():
    """Return a private, stable daily-capacity key that survives cookie resets."""
    from imagedetection.views.login import _trusted_client_ip

    salt = os.environ.get('REALGUARD_CONSENT_AUDIT_SALT', '').strip()
    client_ip = _trusted_client_ip()
    if not salt or not client_ip or client_ip == 'unknown':
        return ''
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return ''
    if address.version == 6:
        client_scope = str(ipaddress.ip_network(f"{address}/64", strict=False).network_address) + '/64'
    else:
        client_scope = str(address)
    material = f"guest-detection:{salt}:{client_scope}".encode('utf-8')
    return hashlib.sha256(material).hexdigest()


def _enqueue_persistent_web_job(job, image_bytes, filename, mimetype, user_info, is_guest):
    from imagedetection.views import developer_platform

    try:
        guest_subject = _guest_capacity_subject() if is_guest else ''
        developer_platform._enqueue_web_detection_task(
            job,
            image_bytes,
            filename,
            mimetype,
            user_info,
            is_guest,
            guest_subject,
        )
    except developer_platform.QueueCapacityError as exc:
        return False, "server_busy", str(exc) or "当前检测任务较多，请稍后重试"
    except Exception as exc:
        print(f"[WEB TASK QUEUE ERROR] {job.get('id')}: {exc}")
        return False, "queue_unavailable", "检测任务暂时无法入队，请稍后重试"
    return True, "", ""


def _load_persistent_web_job(job_id):
    from imagedetection.views import developer_platform

    return developer_platform._persistent_web_job(job_id)


def _seekable_upload_size(file_storage):
    stream = getattr(file_storage, 'stream', None)
    if stream is None:
        return None
    try:
        position = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(position)
        return int(size)
    except (AttributeError, OSError, ValueError):
        return None


@image_upload_blueprint.before_request
def _reject_oversized_upload_requests():
    guarded_paths = {
        '/image_upload/detect': (MAX_IMAGE_UPLOAD_BYTES, '图片', 'image_too_large'),
        '/image_upload/detect_async': (MAX_IMAGE_UPLOAD_BYTES, '图片', 'image_too_large'),
        '/image_upload/detect_swarm': (MAX_IMAGE_UPLOAD_BYTES, '图片', 'image_too_large'),
        '/video_upload/detect': (MAX_VIDEO_UPLOAD_BYTES, '视频', 'video_too_large'),
    }
    upload_limit = guarded_paths.get(request.path)
    if request.method != 'POST' or upload_limit is None:
        return None
    max_bytes, label, error_code = upload_limit
    content_length = request.content_length
    multipart_allowance = 1024 * 1024
    if content_length is None:
        return jsonify({
            'status': 'error',
            'code': 'length_required',
            'message': '上传请求必须提供 Content-Length',
        }), 411
    if content_length > max_bytes + multipart_allowance:
        limit_mb = max(1, max_bytes // (1024 * 1024))
        return jsonify({
            'status': 'error',
            'code': error_code,
            'message': f'{label}不能超过 {limit_mb} MB',
        }), 413
    return None


def _swarm_config():
    try:
        return model_registry.get_swarm_config()
    except Exception:
        return {'enabled': True, 'minExperts': 2, 'experts': SWARM_EXPERT_SPECS}


def _swarm_specs(include_disabled=False):
    config = _swarm_config()
    experts = config.get('experts') if isinstance(config, dict) else None
    if not isinstance(experts, list) or not experts:
        experts = SWARM_EXPERT_SPECS
    normalized = []
    for spec in experts:
        if not isinstance(spec, dict) or not spec.get('id'):
            continue
        item = dict(spec)
        item.setdefault('enabled', True)
        item.setdefault('weight', 0)
        item.setdefault('name', item.get('id'))
        item.setdefault('role', '')
        item.setdefault('provider', '')
        if include_disabled or item.get('enabled') is not False:
            normalized.append(item)
    return normalized


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
    internal_failure_markers = ('LLM调用失败', '无法提供视觉分析', '无法进行视觉分析')
    items = [
        item for item in items
        if not any(marker in item for marker in internal_failure_markers)
    ]
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


def _account_uuid(user_info):
    return normalize_account_uuid((user_info or {}).get('account_uuid'))


def _detection_database_user_id(phone='', openid=''):
    """Resolve an owner ID in the detection database, not the account database."""
    phone = str(phone or '').strip()
    openid = str(openid or '').strip()
    if phone:
        rows = excute_detection_sql("SELECT Userid FROM user WHERE phone = %s LIMIT 1", (phone,))
    elif openid and not openid.startswith('guest-'):
        rows = excute_detection_sql("SELECT Userid FROM user WHERE openid = %s LIMIT 1", (openid,))
    else:
        return None
    return (rows or [{}])[0].get('Userid')


def _detection_owner():
    user_info = session.get('user_info')
    if isinstance(user_info, dict) and user_info:
        user_id = user_info.get('Userid') or user_info.get('userId') or user_info.get('id')
        phone = str(user_info.get('phone') or '').strip()
        openid = str(user_info.get('openid') or '').strip()
        return user_id, phone, openid, False
    guest_openid = str(session.get('guest_openid') or '').strip()
    return None, '', guest_openid, True


def _detection_owner_where(user_id, phone, openid, account_uuid=''):
    return detection_owner_where(
        phone,
        openid,
        account_uuid=account_uuid,
        require_account_uuid=user_id not in (None, ''),
    )


def _runtime_owner_matches(owner, user_id, phone, openid, is_guest, account_uuid=''):
    owner = owner or {}
    owner_user_id = owner.get('Userid') or owner.get('userId') or owner.get('id')
    owner_phone = str(owner.get('phone') or '').strip()
    owner_openid = str(owner.get('openid') or '').strip()
    if is_guest:
        return bool(openid and owner_user_id in (None, '') and not owner_phone and owner_openid == openid)
    immutable_owner = normalize_account_uuid(account_uuid)
    stored_owner = normalize_account_uuid(owner.get('account_uuid') or owner.get('owner_account_uuid'))
    if immutable_owner:
        return bool(stored_owner and stored_owner == immutable_owner)
    if user_id not in (None, ''):
        return False
    if phone and owner_phone:
        return owner_phone == phone
    return bool(openid and not owner_phone and owner_openid == openid)


def _load_detection_record(table, itemid):
    user_id, phone, openid, is_guest = _detection_owner()
    account_uuid = _account_uuid(session.get('user_info'))
    if is_guest:
        if not openid:
            return None
        sql = f"SELECT * FROM {table} WHERE itemid = %s AND Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s LIMIT 1"
        rows = excute_detection_sql(sql, (itemid, openid))
    else:
        owner_where, owner_params = _detection_owner_where(user_id, phone, openid, account_uuid)
        sql = f"SELECT * FROM {table} WHERE itemid = %s AND ({owner_where}) LIMIT 1"
        rows = excute_detection_sql(sql, (itemid, *owner_params))
    if not rows:
        return None
    if table == 'data' and not detection_record_is_publishable(rows[0]):
        return None
    return rows[0]


def _load_detection_record_for_actor(table, itemid, actor, *, is_guest=False):
    if table not in ('data', 'video_data'):
        return None
    actor = actor or {}
    if is_guest:
        openid = str(actor.get('openid') or '').strip()
        if not openid:
            return None
        rows = excute_detection_sql(
            f"SELECT * FROM {table} WHERE itemid = %s AND Userid IS NULL "
            "AND (phone IS NULL OR phone = '') AND openid = %s LIMIT 1",
            (itemid, openid),
        )
    else:
        account_uuid = normalize_account_uuid(actor.get('account_uuid'))
        if not account_uuid:
            return None
        rows = excute_detection_sql(
            f"SELECT * FROM {table} WHERE itemid = %s AND owner_account_uuid = %s LIMIT 1",
            (itemid, account_uuid),
        )
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
    itemid = (record or {}).get('itemid')
    if itemid:
        return f"/api/media/{kind}/{itemid}"
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
    headers = dict(kwargs.pop('headers', {}) or {})
    if DETECTOR_INTERNAL_TOKEN and str(url).startswith(f"{DETECTION_BACKEND_BASE_URL}/"):
        headers['X-RealGuard-Detector-Token'] = DETECTOR_INTERNAL_TOKEN
    with requests.Session() as sess:
        sess.trust_env = False
        return sess.post(url, headers=headers, **kwargs)


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on', 'v2', 'auto')


def _validate_public_video_url(value):
    parsed = urlparse(str(value or '').strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port not in (None, 80, 443):
        return False
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(parsed.hostname, port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError):
        return False
    if not addresses:
        return False
    try:
        return all(ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError:
        return False


def _model_by_route(role):
    routing = model_registry.get_routing()
    model_id = routing.get('imagePrimary') if role == 'primary' else routing.get('imageFallback')
    model = model_registry.get_model(model_id) if model_id else None
    return model or {}


def _primary_image_model():
    return _model_by_route('primary')


def _public_agent_reasoning(value):
    """
    普通用户端不暴露内部模型名、provider、endpoint 等路由细节。
    后台通过 admin_state.modelRuns 保留真实模型记录。
    """
    if not value:
        return ''
    blocked = {
        'modelVersion', 'source', 'fallback', 'provider', 'service', 'modelId',
        'modelName', 'runtime', 'endpoint', 'raw', 'tokenUsage',
    }

    def scrub(item):
        if isinstance(item, dict):
            return {k: scrub(v) for k, v in item.items() if k not in blocked}
        if isinstance(item, list):
            return [scrub(v) for v in item]
        return item

    try:
        cleaned = scrub(json.loads(value))
    except Exception:
        return ''
    try:
        rendered = json.dumps(cleaned, ensure_ascii=False)
    except Exception:
        return ''
    return rendered if rendered not in ('{}', '[]', 'null') else ''


def _visible_watermark_from_backend_data(data):
    remote_evidence = (data or {}).get('remote_evidence')
    if not isinstance(remote_evidence, dict):
        return None
    precheck = remote_evidence.get('visibleWatermarkPrecheck')
    if not isinstance(precheck, dict):
        return None
    try:
        return swarm_visible_watermark_expert._visible_result(precheck)
    except Exception as exc:
        print(f"[VISIBLE WATERMARK MAP ERROR] {type(exc).__name__}")
        return None


def _model_decision_from_backend_data(data):
    remote_evidence = (data or {}).get('remote_evidence')
    if not isinstance(remote_evidence, dict):
        return None
    decision = remote_evidence.get('modelDecision')
    return copy.deepcopy(decision) if isinstance(decision, dict) else None


def _model_decision_is_publishable(decision):
    """Validate the calibration contract before allowing an automatic verdict."""
    return validate_model_decision(decision)


def _stored_model_decision_is_publishable(meta):
    if not isinstance(meta, dict):
        return False
    decision = meta.get('modelDecision')
    audit = meta.get('inferenceAudit')
    return validate_model_decision(decision) and validate_inference_audit(audit, decision)


def _backend_model_decision_is_publishable(data):
    decision = _model_decision_from_backend_data(data)
    remote_evidence = (data or {}).get('remote_evidence')
    audit = (
        remote_evidence.get('modelRun')
        if isinstance(remote_evidence, dict)
        and isinstance(remote_evidence.get('modelRun'), dict)
        else None
    )
    return validate_model_decision(decision) and validate_inference_audit(audit, decision)


def _stored_visible_watermark_for_item(itemid):
    target = str(itemid or '')
    if not target:
        return None
    try:
        run = admin_state.model_runs_by_itemids([target]).get(target) or {}
    except Exception as exc:
        print(f"[VISIBLE WATERMARK LOAD ERROR] {type(exc).__name__}")
        return None
    visible = (run.get('meta') or {}).get('visibleWatermark')
    return copy.deepcopy(visible) if isinstance(visible, dict) else None


def _stored_decision_authorization_for_item(itemid):
    target = str(itemid or '')
    if not target:
        return {'status': 'review_only', 'authority': 'none'}
    try:
        run = admin_state.model_runs_by_itemids([target]).get(target) or {}
    except Exception:
        run = {}
    meta = run.get('meta') if isinstance(run.get('meta'), dict) else {}
    explicit = (
        meta.get('decisionAuthorization')
        if isinstance(meta.get('decisionAuthorization'), dict)
        else {}
    )
    model_decision = (
        meta.get('modelDecision') if isinstance(meta.get('modelDecision'), dict) else {}
    )
    visible = _runtime_visible_watermark_for_item(target)
    if explicit:
        if (
            explicit.get('status') == 'verdict'
            and explicit.get('authority') == 'calibrated_model'
            and _stored_model_decision_is_publishable(meta)
        ):
            return {'status': 'verdict', 'authority': 'calibrated_model'}
        if (
            explicit.get('status') == 'verdict'
            and explicit.get('authority') == 'decisive_provenance'
            and watermark_verdict.has_decisive_ai_watermark(visible)
        ):
            return {'status': 'verdict', 'authority': 'decisive_provenance'}
        return {'status': 'review_only', 'authority': 'none'}
    if _stored_model_decision_is_publishable(meta):
        return {'status': 'verdict', 'authority': 'calibrated_model'}
    if watermark_verdict.has_decisive_ai_watermark(visible):
        return {'status': 'verdict', 'authority': 'decisive_provenance'}
    return {'status': 'review_only', 'authority': 'none'}


def _record_model_run(itemid, data, user_info):
    if not itemid:
        return
    model_id = str((data or {}).get('_route_model_id') or '').strip()
    if not model_id:
        return
    model = model_registry.get_model(model_id) or {'id': model_id}
    try:
        meta = {
            'provider': (data or {}).get('_route_provider') or '',
            'service': (data or {}).get('_route_service') or '',
            'latencyMs': (data or {}).get('_route_latency_ms'),
            'fallback': (data or {}).get('_route_role') == 'fallback',
        }
        visible_watermark = _visible_watermark_from_backend_data(data)
        if isinstance(visible_watermark, dict):
            meta['visibleWatermark'] = visible_watermark
        model_decision = _model_decision_from_backend_data(data)
        if isinstance(model_decision, dict):
            meta['modelDecision'] = {
                'ready': _model_decision_is_publishable(model_decision),
                'mode': str(model_decision.get('mode') or '')[:64],
                'calibrationId': str(model_decision.get('calibrationId') or '')[:160],
                'manifestSha256': str(model_decision.get('manifestSha256') or '')[:64],
                'datasetSha256': str(model_decision.get('datasetSha256') or '')[:64],
                'modelSha256': str(model_decision.get('modelSha256') or '')[:64],
                'preprocessingSha256': str(model_decision.get('preprocessingSha256') or '')[:64],
                'runtimeContractSha256': str(model_decision.get('runtimeContractSha256') or '')[:64],
                'inferenceImplementationSha256': str(model_decision.get('inferenceImplementationSha256') or '')[:64],
                'decisionPolicyImplementationSha256': str(model_decision.get('decisionPolicyImplementationSha256') or '')[:64],
                'runtimeLockSha256': str(model_decision.get('runtimeLockSha256') or '')[:64],
                'probabilityCalibration': copy.deepcopy(model_decision.get('probabilityCalibration')),
                'calibrationManifest': copy.deepcopy(model_decision.get('calibrationManifest')),
                'evaluationCodeRevision': str(model_decision.get('evaluationCodeRevision') or '')[:160],
                'expiresAt': str(model_decision.get('expiresAt') or '')[:64],
                'realSamples': int(model_decision.get('realSamples') or 0),
                'fakeSamples': int(model_decision.get('fakeSamples') or 0),
                'observedFpr': model_decision.get('observedFpr'),
                'observedFnr': model_decision.get('observedFnr'),
                'aiThreshold': model_decision.get('aiThreshold'),
                'rawModelScore': model_decision.get('rawModelScore'),
                'publishedProbability': model_decision.get('publishedProbability'),
                'gateReasons': [
                    str(reason)[:240]
                    for reason in (model_decision.get('gateReasons') or [])[:12]
                ],
            }
        remote_evidence = (data or {}).get('remote_evidence')
        model_run = (
            remote_evidence.get('modelRun')
            if isinstance(remote_evidence, dict)
            and isinstance(remote_evidence.get('modelRun'), dict)
            else {}
        )
        if model_run:
            meta['inferenceAudit'] = {
                'model': str(model_run.get('model') or '')[:160],
                'rawModelScore': model_run.get('rawModelScore'),
                'publishedProbability': model_run.get('fakeProbability'),
                'fakeProbability': model_run.get('fakeProbability'),
                'finalLabel': str(model_run.get('finalLabel') or '')[:64],
                'originalSize': copy.deepcopy(model_run.get('originalSize')),
                'processedSize': copy.deepcopy(model_run.get('processedSize')),
                'downsample': copy.deepcopy(model_run.get('downsample')),
                'chunkCount': model_run.get('chunkCount'),
                'parameters': copy.deepcopy(model_run.get('parameters')),
                'runtime': copy.deepcopy(model_run.get('runtime')),
                'inputImageSha256': str(model_run.get('inputImageSha256') or '')[:64],
                'responseIntegrity': copy.deepcopy(model_run.get('responseIntegrity')),
            }
        admin_state.append_model_run(
            itemid,
            model,
            route=(data or {}).get('_route_role') or 'primary',
            status='success',
            actor=user_info,
            meta=meta,
        )
    except Exception as exc:
        print(f"[MODEL RUN LOG ERROR] {exc}")


def _record_final_decision_run(itemid, result, user_info, *, route='swarm'):
    """Append the final decision authorization used by reports and history."""
    if not itemid or not isinstance(result, dict):
        return
    try:
        previous = admin_state.model_runs_by_itemids([str(itemid)]).get(str(itemid)) or {}
        model = copy.deepcopy(previous.get('model') or {'id': 'swarm-evidence-policy'})
        meta = copy.deepcopy(previous.get('meta') or {})
        visible = result.get('visibleWatermark')
        if isinstance(visible, dict):
            meta['visibleWatermark'] = copy.deepcopy(visible)
        meta['decisionAuthorization'] = {
            'status': (
                'verdict' if result.get('decisionStatus') == 'verdict' else 'review_only'
            ),
            'authority': str(result.get('decisionAuthority') or 'none')[:64],
        }
        admin_state.append_model_run(
            itemid,
            model,
            route=route,
            status='success',
            actor=user_info,
            meta=meta,
        )
    except Exception as exc:
        print(f"[FINAL DECISION RUN LOG ERROR] {exc}")


def _persist_and_freeze_completed_image_result(itemid, result, *, actor=None, is_guest=False):
    """Persist the final fused verdict before freezing its signed evidence."""
    if not itemid:
        raise RuntimeError('检测结果缺少历史记录 ID')
    probability = _clamp01((result or {}).get('probability'), 0.5)
    detector_probability = _clamp01(
        (result or {}).get('detector_probability'),
        probability,
    )
    final_label = (result or {}).get('final_label') or (
        'AI生成图像' if probability >= 0.5 else '真实图像'
    )
    updated = excute_detection_sql(
        """
        UPDATE data
        SET fake = %s, detector_probability = %s, aigc = %s,
            clarity = %s, explantation = %s
        WHERE itemid = %s
        """,
        (
            round(probability * 100.0, 2),
            detector_probability,
            final_label,
            (result or {}).get('confidence') or _conf_level_from_score(probability),
            safe_truncate((result or {}).get('explanation') or '', 500),
            itemid,
        ),
        fetch=False,
    )
    if updated is None:
        raise RuntimeError('最终融合结论写入历史失败')

    item = (
        _load_detection_record('data', itemid)
        if actor is None
        else _load_detection_record_for_actor('data', itemid, actor, is_guest=is_guest)
    )
    if not item:
        raise RuntimeError('最终融合结论写入后无法读取历史记录')
    try:
        reporting.freeze_image_evidence_snapshot(item)
    except Exception as exc:
        raise RuntimeError('检测结论已生成，但证据快照固化失败，请稍后重试') from exc
    return True


def _primary_image_endpoint():
    model = _primary_image_model()
    timeout = int(model.get('timeoutSeconds') or IMAGE_DETECT_TIMEOUT) if model else IMAGE_DETECT_TIMEOUT
    if model and model.get('enabled') is False:
        return '', timeout, 'V1 主检测模型已在后台禁用，请联系管理员启用主模型或调整路由策略'
    if model and aliyun_green.is_aliyun_model(model):
        return str(model.get('endpoint') or '').strip(), timeout, ''
    endpoint = str(model.get('endpoint') or IMAGE_DETECT_API).strip()
    if not endpoint:
        return '', timeout, 'V1 主检测模型端点未配置'
    return endpoint, timeout, ''


def _route_data(model, role, **extra):
    return {
        '_route_model_id': (model or {}).get('id') or '',
        '_route_role': role,
        '_route_provider': extra.get('provider') or ('aliyun' if aliyun_green.is_aliyun_model(model) else 'internal'),
        '_route_service': extra.get('service') or '',
        '_route_latency_ms': extra.get('latencyMs'),
    }


def _v2_fallback_enabled():
    routing = model_registry.get_routing()
    if routing:
        enabled = _truthy(routing.get('fallbackEnabled'))
    else:
        enabled = _truthy(IMAGE_DETECT_FALLBACK)
    fallback = _model_by_route('fallback')
    if fallback and fallback.get('enabled') is False:
        return False
    endpoint = str(fallback.get('endpoint') or V2_DETECT_API).strip()
    return enabled and bool(endpoint and V2_INTERNAL_TOKEN)


def _v2_fallback_endpoint():
    fallback = _model_by_route('fallback')
    endpoint = str(fallback.get('endpoint') or V2_DETECT_API).strip()
    timeout = int(fallback.get('timeoutSeconds') or V2_DETECT_TIMEOUT)
    return endpoint, timeout


def _save_local_upload(image_bytes, folder, filename):
    upload_dir = os.path.join(STATIC_ROOT, 'uploads', folder, 'image')
    create_folder(upload_dir)
    safe_name = secure_filename(filename) or f"{uuid.uuid4().hex}.png"
    stored_name = f"{uuid.uuid4().hex[:12]}-{safe_name}"
    file_path = os.path.join(upload_dir, stored_name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(file_path, flags, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(image_bytes)
    return stored_name, file_path


def _local_detection_record(itemid):
    if itemid in (None, ''):
        return None
    rows = excute_detection_sql(
        "SELECT itemid, filename, Userid, owner_account_uuid, phone, openid FROM data WHERE itemid = %s LIMIT 1",
        (itemid,),
    )
    return rows[0] if rows else None


def _record_matches_detection_actor(record, source_filename, backend_openid, phone, account_uuid=''):
    if not record:
        return False
    record_phone = str(record.get('phone') or '').strip()
    record_openid = str(record.get('openid') or '').strip()
    immutable_owner = normalize_account_uuid(account_uuid)
    record_owner = normalize_account_uuid(record.get('owner_account_uuid'))
    if immutable_owner:
        actor_matches = immutable_owner == record_owner
    else:
        actor_matches = record_phone == phone if phone else record_openid == backend_openid
    filename_matches = bool(source_filename) and str(record.get('filename') or '') == str(source_filename)
    return actor_matches and filename_matches


def _materialize_primary_source(record, data, image_bytes, filename, backend_openid, phone, account_uuid=''):
    folder = backend_openid or phone or 'guest'
    current_name = str((record or {}).get('filename') or '').strip()
    current_path = os.path.join(STATIC_ROOT, 'uploads', folder, 'image', current_name)
    if current_name and os.path.isfile(current_path):
        data.update({
            'filename': current_name,
            'image_url': f"/api/media/image/{record['itemid']}",
        })
        return

    stored_name, file_path = _save_local_upload(image_bytes, folder, filename)
    try:
        img_format, resolution = get_image_info(file_path)
        file_size = get_file_size_str(file_path)
        immutable_owner = normalize_account_uuid(account_uuid)
        if immutable_owner:
            owner_where = 'owner_account_uuid = %s'
            owner_params = (immutable_owner,)
        else:
            guest_openid = str(backend_openid or '').strip()
            if phone or not guest_openid:
                raise RuntimeError('远端检测记录缺少可验证的不可变账号归属')
            owner_where = "Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s"
            owner_params = (guest_openid,)
        updated = excute_detection_sql(
            f"""
            UPDATE data
            SET filename = %s, file_size = %s, img_format = %s, resolution = %s
            WHERE itemid = %s AND ({owner_where})
            """,
            (stored_name, file_size, img_format, resolution, record['itemid'], *owner_params),
            fetch=False,
        )
        if updated != 1:
            raise RuntimeError('远端检测原件归档时所有者校验失败')
    except Exception:
        try:
            os.remove(file_path)
        except OSError:
            pass
        raise
    data.update({
        'filename': stored_name,
        'image_url': f"/api/media/image/{record['itemid']}",
        'file_size': file_size,
        'img_format': img_format,
        'resolution': resolution,
    })


def _insert_local_detection_record(
    data,
    image_bytes,
    filename,
    backend_openid,
    phone,
    user_info,
    source_task_id='',
):
    stored_name, file_path = _save_local_upload(
        image_bytes,
        backend_openid or phone or 'guest',
        filename,
    )
    img_format, resolution = get_image_info(file_path)
    file_size = get_file_size_str(file_path)
    fake_pct = max(0.0, min(100.0, _to_float(data.get('fake_percentage'), 0.0)))
    detector_value = data.get('detector_probability')
    if detector_value is None:
        detector_value = (data.get('meta') or {}).get('detector_probability')
    detector_probability = _clamp01(
        detector_value,
        fake_pct / 100.0,
    )
    final_label = data.get('final_label') or ('AI生成图像' if fake_pct >= 50 else '真实图像')
    confidence = data.get('confidence') or data.get('clarity') or _conf_level_from_score(fake_pct / 100.0)
    explanation = str(data.get('explanation') or data.get('explantation') or '').strip()
    visual_issues = [str(item).strip() for item in (data.get('visual_issues') or []) if str(item).strip()]
    if visual_issues and '视觉可疑点' not in explanation:
        explanation = f"{explanation}\n视觉可疑点\n" + "\n".join(f"- {item}" for item in visual_issues)

    itemid = excute_detection_sql_lastid(
        """
        INSERT INTO data
            (createtime, filename, fake, detector_probability, openid, phone, aigc,
             file_size, img_format, resolution, clarity, explantation, Userid,
             owner_account_uuid, developer_task_id)
        VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            stored_name,
            fake_pct,
            detector_probability,
            backend_openid,
            phone,
            final_label,
            file_size,
            img_format,
            resolution,
            confidence,
            safe_truncate(explanation, 500),
            _detection_database_user_id(phone, backend_openid),
            _account_uuid(user_info) or None,
            str(source_task_id or '').strip() or None,
        ),
    )
    if not itemid:
        raise RuntimeError('检测完成，但历史归档写入失败')

    data.update({
        'data_itemid': itemid,
        'filename': stored_name,
        'image_url': f"/api/media/image/{itemid}",
        'file_size': file_size,
        'img_format': img_format,
        'resolution': resolution,
    })
    return itemid


def _ensure_local_primary_record(
    api_json,
    image_bytes,
    filename,
    backend_openid,
    phone,
    user_info,
    source_task_id='',
):
    data = (api_json or {}).get('data') or {}
    remote_itemid = data.get('data_itemid')
    remote_filename = str(data.get('filename') or '').strip()
    local_record = _local_detection_record(remote_itemid)
    account_uuid = _account_uuid(user_info)
    if _record_matches_detection_actor(local_record, remote_filename, backend_openid, phone, account_uuid):
        if account_uuid and not claim_detection_record_owner('data', remote_itemid, account_uuid):
            raise RuntimeError('检测结果不可验证为当前账号所有')
        if source_task_id:
            owner_where, owner_params = _detection_owner_where(
                user_info.get('Userid'), phone, backend_openid, account_uuid
            )
            linked = excute_detection_sql(
                f"""
                UPDATE data SET developer_task_id = %s
                WHERE itemid = %s AND ({owner_where})
                  AND (developer_task_id IS NULL OR developer_task_id = %s)
                """,
                (source_task_id, remote_itemid, *owner_params, source_task_id),
                fetch=False,
            )
            if linked != 1:
                raise RuntimeError('检测结果任务幂等标识写入失败')
        _materialize_primary_source(
            local_record,
            data,
            image_bytes,
            filename,
            backend_openid,
            phone,
            account_uuid,
        )
        if (data.get('watermark_verdict_override') or {}).get('applied'):
            updated = excute_detection_sql(
                """
                UPDATE data
                SET fake = %s, detector_probability = %s, aigc = %s, clarity = %s, explantation = %s
                WHERE itemid = %s
                """,
                (
                    data.get('fake_percentage'),
                    data.get('detector_probability'),
                    data.get('final_label'),
                    data.get('confidence') or data.get('clarity') or '高',
                    safe_truncate(data.get('explanation') or data.get('explantation') or '', 500),
                    remote_itemid,
                ),
                fetch=False,
            )
            if updated is None:
                raise RuntimeError('水印高置信度结论写入历史失败')
        return remote_itemid
    return _insert_local_detection_record(
        data,
        image_bytes,
        filename,
        backend_openid,
        phone,
        user_info,
        source_task_id,
    )


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
    risk_vector = payload.get('riskVector') if isinstance(payload.get('riskVector'), dict) else {}
    raw_probability = payload.get('aiProbability')
    if raw_probability is None:
        raw_probability = risk_vector.get('aiGenerated')
    if raw_probability is None:
        raw_probability = payload.get('confidence')
    confidence = _to_float(raw_probability, 0.5)
    if confidence > 1:
        confidence = confidence / 100.0
    confidence = max(0.0, min(1.0, confidence))
    # V2 confidence is the calibrated AI-risk score, including for a `real`
    # verdict. It is not confidence in the categorical label and must never be
    # inverted based on the verdict.
    if verdict in ('real', 'suspected_fake', 'highly_suspected_fake', 'fake', 'ai', 'likely_ai_generated'):
        return round(confidence * 100, 1)
    raise ValueError('备用检测服务未返回明确判定')


def _assert_v2_publishable(payload):
    source = str(payload.get('source') or '').strip().lower()
    verdict = str(payload.get('verdict') or '').strip().lower()
    if source not in ('vlm', 'provenance') or verdict in ('', 'unknown'):
        raise RuntimeError('备用检测服务未产生可发布的真实模型结论')


def _insert_v2_fallback_record(payload, image_bytes, filename, backend_openid, phone, user_info):
    _assert_v2_publishable(payload)
    stored_name, file_path = _save_local_upload(image_bytes, backend_openid or phone or 'guest', filename)
    img_format, resolution = get_image_info(file_path)
    file_size = (payload.get('fileMeta') or {}).get('size') or get_file_size_str(file_path)
    aigc_pct = _fake_percentage_from_v2(payload)
    risk_vector = payload.get('riskVector') if isinstance(payload.get('riskVector'), dict) else {}
    tamper_probability = _clamp01(risk_vector.get('tampered'), 0.0)
    deepfake_probability = _clamp01(risk_vector.get('deepfake'), 0.0)
    overall_probability = max(
        _clamp01(payload.get('riskScore', payload.get('confidence')), aigc_pct / 100.0),
        aigc_pct / 100.0,
        tamper_probability,
        deepfake_probability,
    )
    fake_pct = round(overall_probability * 100.0, 1)
    if aigc_pct >= 50:
        final_label = 'AI生成图像'
    elif tamper_probability >= 0.5:
        final_label = '疑似篡改图像'
    elif deepfake_probability >= 0.5:
        final_label = '疑似深伪图像'
    elif str(payload.get('verdict') or '') != 'real':
        final_label = '疑似风险图像'
    else:
        final_label = '真实图像'
    confidence_level = _conf_level_from_score(_conf_score_from_api(payload.get('confidence'), fake_pct))
    explanation = str(payload.get('explanation') or '').strip() or _to_user_explanation(final_label, confidence_level)
    visual_issues = _extract_v2_visual_issues(payload)
    if visual_issues:
        explanation = f"{explanation}\n视觉可疑点\n" + "\n".join(f"- {item}" for item in visual_issues)

    itemid = excute_detection_sql_lastid(
        """
        INSERT INTO data
            (createtime, filename, fake, detector_probability, openid, phone, aigc,
             file_size, img_format, resolution, clarity, explantation, Userid, owner_account_uuid)
        VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            stored_name,
            fake_pct,
            aigc_pct / 100.0,
            backend_openid,
            phone,
            final_label,
            file_size,
            img_format,
            resolution,
            confidence_level,
            safe_truncate(explanation, 500),
            _detection_database_user_id(phone, backend_openid),
            _account_uuid(user_info) or None,
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
            'image_url': f"/api/media/image/{itemid}",
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
                'riskVector': payload.get('riskVector'),
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
    endpoint, timeout = _v2_fallback_endpoint()
    response = _backend_post(
        endpoint,
        headers={'X-Jianzhen-Token': V2_INTERNAL_TOKEN},
        files={'file': (safe_name, io.BytesIO(image_bytes), mimetype or 'application/octet-stream')},
        data={'fileType': 'image'},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    _assert_v2_publishable(payload)
    api_json = _insert_v2_fallback_record(payload, image_bytes, safe_name, backend_openid, phone, user_info)
    fallback_model = _model_by_route('fallback')
    api_json.setdefault('data', {}).update(_route_data(
        fallback_model,
        'fallback',
        provider='internal',
        service='v2',
    ))
    return api_json


def _aliyun_visual_issues(service, normalized):
    label = str((normalized or {}).get('finalLabel') or '').strip()
    labels = [str(item).strip() for item in (normalized or {}).get('labels') or [] if str(item).strip()]
    descriptions = [
        str(item).strip()
        for item in (normalized or {}).get('descriptions') or []
        if str(item).strip()
    ]
    issues = []
    if label and '未发现' not in label:
        issues.append(label)
    issues.extend(labels)
    issues.extend(descriptions)
    deduped = []
    for item in issues:
        if item not in deduped:
            deduped.append(item)
    if not deduped:
        return ['无明显视觉可疑点。']
    return deduped[:6]


def _aliyun_user_explanation(service, normalized, final_label, confidence):
    score = max(0.0, min(1.0, _to_float((normalized or {}).get('riskScore'), 0.5)))
    risk_pct = round(score * 100, 1)
    if service == 'psDetector':
        task = '篡改痕迹'
    elif service == 'recapDetector':
        task = '翻拍风险'
    else:
        task = '生成式内容风险'
    if final_label == 'AI生成图像':
        lines = [f"综合鉴伪分析发现较高{task}，风险评分约 {risk_pct}%。"]
    else:
        lines = [f"综合鉴伪分析未发现明显{task}，风险评分约 {risk_pct}%。"]
    lines.append(f"当前置信度：{confidence}。")
    return "\n".join(lines)


def _insert_aliyun_record(model, aliyun_payload, image_bytes, filename, backend_openid, phone, user_info):
    stored_name, file_path = _save_local_upload(image_bytes, backend_openid or phone or 'guest', filename)
    img_format, resolution = get_image_info(file_path)
    file_size = get_file_size_str(file_path)
    normalized = (aliyun_payload or {}).get('normalized') or {}
    service = (aliyun_payload or {}).get('service') or aliyun_green.service_from_model(model)
    score = max(0.0, min(1.0, _to_float(normalized.get('riskScore'), 0.5)))
    fake_pct = round(score * 100, 1)
    final_label = 'AI生成图像' if fake_pct >= 50 else '真实图像'
    confidence_level = normalized.get('confidence') or _conf_level_from_score(score)
    visual_issues = _aliyun_visual_issues(service, normalized)
    explanation = _aliyun_user_explanation(service, normalized, final_label, confidence_level)
    if visual_issues and visual_issues != ['无明显视觉可疑点。']:
        explanation = f"{explanation}\n视觉可疑点\n" + "\n".join(f"- {item}" for item in visual_issues)

    itemid = excute_detection_sql_lastid(
        """
        INSERT INTO data
            (createtime, filename, fake, openid, phone, aigc,
             file_size, img_format, resolution, clarity, explantation, Userid, owner_account_uuid)
        VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            stored_name,
            fake_pct,
            backend_openid,
            phone,
            final_label,
            file_size,
            img_format,
            resolution,
            confidence_level,
            safe_truncate(explanation, 500),
            _detection_database_user_id(phone, backend_openid),
            _account_uuid(user_info) or None,
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
            'image_url': f"/api/media/image/{itemid}",
            'filename': stored_name,
            'file_size': file_size,
            'img_format': img_format,
            'resolution': resolution,
            'explanation': explanation,
            'visual_issues': visual_issues,
            'agent_reasoning': '',
            'meta': {
                'file_size': file_size,
                'img_format': img_format,
                'resolution': resolution,
            },
            **_route_data(
                model,
                'primary',
                provider='aliyun',
                service=service,
                latencyMs=(aliyun_payload or {}).get('latencyMs'),
            ),
        },
    }


def _detect_with_aliyun_primary(model, image_bytes, safe_name, backend_openid, phone, user_info):
    service = aliyun_green.service_from_model(model)
    payload = aliyun_green.detect_image_bytes(service, image_bytes, safe_name)
    return _insert_aliyun_record(model, payload, image_bytes, safe_name, backend_openid, phone, user_info)


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
    # Compatibility alias: every public image submission must pass through the
    # durable queue, server-side guest allowance, and shared GPU admission.
    return image_detect_async()


def _run_image_detection_payload(
    image_bytes,
    filename,
    mimetype,
    user_info,
    *,
    is_guest=False,
    mark_guest=True,
    include_internal_evidence=False,
    freeze_evidence=True,
    source_task_id='',
):
    backend_openid, phone = _backend_identity(user_info)
    safe_name = secure_filename(filename) or filename
    if not image_bytes:
        return {'status': 'error', 'message': '请上传非空图片文件'}, 400

    primary_model = _primary_image_model()
    primary_endpoint, primary_timeout, primary_error = _primary_image_endpoint()
    if not primary_endpoint:
        try:
            api_json = _detect_with_v2_fallback(image_bytes, safe_name, mimetype, backend_openid, phone, user_info)
        except Exception as fallback_error:
            return {
                'status': 'error',
                'message': (
                    f'{primary_error}。V2 兜底已启用但调用失败: {str(fallback_error)}'
                )
            }, 502
        if not api_json:
            return {
                'status': 'error',
                'message': (
                    f'{primary_error}。后台未启用 V2 兜底；系统不会静默切换模型。'
                )
            }, 503

    if primary_endpoint and aliyun_green.is_aliyun_model(primary_model):
        try:
            api_json = _detect_with_aliyun_primary(primary_model, image_bytes, safe_name, backend_openid, phone, user_info)
        except Exception as e:
            try:
                api_json = _detect_with_v2_fallback(image_bytes, safe_name, mimetype, backend_openid, phone, user_info)
            except Exception as fallback_error:
                return {
                    'status': 'error',
                    'message': (
                        f'图像鉴伪服务暂不可用：主模型调用失败 ({str(e)})，'
                        f'且 V2 兜底调用失败: {str(fallback_error)}'
                    )
                }, 502
            if not api_json:
                return {
                    'status': 'error',
                    'message': (
                        f'图像鉴伪服务暂不可用：主模型调用失败 ({str(e)})，'
                        '且后台未启用 V2 兜底或缺少 REALGUARD_V2_INTERNAL_TOKEN'
                    )
                }, 502
    elif primary_endpoint:
        try:
            api_resp = _backend_post(
                primary_endpoint,
                files={'image_file': (safe_name, io.BytesIO(image_bytes), mimetype)},
                data={
                    'openid': backend_openid,
                    'phone': phone,
                    'account_uuid': _account_uuid(user_info),
                    'source_task_id': str(source_task_id or '').strip(),
                },
                timeout=primary_timeout,
            )
            api_resp.raise_for_status()
            api_json = api_resp.json()
            api_json.setdefault('data', {}).update(_route_data(primary_model, 'primary'))
        except requests.RequestException as e:
            upstream = getattr(e, 'response', None)
            upstream_status = int(getattr(upstream, 'status_code', 0) or 0)
            if upstream_status in {413, 415, 429}:
                try:
                    upstream_payload = upstream.json()
                except (TypeError, ValueError):
                    upstream_payload = {}
                return {
                    'status': 'error',
                    'code': upstream_payload.get('errorCode') or (
                        'gpu_queue_full' if upstream_status == 429 else 'upstream_rejected'
                    ),
                    'message': upstream_payload.get('msg') or str(e),
                    'retryAfter': str(upstream.headers.get('Retry-After') or '') if upstream is not None else '',
                }, upstream_status
            try:
                api_json = _detect_with_v2_fallback(image_bytes, safe_name, mimetype, backend_openid, phone, user_info)
            except Exception as fallback_error:
                return {
                    'status': 'error',
                    'message': (
                        f'图像鉴伪服务暂不可用：V1 主模型调用失败 ({str(e)})，'
                        f'且 V2 兜底调用失败: {str(fallback_error)}'
                    )
                }, 502
            if not api_json:
                return {
                    'status': 'error',
                    'message': (
                        f'图像鉴伪服务暂不可用：V1 主模型调用失败 ({str(e)})，'
                        '且后台未启用 V2 兜底或缺少 REALGUARD_V2_INTERNAL_TOKEN'
                    )
                }, 502
        except ValueError:
            return {'status': 'error', 'message': '图像鉴伪后端返回了非 JSON 数据'}, 502

    if api_json.get('code') != 200:
        return {'status': 'error', 'message': api_json.get('msg', '图像鉴伪失败')}, 400

    try:
        data = api_json.get('data') or {}
        visible_watermark = _visible_watermark_from_backend_data(data)
        model_decision = _model_decision_from_backend_data(data)
        model_decision_ready = _backend_model_decision_is_publishable(data)
        if isinstance(visible_watermark, dict):
            watermark_verdict.apply_to_backend_data(data, visible_watermark)
        data_itemid = _ensure_local_primary_record(
            api_json,
            image_bytes,
            safe_name,
            backend_openid,
            phone,
            user_info,
            source_task_id,
        )
        fake_pct = _to_float(data.get('fake_percentage', 0), 0.0)
        probability = _prob01_from_percent(fake_pct)
        detector_probability = _clamp01(data.get('detector_probability'), probability)
        final_label = data.get('final_label') or ('AI生成图像' if fake_pct >= 50 else '真实图像')
        confidence = data.get('confidence') or data.get('clarity') or ''
        metadata = _metadata_for_item(data_itemid) if data_itemid else {}
        if not metadata and isinstance(data.get('full_exif_info'), dict):
            metadata = data.get('full_exif_info') or {}
        capture = _capture_evidence_for_metadata(metadata)
        if not model_decision_ready:
            probability = 0.5
            detector_probability = 0.5
            probability_model = {
                'version': 'review-only-model-gate-v1',
                'publishable': False,
                'factors': [],
                'calibrationStatus': 'independent_calibration_required',
            }
            metadata_probability = _clamp01(
                _swarm_metadata_expert({'all_metadata': metadata}).get('score'), 0.5
            )
            final_label = '需人工复核'
            confidence = '低'
        else:
            _diagnostic_probability, probability_model, metadata_probability = _fuse_fast_metadata_probability(
                probability,
                metadata,
            )
            probability_model = dict(probability_model or {})
            probability_model['publishable'] = False
            probability_model['decisionContribution'] = 'diagnostic_only'
            probability_model['note'] = (
                '元数据融合尚未单独校准，仅作并列诊断；公开分数和标签保持签名主模型输出。'
            )
        explanation = data.get('explanation') or data.get('explantation') or _to_user_explanation(
            final_label, confidence, has_metadata=bool(metadata)
        )
        split_explanation, split_issues = _split_reasoning_sections(explanation)
        if split_explanation:
            explanation = split_explanation
        visual_issues_source = data.get('visual_issues') or split_issues
        visual_issues = _normalize_visual_issues(visual_issues_source, final_label=final_label)
        agent_reasoning = data.get('agent_reasoning') or ''
        public_agent_reasoning = _public_agent_reasoning(agent_reasoning)
        _record_model_run(data_itemid, data, user_info)

        if mark_guest:
            _mark_guest_detection_used(is_guest)
        result = {
            'itemid': data_itemid,
            'final_label': final_label,
            'probability': probability,
            'detector_probability': detector_probability,
            'p_visual': None,
            'p_metadata': metadata_probability,
            'confidence': confidence,
            'explanation': explanation,
            'agent_reasoning': public_agent_reasoning,
            'llm_used': bool(public_agent_reasoning),
            'visual_issues': visual_issues,
            'image_url': f"/api/media/image/{data_itemid}" if data_itemid else '',
            'filename': data.get('filename') or safe_name,
            'file_size': data.get('file_size') or (data.get('meta') or {}).get('file_size', ''),
            'img_format': data.get('img_format') or (data.get('meta') or {}).get('img_format', ''),
            'resolution': data.get('resolution') or (data.get('meta') or {}).get('resolution', ''),
            'all_metadata': metadata,
            'capture_evidence': capture,
            'feedback': None,
        }
        result['modelDecisionReady'] = model_decision_ready
        result['reviewRequired'] = not model_decision_ready
        result['decisionStatus'] = 'verdict' if model_decision_ready else 'review_only'
        result['decisionAuthority'] = 'calibrated_model' if model_decision_ready else 'none'
        watermark_complete = bool(
            isinstance(visible_watermark, dict)
            and visible_watermark.get('supported') is True
        )
        evidence_warnings = []
        if not watermark_complete:
            evidence_warnings.append(
                '可见水印检测未完成，本次证据链不完整；不能据此判断图片未含水印。'
            )
        if not model_decision_ready:
            evidence_warnings.append(
                '主鉴伪模型缺少完整且通过验证的独立校准契约，原始分数不用于自动真假结论。'
            )
        if probability_model.get('factors'):
            result['probabilityModel'] = probability_model
        watermark_override_applied = False
        if isinstance(visible_watermark, dict):
            result['visibleWatermark'] = visible_watermark
            if watermark_verdict.apply_to_result(result, visible_watermark):
                watermark_override_applied = True
                result['reviewRequired'] = False
                result['decisionStatus'] = 'verdict'
                result['decisionAuthority'] = 'decisive_provenance'
                result.pop('probabilityModel', None)
        result['evidenceCompleteness'] = bool(
            watermark_complete and (model_decision_ready or watermark_override_applied)
        )
        result['evidenceWarnings'] = [
            warning for warning in evidence_warnings
            if not (watermark_override_applied and warning.startswith('主鉴伪模型'))
        ]
        if freeze_evidence:
            _record_final_decision_run(data_itemid, result, user_info, route='primary')
            result['evidenceSnapshotReady'] = _persist_and_freeze_completed_image_result(
                data_itemid,
                result,
                actor=user_info,
                is_guest=is_guest,
            )
        if include_internal_evidence and isinstance(data.get('remote_evidence'), dict):
            result['_remote_evidence'] = data.get('remote_evidence')
        _suppress_review_only_scores(result)
        return {'status': 'success', 'result': result}, 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'status': 'error', 'message': '检测服务处理失败，请稍后重试'}, 500


def image_detect_for_actor(user_info, *, is_guest=False):
    file = request.files.get('image') or request.files.get('file')
    if not file or file.filename == '':
        return jsonify({'status': 'error', 'message': '请上传图片文件'}), 400

    filename = file.filename

    if not allowed_file(filename):
        return jsonify({'status': 'error', 'message': '不支持的文件格式'}), 400

    mimetype = file.mimetype or 'application/octet-stream'
    image_bytes, upload_error = _read_image_upload(file)
    if upload_error:
        return upload_error
    if not BACKGROUND_JOB_SLOTS.acquire(blocking=False):
        return _busy_response()
    try:
        payload, status_code = _run_image_detection_payload(
            image_bytes,
            filename,
            mimetype,
            user_info,
            is_guest=is_guest,
            mark_guest=True,
        )
    finally:
        BACKGROUND_JOB_SLOTS.release()
    return jsonify(payload), status_code


def _swarm_initial_experts():
    experts = []
    for spec in _swarm_specs():
        experts.append({
            'id': spec['id'],
            'name': spec['name'],
            'role': spec['role'],
            'provider': spec.get('provider') or '',
            'weight': spec.get('weight') or 0,
            'status': 'queued',
            'score': None,
            'verdict': '',
            'confidence': '',
            'evidence': [],
            'message': '等待调度',
            'latencyMs': None,
        })
    return experts


def _public_swarm_expert_name(expert, index=0):
    expert_id = str((expert or {}).get('id') or '').lower()
    role = str((expert or {}).get('role') or '').lower()
    if expert_id == 'primary' or 'primary' in role or '主检测' in role:
        return '主鉴伪专家'
    if expert_id == 'metadata' or 'metadata' in role or '元数据' in role:
        return '元数据专家'
    if expert_id == 'v2' or 'multimodal' in role or '语义' in role:
        return '语义复核专家'
    if 'ps' in expert_id or 'tamper' in role or '篡改' in role:
        return '篡改痕迹专家'
    if 'recap' in expert_id or 'recapture' in role or '翻拍' in role:
        return '翻拍风险专家'
    if expert_id == 'c2pa' or 'c2pa' in role or '内容凭证' in role:
        return 'C2PA 凭证专家'
    if expert_id == 'wam' or 'wam' in role or '通用水印' in role:
        return 'WAM 通用水印专家'
    if expert_id == 'visible_watermark' or '可见水印定位' in role or '平台水印' in role:
        return 'AI 平台水印专家'
    if expert_id == 'watermark' or 'watermark' in role or '水印' in role:
        return '隐式水印专家'
    return f'复核专家 {index + 1}'


def _public_swarm_message(expert):
    status = str((expert or {}).get('status') or 'queued')
    if status == 'running':
        return '正在复核'
    if status == 'success':
        return '复核完成'
    if status == 'failed':
        return '该专家暂不可用'
    if status == 'skipped':
        return '已跳过'
    return '等待调度'


def _public_swarm_verdict(expert):
    status = str((expert or {}).get('status') or 'queued')
    if status == 'failed':
        return '该专家暂不可用'
    if status == 'skipped':
        return '已跳过'
    if str((expert or {}).get('id') or '') == 'visible_watermark' and status == 'success':
        visible = (expert or {}).get('visibleWatermark')
        if isinstance(visible, dict):
            return swarm_visible_watermark_expert._expert_verdict(visible)
        verdict = str((expert or {}).get('verdict') or '').strip()
        if verdict:
            return verdict
        count = int((expert or {}).get('watermarkCount') or 0)
        return f'定位 {count} 处可见水印' if count else '未检出可见水印'
    if status != 'success' or (expert or {}).get('score') is None:
        return '等待复核'
    score = _clamp01((expert or {}).get('score'), 0.5)
    if score >= 0.65:
        return '发现较高风险'
    if score >= 0.5:
        return '发现可疑风险'
    return '未发现明显高风险'


def _public_swarm_expert(expert, index=0):
    public_name = _public_swarm_expert_name(expert, index)
    return {
        'id': f'expert-{index + 1}',
        'publicId': f'expert-{index + 1}',
        'status': (expert or {}).get('status') or 'queued',
        'publicName': public_name,
        'publicMessage': _public_swarm_message(expert),
        'publicVerdict': _public_swarm_verdict(expert),
    }


def _public_swarm_evidence(experts, swarm):
    successful = [
        expert for expert in experts or []
        if expert.get('status') == 'success' and expert.get('score') is not None
    ]
    effective = int((swarm or {}).get('effectiveExperts') or len(successful))
    total = int((swarm or {}).get('totalExperts') or len(experts or []))
    consensus = _clamp01((swarm or {}).get('consensusScore'), 0.0)
    disagreement = bool((swarm or {}).get('disagreement'))
    lines = [f'多源鉴伪会诊已完成，{effective}/{total} 路证据形成有效综合判断。']
    if consensus >= 0.75:
        lines.append('共识信号较强，综合结论稳定。')
    elif consensus > 0:
        lines.append('共识信号中等，建议结合来源可信度综合判断。')
    if disagreement:
        lines.append('不同证据源之间存在分歧，建议进行人工复核。')
    else:
        lines.append('未观察到显著分歧，综合意见可作为本次判断依据。')
    return lines[:4]


def _public_provenance_summary(summary):
    """Strip internal score values and provider hints from the provenance
    summary so the public swarm response only sees the user-facing headline
    plus aggregate counts."""
    if not isinstance(summary, dict):
        return None
    return {
        'headline': str(summary.get('headline') or ''),
        'aiCount': int(summary.get('ai_count') or 0),
        'realCount': int(summary.get('real_count') or 0),
        'uncertainCount': int(summary.get('uncertain_count') or 0),
        'memberCount': len(summary.get('members') or []),
    }


def _suppress_review_only_scores(result):
    """Remove internal placeholders and uncalibrated scores from public output."""
    if not isinstance(result, dict) or result.get('decisionStatus') == 'verdict':
        return result
    result['probability'] = None
    result['detector_probability'] = None
    result['p_visual'] = None
    result['p_metadata'] = None
    result['confidence'] = '不适用'
    result['scorePublished'] = False
    result.pop('probabilityModel', None)
    swarm = result.get('swarm')
    if isinstance(swarm, dict):
        for field in ('score', 'generatedScore', 'tamperScore', 'recaptureScore', 'riskVector'):
            swarm.pop(field, None)
        swarm['scorePublished'] = False
    return result


def _public_swarm_result(result):
    if not isinstance(result, dict):
        return result
    public_result = copy.deepcopy(result)
    swarm = public_result.get('swarm')
    if isinstance(swarm, dict):
        raw_experts = swarm.get('experts') or []
        public_experts = [_public_swarm_expert(expert, index) for index, expert in enumerate(raw_experts)]
        public_evidence = _public_swarm_evidence(raw_experts, swarm)
        swarm['experts'] = public_experts
        swarm['evidence'] = public_evidence
        if 'provenanceSummary' in swarm:
            swarm['provenanceSummary'] = _public_provenance_summary(swarm.get('provenanceSummary'))
        public_result['visual_issues'] = public_evidence or public_result.get('visual_issues') or []
    return _suppress_review_only_scores(public_result)


def _visible_watermark_from_raw_experts(experts):
    for expert in experts or []:
        if not isinstance(expert, dict) or expert.get('id') != 'primary':
            continue
        remote_evidence = expert.get('remoteEvidence') or {}
        precheck = remote_evidence.get('visibleWatermarkPrecheck')
        update = _swarm_visible_update_from_precheck(precheck)
        if update and isinstance(update.get('visibleWatermark'), dict):
            return update['visibleWatermark']

    for expert in experts or []:
        if not isinstance(expert, dict) or expert.get('id') != 'visible_watermark':
            continue
        visible = expert.get('visibleWatermark')
        if isinstance(visible, dict):
            return copy.deepcopy(visible)
    return None


def _apply_visible_watermark_to_experts(experts, visible):
    if not isinstance(visible, dict):
        return
    count = len(visible.get('hits') or [])
    for expert in experts or []:
        if not isinstance(expert, dict) or expert.get('id') != 'visible_watermark':
            continue
        expert.update({
            'status': 'success',
            'score': None,
            'verdict': swarm_visible_watermark_expert._expert_verdict(visible),
            'confidence': '高' if _to_float(visible.get('confidence'), 0.0) >= 0.8 else ('中' if count else '无'),
            'evidence': [visible.get('note') or '可见水印检测完成。'],
            'watermarkDetected': bool(count),
            'watermarkCount': count,
            'visibleWatermark': copy.deepcopy(visible),
        })


def _public_detection_job(job):
    if not isinstance(job, dict):
        return job
    public_job = copy.deepcopy(job)
    if public_job.get('mode') != 'swarm' and public_job.get('kind') != 'swarm':
        return public_job
    recovered_visible = _visible_watermark_from_raw_experts(public_job.get('experts') or [])
    _apply_visible_watermark_to_experts(public_job.get('experts') or [], recovered_visible)
    public_job['experts'] = [
        _public_swarm_expert(expert, index)
        for index, expert in enumerate(public_job.get('experts') or [])
    ]
    status = str(public_job.get('status') or '')
    progress = int(public_job.get('progress') or 0)
    if status == 'success':
        public_job['summary'] = 'Swarm 专家会诊完成'
    elif status == 'failed':
        public_job['summary'] = 'Swarm 专家会诊暂不可用'
    elif progress > 0:
        public_job['summary'] = '多名鉴伪专家正在复核'
    else:
        public_job['summary'] = '等待专家队列启动'
    if isinstance(public_job.get('result'), dict):
        payload = public_job['result']
        if isinstance(payload.get('result'), dict):
            if isinstance(recovered_visible, dict):
                payload['result']['visibleWatermark'] = copy.deepcopy(recovered_visible)
                swarm = payload['result'].get('swarm')
                if isinstance(swarm, dict):
                    _apply_visible_watermark_to_experts(swarm.get('experts') or [], recovered_visible)
            payload['result'] = _public_swarm_result(payload['result'])
        if isinstance(payload.get('experts'), list):
            _apply_visible_watermark_to_experts(payload['experts'], recovered_visible)
            payload['experts'] = [
                _public_swarm_expert(expert, index)
                for index, expert in enumerate(payload.get('experts') or [])
            ]
    if public_job.get('error'):
        public_job['error'] = 'Swarm 专家会诊暂不可用，请稍后重试'
    return public_job


def _swarm_set_expert(experts, expert_id, **updates):
    for expert in experts:
        if expert.get('id') == expert_id:
            expert.update(updates)
            return expert
    return None


def _swarm_update_job(job_id, experts, progress, summary='', status='running', result=None, error=None):
    if not job_id:
        return
    updates = {
        'status': status,
        'mode': 'swarm',
        'progress': max(0, min(100, int(progress))),
        'experts': experts,
    }
    if summary:
        updates['summary'] = summary
    if result is not None:
        updates['result'] = result
    if error is not None:
        updates['error'] = error
    admin_state.update_detection_job(job_id, updates)


def _swarm_finish_expert(experts, expert_id, started_at, **updates):
    updates.setdefault('latencyMs', int((time.time() - started_at) * 1000))
    return _swarm_set_expert(experts, expert_id, **updates)


def _run_swarm_expert(runner):
    try:
        return runner()
    except Exception as exc:
        return {
            'status': 'failed',
            'score': None,
            'verdict': '调用失败',
            'confidence': '',
            'evidence': [],
            'message': safe_truncate(str(exc), 120),
            'latencyMs': None,
        }


def _swarm_v2_stagger_seconds(image_bytes):
    return min(
        SWARM_V2_MAX_STAGGER_SECONDS,
        len(image_bytes or b'') / SWARM_V2_STAGGER_BYTES_PER_SECOND,
    )


def _swarm_visible_update_from_precheck(payload):
    if not isinstance(payload, dict) or payload.get('status') != 'ok':
        return None
    try:
        visible = swarm_visible_watermark_expert._visible_result(payload)
    except Exception:
        return None
    count = len(visible.get('hits') or [])
    provenance_decision = payload.get('decision') if isinstance(payload.get('decision'), dict) else {}
    provenance_report = payload.get('report') if isinstance(payload.get('report'), dict) else {}
    return {
        'status': 'success',
        'score': None,
        'verdict': swarm_visible_watermark_expert._expert_verdict(visible),
        'confidence': '高' if _to_float(visible.get('confidence'), 0.0) >= 0.8 else ('中' if count else '无'),
        'evidence': [visible.get('note') or 'AI 平台水印识别完成。'],
        'message': f"detected={str(bool(count)).lower()}|count={count}|source=shared-upload",
        'watermarkDetected': bool(count),
        'watermarkCount': count,
        'visibleWatermark': visible,
        'provenanceDecision': provenance_decision,
        'provenanceReport': provenance_report,
        'probabilityModel': provenance_decision.get('probabilityModel'),
        'latencyMs': int(_to_float(payload.get('elapsedMs'), 0.0)),
    }


def _runtime_visible_watermark_for_item(itemid):
    """Recover the latest visible-watermark evidence for an owned history item."""
    target = str(itemid or '')
    if not target:
        return None
    stored_visible = _stored_visible_watermark_for_item(target)
    if isinstance(stored_visible, dict):
        return stored_visible
    for job in admin_state.list_detection_jobs(limit=500):
        wrapped = job.get('result') if isinstance(job, dict) else None
        result = wrapped.get('result') if isinstance(wrapped, dict) else None
        if not isinstance(result, dict) or str(result.get('itemid') or '') != target:
            continue

        recovered = _visible_watermark_from_raw_experts(job.get('experts') or [])
        if isinstance(recovered, dict):
            return recovered

        stored = result.get('visibleWatermark')
        return stored if isinstance(stored, dict) else None
    return None


def _clamp01(value, default=0.5):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


def _first_text_line(value):
    for line in str(value or '').splitlines():
        line = line.strip()
        if line:
            return line
    return ''


def _swarm_primary_expert(
    image_bytes,
    filename,
    mimetype,
    user_info,
    is_guest,
    source_task_id='',
):
    started_at = time.time()
    payload, status_code = _run_image_detection_payload(
        image_bytes,
        filename,
        mimetype,
        user_info,
        is_guest=is_guest,
        mark_guest=False,
        include_internal_evidence=True,
        freeze_evidence=False,
        source_task_id=source_task_id,
    )
    if status_code >= 400 or payload.get('status') == 'error':
        return None, {
            'status': 'failed',
            'score': None,
            'verdict': '主路由不可用',
            'confidence': '',
            'evidence': [],
            'message': payload.get('message') or f'HTTP {status_code}',
            'latencyMs': int((time.time() - started_at) * 1000),
        }
    result = payload.get('result') or {}
    remote_evidence = result.pop('_remote_evidence', {})
    # Metadata and visible-watermark evidence are separate Swarm experts; keep
    # the primary baseline on the raw detector score to avoid counting them twice.
    decision_ready = result.get('modelDecisionReady') is not False
    score = (
        _clamp01(result.get('detector_probability'), result.get('probability', 0.5))
        if decision_ready else None
    )
    evidence = []
    if result.get('visual_issues'):
        evidence.extend([str(item) for item in (result.get('visual_issues') or [])[:2] if str(item).strip()])
    line = _first_text_line(result.get('explanation'))
    if line:
        evidence.append(line)
    return result, {
        'status': 'success',
        'score': round(score, 4) if score is not None else None,
        'verdict': (
            'AI生成图像' if score is not None and score >= 0.5
            else ('真实图像' if score is not None else '需人工复核')
        ),
        'confidence': result.get('confidence') or (
            _conf_level_from_score(score) if score is not None else '低'
        ),
        'evidence': evidence[:3] or ['主路由完成基础图像鉴伪。'],
        'message': '主路由检测完成',
        'latencyMs': int((time.time() - started_at) * 1000),
        'remoteEvidence': remote_evidence if isinstance(remote_evidence, dict) else {},
    }


_AI_METADATA_VALUE_MARKERS = (
    'midjourney', 'stable diffusion', 'automatic1111', 'comfyui', 'fooocus',
    'invokeai', 'novelai', 'dall-e', 'dalle', 'adobe firefly', 'generative fill',
    'dreamstudio', 'ideogram', 'leonardo ai', 'playground ai', 'flux.1',
    'trainedalgorithmicmedia', 'ai generated', 'generated by ai', 'aigc生成', 'ai生成',
)
_AI_METADATA_FIELD_HINTS = (
    'software', 'creatortool', 'usercomment', 'description', 'comment',
    'parameters', 'prompt', 'workflow', 'generator', 'digital_source_type',
)
_NEGATED_METADATA_VALUES = (
    '未检测到', '未发现', '无明显', 'not detected', 'not found', 'negative', 'false',
)


def _explicit_ai_metadata_markers(metadata):
    """Return only high-specificity generator declarations from structured fields."""
    matches = []
    for raw_key, raw_value in (metadata or {}).items():
        key = str(raw_key or '').strip()
        value = str(raw_value or '').strip()
        if not key or not value:
            continue
        normalized_key = key.lower().replace(':', '_').replace('-', '_')
        normalized_value = value.lower()[:6000]
        if any(marker in normalized_value for marker in _NEGATED_METADATA_VALUES):
            continue
        field_is_relevant = any(hint in normalized_key for hint in _AI_METADATA_FIELD_HINTS)
        generator_is_named = any(marker in normalized_value for marker in _AI_METADATA_VALUE_MARKERS)
        dedicated_key = normalized_key.startswith(('aigc_', 'jumbf_'))
        dedicated_positive = normalized_value in {'1', 'true', 'yes', 'positive', 'detected', 'generated'}
        if (field_is_relevant and generator_is_named) or (dedicated_key and (generator_is_named or dedicated_positive)):
            matches.append(f'{key} = {value[:180]}')
    return matches[:4]


def _capture_evidence_for_metadata(metadata):
    metadata = metadata if isinstance(metadata, dict) else {}
    return capture_evidence.analyze_capture_evidence(
        metadata,
        ai_markers=_explicit_ai_metadata_markers(metadata),
    )


def _fuse_fast_metadata_probability(probability, metadata):
    metadata_result = _swarm_metadata_expert({'all_metadata': metadata or {}})
    model = probability_fusion.fuse([
        {
            'id': 'primary',
            'status': 'success',
            'score': _clamp01(probability),
            'weight': 1.0,
        },
        {
            'id': 'metadata',
            'weight': 0.08,
            **metadata_result,
        },
    ])
    fused = _clamp01(model.get('posterior'), probability) if model.get('factors') else _clamp01(probability)
    return fused, model, _clamp01(metadata_result.get('score'), 0.5)


def _swarm_metadata_expert(primary_result):
    metadata = (primary_result or {}).get('all_metadata') or {}
    keys = set(metadata.keys()) if isinstance(metadata, dict) else set()
    capture = _capture_evidence_for_metadata(metadata)
    if not metadata:
        return {
            'status': 'success',
            'score': 0.5,
            'verdict': '缺少元数据',
            'confidence': '低',
            'evidence': ['图像未提供可验证的 EXIF/拍摄设备元数据。'],
            'message': '元数据缺失，保持中性',
            'details': {'verifiedAiMetadata': False, 'editableAiMetadata': False, 'aiMarkers': [], 'captureEvidence': capture},
            'latencyMs': 0,
        }
    ai_markers = _explicit_ai_metadata_markers(metadata)
    if ai_markers:
        score = 0.5
        verdict = '发现可编辑生成参数'
        confidence = '低'
        evidence = [f'可编辑元数据提到生成工具：{ai_markers[0]}；该文本未经签名验证，不影响最终风险分。']
    elif capture.get('level') == 'medium':
        score = 0.28
        verdict = '拍摄链路一致'
        confidence = '中'
        evidence = [capture.get('summary')]
    elif capture.get('level') == 'weak':
        score = 0.4
        verdict = '含部分拍摄线索'
        confidence = '低'
        evidence = [capture.get('summary')]
    elif capture.get('level') == 'conflict':
        score = 0.58
        verdict = '拍摄元数据存在冲突'
        confidence = '低'
        evidence = [capture.get('summary')]
    else:
        score = 0.5
        verdict = '无明确来源信号'
        confidence = '低'
        evidence = ['已提取元数据，但没有明确生成器声明或完整拍摄链路。']
    for item in (capture.get('evidence') or [])[:2]:
        line = f"{item.get('label')}：{item.get('value')}"
        if line not in evidence:
            evidence.append(line)
    evidence.append(f'可读元数据字段数：{len(keys)}。')
    return {
        'status': 'success',
        'score': round(score, 4),
        'verdict': verdict,
        'confidence': confidence,
        'evidence': evidence[:3],
        'message': '元数据取证完成',
        'details': {
            'verifiedAiMetadata': False,
            'editableAiMetadata': bool(ai_markers),
            'aiMarkers': ai_markers,
            'captureEvidence': capture,
        },
        'latencyMs': 0,
    }


def _capture_evidence_from_experts(experts, primary_result=None):
    metadata_expert = next((item for item in experts or [] if item.get('id') == 'metadata'), None)
    details = (metadata_expert or {}).get('details') or {}
    capture = details.get('captureEvidence') if isinstance(details, dict) else None
    if not isinstance(capture, dict):
        capture = _capture_evidence_for_metadata((primary_result or {}).get('all_metadata') or {})

    c2pa_expert = next((item for item in experts or [] if item.get('id') == 'c2pa'), None)
    c2pa_details = (c2pa_expert or {}).get('details') or {}
    chain_sources = set(c2pa_details.get('chain_sources') or []) if isinstance(c2pa_details, dict) else set()
    validation_ok = str(c2pa_details.get('validation_severity') or '') == 'ok'
    camera_only = 'camera' in chain_sources and 'ai' not in chain_sources
    if (
        (c2pa_expert or {}).get('status') == 'success'
        and camera_only
        and validation_ok
        and not c2pa_details.get('chain_conflict')
    ):
        signer = c2pa_details.get('signer') or {}
        generators = c2pa_details.get('generators') or []
        issuer = signer.get('issuer') if isinstance(signer, dict) else ''
        issuer = issuer or (generators[0] if generators else '')
        capture = capture_evidence.add_verified_camera_credential(capture, issuer=str(issuer or ''))
    return capture


def _swarm_v2_expert(image_bytes, filename, mimetype):
    fallback = _model_by_route('fallback')
    if fallback and fallback.get('enabled') is False:
        return {
            'status': 'skipped',
            'score': None,
            'verdict': '已跳过',
            'confidence': '',
            'evidence': [],
            'message': '后台已禁用 V2 兜底模型',
            'latencyMs': 0,
        }
    endpoint, timeout = _v2_fallback_endpoint()
    if not endpoint or not V2_INTERNAL_TOKEN:
        return {
            'status': 'skipped',
            'score': None,
            'verdict': '未配置',
            'confidence': '',
            'evidence': [],
            'message': '缺少 V2 endpoint 或内部 token',
            'latencyMs': 0,
        }
    started_at = time.time()
    response = _backend_post(
        endpoint,
        headers={'X-Jianzhen-Token': V2_INTERNAL_TOKEN},
        files={'file': (secure_filename(filename) or filename, io.BytesIO(image_bytes), mimetype or 'application/octet-stream')},
        data={'fileType': 'image'},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    score = _clamp01(_fake_percentage_from_v2(payload) / 100.0, 0.5)
    issues = _extract_v2_visual_issues(payload)
    explanation = _first_text_line(payload.get('explanation'))
    evidence = issues[:2]
    if explanation:
        evidence.append(explanation)
    return {
        'status': 'success',
        'score': round(score, 4),
        'verdict': '疑似伪造' if score >= 0.5 else '倾向真实',
        'confidence': _conf_level_from_score(_conf_score_from_api(payload.get('confidence'), score * 100)),
        'evidence': evidence[:3] or ['V2 视觉语言复核完成。'],
        'message': 'V2 复核完成',
        'synthid': payload.get('synthid') if isinstance(payload.get('synthid'), dict) else None,
        'latencyMs': int((time.time() - started_at) * 1000),
    }


def _swarm_aliyun_expert(spec, image_bytes, filename):
    model_id = spec.get('modelId')
    model = model_registry.get_model(model_id) if model_id else None
    if not model:
        return {
            'status': 'skipped',
            'score': None,
            'verdict': '未配置',
            'confidence': '',
            'evidence': [],
            'message': f'未找到模型 {model_id or "-"}',
            'latencyMs': 0,
        }
    if model.get('enabled') is False:
        return {
            'status': 'skipped',
            'score': None,
            'verdict': '已禁用',
            'confidence': '',
            'evidence': [],
            'message': '后台模型已禁用',
            'latencyMs': 0,
        }
    if not aliyun_green.is_aliyun_model(model):
        return {
            'status': 'skipped',
            'score': None,
            'verdict': '配置不匹配',
            'confidence': '',
            'evidence': [],
            'message': '该模型不是阿里云鉴伪适配器',
            'latencyMs': 0,
        }
    if not aliyun_green.configured():
        return {
            'status': 'skipped',
            'score': None,
            'verdict': '未配置',
            'confidence': '',
            'evidence': [],
            'message': '缺少阿里云鉴伪密钥',
            'latencyMs': 0,
        }
    service = aliyun_green.service_from_model(model)
    started_at = time.time()
    payload = aliyun_green.detect_image_bytes(service, image_bytes, secure_filename(filename) or filename)
    normalized = (payload or {}).get('normalized') or {}
    score = _clamp01(normalized.get('riskScore'), 0.5)
    evidence = _aliyun_visual_issues(service, normalized)
    return {
        'status': 'success',
        'score': round(score, 4),
        'verdict': normalized.get('finalLabel') or ('高风险' if score >= 0.5 else '低风险'),
        'confidence': normalized.get('confidence') or _conf_level_from_score(score),
        'evidence': evidence[:3],
        'message': '阿里云专家复核完成',
        'latencyMs': int((time.time() - started_at) * 1000),
    }


def _swarm_fallback_display_result(image_bytes, filename, backend_openid, phone):
    try:
        stored_name, file_path = _save_local_upload(image_bytes, backend_openid or phone or 'guest', filename)
        img_format, resolution = get_image_info(file_path)
        file_size = get_file_size_str(file_path)
        # The upload directory is intentionally blocked by Nginx. A protected
        # media URL is attached after the final history row has an item id.
        image_url = ''
        safe_name = stored_name
    except Exception:
        img_format = ''
        resolution = ''
        file_size = ''
        image_url = ''
        safe_name = secure_filename(filename) or filename
    return {
        'itemid': None,
        'final_label': '疑似风险图像',
        'probability': 0.5,
        'detector_probability': 0.5,
        'p_visual': None,
        'p_metadata': None,
        'confidence': '低',
        'explanation': '',
        'agent_reasoning': '',
        'llm_used': False,
        'visual_issues': [],
        'image_url': image_url,
        'filename': safe_name,
        'file_size': file_size,
        'img_format': img_format,
        'resolution': resolution,
        'all_metadata': {},
        'feedback': None,
    }


def _swarm_provenance_summary(experts):
    """Group the provenance-style experts (C2PA, watermark, WAM) and produce a
    single highlight summary that the user-facing evidence can lead with.

    Returns a dict::

        {
          "members": [...],          # which expert ids were considered
          "ai_count": int,           # number that concluded "ai-generated" (score >= 0.6)
          "real_count": int,         # number that concluded "real" (score <= 0.4)
          "uncertain_count": int,    # 0.4 < score < 0.6 or no manifest / unknown
          "headline": str,           # short user-facing line
        }

    Returns ``None`` if no provenance expert produced a usable result."""
    members = []
    ai_count = real_count = uncertain_count = 0
    for expert in experts or []:
        if not isinstance(expert, dict):
            continue
        if expert.get('provenance_kind') not in ('c2pa', 'watermark', 'wam'):
            continue
        if expert.get('status') != 'success' or expert.get('score') is None:
            continue
        score = _clamp01(expert.get('score'), 0.5)
        kind = expert['provenance_kind']
        verdict = expert.get('verdict') or ''
        members.append({'id': expert.get('id'), 'kind': kind, 'score': round(score, 3), 'verdict': verdict})
        if score >= 0.6:
            ai_count += 1
        elif score <= 0.4:
            real_count += 1
        else:
            uncertain_count += 1
    if not members:
        return None
    total = len(members)
    if ai_count > real_count and ai_count >= max(1, total // 2):
        headline = f"来源溯源：{ai_count}/{total} 路证据指向 AI 生成"
    elif real_count > ai_count and real_count >= max(1, total // 2):
        headline = f"来源溯源：{real_count}/{total} 路证据指向真实拍摄"
    elif uncertain_count >= total - 1:
        headline = f"来源溯源：{total} 路凭证/水印均无明确信号"
    else:
        headline = f"来源溯源：{ai_count} 路指向 AI / {real_count} 路指向真实 / {uncertain_count} 路存疑（存在冲突）"
    return {
        'members': members,
        'ai_count': ai_count,
        'real_count': real_count,
        'uncertain_count': uncertain_count,
        'headline': headline,
    }


def _swarm_aggregate(experts, primary_result, fallback_result):
    config = _swarm_config()
    successful = [
        expert for expert in experts
        if expert.get('status') == 'success' and expert.get('score') is not None
    ]
    primary_authorized = bool(
        isinstance(primary_result, dict) and primary_result.get('modelDecisionReady') is True
    )
    primary_review_only = not primary_authorized
    probability_model = probability_fusion.fuse(experts)
    # The fusion policy is not covered by the signed model calibration. Its
    # experts remain diagnostic and cannot independently authorize a verdict.
    decisive_provenance = False

    def review_only_result(reason):
        base = dict(primary_result or fallback_result or {})
        base.update({
            'final_label': '需人工复核',
            'probability': 0.5,
            'detector_probability': 0.5,
            'confidence': '低',
            'modelDecisionReady': False,
            'reviewRequired': True,
            'decisionStatus': 'review_only',
            'decisionAuthority': 'none',
            'probabilityModel': probability_model,
            'explanation': (
                'Swarm 已完成可用证据复核，但主鉴伪模型尚未通过独立校准，'
                f'且当前没有足以独立形成自动结论的证据（{reason}）。请进行人工复核。'
            ),
            'swarm': {
                'enabled': True,
                'score': 0.5,
                'finalLabel': '需人工复核',
                'confidence': '低',
                'consensusLevel': '低',
                'consensusScore': 0.0,
                'disagreement': True,
                'effectiveExperts': len(successful),
                'totalExperts': len(experts),
                'experts': experts,
                'evidence': [reason],
            },
        })
        return base, ''

    if not successful:
        if primary_review_only:
            return review_only_result('没有可发布的已校准模型或来源证据')
        return None, '所有专家均未返回有效结论'
    if not any(expert.get('id') != 'metadata' for expert in successful):
        if primary_review_only:
            return review_only_result('仅元数据专家返回有效结果')
        return None, '主检测与复核专家均未返回有效结论'
    min_experts = max(1, int((config or {}).get('minExperts') or 2))
    if len(successful) < min_experts and not decisive_provenance:
        if primary_review_only:
            return review_only_result(f'有效专家数 {len(successful)}/{min_experts}')
        return None, f'Swarm 有效专家数不足：{len(successful)}/{min_experts}'
    if primary_review_only and not decisive_provenance:
        return review_only_result('二级模型尚无独立校准授权，不能解除主模型复核态')
    if not probability_model.get('publishable'):
        if primary_review_only:
            return review_only_result('缺少已校准生成风险基线或明确 AI 来源证据')
        return None, '缺少可发布的主鉴伪模型或明确 AI 来源证据'
    risk_vector = probability_model.get('riskVector') or {}
    generated_score = _clamp01(risk_vector.get('aiGenerated'), 0.5)
    tamper_score = (
        _clamp01(risk_vector.get('tampered'), 0.5)
        if risk_vector.get('tampered') is not None else None
    )
    recapture_score = (
        _clamp01(risk_vector.get('recaptured'), 0.5)
        if risk_vector.get('recaptured') is not None else None
    )
    score = max(
        [generated_score]
        + ([tamper_score] if tamper_score is not None else [])
        + ([recapture_score] if recapture_score is not None else [])
    )
    baseline_ids = set(probability_model.get('baselineExperts') or [])
    scores = [
        _clamp01(expert.get('score'), 0.5)
        for expert in successful
        if expert.get('id') in baseline_ids
    ]
    spread = max(scores) - min(scores) if scores else 0.0
    consensus_threshold = _clamp01((config or {}).get('consensusThreshold'), 0.65)
    disagreement_threshold = _clamp01((config or {}).get('disagreementThreshold'), 0.35)
    consensus_score = max(0.0, min(1.0, 1.0 - spread))
    consensus_level = '高' if spread <= 0.18 and len(successful) >= 3 else ('中' if spread <= 0.35 else '低')
    confidence = _conf_level_from_score(score)
    if probability_model.get('corroborated') and generated_score >= 0.99:
        confidence = '高'
    elif spread > 0.42:
        confidence = '低'
    elif spread > 0.28 and confidence == '高':
        confidence = '中'
    if generated_score >= consensus_threshold:
        final_label = 'AI生成图像'
    elif tamper_score is not None and tamper_score >= consensus_threshold:
        final_label = '疑似篡改图像'
    elif recapture_score is not None and recapture_score >= consensus_threshold:
        final_label = '疑似翻拍图像'
    elif score >= 0.5:
        final_label = '疑似风险图像'
    else:
        final_label = '真实图像'

    if primary_authorized:
        signed_probability = _clamp01(
            (primary_result or {}).get('probability'),
            (primary_result or {}).get('detector_probability', 0.5),
        )
        score = signed_probability
        generated_score = signed_probability
        final_label = str((primary_result or {}).get('final_label') or '').strip() or (
            'AI生成图像' if signed_probability >= 0.5 else '真实图像'
        )
        confidence = str((primary_result or {}).get('confidence') or confidence)
        probability_model = dict(probability_model or {})
        probability_model['publishable'] = False
        probability_model['decisionContribution'] = 'diagnostic_only'

    provenance_summary = _swarm_provenance_summary(experts)
    capture = _capture_evidence_from_experts(experts, primary_result)

    evidence = []
    if provenance_summary and provenance_summary.get('headline'):
        evidence.append(provenance_summary['headline'])
    for factor in probability_model.get('factors') or []:
        label = str((factor or {}).get('label') or '').strip()
        if label and label not in evidence:
            evidence.append(label)
    ranked = sorted(successful, key=lambda expert: abs(_clamp01(expert.get('score'), 0.5) - 0.5), reverse=True)
    for expert in ranked:
        expert_score = _clamp01(expert.get('score'), 0.5)
        verdict = expert.get('verdict') or ('高风险' if expert_score >= 0.5 else '低风险')
        line = f"{expert.get('name')}: {verdict}，风险 {round(expert_score * 100, 1)}%"
        if line not in evidence:
            evidence.append(line)
        for item in expert.get('evidence') or []:
            clean = str(item or '').strip()
            if clean and clean not in evidence:
                evidence.append(clean)
            if len(evidence) >= 6:
                break
        if len(evidence) >= 6:
            break

    disagreement = spread > disagreement_threshold
    baseline_probability = _clamp01(probability_model.get('pixelBaseline'), score)
    explanation = [
        f"Swarm 专家会诊完成：签名主模型发布分数为 {generated_score * 100:.2f}%，其他专家仅作并列诊断，不改变该分数和标签。",
        f"有效像素专家 {len(probability_model.get('baselineExperts') or [])} 个，专家一致性：{consensus_level}，当前置信度：{confidence}。",
    ]
    if tamper_score is not None:
        explanation.append(f"独立篡改风险为 {tamper_score * 100:.2f}%，不参与 AI 生成概率平均。")
    if recapture_score is not None:
        explanation.append(f"独立翻拍风险为 {recapture_score * 100:.2f}%，不参与 AI 生成概率平均。")
    if probability_model.get('corroborated') and generated_score >= 0.99:
        explanation.append("辅助证据与签名主模型方向一致，但不改变已发布的模型分数或标签。")
    if disagreement:
        explanation.append("部分专家意见存在分歧，建议结合原始来源或人工复核。")
    else:
        explanation.append("有效专家结论整体一致，可作为本次综合判定依据。")

    base = dict(primary_result or fallback_result or {})
    detector_probability = _clamp01(base.get('detector_probability', base.get('probability', score)), score)
    base.update({
        'final_label': final_label,
        'probability': round(score, 4),
        'detector_probability': round(detector_probability, 4),
        'aigc_probability': round(generated_score, 4),
        'tamper_probability': None if tamper_score is None else round(tamper_score, 4),
        'recapture_probability': None if recapture_score is None else round(recapture_score, 4),
        'probabilityModel': probability_model,
        'confidence': confidence,
        'modelDecisionReady': primary_authorized,
        'reviewRequired': False,
        'decisionStatus': 'verdict',
        'decisionAuthority': (
            'calibrated_model' if primary_authorized else 'decisive_provenance'
        ),
        'explanation': "\n".join(explanation),
        'visual_issues': evidence[:6] or ['暂未提取到明确的视觉可疑点。'],
        'agent_reasoning': json.dumps({
            'mode': 'swarm',
            'effectiveExperts': len(successful),
            'totalExperts': len(experts),
            'score': round(score, 4),
            'riskVector': probability_model.get('riskVector'),
            'probabilityModel': probability_model,
            'consensusLevel': consensus_level,
            'disagreement': disagreement,
        }, ensure_ascii=False),
        'llm_used': False,
        'capture_evidence': capture,
        'swarm': {
            'enabled': True,
            'score': round(score, 4),
            'riskVector': probability_model.get('riskVector'),
            'generatedScore': round(generated_score, 4),
            'tamperScore': None if tamper_score is None else round(tamper_score, 4),
            'recaptureScore': None if recapture_score is None else round(recapture_score, 4),
            'finalLabel': final_label,
            'confidence': confidence,
            'consensusLevel': consensus_level,
            'consensusScore': round(consensus_score, 4),
            'disagreement': disagreement,
            'effectiveExperts': len(successful),
            'totalExperts': len(experts),
            'experts': experts,
            'evidence': evidence[:6],
            'provenanceSummary': provenance_summary,
        },
    })
    return base, ''


def _persist_swarm_history_result(
    final_result,
    image_bytes,
    filename,
    backend_openid,
    phone,
    user_info,
    source_task_id='',
):
    itemid = (final_result or {}).get('itemid')
    record = _local_detection_record(itemid)
    expected_filename = str((final_result or {}).get('filename') or '').strip()
    account_uuid = _account_uuid(user_info)
    if _record_matches_detection_actor(record, expected_filename, backend_openid, phone, account_uuid):
        if account_uuid and not claim_detection_record_owner(
            'data', itemid, account_uuid, phone, backend_openid
        ):
            raise RuntimeError('Swarm 结果归属绑定失败')
        fake_pct = round(_clamp01(final_result.get('probability'), 0.5) * 100.0, 2)
        detector_probability = _clamp01(
            final_result.get('detector_probability'),
            fake_pct / 100.0,
        )
        explanation = safe_truncate(final_result.get('explanation') or '', 500)
        updated = excute_detection_sql(
            """
            UPDATE data
            SET fake = %s, detector_probability = %s, aigc = %s,
                clarity = %s, explantation = %s
            WHERE itemid = %s
            """,
            (
                fake_pct,
                detector_probability,
                final_result.get('final_label') or ('AI生成图像' if fake_pct >= 50 else '真实图像'),
                final_result.get('confidence') or _conf_level_from_score(fake_pct / 100.0),
                explanation,
                itemid,
            ),
            fetch=False,
        )
        if updated is None:
            raise RuntimeError('Swarm 最终结论写入历史失败')
        return itemid

    data = {
        'fake_percentage': round(_clamp01(final_result.get('probability'), 0.5) * 100.0, 2),
        'detector_probability': final_result.get('detector_probability'),
        'final_label': final_result.get('final_label'),
        'confidence': final_result.get('confidence'),
        'explanation': final_result.get('explanation'),
        'visual_issues': final_result.get('visual_issues') or [],
    }
    local_itemid = _insert_local_detection_record(
        data,
        image_bytes,
        filename,
        backend_openid,
        phone,
        user_info,
        source_task_id,
    )
    final_result.update({
        'itemid': local_itemid,
        'filename': data.get('filename') or final_result.get('filename'),
        'image_url': f"/api/media/image/{local_itemid}",
        'file_size': data.get('file_size') or final_result.get('file_size'),
        'img_format': data.get('img_format') or final_result.get('img_format'),
        'resolution': data.get('resolution') or final_result.get('resolution'),
    })
    return local_itemid


def _run_swarm_detection_payload(image_bytes, filename, mimetype, user_info, *, is_guest=False, job_id=None):
    backend_openid, phone = _backend_identity(user_info)
    if not image_bytes:
        return {'status': 'error', 'message': '请上传非空图片文件'}, 400
    config = _swarm_config()
    if isinstance(config, dict) and config.get('enabled') is False:
        return {'status': 'error', 'message': 'Swarm 蜂群模式未在后台启用'}, 400
    specs = _swarm_specs()
    enabled_ids = {spec.get('id') for spec in specs}
    if not enabled_ids:
        return {'status': 'error', 'message': 'Swarm 蜂群模式没有启用任何专家'}, 400
    experts = _swarm_initial_experts()
    _swarm_update_job(job_id, experts, 3, 'Swarm 专家队列已创建')

    primary_finished = threading.Event()
    if 'primary' not in enabled_ids:
        primary_finished.set()

    overlap_steps = []
    network_steps = []
    if 'c2pa' in enabled_ids:
        overlap_steps.append((
            'c2pa',
            lambda: swarm_c2pa_expert.run_c2pa_expert(image_bytes, filename, mimetype),
        ))
    if 'watermark' in enabled_ids:
        overlap_steps.append((
            'watermark',
            lambda: swarm_watermark_expert.run_watermark_expert(image_bytes, filename),
        ))
    if 'v2' in enabled_ids:
        stagger_seconds = _swarm_v2_stagger_seconds(image_bytes)

        def run_staggered_v2():
            primary_finished.wait(timeout=stagger_seconds)
            return _swarm_v2_expert(image_bytes, filename, mimetype)

        overlap_steps.append(('v2', run_staggered_v2))
    if 'wam' in enabled_ids:
        network_steps.append((
            'wam',
            lambda: swarm_wam_expert.run_wam_expert(image_bytes, filename, mimetype),
        ))
    for spec in specs:
        if spec.get('provider') == 'aliyun':
            network_steps.append((spec['id'], lambda spec=spec: _swarm_aliyun_expert(spec, image_bytes, filename)))

    future_experts = {}
    for expert_id, runner in overlap_steps:
        _swarm_set_expert(experts, expert_id, status='running', message='正在并行复核')
        future = SWARM_EXPERT_EXECUTOR.submit(_run_swarm_expert, runner)
        future_experts[future] = expert_id
    if future_experts:
        _swarm_update_job(job_id, experts, 8, f'{len(future_experts)} 路错峰复核已启动')

    primary_result = None
    primary_update = {}
    if 'primary' in enabled_ids:
        _swarm_set_expert(experts, 'primary', status='running', message='正在调用主路由鉴伪')
        _swarm_update_job(job_id, experts, 10, '主路由鉴伪专家正在分析')
        primary_result, primary_update = _swarm_primary_expert(
            image_bytes,
            filename,
            mimetype,
            user_info,
            is_guest,
            source_task_id=job_id or '',
        )
        _swarm_set_expert(experts, 'primary', **primary_update)
        primary_finished.set()
        _swarm_update_job(job_id, experts, 24, '主路由鉴伪完成')

    if 'metadata' in enabled_ids:
        _swarm_set_expert(experts, 'metadata', status='running', message='正在读取元数据证据')
        _swarm_update_job(job_id, experts, 32, '元数据取证专家正在分析')
        metadata_update = _swarm_metadata_expert(primary_result or {})
        _swarm_set_expert(experts, 'metadata', **metadata_update)
        _swarm_update_job(job_id, experts, 40, '元数据取证完成')

    if 'visible_watermark' in enabled_ids:
        remote_evidence = primary_update.get('remoteEvidence') or {}
        precheck_payload = remote_evidence.get('visibleWatermarkPrecheck')
        visible_update = _swarm_visible_update_from_precheck(precheck_payload)
        if visible_update:
            _swarm_set_expert(experts, 'visible_watermark', **visible_update)
            _swarm_update_job(job_id, experts, 42, 'AI 平台水印共享取证完成')
        else:
            network_steps.append((
                'visible_watermark',
                lambda: swarm_visible_watermark_expert.run_visible_watermark_expert(
                    image_bytes,
                    filename,
                    mimetype,
                ),
            ))

    for expert_id, runner in network_steps:
        _swarm_set_expert(experts, expert_id, status='running', message='正在并行复核')
        future = SWARM_EXPERT_EXECUTOR.submit(_run_swarm_expert, runner)
        future_experts[future] = expert_id
    if network_steps:
        _swarm_update_job(job_id, experts, 44, f'{len(network_steps)} 路在线复核已并行启动')

    completed = 0
    for future in as_completed(future_experts):
        expert_id = future_experts[future]
        update = future.result()
        _swarm_set_expert(experts, expert_id, **update)
        completed += 1
        progress = 45 + round(50 * completed / max(1, len(future_experts)))
        expert = next((item for item in experts if item.get('id') == expert_id), None)
        expert_name = (expert or {}).get('name') or expert_id
        _swarm_update_job(job_id, experts, progress, f'{expert_name}完成')

    fallback_result = None
    if not primary_result:
        fallback_result = _swarm_fallback_display_result(image_bytes, filename, backend_openid, phone)
    final_result, error = _swarm_aggregate(experts, primary_result, fallback_result)
    if error:
        return {'status': 'error', 'message': error, 'experts': experts}, 502
    v2_expert = next((expert for expert in experts if expert.get('id') == 'v2'), None)
    if v2_expert and isinstance(v2_expert.get('synthid'), dict):
        final_result['synthid'] = v2_expert['synthid']
    visible_expert = next((expert for expert in experts if expert.get('id') == 'visible_watermark'), None)
    if visible_expert and isinstance(visible_expert.get('visibleWatermark'), dict):
        final_result['visibleWatermark'] = visible_expert['visibleWatermark']
        if watermark_verdict.apply_to_result(final_result, visible_expert['visibleWatermark']):
            final_result['reviewRequired'] = False
            final_result['decisionStatus'] = 'verdict'
            final_result['decisionAuthority'] = 'decisive_provenance'
            swarm = final_result.get('swarm') or {}
            swarm.update({
                'score': final_result['probability'],
                'finalLabel': final_result['final_label'],
                'confidence': final_result['confidence'],
            })
            final_result['swarm'] = swarm
    itemid = _persist_swarm_history_result(
        final_result,
        image_bytes,
        filename,
        backend_openid,
        phone,
        user_info,
        job_id or '',
    )
    _record_final_decision_run(itemid, final_result, user_info, route='swarm')
    final_result['evidenceSnapshotReady'] = _persist_and_freeze_completed_image_result(
        itemid,
        final_result,
        actor=user_info,
        is_guest=is_guest,
    )
    _suppress_review_only_scores(final_result)
    payload = {'status': 'success', 'result': final_result}
    _swarm_update_job(job_id, experts, 100, 'Swarm 专家会诊完成', status='success', result=payload)
    return payload, 200


def _run_async_image_job(job_id, image_bytes, filename, mimetype, user_info, is_guest):
    admin_state.update_detection_job(job_id, {
        "status": "running",
        "progress": 44,
        "summary": "主鉴伪模型正在 GPU 推理",
    })
    try:
        payload, status_code = _run_image_detection_payload(
            image_bytes,
            filename,
            mimetype,
            user_info,
            is_guest=is_guest,
            mark_guest=False,
        )
        if status_code >= 400 or payload.get("status") == "error":
            admin_state.update_detection_job(job_id, {
                "status": "failed",
                "error": payload.get("message") or f"HTTP {status_code}",
                "result": payload,
                "progress": 100,
            })
            return
        admin_state.update_detection_job(job_id, {
            "status": "success",
            "result": payload,
            "progress": 100,
            "summary": "主模型检测完成",
        })
    except Exception as exc:
        admin_state.update_detection_job(job_id, {"status": "failed", "error": str(exc), "progress": 100})


def _run_swarm_image_job(job_id, image_bytes, filename, mimetype, user_info, is_guest):
    try:
        payload, status_code = _run_swarm_detection_payload(
            image_bytes,
            filename,
            mimetype,
            user_info,
            is_guest=is_guest,
            job_id=job_id,
        )
        if status_code >= 400 or payload.get("status") == "error":
            admin_state.update_detection_job(job_id, {
                "status": "failed",
                "error": payload.get("message") or f"HTTP {status_code}",
                "result": payload,
                "progress": 100,
            })
    except Exception as exc:
        admin_state.update_detection_job(job_id, {"status": "failed", "error": str(exc), "progress": 100})


@image_upload_blueprint.route('/image_upload/detect_async', methods=['POST'])
def image_detect_async():
    user_info, is_guest, auth_error = _detection_actor()
    if auth_error:
        return auth_error
    file = request.files.get('image') or request.files.get('file')
    if not file or file.filename == '':
        return jsonify({'status': 'error', 'message': '请上传图片文件'}), 400
    if not allowed_file(file.filename):
        return jsonify({'status': 'error', 'message': '不支持的文件格式'}), 400
    mimetype = file.mimetype or 'application/octet-stream'
    image_bytes, upload_error = _read_image_upload(file)
    if upload_error:
        return upload_error
    job = admin_state.create_detection_job(user_info, file.filename, kind='image')
    started, error_code, error_message = _enqueue_persistent_web_job(
        job,
        image_bytes,
        file.filename,
        mimetype,
        dict(user_info or {}),
        is_guest,
    )
    if not started:
        admin_state.update_detection_job(job['id'], {
            'status': 'failed', 'error': error_message, 'progress': 100,
        })
        if error_code == 'server_busy':
            return _busy_response()
        return jsonify({
            'status': 'error',
            'code': error_code,
            'message': error_message,
        }), 503
    _mark_guest_detection_used(is_guest)
    return jsonify({'status': 'success', 'job': _public_detection_job(job)}), 202


@image_upload_blueprint.route('/image_upload/detect_swarm', methods=['GET', 'POST'])
def image_detect_swarm():
    if request.method == 'GET':
        accept = request.headers.get('Accept', '')
        if 'application/json' in accept:
            return jsonify({
                'status': 'success',
                'message': 'Swarm 检测接口已就绪。请使用 POST multipart/form-data 上传 image 文件。',
            })
        return redirect('/image_upload', code=302)
    user_info, is_guest, auth_error = _detection_actor()
    if auth_error:
        return auth_error
    if is_guest:
        return jsonify({
            'status': 'error',
            'code': 'authentication_required',
            'message': 'Swarm 深度检测需要登录后使用',
        }), 401
    file = request.files.get('image') or request.files.get('file')
    if not file or file.filename == '':
        return jsonify({'status': 'error', 'message': '请上传图片文件'}), 400
    if not allowed_file(file.filename):
        return jsonify({'status': 'error', 'message': '不支持的文件格式'}), 400
    mimetype = file.mimetype or 'application/octet-stream'
    image_bytes, upload_error = _read_image_upload(file)
    if upload_error:
        return upload_error
    experts = _swarm_initial_experts()
    job = admin_state.create_detection_job(user_info, file.filename, kind='swarm', mode='swarm', experts=experts)
    started, error_code, error_message = _enqueue_persistent_web_job(
        job,
        image_bytes,
        file.filename,
        mimetype,
        dict(user_info or {}),
        is_guest,
    )
    if not started:
        admin_state.update_detection_job(job['id'], {
            'status': 'failed', 'error': error_message, 'progress': 100,
        })
        if error_code == 'server_busy':
            return _busy_response()
        return jsonify({
            'status': 'error',
            'code': error_code,
            'message': error_message,
        }), 503
    _mark_guest_detection_used(is_guest)
    return jsonify({'status': 'success', 'job': _public_detection_job(job)}), 202


@image_upload_blueprint.route('/image_upload/jobs/<job_id>')
def image_detection_job(job_id):
    try:
        job = _load_persistent_web_job(job_id)
    except Exception as exc:
        print(f"[WEB TASK QUERY ERROR] {job_id}: {exc}")
        return jsonify({
            'status': 'error',
            'code': 'queue_unavailable',
            'message': '任务状态暂时不可用，请稍后重试',
        }), 503
    if not job:
        job = admin_state.get_detection_job(job_id)
    if not job:
        return jsonify({'status': 'error', 'message': '任务不存在'}), 404
    owner = job.get('actor') or {}
    user_id, phone, openid, is_guest = _detection_owner()
    allowed = _runtime_owner_matches(
        owner, user_id, phone, openid, is_guest, _account_uuid(session.get('user_info'))
    )
    if not allowed:
        return jsonify({'status': 'error', 'message': '无权查看该任务'}), 403
    return jsonify({'status': 'success', 'job': _public_detection_job(job)})


@image_upload_blueprint.route('/image_upload/feedback', methods=['POST'])
def image_detection_feedback():
    """用户对检测结果点赞/点踩/取消，写入 data.feedback（1 / -1 / NULL）"""
    user_id, phone, openid, is_guest = _detection_owner()
    if is_guest and not openid:
        return jsonify({'status': 'error', 'message': '当前访客会话已失效，请重新提交检测'}), 401

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

    db_text = None
    if db_feedback == 1:
        db_text = '满意'
    elif db_feedback == -1:
        db_text = '不满意'
    if is_guest:
        owner_where = "Userid IS NULL AND (phone IS NULL OR phone = '') AND openid = %s"
        owner_params = (openid,)
    else:
        owner_where, owner_params = _detection_owner_where(
            user_id, phone, openid, _account_uuid(session.get('user_info'))
        )
    sql = f"UPDATE data SET feedback = %s WHERE itemid = %s AND ({owner_where})"
    n = excute_detection_sql(sql, (db_text, itemid, *owner_params), fetch=False)
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
    detector_probability = _clamp01(item.get('detector_probability'), _prob01_from_percent(fake_pct))
    image_url = _backend_static_url('image', item)

    all_metadata = _metadata_for_item(itemid)
    stored_label = str(item.get('aigc') or '').strip()
    fake = _prob01_from_percent(fake_pct)
    probability_model = {}
    metadata_probability = None
    final_label = stored_label or ('AI生成图像' if fake >= 0.5 else '真实图像')
    decision = _stored_decision_authorization_for_item(itemid)
    legacy_calibration_unknown = (
        decision.get('status') != 'verdict' and final_label != '需人工复核'
    )
    review_required = final_label == '需人工复核' or legacy_calibration_unknown
    if review_required:
        fake = 0.5
        detector_probability = 0.5
        final_label = '需人工复核'
    feedback_raw = item.get('feedback')
    feedback = 1 if feedback_raw in (1, '1', '满意') else (-1 if feedback_raw in (-1, '-1', '不满意') else None)

    explanation = item.get('explantation') or _to_user_explanation(final_label, item.get('clarity', ''), has_metadata=bool(all_metadata))
    split_explanation, split_issues = _split_reasoning_sections(explanation)
    if split_explanation:
        explanation = split_explanation
    visual_issues = _normalize_visual_issues(split_issues, final_label=final_label)

    result = {
        'itemid': item.get('itemid'),
        'final_label': final_label,
        'probability': fake,
        'detector_probability': detector_probability,
        'p_metadata': metadata_probability,
        'confidence': '低' if review_required else item.get('clarity', ''),
        'explanation': (
            '该历史记录缺少可验证的已校准模型授权或决定性来源证据，原始分数不作为自动真假结论。'
            if legacy_calibration_unknown else explanation
        ),
        'agent_reasoning': '',
        'visual_issues': visual_issues,
        'image_url': image_url,
        'filename': filename,
        'file_size': item.get('file_size', ''),
        'img_format': item.get('img_format', ''),
        'resolution': item.get('resolution', ''),
        'all_metadata': all_metadata,
        'capture_evidence': _capture_evidence_for_metadata(all_metadata),
        'feedback': feedback,
        'reviewRequired': review_required,
        'modelDecisionReady': False if review_required else None,
        'decisionStatus': 'review_only' if review_required else 'verdict',
        'decisionAuthority': decision.get('authority') or 'none',
        'legacyCalibrationUnknown': legacy_calibration_unknown,
    }
    if probability_model.get('factors'):
        result['probabilityModel'] = probability_model
    visible_watermark = _runtime_visible_watermark_for_item(item.get('itemid'))
    if isinstance(visible_watermark, dict):
        result['visibleWatermark'] = visible_watermark
    _suppress_review_only_scores(result)
    return jsonify({'status': 'success', 'result': result})


@image_upload_blueprint.route('/image_upload/report')
def image_report_api():
    itemid = request.args.get('itemid')
    if not itemid:
        return jsonify({'status': 'error', 'message': '缺少参数'}), 400

    item = _load_detection_record('data', itemid)
    if not item:
        return jsonify({'status': 'error', 'message': '未找到该检测记录'}), 404

    fake_pct = _to_float(item.get('fake', 0), 0.0)
    report_metadata = _metadata_for_item(itemid)
    detector_probability = _clamp01(item.get('detector_probability'), _prob01_from_percent(fake_pct))
    report_probability = _prob01_from_percent(fake_pct)
    probability_model = {}
    metadata_probability = None
    report_label = str(item.get('aigc') or '').strip() or (
        'AI生成图像' if report_probability >= 0.5 else '真实图像'
    )
    report_decision = _stored_decision_authorization_for_item(itemid)
    legacy_calibration_unknown = (
        report_decision.get('status') != 'verdict' and report_label != '需人工复核'
    )
    if legacy_calibration_unknown:
        return jsonify({
            'status': 'error',
            'code': 'legacy_calibration_unknown',
            'message': '该历史记录缺少可验证的自动决策授权，请完成人工复核后再生成报告。',
        }), 409
    report_review_required = report_label == '需人工复核'
    if report_review_required:
        detector_probability = report_probability
    result = {
        'itemid': item.get('itemid'),
        'final_label': report_label,
        'probability': report_probability,
        'detector_probability': detector_probability,
        'p_metadata': metadata_probability,
        'confidence': item.get('clarity', ''),
        'explanation': item.get('explantation') or _to_user_explanation(
            report_label,
            item.get('clarity', ''),
            has_metadata=bool(report_metadata),
        ),
        'image_url': _backend_static_url('image', item),
        'filename': item.get('filename', ''),
        'file_size': item.get('file_size', ''),
        'img_format': item.get('img_format', ''),
        'resolution': item.get('resolution', ''),
        'visual_issues': _normalize_visual_issues([], final_label=report_label),
        'all_metadata': report_metadata,
        'capture_evidence': _capture_evidence_for_metadata(report_metadata),
        'reviewRequired': report_review_required,
        'modelDecisionReady': False if report_review_required else None,
        'decisionStatus': 'review_only' if report_review_required else 'verdict',
        'decisionAuthority': report_decision.get('authority') or 'none',
    }
    if probability_model.get('factors'):
        result['probabilityModel'] = probability_model
    visible_watermark = _runtime_visible_watermark_for_item(item.get('itemid'))
    if isinstance(visible_watermark, dict):
        result['visibleWatermark'] = visible_watermark
    _suppress_review_only_scores(result)
    pdf = reporting.image_report_pdf(item, result)
    return Response(
        pdf,
        mimetype='application/pdf',
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
        return jsonify({'status': 'error', 'message': '请上传视频文件'}), 400
    if video_url and not ALLOW_REMOTE_VIDEO_URLS:
        return jsonify({'status': 'error', 'message': '远程视频 URL 已禁用，请直接上传视频文件'}), 400
    if video_url and not _validate_public_video_url(video_url):
        return jsonify({'status': 'error', 'message': '视频 URL 必须是可公开访问的 HTTP(S) 地址'}), 400

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
            upload_size = _seekable_upload_size(file)
            if upload_size is not None and upload_size > MAX_VIDEO_UPLOAD_BYTES:
                return jsonify({
                    'status': 'error',
                    'code': 'video_too_large',
                    'message': f'视频不能超过 {max(1, MAX_VIDEO_UPLOAD_BYTES // (1024 * 1024))} MB',
                }), 413
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
    account_uuid = _account_uuid(user_info)
    if itemid and account_uuid and not claim_detection_record_owner('video_data', itemid, account_uuid):
        return jsonify({
            'status': 'error',
            'message': '视频检测完成，但后端未返回可验证的账号归属；结果已拒绝展示',
        }), 502
    fake_pct = _to_float(data.get('fake_percentage', 0), 0.0)
    conf_score = None
    final_label = '需人工复核'
    explanation = (
        '视频抽帧与时序分析已完成，但当前视频模型及聚合策略尚未通过独立签名校准，'
        '自动真假分数不对外发布。请结合原始视频、可疑片段与人工复核形成结论。'
    )
    conf_level = '不适用'
    meta = data.get('meta') or {}

    duration = meta.get('duration', '')
    resolution = meta.get('resolution', '')
    video_format = meta.get('video_format', '')
    frame_count = data.get('frame_count', 0)
    d3_std = data.get('d3_std', None)
    encoder = data.get('encoder', '')
    record = None
    if itemid:
        record = _load_detection_record('video_data', itemid)
    video_file_url = _backend_static_url('video', record or {'openid': backend_openid, 'filename': ''})

    _mark_guest_detection_used(is_guest)
    return jsonify({
        'status': 'success',
        'result': {
            'itemid': itemid,
            'filename': (record or {}).get('filename', ''),
            'video_url': video_file_url,
            'fake_percentage': None,
            'real_percentage': None,
            'final_label': final_label,
            'confidence_score': conf_score,
            'confidence': conf_level,
            'decisionStatus': 'review_only',
            'decisionAuthority': 'none',
            'reviewRequired': True,
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
    return jsonify({
        'status': 'success',
        'result': {
            'itemid': item.get('itemid'),
            'filename': item.get('filename', ''),
            'video_url': _backend_static_url('video', item),
            'fake_percentage': None,
            'real_percentage': None,
            'final_label': '需人工复核',
            'confidence_score': None,
            'confidence': '不适用',
            'decisionStatus': 'review_only',
            'decisionAuthority': 'none',
            'reviewRequired': True,
            'explanation': '该历史视频结果缺少独立签名校准与逐帧聚合审计，只能用于人工复核。',
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

    result = {
        'itemid': item.get('itemid'),
        'filename': item.get('filename', ''),
        'video_url': _backend_static_url('video', item),
        'fake_percentage': None,
        'real_percentage': None,
        'final_label': '需人工复核',
        'confidence': '不适用',
        'decisionStatus': 'review_only',
        'decisionAuthority': 'none',
        'reviewRequired': True,
        'explanation': '视频模型与逐帧聚合策略尚未通过独立签名校准，本报告不发布自动真假分数。',
        'frame_count': item.get('frame_count', 0),
        'encoder': item.get('encoder', ''),
        'meta': {
            'file_size': item.get('file_size', ''),
            'duration': item.get('duration', ''),
            'resolution': item.get('resolution', ''),
            'video_format': item.get('video_format', ''),
        },
    }
    pdf = reporting.video_report_pdf(item, result)
    return Response(
        pdf,
        mimetype='application/pdf',
        headers={'Content-Disposition': reporting.attachment_header(reporting.video_report_filename(itemid))},
    )
