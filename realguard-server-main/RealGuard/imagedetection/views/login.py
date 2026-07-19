import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import re
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import quote

import requests
from flask import Blueprint, jsonify, has_request_context, render_template, request, session, redirect, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from imagedetection.views.utils import (
    create_folder,
    excute_detection_sql,
    excute_sql,
    get_db_connection,
    normalize_account_uuid,
)

login_blueprint = Blueprint('login_blueprint', __name__)

current_dir = os.path.dirname(os.path.abspath(__file__))

PHONE_RE = re.compile(r'^1[3-9]\d{9}$')
SMS_CODE_TTL = int(os.environ.get('SMS_CODE_TTL', '300'))
SMS_INTERVAL = int(os.environ.get('SMS_INTERVAL', '60'))
SMS_CODE_LENGTH = int(os.environ.get('SMS_CODE_LENGTH', '6'))
SMS_MAX_ATTEMPTS = max(1, int(os.environ.get('SMS_MAX_ATTEMPTS', '5')))
SMS_IP_WINDOW = max(60, int(os.environ.get('SMS_IP_WINDOW', '3600')))
SMS_IP_WINDOW_LIMIT = max(1, int(os.environ.get('SMS_IP_WINDOW_LIMIT', '20')))
SMS_IP_MIN_INTERVAL = max(0, int(os.environ.get('SMS_IP_MIN_INTERVAL', '1')))
TERMS_VERSION = os.environ.get('REALGUARD_TERMS_VERSION', '2026-07-15')
TERMS_SHA256 = os.environ.get('REALGUARD_TERMS_SHA256', '09707ba3b915db9904cc6f8b4951b5c9bbfff7e768fd237c04eedf90fef89ff')
PRIVACY_SHA256 = os.environ.get('REALGUARD_PRIVACY_SHA256', 'cdf839825c20ce283ed76944aba09c5c2962abfb05592244004489b73fae80bb')
PASSWORD_MIN_LENGTH = int(os.environ.get('REALGUARD_PASSWORD_MIN_LENGTH', '8'))
PASSWORD_LOGIN_WINDOW = max(60, int(os.environ.get('REALGUARD_PASSWORD_LOGIN_WINDOW', '900')))
PASSWORD_LOGIN_PHONE_LIMIT = max(1, int(os.environ.get('REALGUARD_PASSWORD_LOGIN_PHONE_LIMIT', '8')))
PASSWORD_LOGIN_IP_LIMIT = max(1, int(os.environ.get('REALGUARD_PASSWORD_LOGIN_IP_LIMIT', '40')))
_USER_ACCOUNT_COLUMNS_READY = False
_SMS_STORAGE_READY = False
_CONSENT_STORAGE_READY = False
_USER_ACCOUNT_COLUMNS_LOCK = threading.Lock()
_SMS_STORAGE_LOCK = threading.Lock()
_CONSENT_STORAGE_LOCK = threading.Lock()


class SmsStorageError(RuntimeError):
    pass


class SmsRateLimitError(RuntimeError):
    def __init__(self, retry_after):
        self.retry_after = max(1, int(retry_after or 1))
        super().__init__(f'请 {self.retry_after} 秒后再获取验证码')


class PasswordLoginRateLimitError(RuntimeError):
    def __init__(self, retry_after):
        self.retry_after = max(1, int(retry_after or 1))
        super().__init__('登录尝试过于频繁，请稍后重试')


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
    with _USER_ACCOUNT_COLUMNS_LOCK:
        if _USER_ACCOUNT_COLUMNS_READY:
            return True
        columns = [
            ('account_uuid', "CHAR(36) NULL COMMENT '不可变账号标识'"),
            ('created_at', "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'"),
            ('terms_version', "VARCHAR(32) NULL COMMENT '用户协议版本'"),
            ('terms_accepted_at', "DATETIME NULL COMMENT '用户协议同意时间'"),
            ('password_updated_at', "DATETIME NULL COMMENT '密码更新时间'"),
            ('session_version', "INT NOT NULL DEFAULT 1 COMMENT '登录态版本'"),
        ]
        for column, definition in columns:
            if not _ensure_column('user', column, definition):
                return False
        if excute_sql(
            "UPDATE `user` SET account_uuid = UUID() WHERE account_uuid IS NULL OR account_uuid = ''",
            fetch=False,
        ) is None:
            return False
        _USER_ACCOUNT_COLUMNS_READY = True
        return True


