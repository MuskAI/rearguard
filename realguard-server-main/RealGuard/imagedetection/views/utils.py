import os
import json
import pymysql
import uuid
from datetime import datetime


# ==============================================================================
# 数据库配置 (连接 system 数据库)
# ==============================================================================
DB_CONFIG = {
    'host': os.environ.get('REALGUARD_DB_HOST', '127.0.0.1'),
    'port': int(os.environ.get('REALGUARD_DB_PORT', '3306')),
    'user': os.environ.get('REALGUARD_DB_USER', 'root'),
    'password': os.environ.get('REALGUARD_DB_PASSWORD', ''),
    'database': os.environ.get('REALGUARD_DB_NAME', 'system'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
    'connect_timeout': int(os.environ.get('REALGUARD_DB_CONNECT_TIMEOUT', '5')),
    'read_timeout': int(os.environ.get('REALGUARD_DB_READ_TIMEOUT', '30')),
    'write_timeout': int(os.environ.get('REALGUARD_DB_WRITE_TIMEOUT', '30')),
}

DETECTION_DB_CONFIG = {
    'host': os.environ.get('REALGUARD_DETECTION_DB_HOST', '127.0.0.1'),
    'port': int(os.environ.get('REALGUARD_DETECTION_DB_PORT', '3306')),
    'user': os.environ.get('REALGUARD_DETECTION_DB_USER', 'root'),
    'password': os.environ.get('REALGUARD_DETECTION_DB_PASSWORD', ''),
    'database': os.environ.get('REALGUARD_DETECTION_DB_NAME', 'image_detection'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
    'connect_timeout': int(os.environ.get('REALGUARD_DETECTION_DB_CONNECT_TIMEOUT', '5')),
    'read_timeout': int(os.environ.get('REALGUARD_DETECTION_DB_READ_TIMEOUT', '30')),
    'write_timeout': int(os.environ.get('REALGUARD_DETECTION_DB_WRITE_TIMEOUT', '30')),
}


def _should_fetch(sql):
    first_token = (sql.strip().split(None, 1) or [''])[0].upper()
    return first_token in {'SELECT', 'SHOW', 'DESCRIBE', 'DESC', 'EXPLAIN', 'WITH'}


def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)


def get_detection_db_connection():
    """连接 /home/ymk/RealGuard 鉴伪后端使用的 image_detection 数据库。"""
    return pymysql.connect(**DETECTION_DB_CONFIG)


def normalize_account_uuid(value):
    raw = str(value or '').strip()
    if not raw:
        return ''
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError):
        return ''


def detection_owner_where(phone='', openid='', *, account_uuid='', require_account_uuid=False):
    """Build a tenant filter from identities verified by the account database.

    The account database and detection database have independent auto-increment
    user IDs. Their ``Userid`` values must never be compared across databases.
    """
    immutable_owner = normalize_account_uuid(account_uuid)
    if immutable_owner:
        return 'owner_account_uuid = %s', (immutable_owner,)
    if require_account_uuid:
        return '1 = 0', ()

    clauses = []
    params = []
    phone = str(phone or '').strip()
    openid = str(openid or '').strip()
    if phone:
        clauses.append('(phone = %s)')
        params.append(phone)
    if openid:
        clauses.append("((phone IS NULL OR phone = '') AND openid = %s)")
        params.append(openid)
    if not clauses:
        return '1 = 0', ()
    return ' OR '.join(clauses), tuple(params)


def _column_info(cursor, table, column):
    cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    return cursor.fetchone()


def _column_exists(cursor, table, column):
    return bool(_column_info(cursor, table, column))


def _index_exists(cursor, table, index_name):
    cursor.execute(f"SHOW INDEX FROM `{table}` WHERE Key_name = %s", (index_name,))
    return bool(cursor.fetchone())


