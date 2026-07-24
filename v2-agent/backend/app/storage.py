"""Lightweight persistent storage for V2 history, cache, and metrics."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import evidence_manifest_v2, privacy_erasure_ledger, watermark_verdict
from .verdict_labels import binary_verdict


DATA_DIR = Path(os.getenv("JIANZHEN_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DB_PATH = DATA_DIR / "jianzhen-v2.sqlite3"
ANALYSIS_CACHE_VERSION = os.getenv("JIANZHEN_ANALYSIS_CACHE_VERSION", "v10-tenant-scoped")
REQUEST_EVENT_RETENTION_DAYS = max(1, int(os.getenv("JIANZHEN_REQUEST_EVENT_RETENTION_DAYS", "90")))
TOKEN_USAGE_RETENTION_DAYS = max(30, int(os.getenv("JIANZHEN_TOKEN_USAGE_RETENTION_DAYS", "730")))
REPORT_SHARE_ACCESS_RETENTION_DAYS = max(
    30, int(os.getenv("JIANZHEN_REPORT_SHARE_ACCESS_RETENTION_DAYS", "90"))
)
REPORT_SHARE_RETENTION_DAYS = max(
    REPORT_SHARE_ACCESS_RETENTION_DAYS,
    int(os.getenv("JIANZHEN_REPORT_SHARE_RETENTION_DAYS", "730")),
)
PUBLISHABLE_VERDICTS = frozenset({"real", "suspected_fake", "highly_suspected_fake"})
PUBLISHABLE_AUTHORITIES = frozenset({"decisive_provenance"})

_INIT_LOCK = threading.Lock()
_INITIALIZED = False

EvidenceConflictError = evidence_manifest_v2.EvidenceConflictError
EvidenceIntegrityError = evidence_manifest_v2.EvidenceIntegrityError


def _connect() -> sqlite3.Connection:
    global _INITIALIZED
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    if not _INITIALIZED:
        with _INIT_LOCK:
            if not _INITIALIZED:
                _init(conn)
                _INITIALIZED = True
    return conn


def healthcheck() -> dict[str, Any]:
    """Verify that the durable SQLite store can acquire a write lock."""
    try:
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO readiness_probe (id, checked_at) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET checked_at = excluded.checked_at",
                (now_iso(),),
            )
            conn.rollback()
        return {"available": True, "writable": True}
    except (OSError, sqlite3.Error) as exc:
        return {"available": False, "error": type(exc).__name__}


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS analysis_cache (
            cache_key TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            file_type TEXT NOT NULL,
            analysis_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            model_version TEXT,
            source TEXT
        );

        CREATE TABLE IF NOT EXISTS history (
            task_id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            resolution TEXT,
            result_json TEXT NOT NULL,
            thumbnail TEXT,
            developer_user_id TEXT,
            developer_account_uuid TEXT,
            developer_key_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_history_created_at ON history(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_history_sha256 ON history(sha256);

        CREATE TABLE IF NOT EXISTS history_artifacts (
            task_id TEXT PRIMARY KEY,
            forensics_json TEXT,
            provenance_json TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES history(task_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS evidence_manifests_v2 (
            task_id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL UNIQUE,
            manifest_json TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            signature TEXT NOT NULL,
            algorithm TEXT NOT NULL,
            key_id TEXT NOT NULL,
            public_key TEXT NOT NULL,
            signed_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES history(task_id) ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_evidence_manifests_v2_hash
            ON evidence_manifests_v2(manifest_sha256);

        CREATE TABLE IF NOT EXISTS report_artifacts_v2 (
            report_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            media_type TEXT NOT NULL,
            artifact_bytes BLOB NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            artifact_size INTEGER NOT NULL,
            report_payload_json TEXT NOT NULL,
            report_payload_sha256 TEXT NOT NULL,
            statement_json TEXT NOT NULL,
            statement_sha256 TEXT NOT NULL,
            signature TEXT NOT NULL,
            algorithm TEXT NOT NULL,
            key_id TEXT NOT NULL,
            public_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES evidence_manifests_v2(task_id) ON DELETE RESTRICT,
            FOREIGN KEY(report_id) REFERENCES evidence_manifests_v2(report_id) ON DELETE RESTRICT
        );

        CREATE TRIGGER IF NOT EXISTS history_signed_evidence_immutable
        BEFORE UPDATE OF task_id, report_id, created_at, sha256, file_type,
                         file_name, file_size, resolution, result_json,
                         developer_user_id, developer_account_uuid, developer_key_id
        ON history
        WHEN EXISTS (
            SELECT 1 FROM evidence_manifests_v2 manifest
            WHERE manifest.task_id = OLD.task_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'signed history evidence is immutable');
        END;

        CREATE TRIGGER IF NOT EXISTS evidence_manifest_v2_immutable
        BEFORE UPDATE ON evidence_manifests_v2
        BEGIN
            SELECT RAISE(ABORT, 'signed evidence manifest is immutable');
        END;

        CREATE TRIGGER IF NOT EXISTS evidence_manifest_v2_binding_insert
        BEFORE INSERT ON evidence_manifests_v2
        WHEN NOT EXISTS (
            SELECT 1 FROM history
            WHERE task_id = NEW.task_id AND report_id = NEW.report_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'evidence manifest binding does not match history');
        END;

        CREATE TRIGGER IF NOT EXISTS report_artifact_v2_immutable
        BEFORE UPDATE ON report_artifacts_v2
        BEGIN
            SELECT RAISE(ABORT, 'signed report artifact is immutable');
        END;

        CREATE TRIGGER IF NOT EXISTS report_artifact_v2_binding_insert
        BEFORE INSERT ON report_artifacts_v2
        WHEN NOT EXISTS (
            SELECT 1 FROM evidence_manifests_v2
            WHERE task_id = NEW.task_id AND report_id = NEW.report_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'report artifact binding does not match evidence manifest');
        END;

        CREATE TRIGGER IF NOT EXISTS history_artifacts_frozen_insert
        BEFORE INSERT ON history_artifacts
        WHEN EXISTS (
            SELECT 1 FROM report_artifacts_v2 report
            WHERE report.task_id = NEW.task_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'report evidence attachments are frozen');
        END;

        CREATE TRIGGER IF NOT EXISTS history_artifacts_frozen_update
        BEFORE UPDATE ON history_artifacts
        WHEN EXISTS (
            SELECT 1 FROM report_artifacts_v2 report
            WHERE report.task_id = OLD.task_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'report evidence attachments are frozen');
        END;

        CREATE TRIGGER IF NOT EXISTS history_artifacts_frozen_delete
        BEFORE DELETE ON history_artifacts
        WHEN EXISTS (
            SELECT 1 FROM report_artifacts_v2 report
            WHERE report.task_id = OLD.task_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'report evidence attachments are frozen');
        END;

        CREATE TABLE IF NOT EXISTS request_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            client_ip TEXT,
            user_agent TEXT,
            method TEXT,
            path TEXT,
            status INTEGER,
            elapsed_ms INTEGER,
            file_type TEXT,
            verdict TEXT,
            cache_hit INTEGER
        );

        CREATE TABLE IF NOT EXISTS guest_upload_consents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_hash TEXT NOT NULL,
            document_version TEXT NOT NULL,
            terms_sha256 TEXT NOT NULL,
            privacy_sha256 TEXT NOT NULL,
            upload_sha256 TEXT NOT NULL,
            idempotency_key_hash TEXT NOT NULL,
            channel TEXT NOT NULL,
            accepted_at TEXT NOT NULL,
            UNIQUE(subject_hash, idempotency_key_hash)
        );

        CREATE INDEX IF NOT EXISTS idx_guest_upload_consents_time
            ON guest_upload_consents(accepted_at DESC);

        CREATE INDEX IF NOT EXISTS idx_events_created_at ON request_events(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_events_type ON request_events(event_type, created_at DESC);

        CREATE TABLE IF NOT EXISTS token_usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            developer_user_id TEXT,
            developer_key_id TEXT,
            task_id TEXT,
            report_id TEXT,
            endpoint TEXT NOT NULL,
            model_version TEXT,
            source TEXT,
            file_type TEXT,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cache_hit INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_token_usage_created_at ON token_usage_events(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_token_usage_user_created_at ON token_usage_events(developer_user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_token_usage_key_created_at ON token_usage_events(developer_key_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS report_shares (
            share_id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            created_by_user_id TEXT NOT NULL,
            created_by_key_id TEXT,
            created_by_mode TEXT NOT NULL,
            revoked_at TEXT,
            legacy INTEGER NOT NULL DEFAULT 0,
            signature_fingerprint TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_report_shares_report_id ON report_shares(report_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_report_shares_creator ON report_shares(created_by_user_id, created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_report_shares_legacy_signature
            ON report_shares(report_id, signature_fingerprint)
            WHERE signature_fingerprint IS NOT NULL;

        CREATE TABLE IF NOT EXISTS report_share_access_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            share_id TEXT NOT NULL,
            report_id TEXT NOT NULL,
            accessed_at TEXT NOT NULL,
            client_ip TEXT,
            user_agent TEXT,
            outcome TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_report_share_access_share
            ON report_share_access_events(share_id, accessed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_report_share_access_report
            ON report_share_access_events(report_id, accessed_at DESC);

        CREATE TABLE IF NOT EXISTS counters (
            name TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS readiness_probe (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            checked_at TEXT NOT NULL
        );
        """
    )
    for statement in (
        "ALTER TABLE history ADD COLUMN developer_user_id TEXT",
        "ALTER TABLE history ADD COLUMN developer_account_uuid TEXT",
        "ALTER TABLE history ADD COLUMN developer_key_id TEXT",
    ):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def cache_key(file_type: str, sha256: str) -> str:
    return f"{ANALYSIS_CACHE_VERSION}:{file_type}:{sha256}"


