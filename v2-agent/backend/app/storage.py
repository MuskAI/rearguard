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
            thumbnail TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_history_created_at ON history(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_history_sha256 ON history(sha256);

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

        CREATE TABLE IF NOT EXISTS counters (
            name TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        );
        """
    )
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def cache_key(file_type: str, sha256: str) -> str:
    return f"{file_type}:{sha256}"


def get_cached_analysis(file_type: str, sha256: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT analysis_json FROM analysis_cache WHERE cache_key = ?",
            (cache_key(file_type, sha256),),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["analysis_json"])


def put_cached_analysis(file_type: str, sha256: str, analysis: dict[str, Any]) -> None:
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


def put_history(result: dict[str, Any], sha256: str, file_size: int, thumbnail: str | None) -> None:
    meta = result.get("fileMeta", {})
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO history
                (task_id, report_id, created_at, sha256, file_type, file_name, file_size,
                 resolution, result_json, thumbnail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        conn.commit()


def list_history(limit: int = 100) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT task_id, report_id, created_at, file_type, file_name, result_json, thumbnail
            FROM history
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        result = json.loads(row["result_json"])
        items.append(
            {
                "taskId": row["task_id"],
                "reportId": row["report_id"],
                "name": row["file_name"],
                "type": row["file_type"],
                "verdict": result.get("verdict"),
                "confidence": result.get("confidence"),
                "createdAt": row["created_at"],
                "thumbnail": row["thumbnail"],
                "cacheHit": bool(result.get("cacheHit")),
            }
        )
    return items


def get_history(item_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT result_json, thumbnail FROM history WHERE task_id = ? OR report_id = ?",
            (item_id, item_id),
        ).fetchone()
    if not row:
        return None
    result = json.loads(row["result_json"])
    if row["thumbnail"]:
        result.setdefault("fileMeta", {})["thumbnail"] = row["thumbnail"]
    return result


def delete_history(item_id: str) -> dict[str, Any] | None:
    item = get_history(item_id)
    if not item:
        return None
    with _connect() as conn:
        conn.execute(
            "DELETE FROM history WHERE task_id = ? OR report_id = ?",
            (item["taskId"], item["reportId"]),
        )
        conn.commit()
    return item


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


def metrics(days: int = 14) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=days - 1)
    today = datetime.now(timezone.utc).date().isoformat()
    with _connect() as conn:
        history_rows = conn.execute(
            "SELECT created_at, file_type, result_json FROM history WHERE created_at >= ? ORDER BY created_at",
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
    today_ips: set[str] = set()
    cache_hits = 0
    cache_known = 0
    latencies: list[int] = []
    errors: list[dict[str, Any]] = []
    requests_today = 0

    for row in history_rows:
        day = row["created_at"][:10]
        by_day[day] += 1
        by_type[row["file_type"]] += 1
        result = json.loads(row["result_json"])
        by_verdict[str(result.get("verdict", "unknown"))] += 1

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
        days_list.append({"date": day, "detections": by_day.get(day, 0)})

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
        },
        "byDay": days_list,
        "byType": dict(by_type),
        "byVerdict": dict(by_verdict),
        "recentErrors": errors[-8:],
    }