def apply_account_identity_schema():
    """Create immutable ownership columns without guessing legacy ownership."""
    account_conn = get_db_connection()
    detection_conn = get_detection_db_connection()
    changes = {'accounts': 0, 'detection_users': 0, 'data': 0, 'video_data': 0}
    try:
        # MySQL DDL auto-commits. Add fail-closed owner columns first so a later
        # account migration failure cannot leave new application code without
        # the columns its tenant filters require.
        detection_conn.begin()
        with detection_conn.cursor() as cursor:
            for table in ('data', 'video_data'):
                if not _column_exists(cursor, table, 'owner_account_uuid'):
                    cursor.execute(
                        f"ALTER TABLE `{table}` ADD COLUMN `owner_account_uuid` CHAR(36) NULL COMMENT 'system.user不可变账号标识' AFTER `Userid`"
                    )
                index_name = f'idx_{table}_owner_uuid_ct'
                if not _index_exists(cursor, table, index_name):
                    cursor.execute(
                        f"ALTER TABLE `{table}` ADD KEY `{index_name}` (`owner_account_uuid`, `createtime`)"
                    )
        detection_conn.commit()

        account_conn.begin()
        with account_conn.cursor() as cursor:
            if not _column_exists(cursor, 'user', 'account_uuid'):
                cursor.execute(
                    "ALTER TABLE `user` ADD COLUMN `account_uuid` CHAR(36) NULL COMMENT '不可变账号标识' AFTER `Userid`"
                )
            cursor.execute(
                "UPDATE `user` SET account_uuid = UUID() WHERE account_uuid IS NULL OR account_uuid = ''"
            )
            changes['accounts'] = cursor.rowcount
            if not _index_exists(cursor, 'user', 'uk_user_account_uuid'):
                cursor.execute("ALTER TABLE `user` ADD UNIQUE KEY `uk_user_account_uuid` (`account_uuid`)")
            account_uuid_column = _column_info(cursor, 'user', 'account_uuid') or {}
            if str(account_uuid_column.get('Null') or '').upper() != 'NO':
                cursor.execute(
                    "ALTER TABLE `user` MODIFY COLUMN account_uuid CHAR(36) NOT NULL COMMENT '不可变账号标识'"
                )
            cursor.execute(
                "SELECT COUNT(*) AS count FROM `user` WHERE account_uuid IS NULL OR account_uuid = ''"
            )
            if int((cursor.fetchone() or {}).get('count') or 0):
                raise RuntimeError('system.user 仍存在缺失的 account_uuid')
        account_conn.commit()

        detection_conn.begin()
        with detection_conn.cursor() as cursor:
            if not _column_exists(cursor, 'user', 'account_uuid'):
                cursor.execute(
                    "ALTER TABLE `user` ADD COLUMN `account_uuid` CHAR(36) NULL COMMENT 'system.user不可变账号标识' AFTER `Userid`"
                )
            # Phone numbers and openids can be recycled. Existing history that
            # predates immutable ownership must remain unclaimed until an admin
            # verifies it against an external migration ledger. Automatic
            # backfill would grant a current identifier holder access to an old
            # account's history, media, and reports.
            for table in ('data', 'video_data'):
                cursor.execute(
                    f"SELECT COUNT(*) AS count FROM `{table}` "
                    "WHERE owner_account_uuid IS NULL OR owner_account_uuid = ''"
                )
                row = cursor.fetchone() or {}
                changes[f'unowned_{table}'] = int(row.get('count') or 0)
            if not _index_exists(cursor, 'user', 'uk_detection_user_account_uuid'):
                cursor.execute(
                    "ALTER TABLE `user` ADD UNIQUE KEY `uk_detection_user_account_uuid` (`account_uuid`)"
                )
            for table in ('data', 'video_data'):
                if not _column_exists(cursor, table, 'owner_account_uuid'):
                    raise RuntimeError(f'image_detection.{table} 缺少 owner_account_uuid')
                if not _index_exists(cursor, table, f'idx_{table}_owner_uuid_ct'):
                    raise RuntimeError(f'image_detection.{table} 缺少属主索引')
        detection_conn.commit()
        return changes
    except Exception:
        account_conn.rollback()
        detection_conn.rollback()
        raise
    finally:
        account_conn.close()
        detection_conn.close()


def claim_detection_record_owner(table, itemid, account_uuid, phone='', openid=''):
    """Verify immutable ownership; legacy identifiers must never claim old rows."""
    if table not in {'data', 'video_data'}:
        raise ValueError('unsupported detection history table')
    immutable_owner = normalize_account_uuid(account_uuid)
    if not immutable_owner or itemid in (None, ''):
        return False
    rows = excute_detection_sql(
        f"SELECT itemid FROM `{table}` WHERE itemid = %s AND owner_account_uuid = %s LIMIT 1",
        (itemid, immutable_owner),
    )
    return bool(rows)


