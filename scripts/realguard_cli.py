#!/usr/bin/env python3
"""RealGuard CLI for agent-oriented AI content forensics workflows."""

from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib import error, request


DEFAULT_BASE_URL = os.getenv("REALGUARD_CLI_BASE_URL", "http://127.0.0.1:8848")
DEFAULT_API_PREFIX = os.getenv("REALGUARD_CLI_API_PREFIX", "/api")
DEFAULT_TOKEN = os.getenv("REALGUARD_CLI_TOKEN")
STRIPPED_MEDIA_KEYS = {"thumbnail", "preview", "crop"}


class CliError(RuntimeError):
    """User-facing CLI failure."""


def build_url(base_url: str, api_prefix: str, path: str) -> str:
    base = base_url.rstrip("/")
    prefix = "/" + api_prefix.strip("/") if api_prefix else ""
    suffix = "/" + path.lstrip("/")
    return f"{base}{prefix}{suffix}"


def encode_multipart(fields: dict[str, str | None], files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = f"----realguard-{uuid.uuid4().hex}"
    parts: list[bytes] = []

    for name, value in fields.items():
        if value is None:
            continue
        parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    for name, path in files.items():
        if not path.is_file():
            raise CliError(f"File not found: {path}")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{path.name}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                path.read_bytes(),
                b"\r\n",
            ]
        )

    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def request_json(
    method: str,
    url: str,
    *,
    token: str | None,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: float = 60,
) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Jianzhen-Token"] = token
    if content_type:
        headers["Content-Type"] = content_type

    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            text = payload.decode("utf-8", errors="replace")
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"statusCode": resp.status, "body": text}
    except error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            detail: Any = json.loads(payload)
        except json.JSONDecodeError:
            detail = payload
        raise CliError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except error.URLError as exc:
        raise CliError(f"Failed to reach {url}: {exc.reason}") from exc


def strip_large_media(value: Any) -> Any:
    if isinstance(value, dict):
        stripped: dict[str, Any] = {}
        for key, item in value.items():
            if key in STRIPPED_MEDIA_KEYS and isinstance(item, str) and item.startswith("data:"):
                stripped[key] = "[stripped data URI; rerun with --raw to include]"
            else:
                stripped[key] = strip_large_media(item)
        return stripped
    if isinstance(value, list):
        return [strip_large_media(item) for item in value]
    return value


def confidence_percent(confidence: Any) -> int | None:
    try:
        return round(float(confidence) * 100)
    except (TypeError, ValueError):
        return None


def build_agent_summary(result: dict[str, Any]) -> dict[str, Any]:
    synthid = result.get("synthid") if isinstance(result.get("synthid"), dict) else {}
    watermark = (
        result.get("visibleWatermark") if isinstance(result.get("visibleWatermark"), dict) else {}
    )
    explanation = result.get("explanation") if isinstance(result.get("explanation"), dict) else {}

    summary = {
        "verdict": result.get("verdict"),
        "confidence": result.get("confidence"),
        "confidencePercent": confidence_percent(result.get("confidence")),
        "reportId": result.get("reportId"),
        "taskId": result.get("taskId"),
        "source": result.get("source"),
        "modelVersion": result.get("modelVersion"),
        "cacheVersion": result.get("cacheVersion"),
        "cacheHit": result.get("cacheHit"),
        "topReasons": explanation.get("reasons"),
        "evidenceFlags": {
            "synthidDetected": synthid.get("detected"),
            "visibleWatermarkDetected": watermark.get("detected"),
            "visibleWatermarkHits": len(watermark.get("hits") or []),
        },
        "disclaimer": result.get("disclaimer"),
    }
    return {key: value for key, value in summary.items() if value is not None}


def compact_result(result: Any) -> Any:
    compact = strip_large_media(copy.deepcopy(result))
    if isinstance(compact, dict) and ("verdict" in compact or "reportId" in compact):
        compact["agentSummary"] = build_agent_summary(compact)
    return compact


