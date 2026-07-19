#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import mimetypes
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import requests


USER_AGENT = "python-requests RealGuard-Capacity-Test/1.0"


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def summarize(results: list[dict[str, Any]], *, wall_seconds: float) -> dict[str, Any]:
    succeeded = [result for result in results if result.get("status") == "success"]
    latencies = [float(result["latencySeconds"]) for result in succeeded]
    errors: dict[str, int] = {}
    for result in results:
        if result.get("status") == "success":
            continue
        error = str(result.get("error") or "unknown")
        errors[error] = errors.get(error, 0) + 1
    attempted = len(results)
    return {
        "attempted": attempted,
        "succeeded": len(succeeded),
        "failed": attempted - len(succeeded),
        "errorRate": round((attempted - len(succeeded)) / attempted, 4) if attempted else 0.0,
        "wallSeconds": round(wall_seconds, 3),
        "throughputPerSecond": round(len(succeeded) / wall_seconds, 4) if wall_seconds > 0 else 0.0,
        "latencySeconds": {
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
            "p50": round(percentile(latencies, 0.50), 3) if latencies else None,
            "p95": round(percentile(latencies, 0.95), 3) if latencies else None,
            "p99": round(percentile(latencies, 0.99), 3) if latencies else None,
            "max": round(max(latencies), 3) if latencies else None,
        },
        "errors": errors,
    }


def _auth_read_once(index: int, args: argparse.Namespace) -> dict[str, Any]:
    phone = os.environ.get(args.phone_env, "").strip()
    password = os.environ.get(args.password_env, "")
    started = time.monotonic()
    try:
        with requests.Session() as session:
            session.headers.update({"User-Agent": USER_AGENT})
            login = session.post(
                f"{args.base_url}/api/login/password",
                json={"phone": phone, "secret": password, "accepted_terms": True},
                timeout=args.request_timeout,
            )
            if login.status_code != 200:
                return {
                    "index": index,
                    "status": "failed",
                    "error": f"login_http_{login.status_code}",
                    "latencySeconds": time.monotonic() - started,
                }
            history = session.get(
                f"{args.base_url}/api/history/image-detections",
                timeout=args.request_timeout,
            )
            if history.status_code != 200:
                return {
                    "index": index,
                    "status": "failed",
                    "error": f"history_http_{history.status_code}",
                    "latencySeconds": time.monotonic() - started,
                }
            return {
                "index": index,
                "status": "success",
                "latencySeconds": time.monotonic() - started,
            }
    except requests.RequestException as exc:
        return {
            "index": index,
            "status": "failed",
            "error": type(exc).__name__,
            "latencySeconds": time.monotonic() - started,
        }