def _ensure_consent_event_storage():
    global _CONSENT_STORAGE_READY
    if _CONSENT_STORAGE_READY:
        return True
    with _CONSENT_STORAGE_LOCK:
        if _CONSENT_STORAGE_READY:
            return True
        result = excute_sql(
            """
            CREATE TABLE IF NOT EXISTS consent_events (
              id BIGINT NOT NULL AUTO_INCREMENT,
              user_id INT NOT NULL,
              phone_hash CHAR(64) NOT NULL,
              document_version VARCHAR(32) NOT NULL,
              terms_sha256 CHAR(64) NOT NULL,
              privacy_sha256 CHAR(64) NOT NULL,
              channel VARCHAR(64) NOT NULL,
              client_ip_hash CHAR(64) NULL,
              user_agent_hash CHAR(64) NULL,
              accepted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_consent_events_user_time (user_id, accepted_at),
              CONSTRAINT fk_consent_events_user FOREIGN KEY (user_id) REFERENCES `user`(Userid)
                ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            fetch=False,
        )
        _CONSENT_STORAGE_READY = result is not None
        return _CONSENT_STORAGE_READY


def _consent_hash(value):
    salt = os.environ.get('REALGUARD_CONSENT_AUDIT_SALT', 'realguard-consent-v1')
    return hashlib.sha256(f'{salt}:{value or ""}'.encode('utf-8')).hexdigest()


def _record_terms_acceptance(phone, channel='web_auth'):
    if not _ensure_user_account_columns():
        return False
    if not _ensure_consent_event_storage():
        return False
    client_ip = request.remote_addr if has_request_context() else ''
    user_agent = request.headers.get('User-Agent', '') if has_request_context() else ''
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute("SELECT Userid FROM `user` WHERE phone = %s LIMIT 1 FOR UPDATE", (phone,))
            user = cursor.fetchone()
            if not user:
                conn.rollback()
                return False
            cursor.execute(
                """
                INSERT INTO consent_events
                    (user_id, phone_hash, document_version, terms_sha256, privacy_sha256,
                     channel, client_ip_hash, user_agent_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user['Userid'], _consent_hash(phone), TERMS_VERSION, TERMS_SHA256, PRIVACY_SHA256,
                    str(channel or 'web_auth')[:64], _consent_hash(client_ip), _consent_hash(user_agent),
                ),
            )
            cursor.execute(
                "UPDATE `user` SET terms_version = %s, terms_accepted_at = NOW() WHERE Userid = %s",
                (TERMS_VERSION, user['Userid']),
            )
        conn.commit()
        return True
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        print(f'[CONSENT EVENT ERROR] {exc}')
        return False
    finally:
        if conn is not None:
            conn.close()


def _hash_code(code, salt=''):
    """Derive a challenge-specific code hash that is stable across workers."""
    return hashlib.pbkdf2_hmac(
        'sha256',
        str(code or '').encode('utf-8'),
        str(salt or '').encode('ascii'),
        120_000,
    ).hex()


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


def _session_version(user):
    try:
        return max(1, int((user or {}).get('session_version') or 1))
    except (TypeError, ValueError):
        return 1


def _user_session_payload(user, phone=None):
    resolved_phone = str(phone if phone is not None else (user or {}).get('phone') or '').strip()
    account_uuid = normalize_account_uuid((user or {}).get('account_uuid'))
    if not account_uuid and (user or {}).get('Userid') not in (None, ''):
        rows = excute_sql(
            "SELECT account_uuid FROM user WHERE Userid = %s LIMIT 1",
            ((user or {}).get('Userid'),),
        )
        account_uuid = normalize_account_uuid((rows or [{}])[0].get('account_uuid'))
    return {
        'Userid': user['Userid'],
        'account_uuid': account_uuid,
        'username': user.get('username') or resolved_phone,
        'phone': resolved_phone,
        'openid': user.get('openid', ''),
        'session_version': _session_version(user),
    }


