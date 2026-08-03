#!/usr/bin/env python3
"""Build a grouped, hierarchical image benchmark manifest without copying images."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REALGUARD = ROOT / "realguard-server-main" / "RealGuard"
sys.path.insert(0, str(REALGUARD))

from imagedetection.views import internal_testing  # noqa: E402


IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif",
}


def _split(group_id: str) -> str:
    bucket = int(hashlib.sha256(group_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "validation"
    return "test"


def _scan(root: Path) -> tuple[str, list[Path]]:
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    relative_paths = [path.relative_to(root).as_posix() for path in files]
    return internal_testing._detect_dataset_profile(relative_paths), files


def build(roots: list[Path], output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    counts = Counter()
    dimensions: dict[str, Counter] = defaultdict(Counter)
    source_summaries = []
    group_splits: dict[str, str] = {}
    total_bytes = 0

    with temporary.open("w", encoding="utf-8") as manifest:
        for root in roots:
            profile, files = _scan(root)
            source_count = Counter()
            for path in files:
                relative_path = path.relative_to(root).as_posix()
                classified = internal_testing._classify_path(relative_path, profile)
                group_id = classified["groupId"] or f"{root.name}/{relative_path}"
                split = group_splits.setdefault(group_id, _split(group_id))
                size = path.stat().st_size
                record = {
                    "source_dataset": root.name,
                    "source_root": str(root),
                    "absolute_path": str(path),
                    "relative_path": relative_path,
                    "profile": profile,
                    "ground_truth": classified["groundTruth"],
                    "label_source": classified["labelSource"],
                    "class_path": classified["classPath"],
                    "subclasses": classified["subclasses"],
                    "group_id": group_id,
                    "split": split,
                    "byte_size": size,
                }
                manifest.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                counts[("label", record["ground_truth"])] += 1
                counts[("split", split)] += 1
                counts[("source", root.name)] += 1
                source_count[record["ground_truth"]] += 1
                total_bytes += size
                for key, value in classified["subclasses"].items():
                    if not key.startswith("level_") and value:
                        dimensions[key][str(value)] += 1
            source_summaries.append({
                "name": root.name,
                "root": str(root),
                "profile": profile,
                "sample_count": len(files),
                "labels": dict(source_count),
            })
    os.replace(temporary, output)
    summary = {
        "name": output.stem,
        "manifest": str(output),
        "sample_count": sum(item["sample_count"] for item in source_summaries),
        "total_bytes": total_bytes,
        "sources": source_summaries,
        "labels": {key[1]: value for key, value in counts.items() if key[0] == "label"},
        "splits": {key[1]: value for key, value in counts.items() if key[0] == "split"},
        "group_count": len(group_splits),
        "dimensions": {key: dict(value) for key, value in dimensions.items()},
        "split_policy": "SHA-256(group_id), 80% train / 10% validation / 10% test",
    }
    summary_path = output.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path, help="dataset root directories")
    parser.add_argument("--output", type=Path, required=True, help="output JSONL manifest")
    arguments = parser.parse_args()
    roots = [path.expanduser().resolve() for path in arguments.roots]
    missing = [str(path) for path in roots if not path.is_dir()]
    if missing:
        parser.error(f"dataset roots do not exist: {', '.join(missing)}")
    summary = build(roots, arguments.output.expanduser().resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
