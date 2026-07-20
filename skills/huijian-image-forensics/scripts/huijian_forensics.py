#!/usr/bin/env python3
"""Small standard-library client for the Huijian AI image-forensics API."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from urllib import error, request


TERMINAL_STATES = {"success", "failed", "rejected"}
MAX_IMAGE_BYTES = 25 * 1024 * 1024


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retry_after: float = 0.0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def api_key() -> str:
    value = os.environ.get("HUIJIAN_API_KEY", "").strip()
    if not value:
        raise ApiError("HUIJIAN_API_KEY is not configured")
    return value


def base_url() -> str:
    return os.environ.get("HUIJIAN_API_BASE_URL", "https://www.rrreal.cn").strip().rstrip("/")


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key()}", "Accept": "application/json"}


def parse_response(response) -> dict:
    raw = response.read()
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError("The API returned an invalid JSON response", response.status) from exc


def request_json(req: request.Request, timeout: float) -> dict:
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return parse_response(response)
    except error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
            message = payload.get("error", {}).get("message") or payload.get("message") or str(exc)
        except (UnicodeDecodeError, json.JSONDecodeError):
            message = str(exc)
        raise ApiError(message, exc.code, retry_after_seconds(exc.headers.get("Retry-After"))) from exc
    except error.URLError as exc:
        raise ApiError(f"Network error: {exc.reason}") from exc


def multipart_body(image_path: Path, mode: str) -> tuple[bytes, str]:
    boundary = f"huijian-{secrets.token_hex(16)}"
    mime = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    safe_name = image_path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    chunks = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"mode\"\r\n\r\n{mode}\r\n".encode(),
        (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"image\"; filename=\"{safe_name}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n"
        ).encode(),
        image_path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), boundary


def retry_after_seconds(value: str | None, default: float = 2.0) -> float:
    try:
        seconds = float((value or "").strip())
    except (TypeError, ValueError):
        seconds = default
    return max(0.2, min(seconds, 30.0))


def create_task(image_path: Path, mode: str, idempotency_key: str | None, timeout: float) -> dict:
    body, boundary = multipart_body(image_path, mode)
    headers = auth_headers()
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = request.Request(
        f"{base_url()}/api/openapi/v1/image-detections",
        data=body,
        headers=headers,
        method="POST",
    )
    return request_json(req, timeout)


def get_task(task: dict, timeout: float) -> dict:
    path = task.get("links", {}).get("self")
    if not path:
        raise ApiError("Task response does not contain links.self")
    url = path if str(path).startswith("http") else f"{base_url()}{path}"
    return request_json(request.Request(url, headers=auth_headers(), method="GET"), timeout)


def wait_for_task(task: dict, timeout: float, poll_interval: float) -> dict:
    deadline = time.monotonic() + timeout
    delay = max(0.5, poll_interval)
    while task.get("status") not in TERMINAL_STATES:
        if time.monotonic() >= deadline:
            raise ApiError(f"Timed out while waiting for task {task.get('id', '')}")
        time.sleep(delay)
        try:
            task = get_task(task, min(30.0, timeout))
        except ApiError as exc:
            if exc.status != 429:
                raise
            time.sleep(max(delay, exc.retry_after or 2.0))
            delay = min(5.0, delay * 1.5)
            continue
        delay = min(5.0, delay * 1.15)
    return task


def download_report(task: dict, destination: Path, timeout: float) -> None:
    path = task.get("links", {}).get("report")
    if not path:
        raise ApiError("Task response does not contain links.report")
    url = path if str(path).startswith("http") else f"{base_url()}{path}"
    req = request.Request(url, headers=auth_headers(), method="GET")
    deadline = time.monotonic() + timeout
    while True:
        try:
            with request.urlopen(req, timeout=min(30.0, timeout)) as response:
                data = response.read()
            break
        except error.HTTPError as exc:
            if exc.code != 429 or time.monotonic() >= deadline:
                raise ApiError(f"Report download failed with HTTP {exc.code}", exc.code) from exc
            time.sleep(retry_after_seconds(exc.headers.get("Retry-After")))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def detect(args: argparse.Namespace) -> int:
    image_path = Path(args.image).expanduser().resolve()
    if not image_path.is_file():
        raise ApiError(f"Image does not exist: {image_path}")
    if image_path.stat().st_size > MAX_IMAGE_BYTES:
        raise ApiError("Image exceeds the 25 MB API limit")
    idempotency_key = args.idempotency_key or str(uuid.uuid4())
    task = create_task(image_path, args.mode, idempotency_key, args.request_timeout)
    if not args.no_wait:
        task = wait_for_task(task, args.timeout, args.poll_interval)
    if args.report:
        if task.get("status") != "success":
            raise ApiError("A report can only be downloaded after a successful task")
        download_report(task, Path(args.report).expanduser().resolve(), args.request_timeout)
    print(json.dumps(task, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if task.get("status") == "success" or args.no_wait else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Call the Huijian AI image-forensics API")
    commands = root.add_subparsers(dest="command", required=True)
    command = commands.add_parser("detect", help="Create and optionally wait for an image detection")
    command.add_argument("image")
    command.add_argument("--mode", choices=("fast", "swarm"), default="fast")
    command.add_argument(
        "--idempotency-key",
        help="Stable key for retrying the same request; defaults to a UUID for this invocation",
    )
    command.add_argument("--no-wait", action="store_true")
    command.add_argument("--report")
    command.add_argument("--timeout", type=float, default=240.0)
    command.add_argument("--request-timeout", type=float, default=30.0)
    command.add_argument("--poll-interval", type=float, default=1.5)
    command.add_argument("--pretty", action="store_true")
    command.set_defaults(handler=detect)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.handler(args)
    except ApiError as exc:
        suffix = f" (HTTP {exc.status})" if exc.status else ""
        print(f"error: {exc}{suffix}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
