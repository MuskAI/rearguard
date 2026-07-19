from flask import Blueprint, render_template, request, session, jsonify

from imagedetection.views.login import _hash_password, _password_matches, _password_policy_error
from imagedetection.views.utils import detection_owner_where, excute_detection_sql, excute_sql

profile_blueprint = Blueprint('profile_blueprint', __name__)


@profile_blueprint.route('/profile')
def profile_page():
    if 'user_info' not in session or session['user_info'] is None:
        return render_template('login.html')

    user_info = session['user_info']
    phone = user_info.get('phone', '')
    openid = user_info.get('openid', '')
    history_where, history_params = detection_owner_where(
        phone,
        openid,
        account_uuid=user_info.get('account_uuid'),
        require_account_uuid=True,
    )

    user = {
        'username': user_info.get('username', ''),
        'phone': phone,
    }

    # 图像检测次数
    detect_count = 0
    r = excute_detection_sql(f"SELECT COUNT(*) as cnt FROM data WHERE {history_where}", history_params)
    if r and len(r) > 0:
        detect_count = r[0].get('cnt', 0)

    # 视频检测次数
    video_detect_count = 0
    r = excute_detection_sql(f"SELECT COUNT(*) as cnt FROM video_data WHERE {history_where}", history_params)
    if r and len(r) > 0:
        video_detect_count = r[0].get('cnt', 0)

    return render_template('profile.html',
                           user=user,
                           detect_count=detect_count,
                           video_detect_count=video_detect_count)


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
    password_error = _password_policy_error(new_password)
    if password_error:
        return jsonify({'status': 'error', 'message': password_error}), 400

    phone = str(session['user_info'].get('phone') or '').strip()
    if not phone:
        return jsonify({'status': 'error', 'message': '当前账号未绑定手机号，请使用短信找回或联系管理员'}), 400

    rows = excute_sql("SELECT secret FROM user WHERE phone = %s LIMIT 1", (phone,))
    if not rows or not _password_matches(rows[0].get('secret', ''), old_password):
        return jsonify({'status': 'error', 'message': '当前密码错误'}), 400

    affected = excute_sql(
        "UPDATE user SET secret = %s, session_version = session_version + 1, password_updated_at = NOW() WHERE phone = %s",
        (_hash_password(new_password), phone),
        fetch=False,
    )
    if affected and affected > 0:
        session.clear()
        return jsonify({'status': 'success', 'message': '密码修改成功，请重新登录'})
    return jsonify({'status': 'error', 'message': '修改失败'}), 500
