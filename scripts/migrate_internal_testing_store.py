#!/usr/bin/env python3
"""Rewrite internal-testing metadata after moving its data root to another host."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _replace_prefix(value: str | None, old_root: str, new_root: str) -> str | None:
    if not value:
        return value
    return f"{new_root}{value[len(old_root):]}" if value.startswith(old_root) else value


def _rewrite_database(path: Path, statements: list[tuple[str, tuple]]) -> int:
    if not path.is_file():
        return 0
    connection = sqlite3.connect(path, timeout=30)
    changed = 0
    try:
        for sql, parameters in statements:
            cursor = connection.execute(sql, parameters)
            changed += max(0, int(cursor.rowcount or 0))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    return changed


def migrate(root: Path, old_root: str, model_url_map: dict[str, str]) -> dict:
    root = root.resolve()
    old_root = old_root.rstrip("/")
    new_root = str(root).rstrip("/")
    changed = _rewrite_database(
        root / "internal-testing.sqlite3",
        [(
            "UPDATE samples SET storage_path = ? || substr(storage_path, ?) "
            "WHERE storage_path = ? OR storage_path LIKE ?",
            (new_root, len(old_root) + 1, old_root, f"{old_root}/%"),
        )],
    )
    sessions = 0
    requeued = 0
    missing: list[str] = []
    import_root = root / "imports"
    for directory in sorted(import_root.glob("imp_*")) if import_root.exists() else []:
        manifest = directory / "manifest.sqlite3"
        changed += _rewrite_database(
            manifest,
            [
                (
                    "UPDATE files SET storage_path = ? || substr(storage_path, ?) "
                    "WHERE storage_path = ? OR storage_path LIKE ?",
                    (new_root, len(old_root) + 1, old_root, f"{old_root}/%"),
                ),
                (
                    "UPDATE chunks SET part_path = ? || substr(part_path, ?) "
                    "WHERE part_path = ? OR part_path LIKE ?",
                    (new_root, len(old_root) + 1, old_root, f"{old_root}/%"),
                ),
            ],
        )
        state_path = directory / "session.json"
        if not state_path.is_file():
            continue
        state = json.loads(state_path.read_text(encoding="utf-8"))
        sessions += 1
        if state.get("status") == "processing":
            state["status"] = "queued"
            state["updatedAt"] = datetime.now(timezone.utc).isoformat()
            requeued += 1
        model = state.get("model") if isinstance(state.get("model"), dict) else {}
        for key in ("endpoint", "healthUrl"):
            current = str(model.get(key) or "")
            for source, target in sorted(model_url_map.items(), key=lambda item: len(item[0]), reverse=True):
                source = source.rstrip("/")
                target = target.rstrip("/")
                if current == source or current.startswith(f"{source}/"):
                    model[key] = f"{target}{current[len(source):]}"
                    break
        temporary = state_path.with_suffix(".json.migrating")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(state_path)

        if state.get("status") not in {"uploading", "queued"} or not manifest.is_file():
            continue
        connection = sqlite3.connect(manifest)
        try:
            for (storage_path,) in connection.execute("SELECT storage_path FROM files"):
                remapped = _replace_prefix(storage_path, old_root, new_root)
                if remapped and not Path(remapped).is_file():
                    missing.append(remapped)
            for (part_path,) in connection.execute(
                "SELECT part_path FROM chunks WHERE status='pending' AND part_path IS NOT NULL"
            ):
                remapped = _replace_prefix(part_path, old_root, new_root)
                if remapped and not Path(remapped).is_file():
                    missing.append(remapped)
        finally:
            connection.close()

    database = root / "internal-testing.sqlite3"
    if database.is_file():
        connection = sqlite3.connect(database)
        try:
            for (storage_path,) in connection.execute("SELECT storage_path FROM samples"):
                remapped = _replace_prefix(storage_path, old_root, new_root)
                if remapped and not Path(remapped).is_file():
                    missing.append(remapped)
        finally:
            connection.close()
    return {
        "root": new_root,
        "updatedDatabaseRows": changed,
        "sessions": sessions,
        "requeuedSessions": requeued,
        "missingFiles": missing[:100],
        "missingFileCount": len(missing),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--old-root", required=True)
    parser.add_argument("--model-url-map", default="{}")
    args = parser.parse_args()
    result = migrate(args.root, args.old_root, json.loads(args.model_url_map))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["missingFileCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
