#!/usr/bin/env python3
"""Materialize an import manifest with same-filesystem hard links."""
from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path, PurePosixPath


def materialize(mapping_path: Path, cache_root: Path, data_root: Path, old_root: str) -> dict:
    rows = json.loads(gzip.decompress(mapping_path.read_bytes()))
    cache_root = cache_root.resolve()
    data_root = data_root.resolve()
    old_root = old_root.rstrip("/")
    linked = 0
    linked_bytes = 0
    for relative_path, expected_bytes, storage_path, *_unused in rows:
        relative = PurePosixPath(str(relative_path).replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe relative path: {relative_path}")
        source = (cache_root / Path(*relative.parts)).resolve()
        if cache_root not in source.parents or not source.is_file():
            raise FileNotFoundError(f"source file is missing: {relative_path}")
        if source.stat().st_size != int(expected_bytes):
            raise ValueError(f"source file size changed: {relative_path}")
        raw_target = str(storage_path)
        if not raw_target.startswith(f"{old_root}/"):
            raise ValueError(f"storage path is outside the old root: {raw_target}")
        target = (data_root / raw_target[len(old_root) + 1:]).resolve()
        if data_root not in target.parents:
            raise ValueError(f"target path is outside the new root: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.stat().st_ino == source.stat().st_ino:
                continue
            target.unlink()
        os.link(source, target)
        target.chmod(0o600)
        linked += 1
        linked_bytes += int(expected_bytes)
    return {
        "files": len(rows),
        "linkedFiles": linked,
        "linkedBytes": linked_bytes,
        "dataRoot": str(data_root),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mapping", type=Path)
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("data_root", type=Path)
    parser.add_argument("--old-root", required=True)
    args = parser.parse_args()
    result = materialize(args.mapping, args.cache_root, args.data_root, args.old_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