def validate_current_user_session(*, allow_legacy=False):
    """Validate the signed cookie against the account's revocation version."""
    user_info = session.get('user_info')
    if not isinstance(user_info, dict):
        return True
    claimed_version = user_info.get('session_version')
    if claimed_version in (None, ''):
        if allow_legacy:
            return True
        session.clear()
        return False
    if not _ensure_user_account_columns():
        session.clear()
        return False
    user_id = user_info.get('Userid') or user_info.get('userId') or user_info.get('id')
    if user_id in (None, ''):
        session.clear()
        return False
    rows = excute_sql(
        "SELECT Userid, account_uuid, phone, openid, session_version FROM user WHERE Userid = %s LIMIT 1",
        (user_id,),
    )
    if not rows:
        session.clear()
        return False
    account = rows[0]
    claimed_account_uuid = normalize_account_uuid(user_info.get('account_uuid'))
    account_uuid = normalize_account_uuid(account.get('account_uuid'))
    try:
        valid_version = int(claimed_version) == _session_version(account)
    except (TypeError, ValueError):
        valid_version = False
    claimed_phone = str(user_info.get('phone') or '').strip()
    account_phone = str(account.get('phone') or '').strip()
    claimed_openid = str(user_info.get('openid') or '').strip()
    account_openid = str(account.get('openid') or '').strip()
    identity_matches = (claimed_phone and claimed_phone == account_phone) or (
        not claimed_phone and claimed_openid and claimed_openid == account_openid
    )
    if not claimed_account_uuid and account_uuid and valid_version and identity_matches:
        # One-time rollout upgrade for already signed, still-valid sessions.
        user_info['account_uuid'] = account_uuid
        session['user_info'] = user_info
        session.modified = True
        claimed_account_uuid = account_uuid
    immutable_identity_matches = bool(
        claimed_account_uuid and account_uuid and hmac.compare_digest(claimed_account_uuid, account_uuid)
    )
    if not valid_version or not identity_matches or not immutable_identity_matches:
        session.clear()
        return False
    return True


