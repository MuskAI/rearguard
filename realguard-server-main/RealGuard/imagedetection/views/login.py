import base64
import hashlib
import hmac
import json
import os
import random
import re
import time
import uuid
from datetime import datetime
from urllib.parse import quote

import requests
from flask import Blueprint, jsonify, render_template, request, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from imagedetection.views.utils import excute_sql, excute_detection_sql, create_folder

login_blueprint = Blueprint('login_blueprint', __name__)

current_dir = os.path.dirname(os.path.abspath(__file__))

PHONE_RE = re.compile(r'^1[3-9]\d{9}$')
SMS_SESSION_KEY = 'sms_verify_codes'
SMS_CODE_TTL = int(os.environ.get('SMS_CODE_TTL', '300'))
SMS_INTERVAL = int(os.environ.get('SMS_INTERVAL', '60'))
SMS_CODE_LENGTH = int(os.environ.get('SMS_CODE_LENGTH', '6'))
TERMS_VERSION = os.environ.get('REALGUARD_TERMS_VERSION', '2026-06-03')
PASSWORD_MIN_LENGTH = int(os.environ.get('REALGUARD_PASSWORD_MIN_LENGTH', '8'))
_USER_ACCOUNT_COLUMNS_READY = False


def _is_valid_phone(phone):
    return bool(PHONE_RE.match(phone or ''))


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on', 'agree', 'accepted')


def _password_policy_error(secret):
    value = str(secret or '')
    if len(value) < PASSWORD_MIN_LENGTH:
        return f'密码至少需要 {PASSWORD_MIN_LENGTH} 位'
    if len(value) > 128:
        return '密码不能超过 128 位'
    if not any(ch.isalpha() for ch in value) or not any(ch.isdigit() for ch in value):
        return '密码需同时包含字母和数字'
    return ''


