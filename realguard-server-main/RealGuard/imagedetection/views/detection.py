import os
import json
import uuid
import io
import copy
import requests
import threading
import time
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, session, jsonify, Response, redirect
from werkzeug.utils import secure_filename

from imagedetection.views import (
    admin_state,
    aliyun_green,
    model_registry,
    reporting,
    swarm_c2pa_expert,
    swarm_wam_expert,
    swarm_watermark_expert,
)
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
IMAGE_DETECT_FALLBACK = os.environ.get('REALGUARD_IMAGE_DETECT_FALLBACK', '0').strip().lower()
VIDEO_DETECT_TIMEOUT_NORMAL = 120
IMAGE_DETECT_TIMEOUT = 180
V2_DETECT_TIMEOUT = int(os.environ.get('REALGUARD_V2_DETECT_TIMEOUT', '180'))
GUEST_DETECTION_SESSION_KEY = 'guest_detection_count'
GUEST_DETECTION_LIMIT = int(os.environ.get('REALGUARD_GUEST_DETECTION_LIMIT', '1'))
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
        'id': 'wam',
        'name': 'WAM 通用水印专家',
        'role': '通用水印',
        'provider': 'wam',
        'weight': 0.08,
    },
]


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


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on', 'v2', 'auto')


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


def _record_model_run(itemid, data, user_info):
    if not itemid:
        return
    model_id = str((data or {}).get('_route_model_id') or '').strip()
    if not model_id:
        return
    model = model_registry.get_model(model_id) or {'id': model_id}
    try:
        admin_state.append_model_run(
            itemid,
            model,
            route=(data or {}).get('_route_role') or 'primary',
            status='success',
            actor=user_info,
            meta={
                'provider': (data or {}).get('_route_provider') or '',
                'service': (data or {}).get('_route_service') or '',
                'latencyMs': (data or {}).get('_route_latency_ms'),
                'fallback': (data or {}).get('_route_role') == 'fallback',
            },
        )
    except Exception as exc:
        print(f"[MODEL RUN LOG ERROR] {exc}")


def _primary_image_endpoint():
    model = _primary_image_model()
    timeout = int(model.get('timeoutSeconds') or IMAGE_DETECT_TIMEOUT) if model else IMAGE_DETECT_TIMEOUT
    if model and model.get('enabled') is False:
        return '', timeout, 'V1 主检测模型已在后台禁用，请联系管理员启用主模型或调整路由策略'
    if model and aliyun_green.is_aliyun_model(model):
        return str(model.get('endpoint') or '').strip(), timeout, ''
    if model and model.get('id') == 'v1-onnx-mil':
        artifact_ready, warnings, _ = model_registry.model_artifact_ready(model)
        if not artifact_ready:
            return '', timeout, 'V1 主检测模型文件未就绪：' + '；'.join(warnings)
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
            (createtime, filename, fake, openid, phone, aigc,
             file_size, img_format, resolution, clarity, explantation, Userid)
        VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            safe_truncate(explanation, 145),
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
             file_size, img_format, resolution, clarity, explantation, Userid)
        VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            safe_truncate(explanation, 145),
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
    user_info, is_guest, auth_error = _detection_actor()
    if auth_error:
        return auth_error
    return image_detect_for_actor(user_info, is_guest=is_guest)


def _run_image_detection_payload(image_bytes, filename, mimetype, user_info, *, is_guest=False, mark_guest=True):
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
                data={'openid': backend_openid, 'phone': phone},
                timeout=primary_timeout,
            )
            api_resp.raise_for_status()
            api_json = api_resp.json()
            api_json.setdefault('data', {}).update(_route_data(primary_model, 'primary'))
        except requests.RequestException as e:
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
        public_agent_reasoning = _public_agent_reasoning(agent_reasoning)
        _record_model_run(data_itemid, data, user_info)

        if mark_guest:
            _mark_guest_detection_used(is_guest)
        return {
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
                'agent_reasoning': public_agent_reasoning,
                'llm_used': bool(public_agent_reasoning),
                'visual_issues': visual_issues,
                'image_url': _public_backend_static_url(data.get('image_url')) or _backend_static_url('image', data),
                'filename': data.get('filename') or safe_name,
                'file_size': data.get('file_size') or (data.get('meta') or {}).get('file_size', ''),
                'img_format': data.get('img_format') or (data.get('meta') or {}).get('img_format', ''),
                'resolution': data.get('resolution') or (data.get('meta') or {}).get('resolution', ''),
                'all_metadata': metadata,
                'feedback': None,
            }
        }, 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'status': 'error', 'message': f'检测失败: {str(e)}'}, 500