def _account_cache_scope(account_uuid: str) -> str:
    raw_scope = f"account:{account_uuid}"
    return hashlib.sha256(raw_scope.encode("utf-8")).hexdigest()[:16]


def _is_forensics_cache(file_type: str) -> bool:
    return str(file_type or "").startswith("image-forensics:")


def is_publishable_analysis(analysis: Any) -> bool:
    """Return whether an analysis carries an explicit fail-closed decision contract."""
    if not isinstance(analysis, dict):
        return False
    status = analysis.get("decisionStatus")
    authority = analysis.get("decisionAuthority")
    if status == "verdict":
        return bool(
            authority in PUBLISHABLE_AUTHORITIES
            and analysis.get("source") == "provenance"
            and analysis.get("verdict") in PUBLISHABLE_VERDICTS
            and analysis.get("reviewRequired") is False
        )
    return bool(
        status == "review_only"
        and authority == "none"
        and analysis.get("verdict") in {*PUBLISHABLE_VERDICTS, "unknown"}
        and analysis.get("reviewRequired") is True
    )


def get_cached_analysis(file_type: str, sha256: str, max_age_seconds: int | None = None) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT analysis_json, created_at FROM analysis_cache WHERE cache_key = ?",
            (cache_key(file_type, sha256),),
        ).fetchone()
    if not row:
        return None
    if max_age_seconds is not None:
        try:
            created_at = datetime.fromisoformat(row["created_at"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created_at > timedelta(seconds=max(0, max_age_seconds)):
                with _connect() as conn:
                    conn.execute(
                        "DELETE FROM analysis_cache WHERE cache_key = ? AND created_at = ?",
                        (cache_key(file_type, sha256), row["created_at"]),
                    )
                    conn.commit()
                return None
        except (TypeError, ValueError):
            return None
    analysis = json.loads(row["analysis_json"])
    if not _is_forensics_cache(file_type) and not is_publishable_analysis(analysis):
        return None
    return analysis


def prune_cached_analyses(file_type_prefix: str, max_age_seconds: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(0, max_age_seconds))).isoformat()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM analysis_cache WHERE file_type LIKE ? AND created_at < ?",
            (f"{file_type_prefix}%", cutoff),
        )
        conn.commit()
    return max(0, int(cursor.rowcount))


def put_cached_analysis(file_type: str, sha256: str, analysis: dict[str, Any]) -> None:
    if not _is_forensics_cache(file_type) and not is_publishable_analysis(analysis):
        return
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO analysis_cache
                (cache_key, sha256, file_type, analysis_json, created_at, model_version, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key(file_type, sha256),
                sha256,
                file_type,
                json.dumps(analysis, ensure_ascii=False),
                now_iso(),
                analysis.get("modelVersion"),
                analysis.get("source"),
            ),
        )
        conn.commit()


def _manifest_record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "payload": json.loads(row["manifest_json"]),
        "sha256": row["manifest_sha256"],
        "signature": {
            "algorithm": row["algorithm"],
            "keyId": row["key_id"],
            "publicKey": row["public_key"],
            "value": row["signature"],
        },
    }


def _get_manifest_record(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT manifest_json, manifest_sha256, signature, algorithm, key_id, public_key
        FROM evidence_manifests_v2
        WHERE task_id = ? OR report_id = ?
        """,
        (item_id, item_id),
    ).fetchone()
    return _manifest_record_from_row(row) if row else None


def _insert_manifest_record(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    payload = record["payload"]
    binding = payload["binding"]
    signature = record["signature"]
    conn.execute(
        """
        INSERT INTO evidence_manifests_v2
            (task_id, report_id, manifest_json, manifest_sha256, signature,
             algorithm, key_id, public_key, signed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            binding["taskId"],
            binding["reportId"],
            evidence_manifest_v2.canonical_json(payload).decode("utf-8"),
            record["sha256"],
            signature["value"],
            signature["algorithm"],
            signature["keyId"],
            signature["publicKey"],
            payload.get("sealedAt") or payload["frozenAt"],
        ),
    )