def excute_sql(sql, params=None, fetch=True):
    """
    执行 SQL 语句
    - SELECT/SHOW/DESCRIBE/EXPLAIN 类语句返回结果列表 (list of dict)
    - INSERT/UPDATE/DELETE 返回受影响行数 (int)
    - 出错返回 None
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            if fetch and _should_fetch(sql):
                result = cursor.fetchall()
            else:
                conn.commit()
                result = cursor.rowcount
        return result
    except Exception as e:
        print(f"[SQL ERROR] {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


def excute_sql_lastid(sql, params=None):
    """
    执行 INSERT 并返回 last_insert_id
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        print(f"[SQL ERROR] {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


def excute_detection_sql(sql, params=None, fetch=True):
    """
    执行鉴伪后端数据库 SQL。
    图像/视频鉴伪历史使用本函数(image_detection)。
    """
    conn = None
    try:
        conn = get_detection_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            if fetch and _should_fetch(sql):
                result = cursor.fetchall()
            else:
                conn.commit()
                result = cursor.rowcount
        return result
    except Exception as e:
        print(f"[DETECTION SQL ERROR] {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


def excute_detection_sql_lastid(sql, params=None):
    """
    执行鉴伪库 INSERT 并返回 last_insert_id。
    """
    conn = None
    try:
        conn = get_detection_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
    except Exception as e:
        print(f"[DETECTION SQL ERROR] {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()


def repair_detection_history_owners():
    """Align stored ``Userid`` values with the detection database user table."""
    conn = get_detection_db_connection()
    changes = {}
    try:
        with conn.cursor() as cursor:
            for table in ('data', 'video_data'):
                cursor.execute(
                    f"""
                    UPDATE `{table}` records
                    JOIN `user` owners ON BINARY records.phone = BINARY owners.phone
                    SET records.Userid = owners.Userid
                    WHERE records.phone IS NOT NULL AND records.phone <> ''
                      AND owners.phone IS NOT NULL AND owners.phone <> ''
                      AND (records.Userid IS NULL OR records.Userid <> owners.Userid)
                    """
                )
                phone_changes = cursor.rowcount
                cursor.execute(
                    f"""
                    UPDATE `{table}` records
                    JOIN `user` owners ON BINARY records.openid = BINARY owners.openid
                    SET records.Userid = owners.Userid
                    WHERE (records.phone IS NULL OR records.phone = '')
                      AND records.openid IS NOT NULL AND records.openid <> ''
                      AND owners.openid IS NOT NULL AND owners.openid <> ''
                      AND (records.Userid IS NULL OR records.Userid <> owners.Userid)
                    """
                )
                changes[table] = phone_changes + cursor.rowcount
        conn.commit()
        return changes
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_folder(path):
    """递归创建文件夹"""
    os.makedirs(path, exist_ok=True)


def format_createtime(raw):
    """格式化时间显示"""
    if not raw:
        return ''
    try:
        if isinstance(raw, datetime):
            return raw.strftime('%Y-%m-%d %H:%M:%S')
        text = str(raw)
        if len(text) == 14 and text.isdigit():
            return datetime.strptime(text, '%Y%m%d%H%M%S').strftime('%Y-%m-%d %H:%M:%S')
        return text
    except Exception:
        return str(raw)


def get_now_str():
    """获取当前时间字符串"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def get_file_size_str(file_path):
    """获取文件大小的可读字符串"""
    try:
        size = os.path.getsize(file_path)
        if size < 1024:
            return f"{size}B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        else:
            return f"{size / (1024 * 1024):.1f}MB"
    except Exception:
        return ''


def get_image_info(file_path):
    """获取图片基本信息: 格式、分辨率"""
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            fmt = img.format or ''
            w, h = img.size
            resolution = f"{w}x{h}"
            return fmt, resolution
    except Exception:
        return '', ''


def safe_truncate(text, max_len=150):
    """安全截断字符串"""
    if not text:
        return ''
    text = str(text)
    if len(text) > max_len:
        return text[:max_len - 3] + '...'
    return text
