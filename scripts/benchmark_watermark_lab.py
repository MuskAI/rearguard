#!/usr/bin/env python3
"""Batch benchmark the standalone explicit-watermark lab with public fixtures."""
from __future__ import annotations

import argparse
import json
import mimetypes
import statistics
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PLATFORM_CAPTURES = {
    "gemini_capture/captures": "Google Gemini",
    "doubao_capture/captures": "豆包",
    "jimeng_capture/captures": "即梦AI",
    "samsung_capture/captures": "Samsung Galaxy AI",
}


@dataclass(frozen=True)
class Case:
    path: Path
    expected_platform: str | None
    group: str


def discover_cases(root: Path) -> list[Case]:
    cases: list[Case] = []
    for relative, platform in PLATFORM_CAPTURES.items():
        for path in sorted((root / relative).glob("*")):
            if path.is_file():
                cases.append(Case(path, platform, "real_platform_capture"))

    doubao_sample = root / "samples" / "doubao-1.png"
    if doubao_sample.exists():
        cases.append(Case(doubao_sample, "豆包", "real_platform_output"))

    synthid_root = root / "synthid_corpus" / "images"
    for path in sorted((synthid_root / "neg").glob("*")):
        if path.is_file():
            cases.append(Case(path, None, "verified_camera_negative"))
    for path in sorted((synthid_root / "cleaned").glob("*")):
        if path.is_file():
            cases.append(Case(path, None, "watermark_removed_negative"))

    manifest_path = root / "synthid_corpus" / "manifest.csv"
    if manifest_path.exists():
        import csv

        with manifest_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("label") != "pos" or "Gemini" not in (row.get("source") or ""):
                    continue
                path = synthid_root / "pos" / str(row.get("filename") or "")
                if path.exists():
                    cases.append(Case(path, "Google Gemini", "real_gemini_output"))

    for path in sorted((root / "samples").glob("*")):
        if not path.is_file() or path.name == "doubao-1.png" or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        cases.append(Case(path, None, "non_visible_ai_output"))
    for path in sorted((root / "qwen_in").glob("openai_*_original.png")):
        cases.append(Case(path, None, "non_visible_ai_output"))
    for path in sorted((root / "qwen_in").glob("gemini_*_original.png")):
        cases.append(Case(path, "Google Gemini", "real_gemini_output"))

    deduplicated: dict[Path, Case] = {case.path.resolve(): case for case in cases}
    return list(deduplicated.values())


def multipart(path: Path) -> tuple[bytes, str]:
    boundary = f"----huijian-{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
    body.extend(path.read_bytes())
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return bytes(body), boundary


def analyze(case: Case, endpoint: str, timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        body, boundary = multipart(case.path)
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
        result = payload.get("result") or {}
        explicit = result.get("explicitWatermark") or {}
        verdict = explicit.get("aiWatermarkVerdict") or {}
        return {
            "file": str(case.path),
            "name": case.path.name,
            "group": case.group,
            "expectedPlatform": case.expected_platform,
            "expectedPositive": case.expected_platform is not None,
            "status": "ok",
            "verdict": verdict.get("verdict", "inconclusive"),
            "confidence": float(verdict.get("confidence") or 0),
            "predictedPlatform": explicit.get("sourcePlatform"),
            "hitCount": len(explicit.get("hits") or []),
            "serviceElapsedMs": result.get("elapsedMs"),
            "wallElapsedMs": round((time.perf_counter() - started) * 1000),
            "reason": verdict.get("reason"),
        }
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
        return {
            "file": str(case.path),
            "name": case.path.name,
            "group": case.group,
            "expectedPlatform": case.expected_platform,
            "expectedPositive": case.expected_platform is not None,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "wallElapsedMs": round((time.perf_counter() - started) * 1000),
        }


def rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [record for record in records if record.get("status") == "ok"]
    positives = [record for record in completed if record["expectedPositive"]]
    negatives = [record for record in completed if not record["expectedPositive"]]
    true_positives = [record for record in positives if record.get("verdict") == "yes"]
    false_positives = [record for record in negatives if record.get("verdict") == "yes"]
    platform_correct = [
        record for record in positives
        if record.get("predictedPlatform") == record.get("expectedPlatform")
    ]
    latencies = sorted(float(record["wallElapsedMs"]) for record in completed)
    per_group: dict[str, Any] = {}
    for group in sorted({str(record["group"]) for record in completed}):
        items = [record for record in completed if record["group"] == group]
        expected_positive = [record for record in items if record["expectedPositive"]]
        predicted_positive = [record for record in items if record.get("verdict") == "yes"]
        per_group[group] = {
            "count": len(items),
            "expectedPositive": len(expected_positive),
            "predictedPositive": len(predicted_positive),
            "positiveRate": rate(len(predicted_positive), len(items)),
        }
    return {
        "total": len(records),
        "completed": len(completed),
        "errors": len(records) - len(completed),
        "positiveCount": len(positives),
        "negativeCount": len(negatives),
        "recall": rate(len(true_positives), len(positives)),
        "falsePositiveRate": rate(len(false_positives), len(negatives)),
        "platformAccuracy": rate(len(platform_correct), len(positives)),
        "inconclusiveCount": sum(record.get("verdict") == "inconclusive" for record in completed),
        "latencyMs": {
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
            "p50": round(statistics.median(latencies), 1) if latencies else None,
            "p95": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 1) if latencies else None,
            "max": round(max(latencies), 1) if latencies else None,
        },
        "perGroup": per_group,
        "falsePositives": [record["name"] for record in false_positives],
        "missedPositives": [record["name"] for record in positives if record.get("verdict") != "yes"],
        "wrongPlatforms": [
            {"name": record["name"], "expected": record["expectedPlatform"], "predicted": record.get("predictedPlatform")}
            for record in positives
            if record.get("predictedPlatform") != record.get("expectedPlatform")
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument("--endpoint", default="http://10.1.20.66:5070/api/analyze")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--extra-negative-dir", action="append", type=Path, default=[])
    args = parser.parse_args()
    cases = discover_cases(args.dataset_root)
    for directory in args.extra_negative_dir:
        for path in sorted(directory.glob("*")):
            if path.is_file():
                cases.append(Case(path, None, "converted_camera_negative"))
    if not cases:
        raise SystemExit("no benchmark cases discovered")
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as executor:
        futures = {executor.submit(analyze, case, args.endpoint, args.timeout): case for case in cases}
        for index, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            records.append(record)
            print(f"[{index:02d}/{len(cases)}] {record['status']:5s} {record['name']}")
    records.sort(key=lambda record: (record["group"], record["name"]))
    result = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "endpoint": args.endpoint,
        "datasetRoot": str(args.dataset_root),
        "summary": summarize(records),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
