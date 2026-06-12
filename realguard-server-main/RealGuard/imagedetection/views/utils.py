import os
import json
import pymysql
from datetime import datetime


# ==============================================================================
# 数据库配置 (连接 system 数据库)
# ==============================================================================
DB_CONFIG = {
    'host': os.environ.get('REALGUARD_DB_HOST', '127.0.0.1'),
    'port': int(os.environ.get('REALGUARD_DB_PORT', '3306')),
    'user': os.environ.get('REALGUARD_DB_USER', 'root'),
    'password': os.environ.get('REALGUARD_DB_PASSWORD', '123456'),
    'database': os.environ.get('REALGUARD_DB_NAME', 'system'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
}

DETECTION_DB_CONFIG = {
    'host': os.environ.get('REALGUARD_DETECTION_DB_HOST', '127.0.0.1'),
    'port': int(os.environ.get('REALGUARD_DETECTION_DB_PORT', '3306')),
    'user': os.environ.get('REALGUARD_DETECTION_DB_USER', 'root'),
    'password': os.environ.get('REALGUARD_DETECTION_DB_PASSWORD', '123456'),
    'database': os.environ.get('REALGUARD_DETECTION_DB_NAME', 'image_detection'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
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
    检索业务仍使用 excute_sql(system)，图像/视频鉴伪历史使用本函数(image_detection)。
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