def _ensure_column(table, column, definition):
    rows = excute_sql(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    if rows is None:
        return False
    if rows:
        return True
    result = excute_sql(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}", fetch=False)
    return result is not None


def _ensure_user_account_columns():
    global _USER_ACCOUNT_COLUMNS_READY
    if _USER_ACCOUNT_COLUMNS_READY:
        return True
    columns = [
        ('created_at', "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'"),
        ('terms_version', "VARCHAR(32) NULL COMMENT '用户协议版本'"),
        ('terms_accepted_at', "DATETIME NULL COMMENT '用户协议同意时间'"),
        ('password_updated_at', "DATETIME NULL COMMENT '密码更新时间'"),
    ]
    for column, definition in columns:
        if not _ensure_column('user', column, definition):
            return False
    _USER_ACCOUNT_COLUMNS_READY = True
    return True


def _hash_code(code):
    secret = os.environ.get('SECRET_KEY') or 'realguard-sms-code'
    return hashlib.sha256((secret + ':' + code).encode('utf-8')).hexdigest()


def _is_password_hash(value):
    stored = str(value or '')
    return stored.startswith('pbkdf2:') or stored.startswith('scrypt:')


def _hash_password(password):
    return generate_password_hash(password)


def _password_matches(stored_secret, candidate):
    stored = str(stored_secret or '')
    plain = str(candidate or '')
    if not stored:
        return False
    if _is_password_hash(stored):
        try:
            return check_password_hash(stored, plain)
        except ValueError:
            return False
    return hmac.compare_digest(stored, plain)


def _find_user_by_phone(phone):
    rows = excute_sql("SELECT * FROM user WHERE phone = %s LIMIT 1", (phone,))
    return rows[0] if rows else None


def _upgrade_password_hash(phone, stored_secret, candidate):
    if not phone or not stored_secret or _is_password_hash(stored_secret):
        return
    if _password_matches(stored_secret, candidate):
        excute_sql(
            "UPDATE user SET secret = %s WHERE phone = %s",
            (_hash_password(candidate), phone),
            fetch=False,
        )


def _authenticate_password_user(phone, candidate):
    user = _find_user_by_phone(phone)
    if not user:
        return None
    if not _password_matches(user.get('secret', ''), candidate):
        return None
    _upgrade_password_hash(phone, user.get('secret', ''), candidate)
    return user


def _get_sms_bucket():
    bucket = session.get(SMS_SESSION_KEY)
    if not isinstance(bucket, dict):
        bucket = {}
    return bucket


def _save_sms_code(scene, phone, code):
    bucket = _get_sms_bucket()
    bucket[f'{scene}:{phone}'] = {
        'hash': _hash_code(code),
        'expires_at': int(time.time()) + SMS_CODE_TTL,
        'sent_at': int(time.time()),
    }
    session[SMS_SESSION_KEY] = bucket
    session.modified = True


def _verify_sms_code(scene, phone, code):
    bucket = _get_sms_bucket()
    key = f'{scene}:{phone}'
    item = bucket.get(key)
    if not item:
        return False, '请先获取短信验证码'
    if int(item.get('expires_at', 0)) < int(time.time()):
        bucket.pop(key, None)
        session[SMS_SESSION_KEY] = bucket
        session.modified = True
        return False, '验证码已过期，请重新获取'
    if not hmac.compare_digest(item.get('hash', ''), _hash_code(code or '')):
        return False, '验证码错误'
    bucket.pop(key, None)
    session[SMS_SESSION_KEY] = bucket
    session.modified = True
    return True, ''


def _check_sms_interval(scene, phone):
    item = _get_sms_bucket().get(f'{scene}:{phone}') or {}
    remain = int(item.get('sent_at', 0)) + SMS_INTERVAL - int(time.time())
    return max(remain, 0)


def _percent_encode(value):
    return quote(str(value), safe='~')


def _aliyun_rpc_signature(params, access_key_secret):
    canonical = '&'.join(
        f'{_percent_encode(k)}={_percent_encode(params[k])}'
        for k in sorted(params)
    )
    string_to_sign = 'POST&%2F&' + _percent_encode(canonical)
    digest = hmac.new(
        (access_key_secret + '&').encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode('utf-8')


def _send_sms_by_aliyun(phone, code, scene):
    access_key_id = os.environ.get('ALIYUN_ACCESS_KEY_ID', '').strip()
    access_key_secret = os.environ.get('ALIYUN_ACCESS_KEY_SECRET', '').strip()
    sign_name = os.environ.get('ALIYUN_SMS_SIGN_NAME', '').strip()
    template_code = os.environ.get('ALIYUN_SMS_TEMPLATE_CODE', '').strip()
    endpoint = os.environ.get('ALIYUN_SMS_ENDPOINT', 'https://dypnsapi.aliyuncs.com/').strip()
    version = os.environ.get('ALIYUN_SMS_VERSION', '2017-05-25').strip()

    if not all([access_key_id, access_key_secret, sign_name, template_code]):
        raise RuntimeError('短信服务未配置，请设置 ALIYUN_ACCESS_KEY_ID、ALIYUN_ACCESS_KEY_SECRET、ALIYUN_SMS_SIGN_NAME、ALIYUN_SMS_TEMPLATE_CODE')

    params = {
        'Action': 'SendSmsVerifyCode',
        'Version': version,
        'Format': 'JSON',
        'AccessKeyId': access_key_id,
        'SignatureMethod': 'HMAC-SHA1',
        'SignatureNonce': str(uuid.uuid4()),
        'SignatureVersion': '1.0',
        'Timestamp': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'PhoneNumber': phone,
        'CountryCode': os.environ.get('ALIYUN_SMS_COUNTRY_CODE', '86'),
        'SignName': sign_name,
        'TemplateCode': template_code,
        'TemplateParam': json.dumps(
            {'code': code, 'min': str(max(1, SMS_CODE_TTL // 60))},
            ensure_ascii=False,
            separators=(',', ':')
        ),
        'CodeLength': str(len(code)),
        'ValidTime': str(SMS_CODE_TTL),
        'DuplicatePolicy': os.environ.get('ALIYUN_SMS_DUPLICATE_POLICY', '1'),
        'Interval': str(SMS_INTERVAL),
        'CodeType': '1',
        'ReturnVerifyCode': 'false',
    }
    scheme_name = (
        os.environ.get(f'ALIYUN_SMS_{scene.upper()}_SCHEME_NAME', '').strip()
        or os.environ.get('ALIYUN_SMS_SCHEME_NAME', '').strip()
    )
    if not scheme_name:
        scheme_name = '注册验证' if scene == 'register' else '登录验证'
    if scheme_name:
        params['SchemeName'] = scheme_name
    params['Signature'] = _aliyun_rpc_signature(params, access_key_secret)

    # 公网 API 直连，避免 HTTP_PROXY/HTTPS_PROXY 指向未启动的本机代理（如 127.0.0.1:7897）导致失败
    with requests.Session() as sess:
        sess.trust_env = False
        response = sess.post(endpoint, data=params, timeout=10)
    try:
        payload = response.json()
    except ValueError:
        payload = {'Code': 'HTTP_ERROR', 'Message': response.text[:200]}
    if response.status_code >= 400 or not payload.get('Success', False):
        message = payload.get('Message') or payload.get('Code') or '短信发送失败'
        code = payload.get('Code')
        if code in ('biz.FREQUENCY', 'FREQUENCY_FAIL'):
            message = f'短信发送太频繁，请等待 {SMS_INTERVAL} 秒后再试'
        request_id = payload.get('RequestId') or (payload.get('Model') or {}).get('RequestId')
        denied_detail = payload.get('AccessDeniedDetail')
        detail_parts = [str(message)]
        if code:
            detail_parts.append(f'Code={code}')
        if request_id:
            detail_parts.append(f'RequestId={request_id}')
        if denied_detail and denied_detail != '无':
            detail_parts.append(f'Detail={denied_detail}')
        message = '；'.join(detail_parts)
        raise RuntimeError(message)
    return payload


def _send_sms_code(phone, scene):
    code = ''.join(random.choice('0123456789') for _ in range(SMS_CODE_LENGTH))
    provider = os.environ.get('SMS_PROVIDER', '').strip().lower()
    if not provider:
        provider = 'aliyun' if os.environ.get('ALIYUN_ACCESS_KEY_ID') else 'mock'

    if provider == 'aliyun':
        _send_sms_by_aliyun(phone, code, scene)
    elif provider != 'mock':
        raise RuntimeError('不支持的短信服务提供方')

    _save_sms_code(scene, phone, code)
    return code if os.environ.get('SMS_DEBUG_RETURN_CODE') == '1' or provider == 'mock' else None


def _sync_detection_user(phone, username='', openid=''):
    """同步网页账号到 /home/ymk/RealGuard 鉴伪后端数据库。"""
    if not phone:
        return
    exists = excute_detection_sql("SELECT Userid FROM user WHERE phone = %s LIMIT 1", (phone,))
    if exists:
        return
    excute_detection_sql(
        "INSERT INTO user (openid, avatar, username, phone) VALUES (%s, %s, %s, %s)",
        (openid or phone, '', username or phone, phone),
        fetch=False
    )


@login_blueprint.route('/sms/send_code', methods=['POST'])
def send_sms_code():
    payload = request.get_json(silent=True) or {}
    phone = (request.form.get('phone') or payload.get('phone') or '').strip()
    scene = (request.form.get('scene') or payload.get('scene') or '').strip()

    if scene not in ('register', 'login', 'reset'):
        return jsonify({'success': False, 'message': '验证码场景不正确'}), 400
    if not _is_valid_phone(phone):
        return jsonify({'success': False, 'message': '请输入正确的手机号'}), 400

    existing = excute_sql("SELECT Userid FROM user WHERE phone = %s LIMIT 1", (phone,))
    if existing is None:
        return jsonify({'success': False, 'message': '数据库连接失败，请稍后重试'}), 500
    if scene == 'register' and existing:
        return jsonify({'success': False, 'message': '该手机号已注册，请直接登录'}), 400
    if scene == 'login' and not existing:
        return jsonify({'success': False, 'message': '该手机号尚未注册，请先注册'}), 400
    if scene == 'reset' and not existing:
        return jsonify({'success': False, 'message': '该手机号尚未注册，无法找回密码'}), 400

    remain = _check_sms_interval(scene, phone)
    if remain > 0:
        return jsonify({'success': False, 'message': f'请 {remain} 秒后再获取验证码', 'remain': remain}), 429

    try:
        debug_code = _send_sms_code(phone, scene)
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500

    data = {'success': True, 'message': '验证码已发送', 'expires_in': SMS_CODE_TTL}
    if debug_code:
        data['debug_code'] = debug_code
        data['message'] = f'开发模式验证码：{debug_code}'
    return jsonify(data)


@login_blueprint.route('/login')
def login():
    return render_template('login.html')


@login_blueprint.route('/register')
def register():
    return render_template('register.html')


@login_blueprint.route('/login_verify', methods=['GET', 'POST'])
def login_verify():
    """通过 phone + secret 登录"""
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        secret = request.form.get('secret', '').strip()

        if not phone or not secret:
            return render_template('login.html', error='请输入手机号和密码')

        user = _authenticate_password_user(phone, secret)
        if user:
            _sync_detection_user(phone, user.get('username') or phone, user.get('openid', '') or phone)
            session.permanent = True
            session['user_info'] = {
                'Userid': user['Userid'],
                'username': user.get('username') or phone,
                'phone': phone,
                'openid': user.get('openid', ''),
            }
            return redirect('/index')
        return render_template('login.html', error='手机号或密码错误')
    return render_template('login.html', error='登录失败')


@login_blueprint.route('/login_sms_verify', methods=['GET', 'POST'])
def login_sms_verify():
    """通过 phone + 短信验证码登录"""
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        sms_code = request.form.get('sms_code', '').strip()

        if not _is_valid_phone(phone):
            return render_template('login.html', error='请输入正确的手机号', login_mode='sms')
        ok, message = _verify_sms_code('login', phone, sms_code)
        if not ok:
            return render_template('login.html', error=message, login_mode='sms')

        user = _find_user_by_phone(phone)
        if user:
            _sync_detection_user(phone, user.get('username') or phone, user.get('openid', '') or phone)
            session.permanent = True
            session['user_info'] = {
                'Userid': user['Userid'],
                'username': user.get('username') or phone,
                'phone': phone,
                'openid': user.get('openid', ''),
            }
            return redirect('/index')
        return render_template('login.html', error='该手机号尚未注册', login_mode='sms')
    return render_template('login.html', error='登录失败', login_mode='sms')


@login_blueprint.route('/password_reset_verify', methods=['GET', 'POST'])
def password_reset_verify():
    """通过 phone + 短信验证码重置密码"""
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        secret = request.form.get('secret', '').strip()
        sms_code = request.form.get('sms_code', '').strip()

        if not _is_valid_phone(phone):
            return render_template('login.html', error='请输入正确的手机号', login_mode='reset')
        password_error = _password_policy_error(secret)
        if password_error:
            return render_template('login.html', error=password_error, login_mode='reset')
        ok, message = _verify_sms_code('reset', phone, sms_code)
        if not ok:
            return render_template('login.html', error=message, login_mode='reset')
        if not _ensure_user_account_columns():
            return render_template('login.html', error='账号表初始化失败，请稍后重试', login_mode='reset')

        user = _find_user_by_phone(phone)
        if not user:
            return render_template('login.html', error='该手机号尚未注册，无法找回密码', login_mode='reset')
        result = excute_sql(
            "UPDATE user SET secret = %s, password_updated_at = NOW() WHERE phone = %s",
            (_hash_password(secret), phone),
            fetch=False
        )
        if result and result > 0:
            current_user = session.get('user_info') or {}
            if str(current_user.get('phone') or '') == phone:
                session.clear()
            return render_template('login.html', error='密码已重置，请使用新密码登录')
        return render_template('login.html', error='密码重置失败，请稍后重试', login_mode='reset')
    return render_template('login.html', error='重置失败', login_mode='reset')


@login_blueprint.route('/register_verify', methods=['GET', 'POST'])
def register_verify():
    """注册新用户，通过 phone + secret"""
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        secret = request.form.get('secret', '').strip()
        username = request.form.get('username', '').strip() or phone

        sms_code = request.form.get('sms_code', '').strip()

        if not _is_valid_phone(phone) or not secret:
            return render_template('register.html', error='请输入正确的手机号和密码')
        password_error = _password_policy_error(secret)
        if password_error:
            return render_template('register.html', error=password_error)
        if not _truthy(request.form.get('accepted_terms')):
            return render_template('register.html', error='请先阅读并同意用户协议和隐私政策')
        ok, message = _verify_sms_code('register', phone, sms_code)
        if not ok:
            return render_template('register.html', error=message)
        if not _ensure_user_account_columns():
            return render_template('register.html', error='账号表初始化失败，请稍后重试')

        # 检查手机号是否已注册
        check_sql = "SELECT Userid FROM user WHERE phone = %s"
        existing = excute_sql(check_sql, (phone,))
        if existing and len(existing) > 0:
            return render_template('register.html', error='该手机号已注册，请直接登录')

        sql = """
        INSERT INTO user
            (phone, secret, username, openid, terms_version, terms_accepted_at, password_updated_at)
        VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
        """
        result = excute_sql(sql, (phone, _hash_password(secret), username, '', TERMS_VERSION), fetch=False)

        if result and result > 0:
            _sync_detection_user(phone, username, phone)
            # 创建用户文件夹
            user_dir = os.path.join(current_dir, '..', 'static', 'uploads', phone)
            create_folder(os.path.join(user_dir, 'image'))
            create_folder(os.path.join(user_dir, 'retrieve'))
            return render_template('login.html', error='注册成功，请登录')
        return render_template('register.html', error='注册失败，请重试')
    return render_template('register.html', error='注册失败，请重试')


@login_blueprint.route('/logout')
def logout():
    session.clear()
    return redirect('/login')