def write_json(value: Any, *, pretty: bool) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def output_json(value: Any, args: argparse.Namespace) -> None:
    rendered = write_json(value, pretty=args.pretty)
    print(rendered)
    save_path = getattr(args, "save_json", None)
    if save_path:
        Path(save_path).write_text(rendered + "\n", encoding="utf-8")


def request_upload(args: argparse.Namespace, endpoint: str, fields: dict[str, str | None]) -> Any:
    body, content_type = encode_multipart(fields, {"file": Path(args.file)})
    url = build_url(args.base_url, args.api_prefix, endpoint)
    result = request_json(
        "POST",
        url,
        token=args.token,
        body=body,
        content_type=content_type,
        timeout=args.timeout,
    )
    return result if args.raw else compact_result(result)


def cmd_health(args: argparse.Namespace) -> int:
    url = build_url(args.base_url, args.api_prefix, "/health")
    output_json(request_json("GET", url, token=args.token, timeout=args.timeout), args)
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    result = request_upload(args, "/detect", {"fileType": args.file_type})
    output_json(result, args)
    return 0


def cmd_forensics(args: argparse.Namespace) -> int:
    result = request_upload(args, "/forensics", {})
    output_json(result, args)
    return 0


def cmd_provenance(args: argparse.Namespace) -> int:
    result = request_upload(args, "/provenance", {})
    output_json(result, args)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    url = build_url(args.base_url, args.api_prefix, f"/report/{args.report_id}/download")
    headers = {}
    if args.token:
        headers["X-Jianzhen-Token"] = args.token
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=args.timeout) as resp:
            payload = resp.read()
    except error.HTTPError as exc:
        raise CliError(f"HTTP {exc.code} from {url}: {exc.read().decode('utf-8', 'replace')}") from exc
    except error.URLError as exc:
        raise CliError(f"Failed to reach {url}: {exc.reason}") from exc

    output = Path(args.output) if args.output else Path(f"{args.report_id}.pdf")
    output.write_bytes(payload)
    print(str(output))
    return 0


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API host base URL")
    parser.add_argument("--api-prefix", default=DEFAULT_API_PREFIX, help="API prefix, e.g. /api or /v2-api")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="API token or REALGUARD_CLI_TOKEN")
    parser.add_argument("--timeout", type=float, default=60, help="HTTP timeout in seconds")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run RealGuard/Jianzhen AI-content detection from scripts or agents."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health", help="Check API health")
    add_common_flags(health)
    health.set_defaults(func=cmd_health)

    detect = subparsers.add_parser("detect", help="Detect AIGC/deepfake/tampering signals")
    detect.add_argument("file", help="File to inspect")
    detect.add_argument("--file-type", choices=["image", "video", "audio", "document"], help="Override file type")
    detect.add_argument("--raw", action="store_true", help="Keep full API payload including data URIs")
    detect.add_argument("--save-json", help="Write JSON output to this path")
    add_common_flags(detect)
    detect.set_defaults(func=cmd_detect)

    forensics = subparsers.add_parser("forensics", help="Run image forensic analysis")
    forensics.add_argument("file", help="Image to inspect")
    forensics.add_argument("--raw", action="store_true", help="Keep full API payload")
    forensics.add_argument("--save-json", help="Write JSON output to this path")
    add_common_flags(forensics)
    forensics.set_defaults(func=cmd_forensics)

    provenance = subparsers.add_parser("provenance", help="Inspect image provenance/C2PA metadata")
    provenance.add_argument("file", help="Image to inspect")
    provenance.add_argument("--raw", action="store_true", help="Keep full API payload")
    provenance.add_argument("--save-json", help="Write JSON output to this path")
    add_common_flags(provenance)
    provenance.set_defaults(func=cmd_provenance)

    report = subparsers.add_parser("report", help="Download a PDF report")
    report.add_argument("report_id", help="Report ID returned by detect")
    report.add_argument("--output", help="Output PDF path")
    add_common_flags(report)
    report.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CliError as exc:
        print(f"realguard_cli: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