def image_detect_for_actor(user_info, *, is_guest=False):
    file = request.files.get('image') or request.files.get('file')
    if not file or file.filename == '':
        return jsonify({'status': 'error', 'message': '请上传图片文件'}), 400

    filename = file.filename

    if not allowed_file(filename):
        return jsonify({'status': 'error', 'message': '不支持的文件格式'}), 400

    mimetype = file.mimetype or 'application/octet-stream'
    file.stream.seek(0)
    image_bytes = file.stream.read()
    payload, status_code = _run_image_detection_payload(
        image_bytes,
        filename,
        mimetype,
        user_info,
        is_guest=is_guest,
        mark_guest=True,
    )
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
    return public_result


def _public_detection_job(job):
    if not isinstance(job, dict):
        return job
    public_job = copy.deepcopy(job)
    if public_job.get('mode') != 'swarm' and public_job.get('kind') != 'swarm':
        return public_job
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
            payload['result'] = _public_swarm_result(payload['result'])
        if isinstance(payload.get('experts'), list):
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


def _swarm_primary_expert(image_bytes, filename, mimetype, user_info, is_guest):
    started_at = time.time()
    payload, status_code = _run_image_detection_payload(
        image_bytes,
        filename,
        mimetype,
        user_info,
        is_guest=is_guest,
        mark_guest=False,
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
    score = _clamp01(result.get('probability'), 0.5)
    evidence = []
    if result.get('visual_issues'):
        evidence.extend([str(item) for item in (result.get('visual_issues') or [])[:2] if str(item).strip()])
    line = _first_text_line(result.get('explanation'))
    if line:
        evidence.append(line)
    return result, {
        'status': 'success',
        'score': round(score, 4),
        'verdict': result.get('final_label') or ('AI生成图像' if score >= 0.5 else '真实图像'),
        'confidence': result.get('confidence') or _conf_level_from_score(score),
        'evidence': evidence[:3] or ['主路由完成基础图像鉴伪。'],
        'message': '主路由检测完成',
        'latencyMs': int((time.time() - started_at) * 1000),
    }


def _swarm_metadata_expert(primary_result):
    metadata = (primary_result or {}).get('all_metadata') or {}
    keys = set(metadata.keys()) if isinstance(metadata, dict) else set()
    if not metadata:
        return {
            'status': 'success',
            'score': 0.56,
            'verdict': '缺少元数据',
            'confidence': '低',
            'evidence': ['图像未提供可验证的 EXIF/拍摄设备元数据。'],
            'message': '元数据缺失，作为弱风险信号处理',
            'latencyMs': 0,
        }
    joined = json.dumps(metadata, ensure_ascii=False)[:12000].lower()
    ai_markers = (
        'midjourney', 'stable diffusion', 'comfyui', 'dall-e', 'dalle',
        'firefly', 'aigc', 'ai generated', 'generated image', 'synthetic',
    )
    has_ai_marker = any(marker in joined for marker in ai_markers)
    camera_keys = {
        'EXIF:Make', 'EXIF:Model', 'EXIF:LensModel', 'EXIF:LensMake',
        'Composite:LensID', 'Composite:LensSpec', 'Composite:FocalLength',
    }
    date_keys = {'EXIF:DateTimeOriginal', 'EXIF:CreateDate', 'File:FileModifyDate'}
    has_camera = bool(keys.intersection(camera_keys))
    has_date = bool(keys.intersection(date_keys))
    if has_ai_marker:
        score = 0.9
        verdict = '发现生成标识'
        confidence = '高'
        evidence = ['元数据中出现生成式工具或 AIGC 标识。']
    elif has_camera and has_date:
        score = 0.24
        verdict = '拍摄链路较完整'
        confidence = '中'
        evidence = ['元数据包含设备与时间字段，可为真实拍摄提供辅助支撑。']
    elif has_camera:
        score = 0.34
        verdict = '含设备信息'
        confidence = '中'
        evidence = ['元数据包含相机或镜头信息，但拍摄链路不完整。']
    else:
        score = 0.48
        verdict = '元数据弱支撑'
        confidence = '低'
        evidence = ['已提取元数据，但缺少明确的设备拍摄字段。']
    evidence.append(f'可读元数据字段数：{len(keys)}。')
    return {
        'status': 'success',
        'score': round(score, 4),
        'verdict': verdict,
        'confidence': confidence,
        'evidence': evidence[:3],
        'message': '元数据取证完成',
        'latencyMs': 0,
    }


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
        image_url = f"/static/uploads/{backend_openid or phone or 'guest'}/image/{stored_name}"
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
    if not successful:
        return None, '所有专家均未返回有效结论'
    if not any(expert.get('id') != 'metadata' for expert in successful):
        return None, '主检测与复核专家均未返回有效结论'
    min_experts = max(1, int((config or {}).get('minExperts') or 2))
    if len(successful) < min_experts:
        return None, f'Swarm 有效专家数不足：{len(successful)}/{min_experts}'
    total_weight = sum(max(0.0, _to_float(expert.get('weight'), 0.0)) for expert in successful)
    if total_weight <= 0:
        total_weight = float(len(successful))
        for expert in successful:
            expert['weight'] = 1.0
    score = sum(_clamp01(expert.get('score'), 0.5) * max(0.0, _to_float(expert.get('weight'), 0.0)) for expert in successful) / total_weight
    scores = [_clamp01(expert.get('score'), 0.5) for expert in successful]
    spread = max(scores) - min(scores) if scores else 0.0
    consensus_threshold = _clamp01((config or {}).get('consensusThreshold'), 0.65)
    disagreement_threshold = _clamp01((config or {}).get('disagreementThreshold'), 0.35)
    consensus_score = max(0.0, min(1.0, 1.0 - spread))
    consensus_level = '高' if spread <= 0.18 and len(successful) >= 3 else ('中' if spread <= 0.35 else '低')
    confidence = _conf_level_from_score(score)
    if spread > 0.42:
        confidence = '低'
    elif spread > 0.28 and confidence == '高':
        confidence = '中'
    riskiest = max(successful, key=lambda expert: _clamp01(expert.get('score'), 0.0))
    if score >= consensus_threshold:
        if riskiest.get('id') == 'aliyun_ps':
            final_label = '疑似篡改图像'
        elif riskiest.get('id') == 'aliyun_recap':
            final_label = '疑似翻拍图像'
        else:
            final_label = 'AI生成图像'
    elif score >= 0.5:
        final_label = '疑似风险图像'
    else:
        final_label = '真实图像'

    provenance_summary = _swarm_provenance_summary(experts)

    evidence = []
    if provenance_summary and provenance_summary.get('headline'):
        evidence.append(provenance_summary['headline'])
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
    explanation = [
        f"Swarm 专家会诊完成：{len(successful)}/{len(experts)} 个专家给出有效结论，综合伪造风险约 {round(score * 100, 1)}%。",
        f"专家一致性：{consensus_level}，当前置信度：{confidence}。",
    ]
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
        'confidence': confidence,
        'explanation': "\n".join(explanation),
        'visual_issues': evidence[:6] or ['暂未提取到明确的视觉可疑点。'],
        'agent_reasoning': json.dumps({
            'mode': 'swarm',
            'effectiveExperts': len(successful),
            'totalExperts': len(experts),
            'score': round(score, 4),
            'consensusLevel': consensus_level,
            'disagreement': disagreement,
        }, ensure_ascii=False),
        'llm_used': False,
        'swarm': {
            'enabled': True,
            'score': round(score, 4),
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

    primary_result = None
    if 'primary' in enabled_ids:
        _swarm_set_expert(experts, 'primary', status='running', message='正在调用主路由鉴伪')
        _swarm_update_job(job_id, experts, 10, '主路由鉴伪专家正在分析')
        primary_result, primary_update = _swarm_primary_expert(
            image_bytes,
            filename,
            mimetype,
            user_info,
            is_guest,
        )
        _swarm_set_expert(experts, 'primary', **primary_update)
        _swarm_update_job(job_id, experts, 24, '主路由鉴伪完成')

    if 'metadata' in enabled_ids:
        _swarm_set_expert(experts, 'metadata', status='running', message='正在读取元数据证据')
        _swarm_update_job(job_id, experts, 32, '元数据取证专家正在分析')
        metadata_update = _swarm_metadata_expert(primary_result or {})
        _swarm_set_expert(experts, 'metadata', **metadata_update)
        _swarm_update_job(job_id, experts, 40, '元数据取证完成')

    expert_steps = []
    if 'v2' in enabled_ids:
        expert_steps.append(('v2', 52, lambda: _swarm_v2_expert(image_bytes, filename, mimetype)))
    if 'c2pa' in enabled_ids:
        expert_steps.append((
            'c2pa',
            None,
            lambda: swarm_c2pa_expert.run_c2pa_expert(image_bytes, filename, mimetype),
        ))
    if 'watermark' in enabled_ids:
        expert_steps.append((
            'watermark',
            None,
            lambda: swarm_watermark_expert.run_watermark_expert(image_bytes, filename),
        ))
    if 'wam' in enabled_ids:
        expert_steps.append((
            'wam',
            None,
            lambda: swarm_wam_expert.run_wam_expert(image_bytes, filename, mimetype),
        ))
    for spec in specs:
        if spec.get('provider') == 'aliyun':
            expert_steps.append((spec['id'], None, lambda spec=spec: _swarm_aliyun_expert(spec, image_bytes, filename)))

    dynamic_progress = [52, 64, 74, 82, 90, 95]
    for idx, (expert_id, fixed_progress, runner) in enumerate(expert_steps):
        progress = fixed_progress if fixed_progress is not None else dynamic_progress[min(idx, len(dynamic_progress) - 1)]
        expert = _swarm_set_expert(experts, expert_id, status='running', message='正在复核')
        expert_name = (expert or {}).get('name') or expert_id
        _swarm_update_job(job_id, experts, max(42, progress - 6), f'{expert_name}正在分析')
        try:
            update = runner()
        except Exception as exc:
            update = {
                'status': 'failed',
                'score': None,
                'verdict': '调用失败',
                'confidence': '',
                'evidence': [],
                'message': safe_truncate(str(exc), 120),
                'latencyMs': None,
            }
        _swarm_set_expert(experts, expert_id, **update)
        _swarm_update_job(job_id, experts, progress, f'{expert_name}完成')

    fallback_result = None
    if not primary_result:
        fallback_result = _swarm_fallback_display_result(image_bytes, filename, backend_openid, phone)
    final_result, error = _swarm_aggregate(experts, primary_result, fallback_result)
    if error:
        return {'status': 'error', 'message': error, 'experts': experts}, 502
    payload = {'status': 'success', 'result': final_result}
    _swarm_update_job(job_id, experts, 100, 'Swarm 专家会诊完成', status='success', result=payload)
    return payload, 200


def _run_async_image_job(job_id, image_bytes, filename, mimetype, user_info, is_guest):
    admin_state.update_detection_job(job_id, {"status": "running"})
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
            })
            return
        admin_state.update_detection_job(job_id, {"status": "success", "result": payload})
    except Exception as exc:
        admin_state.update_detection_job(job_id, {"status": "failed", "error": str(exc)})


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
    file.stream.seek(0)
    image_bytes = file.stream.read()
    if not image_bytes:
        return jsonify({'status': 'error', 'message': '请上传非空图片文件'}), 400
    job = admin_state.create_detection_job(user_info, file.filename, kind='image')
    _mark_guest_detection_used(is_guest)
    thread = threading.Thread(
        target=_run_async_image_job,
        args=(job['id'], image_bytes, file.filename, mimetype, dict(user_info or {}), is_guest),
        daemon=True,
    )
    thread.start()
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
    file = request.files.get('image') or request.files.get('file')
    if not file or file.filename == '':
        return jsonify({'status': 'error', 'message': '请上传图片文件'}), 400
    if not allowed_file(file.filename):
        return jsonify({'status': 'error', 'message': '不支持的文件格式'}), 400
    mimetype = file.mimetype or 'application/octet-stream'
    file.stream.seek(0)
    image_bytes = file.stream.read()
    if not image_bytes:
        return jsonify({'status': 'error', 'message': '请上传非空图片文件'}), 400
    experts = _swarm_initial_experts()
    job = admin_state.create_detection_job(user_info, file.filename, kind='swarm', mode='swarm', experts=experts)
    _mark_guest_detection_used(is_guest)
    thread = threading.Thread(
        target=_run_swarm_image_job,
        args=(job['id'], image_bytes, file.filename, mimetype, dict(user_info or {}), is_guest),
        daemon=True,
    )
    thread.start()
    return jsonify({'status': 'success', 'job': _public_detection_job(job)}), 202


@image_upload_blueprint.route('/image_upload/jobs/<job_id>')
def image_detection_job(job_id):
    job = admin_state.get_detection_job(job_id)
    if not job:
        return jsonify({'status': 'error', 'message': '任务不存在'}), 404
    owner = job.get('actor') or {}
    phone, openid, is_guest = _detection_owner()
    if is_guest:
        allowed = bool(openid and owner.get('openid') == openid)
    else:
        allowed = bool((phone and owner.get('phone') == phone) or (openid and owner.get('openid') == openid))
    if not allowed:
        return jsonify({'status': 'error', 'message': '无权查看该任务'}), 403
    return jsonify({'status': 'success', 'job': _public_detection_job(job)})


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