def _detect_once(
    index: int,
    args: argparse.Namespace,
    image_bytes: bytes,
    image_name: str,
    mime_type: str,
) -> dict[str, Any]:
    api_key = os.environ.get(args.api_key_env, "").strip()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Idempotency-Key": f"capacity-{args.run_id}-{args.mode}-{index}",
        "User-Agent": USER_AGENT,
    }
    started = time.monotonic()
    try:
        submit = requests.post(
            f"{args.base_url}/api/openapi/v1/image-detections",
            headers=headers,
            data={"mode": args.mode},
            files={"image": (image_name, image_bytes, mime_type)},
            timeout=args.request_timeout,
        )
        if submit.status_code not in {200, 202}:
            return {
                "index": index,
                "status": "failed",
                "error": f"submit_http_{submit.status_code}",
                "latencySeconds": time.monotonic() - started,
            }
        task = submit.json()
        task_id = str(task.get("id") or "")
        if not task_id:
            return {
                "index": index,
                "status": "failed",
                "error": "submit_missing_task_id",
                "latencySeconds": time.monotonic() - started,
            }
        deadline = started + args.task_timeout
        terminal = task
        while time.monotonic() < deadline:
            status = str(terminal.get("status") or "")
            if status in {"success", "failed", "rejected"}:
                break
            time.sleep(args.poll_interval)
            response = requests.get(
                f"{args.base_url}/api/openapi/v1/image-detections/{task_id}",
                headers=headers,
                timeout=args.request_timeout,
            )
            if response.status_code != 200:
                return {
                    "index": index,
                    "taskId": task_id,
                    "status": "failed",
                    "error": f"poll_http_{response.status_code}",
                    "latencySeconds": time.monotonic() - started,
                }
            terminal = response.json()
        else:
            return {
                "index": index,
                "taskId": task_id,
                "status": "failed",
                "error": "task_timeout",
                "latencySeconds": time.monotonic() - started,
            }
        if terminal.get("status") != "success":
            return {
                "index": index,
                "taskId": task_id,
                "status": "failed",
                "error": f"task_{terminal.get('status') or 'unknown'}",
                "message": terminal.get("error"),
                "latencySeconds": time.monotonic() - started,
            }
        report_bytes = 0
        if args.download_report:
            report = requests.get(
                f"{args.base_url}/api/openapi/v1/image-detections/{task_id}/report",
                headers=headers,
                timeout=args.request_timeout,
            )
            if report.status_code != 200 or not report.content.startswith(b"%PDF"):
                return {
                    "index": index,
                    "taskId": task_id,
                    "status": "failed",
                    "error": f"report_http_{report.status_code}",
                    "latencySeconds": time.monotonic() - started,
                }
            report_bytes = len(report.content)
        return {
            "index": index,
            "taskId": task_id,
            "itemId": (terminal.get("result") or {}).get("itemid"),
            "status": "success",
            "reportBytes": report_bytes,
            "latencySeconds": time.monotonic() - started,
        }
    except (ValueError, requests.RequestException) as exc:
        return {
            "index": index,
            "status": "failed",
            "error": type(exc).__name__,
            "latencySeconds": time.monotonic() - started,
        }


def _validate_environment(args: argparse.Namespace) -> tuple[bytes, str, str] | None:
    if args.scenario == "auth-read":
        if not os.environ.get(args.phone_env) or not os.environ.get(args.password_env):
            raise SystemExit(f"{args.phone_env} and {args.password_env} are required")
        return None
    if not os.environ.get(args.api_key_env):
        raise SystemExit(f"{args.api_key_env} is required")
    image_path = Path(args.image).resolve()
    if not image_path.is_file():
        raise SystemExit(f"image does not exist: {image_path}")
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    return image_path.read_bytes(), image_path.name, mime_type


def main() -> int:
    parser = argparse.ArgumentParser(description="RealGuard authenticated production capacity test")
    parser.add_argument("--scenario", choices=("auth-read", "detect"), required=True)
    parser.add_argument("--base-url", default="https://www.rrreal.cn")
    parser.add_argument("--requests", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--task-timeout", type=float, default=360.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--phone-env", default="HUIJIAN_TEST_PHONE")
    parser.add_argument("--password-env", default="HUIJIAN_TEST_PASSWORD")
    parser.add_argument("--api-key-env", default="HUIJIAN_API_KEY")
    parser.add_argument("--mode", choices=("fast", "swarm"), default="fast")
    parser.add_argument("--image")
    parser.add_argument("--download-report", action="store_true")
    parser.add_argument("--run-id", default=str(int(time.time())))
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.concurrency > args.requests:
        parser.error("requests and concurrency must satisfy 1 <= concurrency <= requests")
    if args.scenario == "detect" and not args.image:
        parser.error("--image is required for detect")
    args.base_url = args.base_url.rstrip("/")
    image = _validate_environment(args)

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        if args.scenario == "auth-read":
            futures = [executor.submit(_auth_read_once, index, args) for index in range(args.requests)]
        else:
            assert image is not None
            futures = [
                executor.submit(_detect_once, index, args, image[0], image[1], image[2])
                for index in range(args.requests)
            ]
        for future in as_completed(futures):
            results.append(future.result())
    wall_seconds = time.monotonic() - started
    payload = {
        "scenario": args.scenario,
        "mode": args.mode if args.scenario == "detect" else None,
        "concurrency": args.concurrency,
        "runId": args.run_id,
        "summary": summarize(results, wall_seconds=wall_seconds),
        "results": sorted(results, key=lambda item: item["index"]),
    }
    rendered = json.dumps(payload, ensure_ascii=True, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if payload["summary"]["failed"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
