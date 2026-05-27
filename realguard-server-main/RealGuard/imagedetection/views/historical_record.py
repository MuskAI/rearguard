import os
import json
from flask import Blueprint, render_template, request, session, jsonify

from imagedetection.views.utils import excute_sql, excute_detection_sql, format_createtime

historical_record_blueprint = Blueprint('historical_record_blueprint', __name__)

DETECTION_BACKEND_BASE_URL = os.environ.get(
    'REALGUARD_DETECTION_BACKEND_URL',
    'http://127.0.0.1:15000'
).rstrip('/')
DETECTION_PUBLIC_STATIC_PREFIX = os.environ.get(
    'REALGUARD_DETECTION_PUBLIC_STATIC_PREFIX',
    '/detection-static'
).rstrip('/')


def _detection_static_url(kind, item):
    filename = (item or {}).get('filename') or ''
    folder = (item or {}).get('openid') or (item or {}).get('phone') or 'guest'
    if not filename:
        return ''
    if DETECTION_PUBLIC_STATIC_PREFIX:
        return f"{DETECTION_PUBLIC_STATIC_PREFIX}/uploads/{folder}/{kind}/{filename}"
    return f"{DETECTION_BACKEND_BASE_URL}/static/uploads/{folder}/{kind}/{filename}"


@historical_record_blueprint.route('/history_photo')
def history_photo():
    """图像检测历史"""
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    phone = session['user_info'].get('phone', '')
    result = excute_detection_sql("SELECT * FROM data WHERE phone = %s ORDER BY createtime DESC", (phone,))
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
    result = excute_detection_sql(
        "SELECT * FROM video_data WHERE phone = %s ORDER BY createtime DESC",
        (phone,)
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


@historical_record_blueprint.route('/history_image_retrieve')
def history_image_retrieve():
    """图像检索历史"""
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    phone = session['user_info'].get('phone', '')
    result = excute_sql(
        "SELECT * FROM retrieve_data WHERE phone = %s AND search_type = 'image' ORDER BY createtime DESC",
        (phone,)
    )
    records = []
    if result:
        for item in result:
            records.append({
                "itemid": item['itemid'],
                "filename": item['filename'],
                "file_url": f"/static/uploads/{phone}/retrieve/{item['filename']}",
                "result_count": item.get('result_count', 0),
                "top_k": item.get('top_k', 10),
                "file_size": item.get('file_size', ''),
                "createtime": format_createtime(item['createtime']),
            })
    return render_template('history_image_retrieve.html', records=records, username=phone)


@historical_record_blueprint.route('/history_video_retrieve')
def history_video_retrieve():
    """视频检索历史"""
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')
    phone = session['user_info'].get('phone', '')
    result = excute_sql(
        "SELECT * FROM retrieve_data WHERE phone = %s AND search_type = 'video' ORDER BY createtime DESC",
        (phone,)
    )
    records = []
    if result:
        for item in result:
            records.append({
                "itemid": item['itemid'],
                "filename": item['filename'],
                "file_url": f"/static/uploads/{phone}/retrieve/{item['filename']}",
                "result_count": item.get('result_count', 0),
                "top_k": item.get('top_k', 10),
                "file_size": item.get('file_size', ''),
                "createtime": format_createtime(item['createtime']),
            })
    return render_template('history_video_retrieve.html', records=records, username=phone)


@historical_record_blueprint.route('/history_retrieve/result')
def retrieve_result_api():
    """根据 itemid 获取检索历史结果"""
    if 'user_info' not in session or session['user_info'] is None:
        return jsonify({'status': 'error', 'message': '用户未登录'}), 401

    itemid = request.args.get('itemid')
    phone = session['user_info'].get('phone', '')
    if not itemid:
        return jsonify({'status': 'error', 'message': '缺少参数'}), 400

    result = excute_sql("SELECT * FROM retrieve_data WHERE itemid = %s AND phone = %s", (itemid, phone))
    if not result or len(result) == 0:
        return jsonify({'status': 'error', 'message': '未找到记录'}), 404

    item = result[0]
    filename = item.get('filename', '')
    search_type = item.get('search_type', 'image')

    # Parse stored results
    results_data = []
    if item.get('results_json'):
        try:
            results_data = json.loads(item['results_json'])
        except Exception:
            pass

    base_url = '/retrieve/library-file/image/' if search_type == 'image' else '/retrieve/library-file/video/'

    return jsonify({
        'status': 'success',
        'search_type': search_type,
        'query_file_url': f"/static/uploads/{phone}/retrieve/{filename}",
        'base_url': base_url,
        'results': results_data,
        'result_count': item.get('result_count', 0),
        'top_k': item.get('top_k', 10),
        'file_size': item.get('file_size', ''),
        'createtime': format_createtime(item.get('createtime', '')),
    })