def get_evidence_manifest(item_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        return _get_manifest_record(conn, item_id)


def put_history(
    result: dict[str, Any],
    sha256: str,
    file_size: int,
    thumbnail: str | None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = result.get("fileMeta", {})
    developer_user_id = str((actor or {}).get("userId") or "") or None
    developer_account_uuid = str((actor or {}).get("accountUuid") or "") or None
    developer_key_id = str((actor or {}).get("keyId") or "") or None
    result_json = evidence_manifest_v2.canonical_json(result).decode("utf-8")
    manifest = evidence_manifest_v2.build_manifest(
        result,
        original_sha256=sha256,
        file_size=file_size,
        actor=actor,
    )
    signed_manifest = evidence_manifest_v2.seal_manifest(manifest)
    with _connect() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT task_id, report_id, created_at, sha256, file_type, file_name,
                       file_size, resolution, result_json, thumbnail, developer_user_id,
                       developer_account_uuid, developer_key_id
                FROM history
                WHERE task_id = ? OR report_id = ?
                """,
                (result["taskId"], result["reportId"]),
            ).fetchone()
            expected_values = {
                "task_id": result["taskId"],
                "report_id": result["reportId"],
                "created_at": result["createdAt"],
                "sha256": sha256,
                "file_type": meta.get("type", "unknown"),
                "file_name": meta.get("name", "unknown"),
                "file_size": int(file_size),
                "resolution": meta.get("resolution"),
                "thumbnail": thumbnail,
                "developer_user_id": developer_user_id,
                "developer_account_uuid": developer_account_uuid,
                "developer_key_id": developer_key_id,
            }
            if existing:
                same_metadata = all(existing[key] == value for key, value in expected_values.items())
                try:
                    same_result = evidence_manifest_v2.canonical_json(
                        json.loads(existing["result_json"])
                    ).decode("utf-8") == result_json
                except (json.JSONDecodeError, TypeError, ValueError):
                    same_result = False
                if not same_metadata or not same_result:
                    raise EvidenceConflictError(
                        "taskId or reportId already belongs to different immutable evidence"
                    )
                persisted = _get_manifest_record(conn, result["taskId"])
                if persisted is None:
                    _insert_manifest_record(conn, signed_manifest)
                    persisted = signed_manifest
                verification = evidence_manifest_v2.verify_signed_record(
                    persisted,
                    domain=evidence_manifest_v2.MANIFEST_SIGNATURE_DOMAIN,
                    trusted_public_keys=evidence_manifest_v2.configured_verification_keys(),
                )
                if verification["status"] != "valid" or not evidence_manifest_v2.manifest_matches_history(
                    persisted["payload"],
                    result,
                    original_sha256=sha256,
                    file_size=file_size,
                    actor=actor,
                ):
                    raise EvidenceIntegrityError("persisted manifest does not match immutable history")
                conn.commit()
                return persisted

            conn.execute(
                """
                INSERT INTO history
                    (task_id, report_id, created_at, sha256, file_type, file_name, file_size,
                     resolution, result_json, thumbnail, developer_user_id,
                     developer_account_uuid, developer_key_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["taskId"],
                    result["reportId"],
                    result["createdAt"],
                    sha256,
                    meta.get("type", "unknown"),
                    meta.get("name", "unknown"),
                    file_size,
                    meta.get("resolution"),
                    result_json,
                    thumbnail,
                    developer_user_id,
                    developer_account_uuid,
                    developer_key_id,
                ),
            )
            _insert_manifest_record(conn, signed_manifest)
            conn.commit()
            return signed_manifest
        except (sqlite3.Error, EvidenceConflictError, EvidenceIntegrityError):
            conn.rollback()
            raise


def _history_summary_from_row(
    row: sqlite3.Row,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = dict(result) if result is not None else json.loads(row["result_json"])
    watermark_verdict.apply(result, result.get("visibleWatermark"))
    visible = result.get("visibleWatermark") or {}
    synthid = result.get("synthid") or {}
    authorized_verdict = bool(
        result.get("decisionStatus") == "verdict"
        and result.get("decisionAuthority") in PUBLISHABLE_AUTHORITIES
        and result.get("reviewRequired") is False
        and result.get("verdict") in PUBLISHABLE_VERDICTS
        and (
            result.get("source") == "provenance"
            or watermark_verdict.has_decisive_ai_watermark(result.get("visibleWatermark"))
        )
    )
    verdict = result.get("verdict") if authorized_verdict else binary_verdict(result)
    confidence = result.get("confidence") if authorized_verdict else None
    return {
        "taskId": row["task_id"],
        "reportId": row["report_id"],
        "name": row["file_name"],
        "type": row["file_type"],
        "verdict": verdict,
        "confidence": confidence,
        "decisionStatus": "verdict" if authorized_verdict else "review_only",
        "decisionAuthority": result.get("decisionAuthority") if authorized_verdict else "none",
        "reviewRequired": not authorized_verdict,
        "createdAt": row["created_at"],
        "thumbnail": row["thumbnail"],
        "source": result.get("source"),
        "modelVersion": result.get("modelVersion"),
        "cacheVersion": result.get("cacheVersion"),
        "cacheHit": bool(result.get("cacheHit")),
        "hasForensics": bool(row["forensics_json"]),
        "hasProvenance": bool(row["provenance_json"] or result.get("provenance")),
        "hasVisibleWatermark": bool(visible.get("detected")),
        "visibleWatermarkProvider": visible.get("provider"),
        "hasSynthid": bool(synthid.get("detected")),
    }


def _ensure_result_file_meta(result: dict[str, Any], row: sqlite3.Row) -> None:
    meta = result.get("fileMeta")
    if not isinstance(meta, dict):
        meta = {}
    name = meta.get("name") or result.get("name") or result.get("fileName") or row["file_name"] or "未知文件"
    file_type = meta.get("type") or result.get("type") or result.get("fileType") or row["file_type"] or "document"
    if file_type not in {"image", "video", "audio", "document"}:
        file_type = "document"
    size = meta.get("size") or result.get("size") or result.get("fileSize")
    if not size:
        try:
            size = f"{int(row['file_size']) / 1024:.1f}KB"
        except (TypeError, ValueError):
            size = "未知"
    result["fileMeta"] = {
        **meta,
        "name": name,
        "type": file_type,
        "size": str(size),
        "resolution": meta.get("resolution") or result.get("resolution") or row["resolution"],
        "sha256": meta.get("sha256") or result.get("sha256") or row["sha256"],
        "thumbnail": meta.get("thumbnail") or row["thumbnail"],
        "preview": meta.get("preview") or result.get("preview"),
    }


def _searchable_history_fields(item: dict[str, Any]) -> list[str]:
    source_labels = {
        "vlm": ["vlm", "VLM", "真实模型"],
        "mock": ["mock", "Mock", "mock 回退"],
        "maps-only": ["maps-only", "仅证据图"],
        "unknown": ["unknown", "未知来源"],
    }
    verdict_labels = {
        "real": ["real", "真实"],
        "suspected_fake": ["suspected_fake", "疑似"],
        "highly_suspected_fake": ["highly_suspected_fake", "高度疑似"],
        "unknown": ["unknown", "未知"],
    }
    type_labels = {
        "image": ["image", "图像"],
        "video": ["video", "视频"],
        "audio": ["audio", "音频"],
        "document": ["document", "文档"],
    }
    source = str(item.get("source") or "")
    verdict = str(item.get("verdict") or "")
    ftype = str(item.get("type") or "")
    model_version = str(item.get("modelVersion") or "")
    cache_version = str(item.get("cacheVersion") or "")
    provider = str(item.get("visibleWatermarkProvider") or "")
    fields = [
        item.get("name") or "",
        item.get("reportId") or "",
        item.get("createdAt") or "",
        verdict,
        *(verdict_labels.get(verdict) or []),
        ftype,
        *(type_labels.get(ftype) or []),
        source,
        *(source_labels.get(source) or []),
        model_version,
        f"模型 {model_version}" if model_version else "",
        f"模型版本 {model_version}" if model_version else "",
        cache_version,
        f"缓存版本 {cache_version}" if cache_version else "",
        f"分析缓存 {cache_version}" if cache_version else "",
        provider,
        f"{provider} 水印" if provider else "",
        "取证" if item.get("hasForensics") else "",
        "凭证" if item.get("hasProvenance") else "",
        "水印" if item.get("hasVisibleWatermark") else "",
        "SynthID" if item.get("hasSynthid") else "",
        "缓存" if item.get("cacheHit") else "",
    ]
    return [str(field) for field in fields]


def _matches_history_filters(
    item: dict[str, Any],
    *,
    source: str | None = None,
    verdict: str | None = None,
    has_cache: bool | None = None,
    has_forensics: bool | None = None,
    has_provenance: bool | None = None,
    has_watermark: bool | None = None,
    has_synthid: bool | None = None,
) -> bool:
    if source is not None and str(item.get("source") or "") != source:
        return False
    if verdict is not None and str(item.get("verdict") or "") != verdict:
        return False
    if has_cache is not None and bool(item.get("cacheHit")) is not has_cache:
        return False
    if has_forensics is not None and bool(item.get("hasForensics")) is not has_forensics:
        return False
    if has_provenance is not None and bool(item.get("hasProvenance")) is not has_provenance:
        return False
    if has_watermark is not None and bool(item.get("hasVisibleWatermark")) is not has_watermark:
        return False
    if has_synthid is not None and bool(item.get("hasSynthid")) is not has_synthid:
        return False
    return True


def list_history(
    *,
    owner_account_uuid: str | None = None,
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
    source: str | None = None,
    verdict: str | None = None,
    has_cache: bool | None = None,
    has_forensics: bool | None = None,
    has_provenance: bool | None = None,
    has_watermark: bool | None = None,
    has_synthid: bool | None = None,
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    ownership_sql = ""
    ownership_params: tuple[Any, ...] = ()
    if owner_account_uuid is not None:
        ownership_sql = "WHERE h.developer_account_uuid = ?"
        ownership_params = (str(owner_account_uuid),)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT h.task_id, h.report_id, h.created_at, h.sha256, h.file_type, h.file_name, h.result_json, h.thumbnail,
                   h.developer_user_id, h.developer_account_uuid,
                   a.forensics_json, a.provenance_json
            FROM history h
            LEFT JOIN history_artifacts a ON a.task_id = h.task_id
            {ownership_sql}
            ORDER BY created_at DESC
            """,
            ownership_params,
        ).fetchall()
    items = [_history_summary_from_row(row) for row in rows]
    normalized_query = (query or "").strip().lower()
    query_filtered = []
    for item in items:
        if normalized_query and not any(field.lower().find(normalized_query) >= 0 for field in _searchable_history_fields(item)):
            continue
        query_filtered.append(item)

    filter_counts = {
        "all": len(query_filtered),
        "vlm": sum(1 for item in query_filtered if str(item.get("source") or "") == "vlm"),
        "mock": sum(1 for item in query_filtered if str(item.get("source") or "") == "mock"),
        "maps-only": sum(1 for item in query_filtered if str(item.get("source") or "") == "maps-only"),
        "unknown": sum(1 for item in query_filtered if str(item.get("source") or "") == "unknown"),
        "real": sum(1 for item in query_filtered if str(item.get("verdict") or "") == "real"),
        "suspected": sum(1 for item in query_filtered if str(item.get("verdict") or "") == "suspected_fake"),
        "highly": sum(1 for item in query_filtered if str(item.get("verdict") or "") == "highly_suspected_fake"),
        "unknownVerdict": sum(1 for item in query_filtered if str(item.get("verdict") or "") == "unknown"),
        "cache": sum(1 for item in query_filtered if bool(item.get("cacheHit"))),
        "forensics": sum(1 for item in query_filtered if bool(item.get("hasForensics"))),
        "provenance": sum(1 for item in query_filtered if bool(item.get("hasProvenance"))),
        "synthid": sum(1 for item in query_filtered if bool(item.get("hasSynthid"))),
        "watermark": sum(1 for item in query_filtered if bool(item.get("hasVisibleWatermark"))),
    }

    filtered = [
        item
        for item in query_filtered
        if _matches_history_filters(
            item,
            source=source,
            verdict=verdict,
            has_cache=has_cache,
            has_forensics=has_forensics,
            has_provenance=has_provenance,
            has_watermark=has_watermark,
            has_synthid=has_synthid,
        )
    ]
    total = len(filtered)
    return filtered[offset: offset + limit], total, filter_counts


def get_history(item_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT h.task_id, h.created_at, h.sha256, h.result_json, h.thumbnail,
                   h.file_name, h.file_type, h.file_size, h.resolution,
                   h.developer_user_id, h.developer_account_uuid, h.developer_key_id,
                   a.forensics_json, a.provenance_json
            FROM history h
            LEFT JOIN history_artifacts a ON a.task_id = h.task_id
            WHERE h.task_id = ? OR h.report_id = ?
            """,
            (item_id, item_id),
        ).fetchone()
    if not row:
        return None
    result = json.loads(row["result_json"])
    result["_developerUserId"] = row["developer_user_id"]
    result["_developerAccountUuid"] = row["developer_account_uuid"]
    result["_developerKeyId"] = row["developer_key_id"]
    _ensure_result_file_meta(result, row)
    if row["forensics_json"]:
        result["forensics"] = json.loads(row["forensics_json"])
    if row["provenance_json"]:
        result["provenance"] = json.loads(row["provenance_json"])
    return result


def _raw_history_row(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT task_id, report_id, created_at, sha256, file_type, file_name,
               file_size, resolution, result_json, thumbnail, developer_user_id,
               developer_account_uuid, developer_key_id
        FROM history
        WHERE task_id = ? OR report_id = ?
        """,
        (item_id, item_id),
    ).fetchone()


def _actor_from_history_row(row: sqlite3.Row, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    actor: dict[str, Any] = {
        "userId": row["developer_user_id"],
        "accountUuid": row["developer_account_uuid"],
        "keyId": row["developer_key_id"],
    }
    if not actor["accountUuid"]:
        tenant = (((manifest or {}).get("payload") or {}).get("binding") or {}).get("tenant") or {}
        actor["mode"] = tenant.get("scope") or "unowned"
    return actor


def _verify_manifest_against_row(record: dict[str, Any], row: sqlite3.Row) -> dict[str, Any]:
    verification = evidence_manifest_v2.verify_signed_record(
        record,
        domain=evidence_manifest_v2.MANIFEST_SIGNATURE_DOMAIN,
        trusted_public_keys=evidence_manifest_v2.configured_verification_keys(),
    )
    if verification["status"] != "valid":
        raise EvidenceIntegrityError("evidence manifest signature is invalid")
    try:
        result = json.loads(row["result_json"])
    except json.JSONDecodeError as exc:
        raise EvidenceIntegrityError("history result JSON is invalid") from exc
    if not evidence_manifest_v2.manifest_matches_history(
        record["payload"],
        result,
        original_sha256=row["sha256"],
        file_size=row["file_size"],
        actor=_actor_from_history_row(row, record),
    ):
        raise EvidenceIntegrityError("evidence manifest does not match history")
    return verification


def ensure_evidence_manifest(item_id: str, *, origin: str = "legacy_backfill") -> dict[str, Any]:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _raw_history_row(conn, item_id)
            if not row:
                raise KeyError(item_id)
            existing = _get_manifest_record(conn, item_id)
            if existing is not None:
                _verify_manifest_against_row(existing, row)
                conn.commit()
                return existing
            try:
                result = json.loads(row["result_json"])
            except json.JSONDecodeError as exc:
                raise EvidenceIntegrityError("legacy history result JSON is invalid") from exc
            manifest = evidence_manifest_v2.build_manifest(
                result,
                original_sha256=row["sha256"],
                file_size=row["file_size"],
                actor=_actor_from_history_row(row),
                origin=origin,
            )
            signed = evidence_manifest_v2.seal_manifest(manifest)
            _insert_manifest_record(conn, signed)
            conn.commit()
            return signed
        except Exception:
            conn.rollback()
            raise


def backfill_evidence_manifests(*, limit: int = 1000) -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT h.task_id
            FROM history h
            LEFT JOIN evidence_manifests_v2 manifest ON manifest.task_id = h.task_id
            WHERE manifest.task_id IS NULL
            ORDER BY h.created_at ASC
            LIMIT ?
            """,
            (max(1, min(int(limit), 10_000)),),
        ).fetchall()
    completed = 0
    for row in rows:
        ensure_evidence_manifest(row["task_id"])
        completed += 1
    return {"discovered": len(rows), "completed": completed}


def _artifact_from_row(row: sqlite3.Row) -> dict[str, Any]:
    signed_record = {
        "payload": json.loads(row["statement_json"]),
        "sha256": row["statement_sha256"],
        "signature": {
            "algorithm": row["algorithm"],
            "keyId": row["key_id"],
            "publicKey": row["public_key"],
            "value": row["signature"],
        },
    }
    return {
        "taskId": row["task_id"],
        "reportId": row["report_id"],
        "filename": row["filename"],
        "mediaType": row["media_type"],
        "bytes": bytes(row["artifact_bytes"]),
        "artifactSha256": row["artifact_sha256"],
        "artifactSize": int(row["artifact_size"]),
        "reportPayload": json.loads(row["report_payload_json"]),
        "reportPayloadSha256": row["report_payload_sha256"],
        "signedRecord": signed_record,
        "createdAt": row["created_at"],
    }


def _get_report_artifact(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT report_id, task_id, filename, media_type, artifact_bytes,
               artifact_sha256, artifact_size, report_payload_json,
               report_payload_sha256, statement_json, statement_sha256,
               signature, algorithm, key_id, public_key, created_at
        FROM report_artifacts_v2
        WHERE report_id = ? OR task_id = ?
        """,
        (item_id, item_id),
    ).fetchone()
    return _artifact_from_row(row) if row else None


def get_report_artifact(item_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        return _get_report_artifact(conn, item_id)


def put_report_artifact(
    item_id: str,
    *,
    artifact_bytes: bytes,
    filename: str,
    media_type: str,
    report_payload: dict[str, Any],
) -> dict[str, Any]:
    if not artifact_bytes.startswith(b"%PDF-"):
        raise EvidenceIntegrityError("report artifact is not a PDF")
    manifest_record = ensure_evidence_manifest(item_id)
    statement = evidence_manifest_v2.build_artifact_statement(
        manifest_record,
        report_payload=report_payload,
        artifact_bytes=artifact_bytes,
        filename=filename,
        media_type=media_type,
    )
    report_payload_json = evidence_manifest_v2.canonical_json(report_payload).decode("utf-8")
    artifact_meta = statement["artifact"]
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = _get_report_artifact(conn, item_id)
            if existing is not None:
                existing_payload = dict(existing["signedRecord"]["payload"])
                existing_payload.pop("sealedAt", None)
                existing_payload.pop("signing", None)
                existing_payload.pop("timeEvidence", None)
                existing_bundle = {
                    "schema": evidence_manifest_v2.BUNDLE_SCHEMA,
                    "manifest": manifest_record,
                    "artifact": existing["signedRecord"],
                }
                existing_verification = evidence_manifest_v2.verify_bundle(
                    existing_bundle,
                    artifact_bytes=existing["bytes"],
                    report_payload=existing["reportPayload"],
                    trusted_public_keys=evidence_manifest_v2.configured_verification_keys(),
                )
                if existing_verification["status"] != "valid":
                    raise EvidenceIntegrityError("persisted PDF artifact is invalid")
                same = bool(
                    existing["bytes"] == artifact_bytes
                    and existing["filename"] == filename
                    and existing["mediaType"] == media_type
                    and evidence_manifest_v2.canonical_json(existing["reportPayload"]).decode("utf-8")
                    == report_payload_json
                    and evidence_manifest_v2.canonical_json(existing_payload)
                    == evidence_manifest_v2.canonical_json(statement)
                )
                if not same:
                    raise EvidenceConflictError(
                        "a different immutable PDF artifact already exists for this report"
                    )
                conn.commit()
                return existing

            snapshot_row = conn.execute(
                """
                SELECT h.result_json, a.forensics_json, a.provenance_json
                FROM history h
                LEFT JOIN history_artifacts a ON a.task_id = h.task_id
                WHERE h.task_id = ? AND h.report_id = ?
                """,
                (
                    manifest_record["payload"]["binding"]["taskId"],
                    manifest_record["payload"]["binding"]["reportId"],
                ),
            ).fetchone()
            if snapshot_row is None:
                raise EvidenceIntegrityError("history disappeared before report freeze")
            current_result = json.loads(snapshot_row["result_json"])
            for field, column in (
                ("forensics", "forensics_json"),
                ("provenance", "provenance_json"),
            ):
                current_value = (
                    json.loads(snapshot_row[column])
                    if snapshot_row[column] is not None
                    else current_result.get(field)
                )
                if (
                    evidence_manifest_v2.canonical_json(report_payload.get(field))
                    != evidence_manifest_v2.canonical_json(current_value)
                ):
                    raise EvidenceConflictError(
                        f"report payload {field} does not match current evidence attachments"
                    )

            signed_statement = evidence_manifest_v2.seal_artifact_statement(statement)
            binding = manifest_record["payload"]["binding"]
            signature = signed_statement["signature"]
            conn.execute(
                """
                INSERT INTO report_artifacts_v2
                    (report_id, task_id, filename, media_type, artifact_bytes,
                     artifact_sha256, artifact_size, report_payload_json,
                     report_payload_sha256, statement_json, statement_sha256,
                     signature, algorithm, key_id, public_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    binding["reportId"],
                    binding["taskId"],
                    filename,
                    media_type,
                    sqlite3.Binary(artifact_bytes),
                    artifact_meta["sha256"],
                    artifact_meta["sizeBytes"],
                    report_payload_json,
                    statement["reportPayloadSha256"],
                    evidence_manifest_v2.canonical_json(signed_statement["payload"]).decode("utf-8"),
                    signed_statement["sha256"],
                    signature["value"],
                    signature["algorithm"],
                    signature["keyId"],
                    signature["publicKey"],
                    signed_statement["payload"].get("sealedAt") or now_iso(),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    artifact = get_report_artifact(item_id)
    if artifact is None:
        raise EvidenceIntegrityError("report artifact was not persisted")
    return artifact


def get_evidence_bundle(item_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        manifest = _get_manifest_record(conn, item_id)
        if manifest is None:
            return None
        artifact = _get_report_artifact(conn, item_id)
    return {
        "schema": evidence_manifest_v2.BUNDLE_SCHEMA,
        "manifest": manifest,
        "artifact": artifact["signedRecord"] if artifact else None,
    }


def verify_evidence(item_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = _raw_history_row(conn, item_id)
        manifest = _get_manifest_record(conn, item_id)
        artifact = _get_report_artifact(conn, item_id)
    if row is None:
        return {
            "status": "missing",
            "complete": False,
            "packageIntegrityVerified": False,
            "manifest": {"status": "missing", "errors": ["history_missing"]},
            "artifact": {"status": "missing", "errors": ["history_missing"]},
        }
    bundle = {
        "schema": evidence_manifest_v2.BUNDLE_SCHEMA,
        "manifest": manifest,
        "artifact": artifact["signedRecord"] if artifact else None,
    }
    verification = evidence_manifest_v2.verify_bundle(
        bundle,
        artifact_bytes=artifact["bytes"] if artifact else None,
        report_payload=artifact["reportPayload"] if artifact else None,
        trusted_public_keys=evidence_manifest_v2.configured_verification_keys(),
    )
    if manifest is not None:
        try:
            _verify_manifest_against_row(manifest, row)
        except EvidenceIntegrityError:
            errors = list(verification["manifest"].get("errors") or [])
            errors.append("history_binding_mismatch")
            verification["manifest"] = {
                **verification["manifest"],
                "status": "invalid",
                "errors": list(dict.fromkeys(errors)),
            }
            verification["status"] = "invalid"
            verification["complete"] = False
    return verification


def delete_history(item_id: str) -> dict[str, Any] | None:
    """Erase tenant-identifying report content while retaining anonymous usage totals.

    Signed rows are removed leaf-first because their RESTRICT relationships are
    intentional: ordinary writes cannot replace evidence, while this explicit
    privacy path may erase it without leaving a content-bearing tombstone.
    """
    item = get_history(item_id)
    if not item:
        return None
    task_id = item["taskId"]
    report_id = item["reportId"]
    file_meta = item.get("fileMeta") or {}
    sha256 = str(file_meta.get("sha256") or "")
    file_type = str(file_meta.get("type") or "")
    owner_account_uuid = str(item.get("_developerAccountUuid") or "")
    privacy_erasure_ledger.record_tombstone(
        "jianzhen-v2",
        "history",
        task_id,
        report_id,
        resource_fingerprint_value=privacy_erasure_ledger.resource_fingerprint({
            "taskId": task_id,
            "reportId": report_id,
            "sha256": sha256,
            "fileType": file_type,
            "accountUuid": owner_account_uuid,
        }),
    )
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            DELETE FROM report_share_access_events
            WHERE report_id = ?
               OR share_id IN (SELECT share_id FROM report_shares WHERE report_id = ?)
            """,
            (report_id, report_id),
        )
        conn.execute("DELETE FROM report_shares WHERE report_id = ?", (report_id,))
        conn.execute(
            "DELETE FROM report_artifacts_v2 WHERE task_id = ? OR report_id = ?",
            (task_id, report_id),
        )
        conn.execute(
            "DELETE FROM evidence_manifests_v2 WHERE task_id = ? OR report_id = ?",
            (task_id, report_id),
        )
        conn.execute(
            "DELETE FROM history_artifacts WHERE task_id = ?",
            (task_id,),
        )
        conn.execute(
            "DELETE FROM history WHERE task_id = ? OR report_id = ?",
            (task_id, report_id),
        )
        # Keep only de-identified aggregate inputs for operational totals. A
        # deleted report must not remain linkable to a task, user, or API key.
        conn.execute(
            """
            UPDATE token_usage_events
            SET developer_user_id = NULL,
                developer_key_id = NULL,
                task_id = NULL,
                report_id = NULL
            WHERE task_id = ? OR report_id = ?
            """,
            (task_id, report_id),
        )
        conn.execute(
            """
            UPDATE request_events
            SET client_ip = NULL,
                user_agent = NULL,
                path = '[erased-resource-route]'
            WHERE (path IS NOT NULL AND instr(path, ?) > 0)
               OR (path IS NOT NULL AND instr(path, ?) > 0)
            """,
            (task_id, report_id),
        )
        if sha256 and file_type:
            if owner_account_uuid:
                remaining_for_tenant = conn.execute(
                    """
                    SELECT 1
                    FROM history
                    WHERE developer_account_uuid = ? AND sha256 = ? AND file_type = ?
                    LIMIT 1
                    """,
                    (owner_account_uuid, sha256, file_type),
                ).fetchone()
                if not remaining_for_tenant:
                    cache_scope = _account_cache_scope(owner_account_uuid)
                    if file_type == "image":
                        conn.execute(
                            """
                            DELETE FROM analysis_cache
                            WHERE sha256 = ?
                              AND (
                                  file_type = ?
                                  OR file_type LIKE ?
                              )
                            """,
                            (
                                sha256,
                                f"image:tenant:{cache_scope}",
                                f"image-forensics:%:{cache_scope}",
                            ),
                        )
                    else:
                        conn.execute(
                            "DELETE FROM analysis_cache WHERE sha256 = ? AND file_type = ?",
                            (sha256, f"{file_type}:tenant:{cache_scope}"),
                        )

            remaining_global = conn.execute(
                "SELECT 1 FROM history WHERE sha256 = ? AND file_type = ? LIMIT 1",
                (sha256, file_type),
            ).fetchone()
            if not remaining_global:
                conn.execute(
                    "DELETE FROM analysis_cache WHERE sha256 = ? AND file_type = ?",
                    (sha256, file_type),
                )
        conn.commit()
    return item


def _report_share_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "shareId": row["share_id"],
        "reportId": row["report_id"],
        "createdAt": row["created_at"],
        "expiresAt": int(row["expires_at"]),
        "createdByUserId": row["created_by_user_id"],
        "createdByKeyId": row["created_by_key_id"],
        "createdByMode": row["created_by_mode"],
        "revokedAt": row["revoked_at"],
        "legacy": bool(row["legacy"]),
    }


def create_report_share(
    *,
    share_id: str,
    report_id: str,
    expires_at: int,
    created_by_user_id: str,
    created_by_key_id: str | None,
    created_by_mode: str,
    legacy: bool = False,
    signature_fingerprint: str | None = None,
    require_existing_report: bool = False,
) -> dict[str, Any]:
    created_at = now_iso()
    with _connect() as conn:
        if require_existing_report:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM history WHERE report_id = ? LIMIT 1",
                (report_id,),
            ).fetchone() is None:
                conn.rollback()
                raise RuntimeError("report does not exist")
        conn.execute(
            """
            INSERT INTO report_shares
                (share_id, report_id, created_at, expires_at, created_by_user_id,
                 created_by_key_id, created_by_mode, revoked_at, legacy, signature_fingerprint)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                share_id,
                report_id,
                created_at,
                int(expires_at),
                created_by_user_id,
                created_by_key_id,
                created_by_mode,
                int(legacy),
                signature_fingerprint,
            ),
        )
        conn.commit()
    share = get_report_share(share_id)
    if share is None:
        raise RuntimeError("report share was not persisted")
    return share


def register_legacy_report_share(
    *,
    share_id: str,
    report_id: str,
    expires_at: int,
    owner_user_id: str,
    signature_fingerprint: str,
    require_existing_report: bool = False,
) -> dict[str, Any]:
    with _connect() as conn:
        if require_existing_report:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM history WHERE report_id = ? LIMIT 1",
                (report_id,),
            ).fetchone() is None:
                conn.rollback()
                raise RuntimeError("report does not exist")
        conn.execute(
            """
            INSERT INTO report_shares
                (share_id, report_id, created_at, expires_at, created_by_user_id,
                 created_by_key_id, created_by_mode, revoked_at, legacy, signature_fingerprint)
            VALUES (?, ?, ?, ?, ?, NULL, 'legacy', NULL, 1, ?)
            ON CONFLICT(report_id, signature_fingerprint) WHERE signature_fingerprint IS NOT NULL
            DO NOTHING
            """,
            (share_id, report_id, now_iso(), int(expires_at), owner_user_id, signature_fingerprint),
        )
        row = conn.execute(
            """
            SELECT * FROM report_shares
            WHERE report_id = ? AND signature_fingerprint = ?
            """,
            (report_id, signature_fingerprint),
        ).fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError("legacy report share was not persisted")
    return _report_share_from_row(row)


def get_report_share(share_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM report_shares WHERE share_id = ?",
            (share_id,),
        ).fetchone()
    return _report_share_from_row(row) if row else None


def list_report_shares(report_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM report_shares
            WHERE report_id = ?
            ORDER BY created_at DESC
            """,
            (report_id,),
        ).fetchall()
    return [_report_share_from_row(row) for row in rows]


def revoke_report_share(report_id: str, share_id: str) -> dict[str, Any] | None:
    revoked_at = now_iso()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM report_shares WHERE share_id = ? AND report_id = ?",
            (share_id, report_id),
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        if row["revoked_at"] is None:
            conn.execute(
                "UPDATE report_shares SET revoked_at = ? WHERE share_id = ?",
                (revoked_at, share_id),
            )
        conn.commit()
    return get_report_share(share_id)


def record_report_share_access(
    *,
    share_id: str,
    report_id: str,
    client_ip: str | None,
    user_agent: str | None,
    outcome: str,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO report_share_access_events
                (share_id, report_id, accessed_at, client_ip, user_agent, outcome)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                share_id[:128],
                report_id[:256],
                now_iso(),
                (client_ip or "")[:128] or None,
                (user_agent or "")[:512] or None,
                outcome[:64],
            ),
        )
        conn.commit()


def record_guest_upload_consent(
    *,
    subject_hash: str,
    document_version: str,
    terms_sha256: str,
    privacy_sha256: str,
    upload_sha256: str,
    idempotency_key_hash: str,
    channel: str,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO guest_upload_consents
                (subject_hash, document_version, terms_sha256, privacy_sha256,
                 upload_sha256, idempotency_key_hash, channel, accepted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                subject_hash,
                document_version,
                terms_sha256,
                privacy_sha256,
                upload_sha256,
                idempotency_key_hash,
                channel[:64],
                now_iso(),
            ),
        )
        conn.commit()


def put_history_artifacts(
    task_id: str,
    *,
    forensics: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> None:
    incoming_forensics = None if forensics is None else json.dumps(forensics, ensure_ascii=False)
    incoming_provenance = None if provenance is None else json.dumps(provenance, ensure_ascii=False)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                """
                SELECT forensics_json, provenance_json
                FROM history_artifacts
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            frozen = conn.execute(
                "SELECT 1 FROM report_artifacts_v2 WHERE task_id = ? LIMIT 1",
                (task_id,),
            ).fetchone()
            if frozen:
                current_forensics = existing["forensics_json"] if existing else None
                current_provenance = existing["provenance_json"] if existing else None
                desired_forensics = incoming_forensics if incoming_forensics is not None else current_forensics
                desired_provenance = incoming_provenance if incoming_provenance is not None else current_provenance

                def canonical_optional(raw: str | None) -> bytes:
                    return evidence_manifest_v2.canonical_json(None if raw is None else json.loads(raw))

                if (
                    canonical_optional(desired_forensics) != canonical_optional(current_forensics)
                    or canonical_optional(desired_provenance) != canonical_optional(current_provenance)
                ):
                    raise EvidenceConflictError(
                        "report evidence attachments cannot change after the PDF is frozen"
                    )
                conn.commit()
                return

            conn.execute(
                """
                INSERT INTO history_artifacts
                    (task_id, forensics_json, provenance_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    forensics_json = COALESCE(excluded.forensics_json, history_artifacts.forensics_json),
                    provenance_json = COALESCE(excluded.provenance_json, history_artifacts.provenance_json),
                    updated_at = excluded.updated_at
                """,
                (task_id, incoming_forensics, incoming_provenance, now_iso()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def next_sequence(prefix_date: str) -> int:
    name = f"history:{prefix_date}"
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
        if row:
            value = int(row["value"]) + 1
            conn.execute("UPDATE counters SET value = ? WHERE name = ?", (value, name))
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM history WHERE task_id LIKE ?",
                (f"rj-{prefix_date}-%",),
            ).fetchone()
            value = int(row["n"]) + 1
            conn.execute("INSERT INTO counters (name, value) VALUES (?, ?)", (name, value))
        conn.commit()
        return value
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_event(
    event_type: str,
    *,
    client_ip: str | None = None,
    user_agent: str | None = None,
    method: str | None = None,
    path: str | None = None,
    status: int | None = None,
    elapsed_ms: int | None = None,
    file_type: str | None = None,
    verdict: str | None = None,
    cache_hit: bool | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO request_events
                (created_at, event_type, client_ip, user_agent, method, path, status,
                 elapsed_ms, file_type, verdict, cache_hit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso(),
                event_type,
                client_ip,
                user_agent,
                method,
                path,
                status,
                elapsed_ms,
                file_type,
                verdict,
                None if cache_hit is None else int(cache_hit),
            ),
        )
        conn.commit()


def prune_telemetry() -> dict[str, int]:
    request_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=REQUEST_EVENT_RETENTION_DAYS)
    ).isoformat()
    token_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=TOKEN_USAGE_RETENTION_DAYS)
    ).isoformat()
    share_access_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=REPORT_SHARE_ACCESS_RETENTION_DAYS)
    ).isoformat()
    share_cutoff_epoch = int(
        (datetime.now(timezone.utc) - timedelta(days=REPORT_SHARE_RETENTION_DAYS)).timestamp()
    )
    share_revoked_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=REPORT_SHARE_RETENTION_DAYS)
    ).isoformat()
    with _connect() as conn:
        request_cursor = conn.execute(
            "DELETE FROM request_events WHERE created_at < ?",
            (request_cutoff,),
        )
        token_cursor = conn.execute(
            "DELETE FROM token_usage_events WHERE created_at < ?",
            (token_cutoff,),
        )
        share_access_cursor = conn.execute(
            "DELETE FROM report_share_access_events WHERE accessed_at < ?",
            (share_access_cutoff,),
        )
        share_cursor = conn.execute(
            """
            DELETE FROM report_shares
            WHERE expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)
            """,
            (share_cutoff_epoch, share_revoked_cutoff),
        )
        conn.commit()
        return {
            "requestEvents": max(int(request_cursor.rowcount or 0), 0),
            "tokenUsageEvents": max(int(token_cursor.rowcount or 0), 0),
            "reportShareAccessEvents": max(int(share_access_cursor.rowcount or 0), 0),
            "reportShares": max(int(share_cursor.rowcount or 0), 0),
        }


