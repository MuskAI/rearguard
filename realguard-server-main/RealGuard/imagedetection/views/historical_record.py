import os
from flask import Blueprint, render_template, session

from imagedetection.views.utils import excute_detection_sql, format_createtime

historical_record_blueprint = Blueprint('historical_record_blueprint', __name__)

DETECTION_BACKEND_BASE_URL = os.environ.get(
    'REALGUARD_DETECTION_BACKEND_URL',
    'http://127.0.0.1:15000'
).rstrip('/')
DETECTION_PUBLIC_STATIC_PREFIX = os.environ.get(
    'REALGUARD_DETECTION_PUBLIC_STATIC_PREFIX',
    '/detection-static'
).rstrip('/')
STATIC_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'static'))


def _session_history_where(user_info):
    clauses = []
    params = []
    user_id = (user_info or {}).get('Userid') or (user_info or {}).get('userId') or (user_info or {}).get('id')
    phone = str((user_info or {}).get('phone') or '').strip()
    openid = str((user_info or {}).get('openid') or '').strip()
    if user_id not in (None, ''):
        clauses.append('Userid = %s')
        params.append(user_id)
    if phone:
        clauses.append('phone = %s')
        params.append(phone)
    if openid:
        clauses.append('openid = %s')
        params.append(openid)
    if not clauses:
        return '1 = 0', ()
    return ' OR '.join(clauses), tuple(params)


def _detection_static_url(kind, item):
    filename = (item or {}).get('filename') or ''
    folder = (item or {}).get('openid') or (item or {}).get('phone') or 'guest'
    if not filename:
        return ''
    local_path = os.path.join(STATIC_ROOT, 'uploads', folder, kind, filename)
    if os.path.exists(local_path):
        return f"/static/uploads/{folder}/{kind}/{filename}"
    if DETECTION_PUBLIC_STATIC_PREFIX:
        return f"{DETECTION_PUBLIC_STATIC_PREFIX}/uploads/{folder}/{kind}/{filename}"
    return f"{DETECTION_BACKEND_BASE_URL}/static/uploads/{folder}/{kind}/{filename}"


@historical_record_blueprint.route('/history_photo')
def history_photo():
    """图像检测历史"""
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    phone = session['user_info'].get('phone', '')
    history_where, history_params = _session_history_where(session['user_info'])
    result = excute_detection_sql(f"SELECT * FROM data WHERE {history_where} ORDER BY createtime DESC", history_params)
    records = []
    if result:
        for item in result:
            fake_pct = round(float(item.get('fake', 0) or 0), 1)
            final_label = 'AI生成图像' if fake_pct >= 50 else '真实图像'
            records.append({
                "itemid": item['itemid'],
                "filename": item['filename'],
                "image_url": _detection_static_url('image', item),
                "real_prob": round(100 - fake_pct, 1),
                "fake_prob": fake_pct,
                "aigc": final_label,
                "confidence": item.get('clarity', ''),
                "createtime": format_createtime(item['createtime']),
            })
    return render_template('history_photo.html', records=records, username=phone)


@historical_record_blueprint.route('/history_video_detect')
def history_video_detect():
    """视频检测历史"""
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    phone = session['user_info'].get('phone', '')
    history_where, history_params = _session_history_where(session['user_info'])
    result = excute_detection_sql(
        f"SELECT * FROM video_data WHERE {history_where} ORDER BY createtime DESC",
        history_params
    )
    records = []
    ai_count = 0
    real_count = 0
    if result:
        for item in result:
            final_label = item.get('final_label', '') or ''
            if 'AI' in final_label:
                ai_count += 1
            else:
                real_count += 1
            records.append({
                "itemid": item.get('itemid'),
                "filename": item.get('filename', ''),
                "video_url": _detection_static_url('video', item),
                "fake_percentage": round(float(item.get('fake', 0) or 0), 1),
                "real_percentage": round(100 - float(item.get('fake', 0) or 0), 1),
                "final_label": final_label,
                "confidence": item.get('confidence', ''),
                "createtime": format_createtime(item.get('createtime', '')),
            })
    return render_template(
        'history_video_detect.html',
        records=records,
        username=phone,
        ai_count=ai_count,
        real_count=real_count
    )