def _ensure_sms_storage():
    global _SMS_STORAGE_READY
    if _SMS_STORAGE_READY:
        return True
    with _SMS_STORAGE_LOCK:
        if _SMS_STORAGE_READY:
            return True
        challenge_result = excute_sql(
            """
            CREATE TABLE IF NOT EXISTS sms_verification_challenges (
                scene VARCHAR(16) NOT NULL,
                phone VARCHAR(32) NOT NULL,
                code_hash CHAR(64) NOT NULL,
                code_salt CHAR(32) NOT NULL,
                expires_at BIGINT UNSIGNED NOT NULL,
                sent_at BIGINT UNSIGNED NOT NULL,
                failed_attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
                consumed_at BIGINT UNSIGNED NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (scene, phone),
                KEY idx_sms_challenge_expiry (expires_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            fetch=False,
        )
        limit_result = excute_sql(
            """
            CREATE TABLE IF NOT EXISTS sms_send_limits (
                scope_key CHAR(64) NOT NULL,
                scope_type VARCHAR(16) NOT NULL,
                window_started_at BIGINT UNSIGNED NOT NULL DEFAULT 0,
                request_count INT UNSIGNED NOT NULL DEFAULT 0,
                last_sent_at BIGINT UNSIGNED NOT NULL DEFAULT 0,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (scope_key),
                KEY idx_sms_limit_updated (updated_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """,
            fetch=False,
        )
        _SMS_STORAGE_READY = challenge_result is not None and limit_result is not None
        return _SMS_STORAGE_READY


def _normalized_ip(value):
    try:
        return str(ipaddress.ip_address(str(value or '').strip()))
    except ValueError:
        return ''


def _trusted_proxy_networks():
    configured = os.environ.get('REALGUARD_TRUSTED_PROXY_IPS', '127.0.0.0/8,::1/128')
    networks = []
    for raw in configured.split(','):
        try:
            networks.append(ipaddress.ip_network(raw.strip(), strict=False))
        except ValueError:
            continue
    return networks


def _trusted_client_ip():
    peer = _normalized_ip(request.remote_addr)
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        return 'unknown'
    if any(peer_address in network for network in _trusted_proxy_networks()):
        forwarded = _normalized_ip(request.headers.get('X-Real-IP'))
        if forwarded:
            return forwarded
    return peer


def _sms_scope_key(scope_type, value):
    return hashlib.sha256(f'{scope_type}:{value}'.encode('utf-8')).hexdigest()


def _reserve_password_login_attempt(phone, client_ip=None, now=None):
    """Atomically limit password attempts across workers by phone and IP."""
    if not _ensure_sms_storage():
        raise SmsStorageError('登录保护服务暂不可用')
    now = int(time.time() if now is None else now)
    phone_key = _sms_scope_key('password-phone', phone)
    ip_key = _sms_scope_key('password-ip', client_ip or _trusted_client_ip() or 'unknown')
    scopes = sorted((
        (phone_key, 'password-phone', PASSWORD_LOGIN_PHONE_LIMIT),
        (ip_key, 'password-ip', PASSWORD_LOGIN_IP_LIMIT),
    ), key=lambda item: item[0])
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            for scope_key, scope_type, _ in scopes:
                cursor.execute(
                    """
                    INSERT IGNORE INTO sms_send_limits
                        (scope_key, scope_type, window_started_at, request_count, last_sent_at)
                    VALUES (%s, %s, 0, 0, 0)
                    """,
                    (scope_key, scope_type),
                )
            cursor.execute(
                """
                SELECT scope_key, scope_type, window_started_at, request_count
                FROM sms_send_limits
                WHERE scope_key IN (%s, %s)
                ORDER BY scope_key
                FOR UPDATE
                """,
                (scopes[0][0], scopes[1][0]),
            )
            rows = {row['scope_key']: row for row in (cursor.fetchall() or [])}
            updates = []
            for scope_key, _, limit in scopes:
                row = rows.get(scope_key) or {}
                window_start = int(row.get('window_started_at') or 0)
                count = int(row.get('request_count') or 0)
                if window_start <= 0 or now - window_start >= PASSWORD_LOGIN_WINDOW:
                    window_start = now
                    count = 0
                if count >= limit:
                    raise PasswordLoginRateLimitError(window_start + PASSWORD_LOGIN_WINDOW - now)
                updates.append((window_start, count + 1, scope_key))
            for window_start, count, scope_key in updates:
                cursor.execute(
                    """
                    UPDATE sms_send_limits
                    SET window_started_at = %s, request_count = %s, last_sent_at = %s
                    WHERE scope_key = %s
                    """,
                    (window_start, count, now, scope_key),
                )
        conn.commit()
    except PasswordLoginRateLimitError:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise SmsStorageError('登录保护服务暂不可用') from exc
    finally:
        if conn:
            conn.close()


def _clear_password_phone_attempts(phone):
    if not phone:
        return False
    result = excute_sql(
        """
        UPDATE sms_send_limits
        SET window_started_at = 0, request_count = 0, last_sent_at = 0
        WHERE scope_key = %s
        """,
        (_sms_scope_key('password-phone', phone),),
        fetch=False,
    )
    return result is not None


def _reserve_sms_send(scene, phone, client_ip, now=None):
    """Atomically reserve both phone and IP rate-limit capacity."""
    if not _ensure_sms_storage():
        raise SmsStorageError('短信验证服务暂不可用')
    now = int(time.time() if now is None else now)
    phone_key = _sms_scope_key('phone', phone)
    ip_key = _sms_scope_key('ip', client_ip or 'unknown')
    scope_rows = sorted(((phone_key, 'phone'), (ip_key, 'ip')), key=lambda item: item[0])
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            for scope_key, scope_type in scope_rows:
                cursor.execute(
                    """
                    INSERT IGNORE INTO sms_send_limits
                        (scope_key, scope_type, window_started_at, request_count, last_sent_at)
                    VALUES (%s, %s, 0, 0, 0)
                    """,
                    (scope_key, scope_type),
                )
            cursor.execute(
                """
                SELECT scope_key, scope_type, window_started_at, request_count, last_sent_at
                FROM sms_send_limits
                WHERE scope_key IN (%s, %s)
                ORDER BY scope_key
                FOR UPDATE
                """,
                (scope_rows[0][0], scope_rows[1][0]),
            )
            rows = {row['scope_key']: row for row in (cursor.fetchall() or [])}
            phone_row = rows.get(phone_key) or {}
            phone_remain = int(phone_row.get('last_sent_at') or 0) + SMS_INTERVAL - now
            if phone_remain > 0:
                raise SmsRateLimitError(phone_remain)

            ip_row = rows.get(ip_key) or {}
            ip_window_start = int(ip_row.get('window_started_at') or 0)
            ip_count = int(ip_row.get('request_count') or 0)
            if ip_window_start <= 0 or now - ip_window_start >= SMS_IP_WINDOW:
                ip_window_start = now
                ip_count = 0
            ip_remain = int(ip_row.get('last_sent_at') or 0) + SMS_IP_MIN_INTERVAL - now
            if ip_remain > 0:
                raise SmsRateLimitError(ip_remain)
            if ip_count >= SMS_IP_WINDOW_LIMIT:
                raise SmsRateLimitError(ip_window_start + SMS_IP_WINDOW - now)

            cursor.execute(
                """
                UPDATE sms_send_limits
                SET window_started_at = %s, request_count = request_count + 1, last_sent_at = %s
                WHERE scope_key = %s
                """,
                (now, now, phone_key),
            )
            cursor.execute(
                """
                UPDATE sms_send_limits
                SET window_started_at = %s, request_count = %s, last_sent_at = %s
                WHERE scope_key = %s
                """,
                (ip_window_start, ip_count + 1, now, ip_key),
            )
        conn.commit()
    except SmsRateLimitError:
        if conn:
            conn.rollback()
        raise
    except Exception as exc:
        if conn:
            conn.rollback()
        raise SmsStorageError('短信验证服务暂不可用') from exc
    finally:
        if conn:
            conn.close()


def _save_sms_code(scene, phone, code, now=None):
    if not _ensure_sms_storage():
        raise SmsStorageError('短信验证服务暂不可用')
    now = int(time.time() if now is None else now)
    salt = secrets.token_hex(16)
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sms_verification_challenges
                    (scene, phone, code_hash, code_salt, expires_at, sent_at, failed_attempts, consumed_at)
                VALUES (%s, %s, %s, %s, %s, %s, 0, NULL)
                ON DUPLICATE KEY UPDATE
                    code_hash = VALUES(code_hash),
                    code_salt = VALUES(code_salt),
                    expires_at = VALUES(expires_at),
                    sent_at = VALUES(sent_at),
                    failed_attempts = 0,
                    consumed_at = NULL
                """,
                (scene, phone, _hash_code(code, salt), salt, now + SMS_CODE_TTL, now),
            )
        conn.commit()
        return True
    except Exception as exc:
        if conn:
            conn.rollback()
        raise SmsStorageError('短信验证服务暂不可用') from exc
    finally:
        if conn:
            conn.close()


def _delete_sms_challenge(scene, phone):
    if not _ensure_sms_storage():
        return False
    result = excute_sql(
        "DELETE FROM sms_verification_challenges WHERE scene = %s AND phone = %s",
        (scene, phone),
        fetch=False,
    )
    return result is not None


def _verify_sms_code(scene, phone, code, now=None):
    """Verify and consume one challenge while holding its database row lock."""
    if not _ensure_sms_storage():
        return False, '短信验证服务暂不可用，请稍后重试'
    now = int(time.time() if now is None else now)
    conn = None
    try:
        conn = get_db_connection()
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT code_hash, code_salt, expires_at, failed_attempts, consumed_at
                FROM sms_verification_challenges
                WHERE scene = %s AND phone = %s
                LIMIT 1
                FOR UPDATE
                """,
                (scene, phone),
            )
            item = cursor.fetchone()
            if not item:
                conn.commit()
                return False, '验证码无效或已过期，请重新获取'
            attempts = int(item.get('failed_attempts') or 0)
            if item.get('consumed_at') is not None or attempts >= SMS_MAX_ATTEMPTS:
                conn.commit()
                return False, '验证码无效或已过期，请重新获取'
            if int(item.get('expires_at') or 0) < now:
                cursor.execute(
                    """
                    UPDATE sms_verification_challenges
                    SET consumed_at = %s
                    WHERE scene = %s AND phone = %s AND consumed_at IS NULL
                    """,
                    (now, scene, phone),
                )
                conn.commit()
                return False, '验证码无效或已过期，请重新获取'

            candidate_hash = _hash_code(code, item.get('code_salt') or '')
            if not hmac.compare_digest(str(item.get('code_hash') or ''), candidate_hash):
                attempts += 1
                consumed_at = now if attempts >= SMS_MAX_ATTEMPTS else None
                cursor.execute(
                    """
                    UPDATE sms_verification_challenges
                    SET failed_attempts = %s, consumed_at = %s
                    WHERE scene = %s AND phone = %s AND consumed_at IS NULL
                    """,
                    (attempts, consumed_at, scene, phone),
                )
                conn.commit()
                if attempts >= SMS_MAX_ATTEMPTS:
                    return False, '验证码错误次数过多，请重新获取'
                return False, f'验证码错误，还可尝试 {SMS_MAX_ATTEMPTS - attempts} 次'

            cursor.execute(
                """
                UPDATE sms_verification_challenges
                SET consumed_at = %s
                WHERE scene = %s AND phone = %s
                  AND consumed_at IS NULL AND failed_attempts < %s
                """,
                (now, scene, phone, SMS_MAX_ATTEMPTS),
            )
            consumed = cursor.rowcount == 1
        conn.commit()
        if consumed:
            return True, ''
        return False, '验证码无效或已过期，请重新获取'
    except Exception:
        if conn:
            conn.rollback()
        return False, '短信验证服务暂不可用，请稍后重试'
    finally:
        if conn:
            conn.close()


def _check_sms_interval(scene, phone):
    """Compatibility helper for callers that only need the phone cooldown."""
    del scene
    if not _ensure_sms_storage():
        raise SmsStorageError('短信验证服务暂不可用')
    rows = excute_sql(
        "SELECT last_sent_at FROM sms_send_limits WHERE scope_key = %s LIMIT 1",
        (_sms_scope_key('phone', phone),),
    )
    if rows is None:
        raise SmsStorageError('短信验证服务暂不可用')
    last_sent_at = int((rows[0] if rows else {}).get('last_sent_at') or 0)
    return max(0, last_sent_at + SMS_INTERVAL - int(time.time()))


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
    code = ''.join(secrets.choice('0123456789') for _ in range(SMS_CODE_LENGTH))
    provider = os.environ.get('SMS_PROVIDER', '').strip().lower()
    if not provider:
        provider = 'aliyun' if os.environ.get('ALIYUN_ACCESS_KEY_ID') else 'disabled'

    _save_sms_code(scene, phone, code)
    try:
        if provider == 'aliyun':
            _send_sms_by_aliyun(phone, code, scene)
        elif provider != 'mock':
            raise RuntimeError('短信服务未配置，请设置 SMS_PROVIDER=aliyun；本地测试需显式设置 SMS_PROVIDER=mock')
    except Exception:
        _delete_sms_challenge(scene, phone)
        raise
    return code if os.environ.get('SMS_DEBUG_RETURN_CODE') == '1' else None


def _sync_detection_user(phone, username='', openid='', account_uuid=''):
    """同步网页账号到 /home/ymk/RealGuard 鉴伪后端数据库。"""
    if not phone:
        return
    immutable_owner = normalize_account_uuid(account_uuid)
    result = excute_detection_sql(
        """
        INSERT INTO user (account_uuid, openid, avatar, username, phone)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            account_uuid = IF(
                account_uuid IS NULL OR account_uuid = '', VALUES(account_uuid), account_uuid
            ),
            openid = IF(openid IS NULL OR openid = '', VALUES(openid), openid),
            username = IF(username IS NULL OR username = '', VALUES(username), username)
        """,
        (immutable_owner or None, openid or phone, '', username or phone, phone),
        fetch=False,
    )
    if result is None:
        return False
    rows = excute_detection_sql(
        "SELECT account_uuid FROM user WHERE phone = %s LIMIT 1",
        (phone,),
    )
    stored_owner = normalize_account_uuid((rows or [{}])[0].get('account_uuid'))
    if immutable_owner and stored_owner != immutable_owner:
        print('[DETECTION USER SYNC ERROR] immutable owner mismatch')
        return False
    return True


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
        return jsonify({'success': False, 'message': '短信验证服务暂不可用，请稍后重试'}), 503

    try:
        _reserve_sms_send(scene, phone, _trusted_client_ip())
    except SmsRateLimitError as exc:
        response = jsonify({
            'success': False,
            'message': str(exc),
            'remain': exc.retry_after,
        })
        response.headers['Retry-After'] = str(exc.retry_after)
        return response, 429
    except SmsStorageError:
        return jsonify({'success': False, 'message': '短信验证服务暂不可用，请稍后重试'}), 503

    eligible = (scene == 'register' and not existing) or (scene in ('login', 'reset') and bool(existing))
    generic_message = '如手机号符合当前操作条件，验证码将发送，请留意短信'
    if not eligible:
        return jsonify({'success': True, 'message': generic_message, 'expires_in': SMS_CODE_TTL})

    try:
        debug_code = _send_sms_code(phone, scene)
    except SmsStorageError:
        return jsonify({'success': False, 'message': '短信验证服务暂不可用，请稍后重试'}), 503
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500

    data = {'success': True, 'message': generic_message, 'expires_in': SMS_CODE_TTL}
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
        if not _truthy(request.form.get('accepted_terms')):
            return render_template('login.html', error='请先阅读并同意用户协议和隐私政策')

        try:
            _reserve_password_login_attempt(phone)
        except PasswordLoginRateLimitError as exc:
            return render_template('login.html', error=str(exc)), 429, {'Retry-After': str(exc.retry_after)}
        except SmsStorageError as exc:
            return render_template('login.html', error=str(exc)), 503

        user = _authenticate_password_user(phone, secret)
        if user:
            _clear_password_phone_attempts(phone)
            if not _record_terms_acceptance(phone):
                return render_template('login.html', error='协议确认记录失败，请稍后重试')
            session_payload = _user_session_payload(user, phone)
            _sync_detection_user(
                phone,
                user.get('username') or phone,
                user.get('openid', '') or phone,
                session_payload.get('account_uuid'),
            )
            session.clear()
            session.permanent = True
            session['user_info'] = session_payload
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
        if not _truthy(request.form.get('accepted_terms')):
            return render_template('login.html', error='请先阅读并同意用户协议和隐私政策', login_mode='sms')
        ok, message = _verify_sms_code('login', phone, sms_code)
        if not ok:
            return render_template('login.html', error=message, login_mode='sms')

        user = _find_user_by_phone(phone)
        if user:
            if not _record_terms_acceptance(phone):
                return render_template('login.html', error='协议确认记录失败，请稍后重试', login_mode='sms')
            session_payload = _user_session_payload(user, phone)
            _sync_detection_user(
                phone,
                user.get('username') or phone,
                user.get('openid', '') or phone,
                session_payload.get('account_uuid'),
            )
            session.clear()
            session.permanent = True
            session['user_info'] = session_payload
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
            "UPDATE user SET secret = %s, session_version = session_version + 1, password_updated_at = NOW() WHERE phone = %s",
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
            (account_uuid, phone, secret, username, openid, terms_version, terms_accepted_at, password_updated_at)
        VALUES (UUID(), %s, %s, %s, %s, %s, NOW(), NOW())
        """
        result = excute_sql(sql, (phone, _hash_password(secret), username, '', TERMS_VERSION), fetch=False)

        if result and result > 0:
            if not _record_terms_acceptance(phone, channel='web_register'):
                return render_template('register.html', error='协议确认记录失败，请稍后重试')
            created_user = _find_user_by_phone(phone) or {}
            _sync_detection_user(phone, username, phone, created_user.get('account_uuid'))
            # 创建用户文件夹
            user_dir = os.path.join(current_dir, '..', 'static', 'uploads', phone)
            create_folder(os.path.join(user_dir, 'image'))
            return render_template('login.html', error='注册成功，请登录')
        return render_template('register.html', error='注册失败，请重试')
    return render_template('register.html', error='注册失败，请重试')


@login_blueprint.route('/logout')
def logout():
    session.clear()
    return redirect('/login')
