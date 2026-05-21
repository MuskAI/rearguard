import os
from flask import Blueprint, render_template, request, session, jsonify

from imagedetection.views.utils import excute_sql, excute_detection_sql

profile_blueprint = Blueprint('profile_blueprint', __name__)


@profile_blueprint.route('/profile')
def profile_page():
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')

    user_info = session['user_info']
    phone = user_info.get('phone', '')

    user = {
        'username': user_info.get('username', ''),
        'phone': phone,
    }

    # 图像检测次数
    detect_count = 0
    r = excute_detection_sql("SELECT COUNT(*) as cnt FROM data WHERE phone = %s", (phone,))
    if r and len(r) > 0:
        detect_count = r[0].get('cnt', 0)

    # 图像检索次数
    image_retrieve_count = 0
    r = excute_sql("SELECT COUNT(*) as cnt FROM retrieve_data WHERE phone = %s AND search_type = 'image'", (phone,))
    if r and len(r) > 0:
        image_retrieve_count = r[0].get('cnt', 0)

    # 视频检索次数
    video_retrieve_count = 0
    r = excute_sql("SELECT COUNT(*) as cnt FROM retrieve_data WHERE phone = %s AND search_type = 'video'", (phone,))
    if r and len(r) > 0:
        video_retrieve_count = r[0].get('cnt', 0)

    # 视频检测次数
    video_detect_count = 0
    r = excute_detection_sql("SELECT COUNT(*) as cnt FROM video_data WHERE phone = %s", (phone,))
    if r and len(r) > 0:
        video_detect_count = r[0].get('cnt', 0)

    return render_template('profile.html',
                           user=user,
                           detect_count=detect_count,
                           video_detect_count=video_detect_count,
                           image_retrieve_count=image_retrieve_count,
                           video_retrieve_count=video_retrieve_count)


@profile_blueprint.route('/profile/change_password', methods=['POST'])
def change_password():
    if 'user_info' not in session or session['user_info'] is None:
        return jsonify({'status': 'error', 'message': '用户未登录'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': '请求数据为空'}), 400

    old_password = data.get('old_password', '').strip()
    new_password = data.get('new_password', '').strip()

    if not old_password or not new_password:
        return jsonify({'status': 'error', 'message': '请填写完整信息'}), 400
    if len(new_password) < 4:
        return jsonify({'status': 'error', 'message': '新密码至少4位'}), 400

    phone = session['user_info'].get('phone', '')

    check = excute_sql("SELECT Userid FROM user WHERE phone = %s AND secret = %s", (phone, old_password))
    if not check or len(check) == 0:
        return jsonify({'status': 'error', 'message': '当前密码错误'}), 400

    affected = excute_sql("UPDATE user SET secret = %s WHERE phone = %s", (new_password, phone), fetch=False)
    if affected and affected > 0:
        return jsonify({'status': 'success', 'message': '密码修改成功'})
    return jsonify({'status': 'error', 'message': '修改失败'}), 500
