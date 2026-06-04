import io
import json
import os
import uuid
from datetime import datetime

import requests
from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

from imagedetection.views.utils import (
    create_folder,
    excute_detection_sql_lastid,
    get_file_size_str,
    get_image_info,
    safe_truncate,
)


ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'bmp', 'gif'}
STATIC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), 'imagedetection', 'static'))
V2_DETECT_API = os.environ.get(
    'REALGUARD_V2_INTERNAL_DETECT_URL',
    'http://127.0.0.1:8848/api/detect',
).strip()
V2_INTERNAL_TOKEN = (
    os.environ.get('REALGUARD_V2_INTERNAL_TOKEN')
    or os.environ.get('JIANZHEN_ACCESS_TOKEN')
    or ''
).strip()
V2_DETECT_TIMEOUT = int(os.environ.get('REALGUARD_V2_DETECT_TIMEOUT', '180'))


def _load_env_file(path='.env'):
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _to_float(value, default=0.0):
    if value is None:
        return float(default)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace('%', '')
    if not text:
        return float(default)
    try:
        return float(text)
    except ValueError:
        return float(default)


def _confidence_level(score):
    score = max(0.0, min(1.0, _to_float(score, 0.5)))
    delta = abs(score - 0.5)
    if delta >= 0.35:
        return '高'
    if delta >= 0.18:
        return '中'
    return '低'


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


def _save_upload(image_bytes, folder, filename):
    upload_dir = os.path.join(STATIC_ROOT, 'uploads', folder, 'image')
    create_folder(upload_dir)
    safe_name = secure_filename(filename) or f'{uuid.uuid4().hex}.png'
    stored_name = f'{uuid.uuid4().hex[:12]}-{safe_name}'
    file_path = os.path.join(upload_dir, stored_name)
    with open(file_path, 'wb') as out:
        out.write(image_bytes)
    return stored_name, file_path


def _post_internal(url, **kwargs):
    with requests.Session() as sess:
        sess.trust_env = False
        return sess.post(url, **kwargs)


def _visual_issues(payload):
    issues = []
    for region in payload.get('regions') or []:
        label = str(region.get('label') or '').strip()
        score = _to_float(region.get('score'), 0.0)
        if label:
            issues.append(f'{label}（{round(score * 100, 1)}%）' if score else label)
    for dim in payload.get('dimensions') or []:
        label = str(dim.get('label') or dim.get('key') or '').strip()
        result = str(dim.get('result') or '').strip()
        score = _to_float(dim.get('score'), 0.0)
        if label and result:
            suffix = f'（{round(score * 100, 1)}%）' if score else ''
            issues.append(f'{label}: {result}{suffix}')
    return issues[:6]


def _detect_via_v2(image_bytes, filename, mimetype):
    if not V2_DETECT_API or not V2_INTERNAL_TOKEN:
        raise RuntimeError('未配置 V2 内部检测地址或令牌')
    response = _post_internal(
        V2_DETECT_API,
        headers={'X-Jianzhen-Token': V2_INTERNAL_TOKEN},
        files={'file': (filename, io.BytesIO(image_bytes), mimetype or 'application/octet-stream')},
        data={'fileType': 'image'},
        timeout=V2_DETECT_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _persist_result(payload, image_bytes, filename, openid, phone):
    folder = openid or phone or 'guest'
    stored_name, file_path = _save_upload(image_bytes, folder, filename)
    img_format, resolution = get_image_info(file_path)
    file_size = (payload.get('fileMeta') or {}).get('size') or get_file_size_str(file_path)
    fake_pct = _fake_percentage_from_v2(payload)
    final_label = 'AI生成图像' if fake_pct >= 50 else '真实图像'
    confidence = _confidence_level(payload.get('confidence'))
    explanation = str(payload.get('explanation') or '').strip()
    issues = _visual_issues(payload)
    if issues:
        explanation = f"{explanation}\n视觉可疑点\n" + "\n".join(f"- {item}" for item in issues)

    itemid = excute_detection_sql_lastid(
        """
        INSERT INTO data
            (createtime, filename, fake, openid, phone, aigc,
             file_size, img_format, resolution, clarity, explantation)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            stored_name,
            fake_pct,
            openid,
            phone,
            final_label,
            file_size,
            img_format,
            resolution,
            confidence,
            safe_truncate(explanation, 145),
        ),
    )
    if not itemid:
        raise RuntimeError('检测结果写入失败')

    return {
        'data_itemid': itemid,
        'fake_percentage': fake_pct,
        'final_label': final_label,
        'confidence': confidence,
        'image_url': f"{request.host_url.rstrip('/')}/static/uploads/{folder}/image/{stored_name}",
        'filename': stored_name,
        'file_size': file_size,
        'img_format': img_format,
        'resolution': resolution,
        'explanation': explanation,
        'visual_issues': issues,
        'agent_reasoning': json.dumps({
            'backend': 'realguard-detector-backend',
            'upstream': 'jianzhen-v2',
            'taskId': payload.get('taskId'),
            'reportId': payload.get('reportId'),
            'modelVersion': payload.get('modelVersion'),
            'source': payload.get('source'),
            'tokenUsage': payload.get('tokenUsage'),
        }, ensure_ascii=False),
        'full_exif_info': {},
        'meta': {
            'file_size': file_size,
            'img_format': img_format,
            'resolution': resolution,
        },
    }


def create_app():
    app = Flask(__name__, static_folder=STATIC_ROOT, static_url_path='/static')

    @app.get('/health')
    def health():
        return jsonify({'status': 'ok', 'service': 'realguard-detector-backend'})

    @app.post('/image')
    def image():
        file = request.files.get('image_file') or request.files.get('image') or request.files.get('file')
        if not file or not file.filename:
            return jsonify({'code': 400, 'msg': '请上传图片文件'}), 400
        if not _allowed_file(file.filename):
            return jsonify({'code': 400, 'msg': '不支持的文件格式'}), 400

        safe_name = secure_filename(file.filename) or file.filename
        image_bytes = file.read()
        if not image_bytes:
            return jsonify({'code': 400, 'msg': '请上传非空图片文件'}), 400

        openid = str(request.form.get('openid') or '').strip()[:64]
        phone = str(request.form.get('phone') or '').strip()[:20]
        try:
            payload = _detect_via_v2(image_bytes, safe_name, file.mimetype)
            data = _persist_result(payload, image_bytes, safe_name, openid, phone)
            return jsonify({'code': 200, 'msg': 'success', 'data': data})
        except requests.RequestException as exc:
            return jsonify({'code': 502, 'msg': f'上游 V2 检测服务调用失败: {exc}'}), 502
        except Exception as exc:
            return jsonify({'code': 500, 'msg': str(exc)}), 500

    @app.post('/video')
    def video():
        return jsonify({'code': 501, 'msg': 'V1 视频检测后端暂未启用'}), 501

    return app


_load_env_file()
app = create_app()


if __name__ == '__main__':
    host = os.environ.get('REALGUARD_DETECTOR_HOST', '127.0.0.1')
    port = int(os.environ.get('REALGUARD_DETECTOR_PORT', '15000'))
    app.run(host=host, port=port)
