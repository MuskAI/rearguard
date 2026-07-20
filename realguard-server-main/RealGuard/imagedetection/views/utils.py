import os
import json
import hashlib
import hmac
import pymysql
import stat
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


def detection_record_is_publishable(record):
    """Hide developer API results until their billing reservation is settled."""
    item = record or {}
    filename = os.path.basename(str(item.get('filename') or ''))
    stem = os.path.splitext(filename)[0]
    task_suffix = str(item.get('developer_task_id') or '').strip()
    if not task_suffix:
        marker = 'developer-job_'
        marker_index = stem.find(marker)
        if marker_index < 0:
            return True
        storage_prefix = stem[:marker_index]
        if storage_prefix:
            if len(storage_prefix) != 13 or storage_prefix[-1] != '-':
                return True
            if any(char not in '0123456789abcdef' for char in storage_prefix[:12].lower()):
                return True
        task_suffix = stem[marker_index + len('developer-'):]
    if len(task_suffix) != 24 or not task_suffix.startswith('job_'):
        return True
    if any(char not in '0123456789abcdef' for char in task_suffix[4:].lower()):
        return True
    itemid = item.get('itemid')
    if itemid in (None, ''):
        return False
    rows = excute_sql(
        """
        SELECT task.status AS task_status, reservation.status AS billing_status
        FROM developer_detection_tasks AS task
        LEFT JOIN developer_billing_reservations AS reservation
          ON reservation.task_id = task.task_id
        WHERE task.task_id = %s
          AND (task.effect_item_id = %s OR task.result_item_id = %s)
        LIMIT 1
        """,
        (task_suffix, itemid, itemid),
    )
    if rows:
        return (
            rows[0].get('task_status') == 'success'
            and rows[0].get('billing_status') == 'settled'
        )
    web_rows = excute_sql(
        """
        SELECT status
        FROM web_detection_tasks
        WHERE job_id = %s AND effect_item_id = %s
        LIMIT 1
        """,
        (task_suffix, itemid),
    )
    return bool(web_rows and web_rows[0].get('status') == 'success')


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
            if not _column_exists(cursor, 'data', 'developer_task_id'):
                cursor.execute(
                    "ALTER TABLE `data` ADD COLUMN `developer_task_id` VARCHAR(64) NULL "
                    "COMMENT '开发者任务结算可见性标识' AFTER `owner_account_uuid`"
                )
            if not _index_exists(cursor, 'data', 'idx_data_developer_task'):
                cursor.execute(
                    "ALTER TABLE `data` ADD KEY `idx_data_developer_task` (`developer_task_id`)"
                )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS `legacy_record_governance` (
                    `id` BIGINT NOT NULL AUTO_INCREMENT,
                    `request_operation_id` CHAR(36) NOT NULL,
                    `record_type` VARCHAR(16) NOT NULL,
                    `record_id` BIGINT NOT NULL,
                    `record_fingerprint` CHAR(64) NOT NULL,
                    `media_sha256` CHAR(64) NOT NULL,
                    `active_record_key` VARCHAR(64) NULL,
                    `status` VARCHAR(24) NOT NULL DEFAULT 'claim_pending',
                    `target_account_uuid` CHAR(36) NOT NULL,
                    `target_user_id` BIGINT NOT NULL,
                    `target_account_fingerprint` CHAR(64) NOT NULL,
                    `evidence_reference` VARCHAR(512) NOT NULL,
                    `evidence_sha256` CHAR(64) NOT NULL,
                    `reason` VARCHAR(1000) NOT NULL,
                    `requester_admin_id` BIGINT NOT NULL,
                    `requester_username` VARCHAR(64) NOT NULL,
                    `requester_identity_hash` CHAR(64) NOT NULL,
                    `request_integrity_hmac` CHAR(64) NOT NULL,
                    `requested_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    `approval_operation_id` CHAR(36) NULL,
                    `approver_admin_id` BIGINT NULL,
                    `approver_username` VARCHAR(64) NULL,
                    `approver_identity_hash` CHAR(64) NULL,
                    `approval_integrity_hmac` CHAR(64) NULL,
                    `decision_reason` VARCHAR(1000) NULL,
                    `audit_key_id` VARCHAR(64) NOT NULL,
                    `approved_at` DATETIME NULL,
                    `version` INT NOT NULL DEFAULT 1,
                    PRIMARY KEY (`id`),
                    UNIQUE KEY `uk_legacy_request_operation` (`request_operation_id`),
                    UNIQUE KEY `uk_legacy_active_record` (`active_record_key`),
                    UNIQUE KEY `uk_legacy_approval_operation` (`approval_operation_id`),
                    KEY `idx_legacy_status_requested` (`status`, `requested_at`),
                    KEY `idx_legacy_target_account` (`target_account_uuid`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
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
            if not _column_exists(cursor, 'data', 'developer_task_id'):
                raise RuntimeError('image_detection.data 缺少 developer_task_id')
        detection_conn.commit()
        return changes
    except Exception:
        account_conn.rollback()
        detection_conn.rollback()
        raise
    finally:
        account_conn.close()
        detection_conn.close()


LEGACY_RECORD_FIELDS = {
    'data': (
        'itemid', 'createtime', 'filename', 'phone', 'openid', 'Userid',
        'file_size', 'img_format', 'resolution',
    ),
    'video_data': (
        'itemid', 'createtime', 'filename', 'phone', 'openid', 'Userid',
        'file_size', 'video_format', 'resolution', 'duration', 'frame_count',
        'file_url', 'source_type',
    ),
}


class LegacyGovernanceError(ValueError):
    def __init__(self, message, code='invalid_request'):
        super().__init__(message)
        self.code = code


def _legacy_operation_id(value, field_name):
    normalized = normalize_account_uuid(value)
    if not normalized:
        raise LegacyGovernanceError(f'{field_name} 必须是有效 UUID')
    return normalized


def _legacy_record_fingerprint(table, row, media_sha256=None):
    payload = {
        field: (row.get(field).isoformat() if isinstance(row.get(field), datetime) else row.get(field))
        for field in LEGACY_RECORD_FIELDS[table]
    }
    payload['mediaContentSha256'] = str(media_sha256 or row.get('mediaContentSha256') or '')
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _sha256_regular_file(path):
    file_stat = os.lstat(path)
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
        raise LegacyGovernanceError('治理材料必须是普通单链接文件', 'evidence_unverifiable')
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _legacy_media_sha256(table, row):
    root = os.path.realpath(os.environ.get('REALGUARD_UPLOADS_DIR', '/opt/realguard-data/uploads'))
    folder = os.path.basename(str(row.get('openid') or row.get('phone') or 'guest'))
    filename = os.path.basename(str(row.get('filename') or ''))
    if not filename:
        raise LegacyGovernanceError('历史记录缺少媒体文件名，禁止认领', 'media_unverifiable')
    candidate = os.path.realpath(os.path.join(root, folder, 'image' if table == 'data' else 'video', filename))
    if os.path.commonpath((root, candidate)) != root or not os.path.exists(candidate):
        raise LegacyGovernanceError('历史媒体文件不可读取，必须先完成媒体归档', 'media_unverifiable')
    try:
        return _sha256_regular_file(candidate)
    except OSError as exc:
        raise LegacyGovernanceError('历史媒体文件不可读取', 'media_unverifiable') from exc


def _verify_evidence_reference(reference, expected_sha256):
    root = os.path.realpath(os.environ.get(
        'REALGUARD_LEGACY_EVIDENCE_ROOT', '/opt/realguard-data/legacy-governance-evidence'
    ))
    if os.path.isabs(reference):
        raise LegacyGovernanceError('evidenceReference 必须是治理证据目录内的相对路径')
    candidate = os.path.realpath(os.path.join(root, reference))
    if os.path.commonpath((root, candidate)) != root:
        raise LegacyGovernanceError('evidenceReference 越出治理证据目录')
    try:
        actual = _sha256_regular_file(candidate)
    except (OSError, LegacyGovernanceError) as exc:
        raise LegacyGovernanceError('治理证据不可读取或不安全', 'evidence_unverifiable') from exc
    if not hmac.compare_digest(actual, expected_sha256):
        raise LegacyGovernanceError('治理证据哈希不匹配', 'evidence_mismatch')
    return actual


def _legacy_audit_key(key_id=None):
    raw = str(os.environ.get('REALGUARD_SECURITY_AUDIT_HMAC_KEY') or '').strip().lower()
    if len(raw) != 64 or any(char not in '0123456789abcdef' for char in raw):
        raise LegacyGovernanceError('独立安全审计密钥未正确配置', 'audit_unavailable')
    current_id = str(os.environ.get('REALGUARD_SECURITY_AUDIT_HMAC_KEY_ID') or 'session-v1')
    requested_id = str(key_id or current_id)
    if requested_id == current_id:
        return bytes.fromhex(raw)
    try:
        historical = json.loads(os.environ.get('REALGUARD_SECURITY_AUDIT_HMAC_KEYS_JSON') or '{}')
    except json.JSONDecodeError as exc:
        raise LegacyGovernanceError('安全审计历史密钥环无效', 'audit_unavailable') from exc
    encoded = str((historical if isinstance(historical, dict) else {}).get(requested_id) or '').lower()
    if len(encoded) != 64 or any(char not in '0123456789abcdef' for char in encoded):
        raise LegacyGovernanceError('治理记录使用了未知审计密钥', 'audit_integrity_failed')
    return bytes.fromhex(encoded)


def _legacy_hmac(payload, key_id=None):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hmac.new(_legacy_audit_key(key_id), canonical.encode('utf-8'), hashlib.sha256).hexdigest()


def _legacy_request_seal_payload(claim):
    return {
        'operationId': claim.get('request_operation_id'),
        'recordType': claim.get('record_type'),
        'recordId': int(claim.get('record_id')),
        'recordFingerprint': claim.get('record_fingerprint'),
        'mediaSha256': claim.get('media_sha256'),
        'targetAccountUuid': normalize_account_uuid(claim.get('target_account_uuid')),
        'targetUserId': int(claim.get('target_user_id')),
        'targetAccountFingerprint': claim.get('target_account_fingerprint'),
        'evidenceReference': claim.get('evidence_reference'),
        'evidenceSha256': claim.get('evidence_sha256'),
        'reason': claim.get('reason'),
        'requesterAdminId': int(claim.get('requester_admin_id')),
        'requesterIdentityHash': claim.get('requester_identity_hash'),
        'auditKeyId': claim.get('audit_key_id'),
    }


def _verify_legacy_claim_integrity(claim):
    key_id = claim.get('audit_key_id')
    if not hmac_compare(
        _legacy_hmac(_legacy_request_seal_payload(claim), key_id),
        claim.get('request_integrity_hmac'),
    ):
        raise LegacyGovernanceError('治理申请完整性校验失败', 'audit_integrity_failed')
    status = claim.get('status')
    if status not in {'claimed', 'rejected'}:
        return True
    if status == 'claimed':
        decision_payload = {
            'claimId': int(claim.get('id')),
            'approvalOperationId': claim.get('approval_operation_id'),
            'expectedVersion': int(claim.get('version')) - 1,
            'requestIntegrityHmac': claim.get('request_integrity_hmac'),
            'approverAdminId': int(claim.get('approver_admin_id')),
            'approverIdentityHash': claim.get('approver_identity_hash'),
        }
    else:
        decision_payload = {
            'claimId': int(claim.get('id')),
            'rejectionOperationId': claim.get('approval_operation_id'),
            'expectedVersion': int(claim.get('version')) - 1,
            'requestIntegrityHmac': claim.get('request_integrity_hmac'),
            'reviewerAdminId': int(claim.get('approver_admin_id')),
            'reviewerIdentityHash': claim.get('approver_identity_hash'),
            'reason': claim.get('decision_reason'),
        }
    if not hmac_compare(
        _legacy_hmac(decision_payload, key_id),
        claim.get('approval_integrity_hmac'),
    ):
        raise LegacyGovernanceError('治理审批完整性校验失败', 'audit_integrity_failed')
    return True


def _target_account_row(cursor, account_uuid):
    cursor.execute(
        "SELECT Userid, account_uuid, username, phone, openid FROM `user` "
        "WHERE account_uuid = %s LIMIT 1 FOR SHARE",
        (account_uuid,),
    )
    row = cursor.fetchone()
    if not row:
        raise LegacyGovernanceError('目标账号不存在', 'account_not_found')
    return row


def _target_account_fingerprint(row):
    payload = {
        'Userid': row.get('Userid'),
        'account_uuid': normalize_account_uuid(row.get('account_uuid')),
        'username': row.get('username') or '',
        'phone': row.get('phone') or '',
        'openid': row.get('openid') or '',
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _legacy_record_row(cursor, table, itemid, *, for_update=False):
    if table not in LEGACY_RECORD_FIELDS:
        raise LegacyGovernanceError('recordType 仅支持 data 或 video_data')
    try:
        record_id = int(itemid)
    except (TypeError, ValueError):
        raise LegacyGovernanceError('itemid 必须是正整数')
    if record_id <= 0:
        raise LegacyGovernanceError('itemid 必须是正整数')
    fields = ', '.join(f'`{field}`' for field in LEGACY_RECORD_FIELDS[table])
    suffix = ' FOR UPDATE' if for_update else ''
    cursor.execute(
        f"SELECT {fields}, `owner_account_uuid` FROM `{table}` WHERE `itemid` = %s LIMIT 1{suffix}",
        (record_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise LegacyGovernanceError('历史记录不存在', 'not_found')
    return row


def legacy_record_preview(table, itemid):
    conn = get_detection_db_connection()
    try:
        with conn.cursor() as cursor:
            row = _legacy_record_row(cursor, table, itemid)
        media_sha256 = _legacy_media_sha256(table, row)
        return {
            'recordType': table,
            'recordId': int(row.get('itemid')),
            'fingerprint': _legacy_record_fingerprint(table, row, media_sha256),
            'mediaSha256': media_sha256,
            'unowned': not bool(normalize_account_uuid(row.get('owner_account_uuid'))),
            'createdAt': str(row.get('createtime') or ''),
            'filenamePresent': bool(row.get('filename')),
            'legacyPhonePresent': bool(row.get('phone')),
            'legacyOpenidPresent': bool(row.get('openid')),
            'legacyUserIdPresent': row.get('Userid') not in (None, ''),
        }
    finally:
        conn.close()


def request_legacy_record_claim(
    table, itemid, account_uuid, expected_fingerprint, expected_media_sha256,
    evidence_reference, evidence_sha256, reason, operation_id, requester_admin_id,
    requester_username, requester_identity,
):
    target_uuid = normalize_account_uuid(account_uuid)
    if not target_uuid:
        raise LegacyGovernanceError('目标账号不存在', 'account_not_found')
    operation_id = _legacy_operation_id(operation_id, 'operationId')
    fingerprint = str(expected_fingerprint or '').strip().lower()
    evidence_hash = str(evidence_sha256 or '').strip().lower()
    evidence_reference = str(evidence_reference or '').strip()
    reason = str(reason or '').strip()
    expected_media_sha256 = str(expected_media_sha256 or '').strip().lower()
    if len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
        raise LegacyGovernanceError('expectedFingerprint 必须是 SHA-256')
    if len(evidence_hash) != 64 or any(c not in '0123456789abcdef' for c in evidence_hash):
        raise LegacyGovernanceError('evidenceSha256 必须是 SHA-256')
    if len(expected_media_sha256) != 64 or any(c not in '0123456789abcdef' for c in expected_media_sha256):
        raise LegacyGovernanceError('expectedMediaSha256 必须是 SHA-256')
    if not evidence_reference or len(evidence_reference) > 512:
        raise LegacyGovernanceError('evidenceReference 长度必须为 1 到 512 个字符')
    if len(reason) < 10 or len(reason) > 1000:
        raise LegacyGovernanceError('reason 长度必须为 10 到 1000 个字符')
    if not requester_admin_id:
        raise LegacyGovernanceError('必须使用实名后台管理员账号', 'named_admin_required')
    requester_identity_hash = hashlib.sha256(str(requester_identity or '').strip().encode()).hexdigest()
    if not str(requester_identity or '').strip():
        raise LegacyGovernanceError('遗留数据治理账号必须绑定手机号', 'verified_identity_required')
    _verify_evidence_reference(evidence_reference, evidence_hash)

    account_conn = get_db_connection()
    conn = get_detection_db_connection()
    try:
        account_conn.begin()
        with account_conn.cursor() as account_cursor:
            target_account = _target_account_row(account_cursor, target_uuid)
            target_fingerprint = _target_account_fingerprint(target_account)
        conn.begin()
        with conn.cursor() as cursor:
            audit_key_id = str(os.environ.get('REALGUARD_SECURITY_AUDIT_HMAC_KEY_ID') or 'session-v1')[:64]
            request_seal_payload = {
                'operationId': operation_id,
                'recordType': table,
                'recordId': int(itemid),
                'recordFingerprint': fingerprint,
                'mediaSha256': expected_media_sha256,
                'targetAccountUuid': target_uuid,
                'targetUserId': int(target_account.get('Userid')),
                'targetAccountFingerprint': target_fingerprint,
                'evidenceReference': evidence_reference,
                'evidenceSha256': evidence_hash,
                'reason': reason,
                'requesterAdminId': int(requester_admin_id),
                'requesterIdentityHash': requester_identity_hash,
                'auditKeyId': audit_key_id,
            }
            request_hmac = _legacy_hmac(request_seal_payload)
            try:
                cursor.execute(
                    """
                    INSERT INTO legacy_record_governance (
                        request_operation_id, record_type, record_id, record_fingerprint,
                        media_sha256, active_record_key, target_account_uuid, target_user_id,
                        target_account_fingerprint, evidence_reference, evidence_sha256, reason,
                        requester_admin_id, requester_username, requester_identity_hash,
                        request_integrity_hmac, audit_key_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        operation_id, table, int(itemid), fingerprint, expected_media_sha256,
                        f'{table}:{int(itemid)}', target_uuid, int(target_account.get('Userid')),
                        target_fingerprint, evidence_reference, evidence_hash, reason,
                        int(requester_admin_id), str(requester_username or '')[:64],
                        requester_identity_hash, request_hmac, audit_key_id,
                    ),
                )
            except pymysql.err.IntegrityError as exc:
                raise LegacyGovernanceError('该记录或操作已存在有效治理申请', 'duplicate_request') from exc
            claim_id = cursor.lastrowid
            row = _legacy_record_row(cursor, table, itemid, for_update=True)
            if normalize_account_uuid(row.get('owner_account_uuid')):
                raise LegacyGovernanceError('该记录已有不可变属主，禁止重新认领', 'already_owned')
            actual_media_sha256 = _legacy_media_sha256(table, row)
            if not hmac_compare(expected_media_sha256, actual_media_sha256):
                raise LegacyGovernanceError('媒体内容已变化，请重新获取预览', 'media_mismatch')
            actual_fingerprint = _legacy_record_fingerprint(table, row, actual_media_sha256)
            if not hmac_compare(fingerprint, actual_fingerprint):
                raise LegacyGovernanceError('记录已变化，请重新获取指纹', 'fingerprint_mismatch')
        conn.commit()
        account_conn.commit()
        return get_legacy_record_claim(claim_id)
    except Exception:
        account_conn.rollback()
        conn.rollback()
        raise
    finally:
        account_conn.close()
        conn.close()


def hmac_compare(left, right):
    # compare_digest avoids leaking which part of a persisted fingerprint changed.
    import hmac
    return hmac.compare_digest(str(left), str(right))


def _legacy_claim_payload(row):
    if not row:
        return None
    _verify_legacy_claim_integrity(row)
    return {
        'id': int(row.get('id')),
        'recordType': row.get('record_type'),
        'recordId': int(row.get('record_id')),
        'fingerprint': row.get('record_fingerprint'),
        'mediaSha256': row.get('media_sha256'),
        'status': row.get('status'),
        'targetAccountUuid': row.get('target_account_uuid'),
        'targetUserId': int(row.get('target_user_id')),
        'evidenceReference': row.get('evidence_reference'),
        'evidenceSha256': row.get('evidence_sha256'),
        'reason': row.get('reason'),
        'requesterAdminId': int(row.get('requester_admin_id')),
        'requesterUsername': row.get('requester_username'),
        'requestedAt': str(row.get('requested_at') or ''),
        'approverAdminId': int(row.get('approver_admin_id')) if row.get('approver_admin_id') else None,
        'approverUsername': row.get('approver_username'),
        'approvedAt': str(row.get('approved_at') or ''),
        'decisionReason': row.get('decision_reason') or '',
        'integrityVerified': True,
        'version': int(row.get('version') or 1),
    }


def get_legacy_record_claim(claim_id):
    rows = excute_detection_sql(
        "SELECT * FROM legacy_record_governance WHERE id = %s LIMIT 1",
        (claim_id,),
    ) or []
    return _legacy_claim_payload(rows[0]) if rows else None


def list_legacy_record_claims(status='claim_pending', limit=50):
    allowed = {'claim_pending', 'claimed', 'rejected'}
    status = str(status or 'claim_pending').strip()
    if status not in allowed:
        raise LegacyGovernanceError('status 参数无效')
    rows = excute_detection_sql(
        "SELECT * FROM legacy_record_governance WHERE status = %s ORDER BY id DESC LIMIT %s",
        (status, min(max(int(limit), 1), 200)),
    ) or []
    return [_legacy_claim_payload(row) for row in rows]


def approve_legacy_record_claim(
    claim_id, approval_operation_id, expected_version, approver_admin_id, approver_username,
    approver_identity,
):
    operation_id = _legacy_operation_id(approval_operation_id, 'approvalOperationId')
    if not approver_admin_id:
        raise LegacyGovernanceError('必须使用实名后台管理员账号', 'named_admin_required')
    if not str(approver_identity or '').strip():
        raise LegacyGovernanceError('遗留数据治理账号必须绑定手机号', 'verified_identity_required')
    approver_identity_hash = hashlib.sha256(str(approver_identity).strip().encode()).hexdigest()
    try:
        claim_id = int(claim_id)
        expected_version = int(expected_version)
    except (TypeError, ValueError):
        raise LegacyGovernanceError('申请编号或版本无效')

    pre_claim_rows = excute_detection_sql(
        "SELECT target_account_uuid, target_account_fingerprint "
        "FROM legacy_record_governance WHERE id = %s LIMIT 1",
        (claim_id,),
    ) or []
    if not pre_claim_rows:
        raise LegacyGovernanceError('治理申请不存在', 'not_found')
    pre_claim = pre_claim_rows[0]
    account_conn = get_db_connection()
    conn = get_detection_db_connection()
    try:
        account_conn.begin()
        with account_conn.cursor() as account_cursor:
            target_account = _target_account_row(account_cursor, pre_claim.get('target_account_uuid'))
            if not hmac_compare(
                _target_account_fingerprint(target_account),
                pre_claim.get('target_account_fingerprint'),
            ):
                raise LegacyGovernanceError('目标账号身份信息已变化，请重新申请', 'account_changed')
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM legacy_record_governance WHERE id = %s LIMIT 1 FOR UPDATE",
                (claim_id,),
            )
            claim = cursor.fetchone()
            if not claim:
                raise LegacyGovernanceError('治理申请不存在', 'not_found')
            if claim.get('status') != 'claim_pending':
                raise LegacyGovernanceError('治理申请已处理', 'already_processed')
            if int(claim.get('version') or 0) != expected_version:
                raise LegacyGovernanceError('治理申请版本已变化', 'version_mismatch')
            if int(claim.get('requester_admin_id')) == int(approver_admin_id):
                raise LegacyGovernanceError('申请人不能审批自己的申请', 'separation_of_duties')
            if hmac_compare(claim.get('requester_identity_hash'), approver_identity_hash):
                raise LegacyGovernanceError('同一人员身份不能申请并审批', 'separation_of_duties')
            target_uuid = normalize_account_uuid(claim.get('target_account_uuid'))
            _verify_legacy_claim_integrity(claim)
            _verify_evidence_reference(claim.get('evidence_reference'), claim.get('evidence_sha256'))
            table = claim.get('record_type')
            row = _legacy_record_row(cursor, table, claim.get('record_id'), for_update=True)
            if normalize_account_uuid(row.get('owner_account_uuid')):
                raise LegacyGovernanceError('该记录已被其他流程认领', 'already_owned')
            actual_media_sha256 = _legacy_media_sha256(table, row)
            if not hmac_compare(actual_media_sha256, claim.get('media_sha256')):
                raise LegacyGovernanceError('媒体内容已变化，审批已中止', 'media_mismatch')
            if not hmac_compare(_legacy_record_fingerprint(table, row, actual_media_sha256), claim.get('record_fingerprint')):
                raise LegacyGovernanceError('记录内容已变化，审批已中止', 'fingerprint_mismatch')
            if table == 'data':
                cursor.execute(
                    "SELECT id, owner_account_uuid FROM exif WHERE data_itemid = %s FOR UPDATE",
                    (claim.get('record_id'),),
                )
                exif_rows = cursor.fetchall() or []
                conflicting = [
                    exif for exif in exif_rows
                    if normalize_account_uuid(exif.get('owner_account_uuid')) not in ('', target_uuid)
                ]
                if conflicting:
                    raise LegacyGovernanceError('关联 EXIF 已属于其他账号，审批已中止', 'exif_owner_conflict')
            cursor.execute(
                f"UPDATE `{table}` SET owner_account_uuid = %s WHERE itemid = %s "
                "AND (owner_account_uuid IS NULL OR owner_account_uuid = '')",
                (target_uuid, claim.get('record_id')),
            )
            if cursor.rowcount != 1:
                raise LegacyGovernanceError('记录归属更新发生并发冲突', 'write_conflict')
            if table == 'data':
                cursor.execute(
                    "UPDATE exif SET owner_account_uuid = %s WHERE data_itemid = %s "
                    "AND (owner_account_uuid IS NULL OR owner_account_uuid = '' OR owner_account_uuid = %s)",
                    (target_uuid, claim.get('record_id'), target_uuid),
                )
            approval_payload = {
                'claimId': claim_id,
                'approvalOperationId': operation_id,
                'expectedVersion': expected_version,
                'requestIntegrityHmac': claim.get('request_integrity_hmac'),
                'approverAdminId': int(approver_admin_id),
                'approverIdentityHash': approver_identity_hash,
            }
            approval_hmac = _legacy_hmac(approval_payload, claim.get('audit_key_id'))
            try:
                cursor.execute(
                    """
                    UPDATE legacy_record_governance
                    SET status = 'claimed', approval_operation_id = %s,
                        approver_admin_id = %s, approver_username = %s,
                        approver_identity_hash = %s, approval_integrity_hmac = %s,
                        approved_at = NOW(), version = version + 1
                    WHERE id = %s AND status = 'claim_pending' AND version = %s
                    """,
                    (
                        operation_id, int(approver_admin_id), str(approver_username or '')[:64],
                        approver_identity_hash, approval_hmac,
                        claim_id, expected_version,
                    ),
                )
            except pymysql.err.IntegrityError as exc:
                raise LegacyGovernanceError('审批操作编号已使用', 'duplicate_approval') from exc
            if cursor.rowcount != 1:
                raise LegacyGovernanceError('审批发生并发冲突', 'write_conflict')
        conn.commit()
        account_conn.commit()
        return get_legacy_record_claim(claim_id)
    except Exception:
        account_conn.rollback()
        conn.rollback()
        raise
    finally:
        account_conn.close()
        conn.close()


def reject_legacy_record_claim(
    claim_id, rejection_operation_id, expected_version, reason,
    reviewer_admin_id, reviewer_username, reviewer_identity,
):
    operation_id = _legacy_operation_id(rejection_operation_id, 'rejectionOperationId')
    reason = str(reason or '').strip()
    if len(reason) < 10 or len(reason) > 1000:
        raise LegacyGovernanceError('reason 长度必须为 10 到 1000 个字符')
    if not reviewer_admin_id or not str(reviewer_identity or '').strip():
        raise LegacyGovernanceError('必须使用绑定手机号的实名后台管理员账号', 'verified_identity_required')
    reviewer_identity_hash = hashlib.sha256(str(reviewer_identity).strip().encode()).hexdigest()
    try:
        claim_id = int(claim_id)
        expected_version = int(expected_version)
    except (TypeError, ValueError):
        raise LegacyGovernanceError('申请编号或版本无效')
    conn = get_detection_db_connection()
    try:
        conn.begin()
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM legacy_record_governance WHERE id = %s LIMIT 1 FOR UPDATE",
                (claim_id,),
            )
            claim = cursor.fetchone()
            if not claim:
                raise LegacyGovernanceError('治理申请不存在', 'not_found')
            if claim.get('status') != 'claim_pending':
                raise LegacyGovernanceError('治理申请已处理', 'already_processed')
            if int(claim.get('version') or 0) != expected_version:
                raise LegacyGovernanceError('治理申请版本已变化', 'version_mismatch')
            _verify_legacy_claim_integrity(claim)
            if int(claim.get('requester_admin_id')) == int(reviewer_admin_id) or hmac_compare(
                claim.get('requester_identity_hash'), reviewer_identity_hash
            ):
                raise LegacyGovernanceError('申请人不能驳回自己的申请', 'separation_of_duties')
            decision_hmac = _legacy_hmac({
                'claimId': claim_id,
                'rejectionOperationId': operation_id,
                'expectedVersion': expected_version,
                'requestIntegrityHmac': claim.get('request_integrity_hmac'),
                'reviewerAdminId': int(reviewer_admin_id),
                'reviewerIdentityHash': reviewer_identity_hash,
                'reason': reason,
            }, claim.get('audit_key_id'))
            try:
                cursor.execute(
                    """
                    UPDATE legacy_record_governance
                    SET status = 'rejected', active_record_key = NULL,
                        approval_operation_id = %s, approver_admin_id = %s,
                        approver_username = %s, approver_identity_hash = %s,
                        approval_integrity_hmac = %s, decision_reason = %s,
                        approved_at = NOW(), version = version + 1
                    WHERE id = %s AND status = 'claim_pending' AND version = %s
                    """,
                    (
                        operation_id, int(reviewer_admin_id), str(reviewer_username or '')[:64],
                        reviewer_identity_hash, decision_hmac, reason, claim_id, expected_version,
                    ),
                )
            except pymysql.err.IntegrityError as exc:
                raise LegacyGovernanceError('驳回操作编号已使用', 'duplicate_approval') from exc
            if cursor.rowcount != 1:
                raise LegacyGovernanceError('驳回发生并发冲突', 'write_conflict')
        conn.commit()
        return get_legacy_record_claim(claim_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
