"""Lightweight persistent storage for V2 history, cache, and metrics."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DATA_DIR = Path(os.getenv("JIANZHEN_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DB_PATH = DATA_DIR / "jianzhen-v2.sqlite3"
ANALYSIS_CACHE_VERSION = os.getenv("JIANZHEN_ANALYSIS_CACHE_VERSION", "v8-provenance-first")
REQUEST_EVENT_RETENTION_DAYS = max(1, int(os.getenv("JIANZHEN_REQUEST_EVENT_RETENTION_DAYS", "90")))
TOKEN_USAGE_RETENTION_DAYS = max(30, int(os.getenv("JIANZHEN_TOKEN_USAGE_RETENTION_DAYS", "730")))
PUBLISHABLE_VERDICTS = frozenset({"real", "suspected_fake", "highly_suspected_fake"})
PUBLISHABLE_SOURCES = frozenset({"vlm", "provenance"})

_INIT_LOCK = threading.Lock()
_INITIALIZED = False


def _connect() -> sqlite3.Connection:
    global _INITIALIZED
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    if not _INITIALIZED:
        with _INIT_LOCK:
            if not _INITIALIZED:
                _init(conn)
                _INITIALIZED = True
    return conn


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

        CREATE TABLE IF NOT EXISTS counters (
            name TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        );
        """
    )
    for statement in (
        "ALTER TABLE history ADD COLUMN developer_user_id TEXT",
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


def is_publishable_analysis(analysis: Any) -> bool:
    """Return whether an analysis may be exposed as a detection conclusion."""
    return (
        isinstance(analysis, dict)
        and analysis.get("source") in PUBLISHABLE_SOURCES
        and analysis.get("verdict") in PUBLISHABLE_VERDICTS
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
    if not is_publishable_analysis(analysis):
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
    if not is_publishable_analysis(analysis):
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


def put_history(
    result: dict[str, Any],
    sha256: str,
    file_size: int,
    thumbnail: str | None,
    actor: dict[str, Any] | None = None,
) -> None:
    meta = result.get("fileMeta", {})
    developer_user_id = str((actor or {}).get("userId") or "") or None
    developer_key_id = str((actor or {}).get("keyId") or "") or None
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO history
                (task_id, report_id, created_at, sha256, file_type, file_name, file_size,
                 resolution, result_json, thumbnail, developer_user_id, developer_key_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(result, ensure_ascii=False),
                thumbnail,
                developer_user_id,
                developer_key_id,
            ),
        )
        conn.commit()


def _history_summary_from_row(
    row: sqlite3.Row,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = result if result is not None else json.loads(row["result_json"])
    visible = result.get("visibleWatermark") or {}
    synthid = result.get("synthid") or {}
    return {
        "taskId": row["task_id"],
        "reportId": row["report_id"],
        "name": row["file_name"],
        "type": row["file_type"],
        "verdict": result.get("verdict"),
        "confidence": result.get("confidence"),
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
    owner_user_id: str | None = None,
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
    if owner_user_id is not None:
        ownership_sql = "WHERE h.developer_user_id = ?"
        ownership_params = (str(owner_user_id),)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT h.task_id, h.report_id, h.created_at, h.sha256, h.file_type, h.file_name, h.result_json, h.thumbnail,
                   h.developer_user_id,
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
                   h.developer_user_id, h.developer_key_id,
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
    result["_developerKeyId"] = row["developer_key_id"]
    _ensure_result_file_meta(result, row)
    if row["forensics_json"]:
        result["forensics"] = json.loads(row["forensics_json"])
    if row["provenance_json"]:
        result["provenance"] = json.loads(row["provenance_json"])
    return result


def delete_history(item_id: str) -> dict[str, Any] | None:
    item = get_history(item_id)
    if not item:
        return None
    task_id = item["taskId"]
    report_id = item["reportId"]
    file_meta = item.get("fileMeta") or {}
    sha256 = str(file_meta.get("sha256") or "")
    file_type = str(file_meta.get("type") or "")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM history_artifacts WHERE task_id = ?",
            (task_id,),
        )
        conn.execute(
            "DELETE FROM history WHERE task_id = ? OR report_id = ?",
            (task_id, report_id),
        )
        # Usage rows are retained for billing/audit totals but no longer point
        # back to deleted content or a downloadable report.
        conn.execute(
            """
            UPDATE token_usage_events
            SET task_id = NULL, report_id = NULL
            WHERE task_id = ? OR report_id = ?
            """,
            (task_id, report_id),
        )
        if sha256 and file_type:
            remaining = conn.execute(
                "SELECT 1 FROM history WHERE sha256 = ? AND file_type = ? LIMIT 1",
                (sha256, file_type),
            ).fetchone()
            if not remaining:
                conn.execute(
                    "DELETE FROM analysis_cache WHERE sha256 = ? AND file_type = ?",
                    (sha256, file_type),
                )
        conn.commit()
    return item


def put_history_artifacts(
    task_id: str,
    *,
    forensics: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> None:
    with _connect() as conn:
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
            (
                task_id,
                None if forensics is None else json.dumps(forensics, ensure_ascii=False),
                None if provenance is None else json.dumps(provenance, ensure_ascii=False),
                now_iso(),
            ),
        )
        conn.commit()


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
    with _connect() as conn:
        request_cursor = conn.execute(
            "DELETE FROM request_events WHERE created_at < ?",
            (request_cutoff,),
        )
        token_cursor = conn.execute(
            "DELETE FROM token_usage_events WHERE created_at < ?",
            (token_cutoff,),
        )
        conn.commit()
        return {
            "requestEvents": max(int(request_cursor.rowcount or 0), 0),
            "tokenUsageEvents": max(int(token_cursor.rowcount or 0), 0),
        }


def _safe_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


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
        result = json.loads(row["result_json"])
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
            result = json.loads(row["result_json"])
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
