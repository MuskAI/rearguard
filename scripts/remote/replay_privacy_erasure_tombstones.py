#!/usr/bin/env python3
"""Replay append-only erasure tombstones onto restored RealGuard data."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
from typing import Any


IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def tombstone_phase(row: dict[str, str | None]) -> str:
    raw_payload = row.get("payload_json")
    if not raw_payload:
        # Unit callers and legacy ledgers without phase metadata represent a
        # completed deletion; the persisted v2 format always carries phase.
        return "committed"
    try:
        payload = json.loads(raw_payload)
    except (TypeError, ValueError):
        return "invalid"
    return str(payload.get("phase") or "committed").strip().lower() if isinstance(payload, dict) else "invalid"


def read_tombstones(path: Path) -> list[dict[str, str | None]]:
    original_path = path.expanduser()
    file_stat = original_path.lstat()
    if stat.S_ISLNK(file_stat.st_mode):
        raise RuntimeError("privacy erasure ledger cannot be a symlink")
    path = original_path.resolve(strict=True)
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
        raise RuntimeError("privacy erasure ledger is not a regular single-link file")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        integrity = [
            tuple(row) for row in connection.execute("PRAGMA integrity_check").fetchall()
        ]
        if integrity != [("ok",)]:
            raise RuntimeError("privacy erasure ledger integrity check failed")
        rows = connection.execute(
            """
            SELECT tombstone_id, source_system, resource_kind, primary_id,
                   secondary_id, payload_json, payload_sha256
            FROM privacy_erasure_tombstones
            ORDER BY created_at, tombstone_id
            """
        ).fetchall()
    tombstones: list[dict[str, str | None]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict) or canonical(payload) != row["payload_json"]:
            raise RuntimeError("privacy erasure tombstone payload is not canonical")
        digest = hashlib.sha256(row["payload_json"].encode("ascii")).hexdigest()
        phase = str(payload.get("phase") or "committed").strip().lower()
        expected_ids = {f"erase_{digest}"}
        if phase in {"prepared", "committed"}:
            expected_ids.add(f"erase_{phase}_{digest}")
        if row["payload_sha256"] != digest or row["tombstone_id"] not in expected_ids:
            raise RuntimeError("privacy erasure tombstone digest mismatch")
        expected = {
            "sourceSystem": row["source_system"],
            "resourceKind": row["resource_kind"],
            "primaryId": row["primary_id"],
            "secondaryId": row["secondary_id"],
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise RuntimeError("privacy erasure tombstone columns do not match payload")
        tombstones.append(dict(row))
    return tombstones


def ensure_snapshot_is_covered(
    live: list[dict[str, str | None]],
    snapshot: list[dict[str, str | None]],
) -> None:
    live_digests = {
        str(row["tombstone_id"]): str(row["payload_sha256"])
        for row in live
    }
    for row in snapshot:
        if live_digests.get(str(row["tombstone_id"])) != row["payload_sha256"]:
            raise RuntimeError("live erasure ledger does not cover the backup snapshot ledger")


def checked_database_name(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise RuntimeError(f"unsafe database identifier: {value}")
    return value


def mysql_run(mysql_bin: str, database: str, sql: str, *, capture: bool = False) -> str:
    database = checked_database_name(database)
    completed = subprocess.run(
        [
            mysql_bin,
            "--batch",
            "--skip-column-names",
            "--raw",
            f"--database={database}",
            f"--execute={sql}",
        ],
        check=True,
        text=True,
        capture_output=capture,
    )
    return completed.stdout.strip() if capture else ""


def mysql_table_exists(mysql_bin: str, database: str, table: str) -> bool:
    checked_database_name(database)
    if not IDENTIFIER.fullmatch(table):
        raise RuntimeError("unsafe table identifier")
    result = mysql_run(
        mysql_bin,
        database,
        "SELECT COUNT(*) FROM information_schema.tables "
        f"WHERE table_schema = '{database}' AND table_name = '{table}'",
        capture=True,
    )
    return result == "1"


def mysql_text(value: str) -> str:
    return f"CONVERT(0x{value.encode('utf-8').hex()} USING utf8mb4)"


def decode_mysql_base64(value: str) -> str:
    return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")


def unlink_owned_file(root: Path | None, *parts: str) -> int:
    if root is None:
        return 0
    root = root.resolve(strict=True)
    if any(not part or Path(part).name != part or part in {".", ".."} for part in parts):
        raise RuntimeError("restored private file path is unsafe")
    candidate = root.joinpath(*parts)
    try:
        candidate_stat = candidate.lstat()
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(candidate_stat.st_mode) or candidate_stat.st_nlink != 1:
        raise RuntimeError("restored private file is unsafe")
    candidate.unlink()
    return 1


def scrub_admin_state(path: Path | None, item_ids: set[str]) -> int:
    if path is None or not path.is_file() or not item_ids:
        return 0
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise RuntimeError("restored admin state is not an object")

    def contains_item(value: Any) -> bool:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).lower() in {
                    "itemid", "item_id", "result_item_id", "effect_item_id",
                } and str(nested) in item_ids:
                    return True
                if contains_item(nested):
                    return True
        elif isinstance(value, list):
            return any(contains_item(item) for item in value)
        return False

    scrubbed = 0
    for run in state.get("modelRuns") or []:
        if isinstance(run, dict) and str(run.get("itemid") or "") in item_ids:
            run["itemid"] = None
            run["actor"] = {"id": "", "account_uuid": "", "username": "", "phone": ""}
            run["meta"] = {"erased": True, "restoredTombstone": True}
            scrubbed += 1
    for job in (state.get("detectionJobs") or {}).values():
        if isinstance(job, dict) and contains_item(job.get("result")):
            job["filename"] = "[erased]"
            job["actor"] = {
                "id": "", "account_uuid": "", "username": "", "phone": "", "openid": "",
            }
            job["result"] = None
            job["error"] = ""
            job["summary"] = "content erased before restore"
            scrubbed += 1
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return scrubbed


def replay_v1(
    rows: list[dict[str, str | None]],
    *,
    mysql_bin: str,
    system_database: str,
    detection_database: str,
    uploads_root: Path | None,
    evidence_root: Path | None,
    admin_state: Path | None,
) -> dict[str, int]:
    replayed = 0
    files = 0
    item_ids: set[str] = set()
    for row in rows:
        if row["source_system"] != "realguard-v1":
            continue
        if tombstone_phase(row) != "committed":
            continue
        kind = str(row["resource_kind"])
        item_id = str(row["primary_id"])
        if kind not in {"image-history", "video-history"} or not item_id.isdigit():
            raise RuntimeError("unsupported V1 privacy erasure tombstone")
        table = "data" if kind == "image-history" else "video_data"
        media_kind = "image" if table == "data" else "video"
        encoded = mysql_run(
            mysql_bin,
            detection_database,
            "SELECT TO_BASE64(COALESCE(filename,'')), "
            "TO_BASE64(COALESCE(NULLIF(openid,''),NULLIF(phone,''),'guest')) "
            f"FROM `{table}` WHERE itemid = {int(item_id)} LIMIT 1",
            capture=True,
        )
        if encoded:
            columns = encoded.split("\t")
            if len(columns) != 2:
                raise RuntimeError("restored V1 media lookup returned an invalid row")
            filename, folder = map(decode_mysql_base64, columns)
            files += unlink_owned_file(uploads_root, folder, media_kind, filename)
        if table == "data" and mysql_table_exists(mysql_bin, detection_database, "exif"):
            mysql_run(
                mysql_bin,
                detection_database,
                f"START TRANSACTION; DELETE FROM `exif` WHERE data_itemid = {int(item_id)}; "
                f"DELETE FROM `data` WHERE itemid = {int(item_id)}; COMMIT;",
            )
        else:
            mysql_run(
                mysql_bin,
                detection_database,
                f"DELETE FROM `{table}` WHERE itemid = {int(item_id)}",
            )
        erased_hash = hashlib.sha256(
            f"restored-erasure:{row['tombstone_id']}".encode("utf-8")
        ).hexdigest()
        if mysql_table_exists(mysql_bin, system_database, "developer_detection_tasks"):
            mysql_run(
                mysql_bin,
                system_database,
                "UPDATE developer_detection_tasks SET user_id=0, account_uuid="
                f"{mysql_text(erased_hash[:32])}, key_id=0, filename='[erased]', "
                "mime_type='application/octet-stream', execution_filename=NULL, "
                f"request_sha256='{erased_hash}', spool_path=NULL, spool_size=NULL, "
                "request_context_json=NULL, idempotency_key=NULL, effect_item_id=NULL, "
                "effect_result_json=NULL, result_item_id=NULL, result_json=NULL, "
                f"error_message=NULL WHERE result_item_id={int(item_id)} "
                f"OR effect_item_id={int(item_id)}",
            )
        if mysql_table_exists(mysql_bin, system_database, "web_detection_tasks"):
            mysql_run(
                mysql_bin,
                system_database,
                "UPDATE web_detection_tasks SET filename='[erased]', "
                "mime_type='application/octet-stream', "
                f"request_sha256='{erased_hash}', spool_path=NULL, spool_size=NULL, "
                "request_context_json=NULL, owner_type='erased', "
                f"owner_key='{erased_hash}', idempotency_key=NULL, effect_item_id=NULL, "
                "effect_result_json=NULL, result_json=NULL, error_message=NULL "
                f"WHERE effect_item_id={int(item_id)}",
            )
        if mysql_table_exists(mysql_bin, system_database, "admin_model_runs"):
            mysql_run(
                mysql_bin,
                system_database,
                "UPDATE admin_model_runs SET itemid=NULL, actor_id=NULL, "
                "actor_username=NULL, actor_phone=NULL, "
                "meta_json='{" + '"erased":true,"restoredTombstone":true' + "}' "
                f"WHERE itemid={int(item_id)}",
            )
        if media_kind == "image" and evidence_root is not None:
            files += unlink_owned_file(evidence_root, f"image-{item_id}.manifest.json")
        item_ids.add(item_id)
        replayed += 1
    admin_records = scrub_admin_state(admin_state, item_ids)
    return {"tombstones": replayed, "files": files, "adminRecords": admin_records}


def sqlite_table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def replay_v2(rows: list[dict[str, str | None]], database: Path | None) -> int:
    if database is None:
        return 0
    replayed = 0
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        for tombstone in rows:
            if tombstone["source_system"] != "jianzhen-v2":
                continue
            if tombstone_phase(tombstone) != "committed":
                continue
            if tombstone["resource_kind"] != "history":
                raise RuntimeError("unsupported V2 privacy erasure tombstone")
            task_id = str(tombstone["primary_id"] or "").strip()
            report_id = str(tombstone["secondary_id"] or "").strip()
            if not task_id:
                raise RuntimeError("V2 privacy erasure tombstone is missing its primary id")
            if not report_id:
                raise RuntimeError("V2 privacy erasure tombstone is missing its secondary id")
            connection.execute("BEGIN IMMEDIATE")
            history_row = None
            if sqlite_table_exists(connection, "history"):
                history_row = connection.execute(
                    "SELECT sha256, file_type FROM history WHERE task_id=? OR report_id=?",
                    (task_id, report_id),
                ).fetchone()
            for table, clause, params in (
                ("report_share_access_events", "report_id=?", (report_id,)),
                ("report_shares", "report_id=?", (report_id,)),
                ("report_artifacts_v2", "task_id=? OR report_id=?", (task_id, report_id)),
                ("evidence_manifests_v2", "task_id=? OR report_id=?", (task_id, report_id)),
                ("history_artifacts", "task_id=?", (task_id,)),
                ("history", "task_id=? OR report_id=?", (task_id, report_id)),
            ):
                if sqlite_table_exists(connection, table):
                    connection.execute(f"DELETE FROM {table} WHERE {clause}", params)
            if sqlite_table_exists(connection, "token_usage_events"):
                connection.execute(
                    "UPDATE token_usage_events SET developer_user_id=NULL, "
                    "developer_key_id=NULL, task_id=NULL, report_id=NULL "
                    "WHERE task_id=? OR report_id=?",
                    (task_id, report_id),
                )
            if sqlite_table_exists(connection, "request_events"):
                connection.execute(
                    "UPDATE request_events SET client_ip=NULL, user_agent=NULL, "
                    "path='[erased-resource-route]' WHERE instr(path,?)>0 OR instr(path,?)>0",
                    (task_id, report_id),
                )
            if history_row and sqlite_table_exists(connection, "analysis_cache"):
                remaining = connection.execute(
                    "SELECT 1 FROM history WHERE sha256=? AND file_type=? LIMIT 1",
                    (history_row["sha256"], history_row["file_type"]),
                ).fetchone()
                if not remaining:
                    connection.execute(
                        "DELETE FROM analysis_cache WHERE sha256=?",
                        (history_row["sha256"],),
                    )
            connection.commit()
            replayed += 1
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("V2 foreign key check failed after erasure replay")
    return replayed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--snapshot-ledger", type=Path)
    parser.add_argument("--mysql-bin", default="mysql")
    parser.add_argument("--system-database", required=True)
    parser.add_argument("--detection-database", required=True)
    parser.add_argument("--v2-database", type=Path)
    parser.add_argument("--uploads-root", type=Path)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--admin-state", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    live = read_tombstones(args.ledger)
    if args.snapshot_ledger:
        ensure_snapshot_is_covered(live, read_tombstones(args.snapshot_ledger))
    v1 = replay_v1(
        live,
        mysql_bin=args.mysql_bin,
        system_database=checked_database_name(args.system_database),
        detection_database=checked_database_name(args.detection_database),
        uploads_root=args.uploads_root,
        evidence_root=args.evidence_root,
        admin_state=args.admin_state,
    )
    v2 = replay_v2(live, args.v2_database)
    print(json.dumps({"status": "ok", "v1": v1, "v2Tombstones": v2}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"privacy erasure replay failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