def _safe_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _authorized_metrics_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Fail closed when aggregating legacy or incompletely authorized results."""
    result = dict(raw or {})
    visible = result.get("visibleWatermark")
    decisive = False
    if isinstance(visible, dict):
        try:
            from .watermark_verdict import has_decisive_ai_watermark

            decisive = has_decisive_ai_watermark(visible)
        except (ImportError, TypeError, ValueError):
            decisive = False
    authorized = bool(
        result.get("decisionStatus") == "verdict"
        and result.get("decisionAuthority") == "decisive_provenance"
        and result.get("verdict") in PUBLISHABLE_VERDICTS
        and (result.get("source") == "provenance" or decisive)
    )
    if not authorized:
        result["verdict"] = binary_verdict(result)
        result["decisionStatus"] = "review_only"
        result["decisionAuthority"] = "none"
    return result


def metrics(days: int = 14) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=days - 1)
    today = datetime.now(timezone.utc).date().isoformat()
    with _connect() as conn:
        history_rows = conn.execute(
            """
            SELECT h.created_at, h.file_type, h.result_json, a.forensics_json, a.provenance_json
            FROM history h
            LEFT JOIN history_artifacts a ON a.task_id = h.task_id
            WHERE h.created_at >= ?
            ORDER BY h.created_at
            """,
            (since.isoformat(),),
        ).fetchall()
        event_rows = conn.execute(
            "SELECT created_at, event_type, client_ip, status, elapsed_ms, path, cache_hit FROM request_events WHERE created_at >= ?",
            (since.isoformat(),),
        ).fetchall()
        total_history = conn.execute("SELECT COUNT(*) AS n FROM history").fetchone()["n"]
        total_cache = conn.execute("SELECT COUNT(*) AS n FROM analysis_cache").fetchone()["n"]

    by_day: dict[str, int] = defaultdict(int)
    by_type: Counter[str] = Counter()
    by_verdict: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    source_verdict: dict[str, Counter[str]] = defaultdict(Counter)
    source_evidence: dict[str, Counter[str]] = defaultdict(Counter)
    by_day_evidence: dict[str, Counter[str]] = defaultdict(Counter)
    today_ips: set[str] = set()
    cache_hits = 0
    cache_known = 0
    latencies: list[int] = []
    errors: list[dict[str, Any]] = []
    requests_today = 0
    visible_watermark_hits = 0
    synthid_hits = 0
    forensics_completed = 0
    provenance_completed = 0

    for row in history_rows:
        day = row["created_at"][:10]
        by_day[day] += 1
        by_type[row["file_type"]] += 1
        result = _authorized_metrics_result(json.loads(row["result_json"]))
        verdict = str(result.get("verdict", "unknown"))
        source = str(result.get("source", "unknown"))
        by_verdict[verdict] += 1
        by_source[source] += 1
        source_verdict[source][verdict] += 1
        if (result.get("visibleWatermark") or {}).get("detected"):
            visible_watermark_hits += 1
            source_evidence[source]["visibleWatermarkHits"] += 1
            by_day_evidence[day]["visibleWatermarkHits"] += 1
        if (result.get("synthid") or {}).get("detected"):
            synthid_hits += 1
            source_evidence[source]["synthidHits"] += 1
            by_day_evidence[day]["synthidHits"] += 1
        if row["forensics_json"]:
            forensics_completed += 1
            source_evidence[source]["forensicsCompleted"] += 1
            by_day_evidence[day]["forensicsCompleted"] += 1
        if row["provenance_json"]:
            provenance_completed += 1
            source_evidence[source]["provenanceCompleted"] += 1
            by_day_evidence[day]["provenanceCompleted"] += 1

    for row in event_rows:
        day = row["created_at"][:10]
        if day == today:
            requests_today += 1
            if row["client_ip"]:
                today_ips.add(row["client_ip"])
        if row["elapsed_ms"] is not None:
            latencies.append(int(row["elapsed_ms"]))
        if row["cache_hit"] is not None:
            cache_known += 1
            cache_hits += int(row["cache_hit"])
        if row["status"] and int(row["status"]) >= 400:
            errors.append(
                {
                    "createdAt": row["created_at"],
                    "status": int(row["status"]),
                    "path": row["path"],
                }
            )

    days_list = []
    for i in range(days):
        day = (since + timedelta(days=i)).date().isoformat()
        day_sources = {"vlm": 0, "mock": 0, "maps-only": 0, "unknown": 0}
        day_verdicts = {"real": 0, "suspected_fake": 0, "highly_suspected_fake": 0, "unknown": 0}
        for row in history_rows:
            if row["created_at"][:10] != day:
                continue
            result = _authorized_metrics_result(json.loads(row["result_json"]))
            source = str(result.get("source", "unknown"))
            verdict = str(result.get("verdict", "unknown"))
            day_sources[source if source in day_sources else "unknown"] += 1
            day_verdicts[verdict if verdict in day_verdicts else "unknown"] += 1
        days_list.append({
            "date": day,
            "detections": by_day.get(day, 0),
            "sources": day_sources,
            "verdicts": day_verdicts,
            "evidence": {
                "visibleWatermarkHits": by_day_evidence[day]["visibleWatermarkHits"],
                "synthidHits": by_day_evidence[day]["synthidHits"],
                "forensicsCompleted": by_day_evidence[day]["forensicsCompleted"],
                "provenanceCompleted": by_day_evidence[day]["provenanceCompleted"],
            },
        })

    total_recent = sum(by_day.values())
    today_detections = by_day.get(today, 0)
    avg_latency = round(sum(latencies) / len(latencies)) if latencies else 0

    return {
        "summary": {
            "totalDetections": int(total_history),
            "recentDetections": int(total_recent),
            "todayDetections": int(today_detections),
            "uniqueClientsToday": len(today_ips),
            "requestsToday": requests_today,
            "avgLatencyMs": avg_latency,
            "cacheEntries": int(total_cache),
            "cacheHitRate": round(cache_hits / cache_known, 3) if cache_known else 0.0,
            "analysisCacheVersion": ANALYSIS_CACHE_VERSION,
        },
        "byDay": days_list,
        "byType": dict(by_type),
        "byVerdict": dict(by_verdict),
        "bySource": dict(by_source),
        "sourceVerdict": {source: dict(counter) for source, counter in source_verdict.items()},
        "sourceEvidence": {source: dict(counter) for source, counter in source_evidence.items()},
        "evidence": {
            "visibleWatermarkHits": visible_watermark_hits,
            "synthidHits": synthid_hits,
            "forensicsCompleted": forensics_completed,
            "provenanceCompleted": provenance_completed,
        },
        "recentErrors": errors[-8:],
    }


def record_token_usage(
    *,
    actor: dict[str, Any] | None = None,
    endpoint: str,
    file_type: str | None = None,
    result: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    cache_hit: bool = False,
) -> None:
    payload = result or {}
    usage_payload = usage or payload.get("tokenUsage") or {}
    prompt_tokens = _safe_int(usage_payload.get("promptTokens"))
    completion_tokens = _safe_int(usage_payload.get("completionTokens"))
    total_tokens = _safe_int(usage_payload.get("totalTokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    developer_user_id = str((actor or {}).get("userId") or "") or None
    developer_key_id = str((actor or {}).get("keyId") or "") or None
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO token_usage_events
                (created_at, developer_user_id, developer_key_id, task_id, report_id,
                 endpoint, model_version, source, file_type, prompt_tokens,
                 completion_tokens, total_tokens, cache_hit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso(),
                developer_user_id,
                developer_key_id,
                payload.get("taskId"),
                payload.get("reportId"),
                endpoint,
                payload.get("modelVersion"),
                payload.get("source"),
                file_type or (payload.get("fileMeta") or {}).get("type"),
                prompt_tokens,
                completion_tokens,
                total_tokens,
                int(cache_hit),
            ),
        )
        conn.commit()


def token_usage(
    *,
    days: int = 30,
    developer_user_id: str | None = None,
    developer_key_id: str | None = None,
) -> dict[str, Any]:
    days = max(1, min(int(days), 90))
    since = datetime.now(timezone.utc) - timedelta(days=days - 1)
    clauses = ["created_at >= ?"]
    params: list[Any] = [since.isoformat()]
    if developer_user_id:
        clauses.append("developer_user_id = ?")
        params.append(str(developer_user_id))
    if developer_key_id:
        clauses.append("developer_key_id = ?")
        params.append(str(developer_key_id))

    where_sql = " AND ".join(clauses)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT created_at, developer_key_id, endpoint, model_version, source, file_type,
                   prompt_tokens, completion_tokens, total_tokens, cache_hit
            FROM token_usage_events
            WHERE {where_sql}
            ORDER BY created_at ASC
            """,
            params,
        ).fetchall()

    by_day: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "requests": 0,
            "billableRequests": 0,
            "cacheHits": 0,
            "promptTokens": 0,
            "completionTokens": 0,
            "totalTokens": 0,
        }
    )
    by_endpoint: dict[str, dict[str, int]] = defaultdict(
        lambda: {"requests": 0, "promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
    )
    by_model: dict[str, dict[str, int]] = defaultdict(
        lambda: {"requests": 0, "promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
    )
    by_key: dict[str, dict[str, int]] = defaultdict(
        lambda: {"requests": 0, "cacheHits": 0, "promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
    )

    summary = {
        "totalRequests": 0,
        "billableRequests": 0,
        "cacheHits": 0,
        "promptTokens": 0,
        "completionTokens": 0,
        "totalTokens": 0,
        "lastEventAt": None,
    }

    for row in rows:
        prompt_tokens = int(row["prompt_tokens"] or 0)
        completion_tokens = int(row["completion_tokens"] or 0)
        total_tokens = int(row["total_tokens"] or 0)
        cache_hit = int(row["cache_hit"] or 0)
        is_billable = total_tokens > 0 and not cache_hit
        day = row["created_at"][:10]
        endpoint = row["endpoint"] or "unknown"
        model = row["model_version"] or row["source"] or "unknown"
        key = row["developer_key_id"] or "unknown"

        summary["totalRequests"] += 1
        summary["billableRequests"] += int(is_billable)
        summary["cacheHits"] += cache_hit
        summary["promptTokens"] += prompt_tokens
        summary["completionTokens"] += completion_tokens
        summary["totalTokens"] += total_tokens
        summary["lastEventAt"] = row["created_at"]

        for bucket in (by_day[day], by_endpoint[endpoint], by_model[model], by_key[key]):
            bucket["requests"] += 1
            bucket["promptTokens"] += prompt_tokens
            bucket["completionTokens"] += completion_tokens
            bucket["totalTokens"] += total_tokens
        by_day[day]["billableRequests"] += int(is_billable)
        by_day[day]["cacheHits"] += cache_hit
        by_key[key]["cacheHits"] += cache_hit

    days_list = []
    for i in range(days):
        day = (since + timedelta(days=i)).date().isoformat()
        days_list.append({"date": day, **by_day[day]})

    return {
        "days": days,
        "summary": summary,
        "byDay": days_list,
        "byEndpoint": [{"endpoint": key, **value} for key, value in sorted(by_endpoint.items())],
        "byModel": [{"modelVersion": key, **value} for key, value in sorted(by_model.items())],
        "byKey": [{"keyId": key, **value} for key, value in sorted(by_key.items())],
    }
