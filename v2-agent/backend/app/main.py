"""慧鉴 AI 鉴伪工作台后端。

图像和可提取正文的文档仅返回真实模型结果；未部署能力与模型故障会明确失败。
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import hashlib
import hmac
import io
import ipaddress
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib import error as urlerror
from urllib.parse import quote
from urllib import request as urlrequest

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response

from . import (
    detector,
    evidence_probability,
    provenance,
    provenance_precheck,
    reporting,
    storage,
    synthid_detector,
    unified_forensics,
    visible_watermark_detector,
    watermark_verdict,
    watermark_yolo,
)

logger = logging.getLogger(__name__)
ACCESS_TOKEN = os.getenv("JIANZHEN_ACCESS_TOKEN", "").strip()
ADMIN_ACCESS_TOKEN = os.getenv("JIANZHEN_ADMIN_ACCESS_TOKEN", "").strip()
MAX_UPLOAD_BYTES = int(os.getenv("JIANZHEN_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
SESSION_AUTH_URL = (
    os.getenv("JIANZHEN_SESSION_AUTH_URL")
    or os.getenv("REALGUARD_SESSION_AUTH_URL")
    or "http://127.0.0.1:5000/api/me"
).strip()
DEVELOPER_AUTH_URL = os.getenv("JIANZHEN_DEVELOPER_AUTH_URL", "http://127.0.0.1:5000/api/developer/keys/verify").strip()
DEVELOPER_AUTH_SECRET = (
    os.getenv("REALGUARD_DEVELOPER_AUTH_SECRET")
    or os.getenv("JIANZHEN_DEVELOPER_AUTH_SECRET")
    or ""
).strip()
ALLOW_ANONYMOUS_DETECT = str(os.getenv("JIANZHEN_ALLOW_ANONYMOUS_DETECT", "0")).lower() in {"1", "true", "yes"}
ALLOW_DIRECT_DEVELOPER_KEYS = str(os.getenv("JIANZHEN_ALLOW_DIRECT_DEVELOPER_KEYS", "0")).lower() in {"1", "true", "yes"}
DEVELOPER_AUTH_CONFIGURED = bool(DEVELOPER_AUTH_URL and DEVELOPER_AUTH_SECRET)
REPORT_SHARE_SECRET = os.getenv("JIANZHEN_REPORT_SHARE_SECRET", "").strip()
PUBLIC_BASE_URL = os.getenv("JIANZHEN_PUBLIC_BASE_URL", "").strip().rstrip("/")
REPORT_SHARE_DEFAULT_SECONDS = int(os.getenv("JIANZHEN_REPORT_SHARE_DEFAULT_SECONDS", str(7 * 24 * 60 * 60)))
REPORT_SHARE_MAX_SECONDS = int(os.getenv("JIANZHEN_REPORT_SHARE_MAX_SECONDS", str(30 * 24 * 60 * 60)))
TRUSTED_PROXY_NETWORKS = tuple(
    ipaddress.ip_network(value.strip(), strict=False)
    for value in os.getenv("JIANZHEN_TRUSTED_PROXY_CIDRS", "127.0.0.0/8,::1/128").split(",")
    if value.strip()
)
FORENSICS_CACHE_MAX_AGE_SECONDS = int(os.getenv("JIANZHEN_FORENSICS_CACHE_MAX_AGE_SECONDS", str(7 * 24 * 60 * 60)))
FORENSICS_MAX_SOURCE_PIXELS = int(os.getenv("JIANZHEN_FORENSICS_MAX_SOURCE_PIXELS", "24000000"))
FORENSICS_MAX_CONCURRENCY = max(1, int(os.getenv("JIANZHEN_FORENSICS_MAX_CONCURRENCY", "2")))
_FORENSICS_SEMAPHORE = asyncio.Semaphore(FORENSICS_MAX_CONCURRENCY)
FORENSICS_QUEUE_TIMEOUT_SECONDS = max(
    0.1,
    float(os.getenv("JIANZHEN_FORENSICS_QUEUE_TIMEOUT_SECONDS", "15")),
)
DETECTION_MAX_CONCURRENCY = max(1, int(os.getenv("JIANZHEN_DETECTION_MAX_CONCURRENCY", "4")))
DETECTION_QUEUE_TIMEOUT_SECONDS = max(
    0.1,
    float(os.getenv("JIANZHEN_DETECTION_QUEUE_TIMEOUT_SECONDS", "15")),
)
_DETECTION_SEMAPHORE = asyncio.Semaphore(DETECTION_MAX_CONCURRENCY)
TELEMETRY_QUEUE_MAX = max(64, int(os.getenv("JIANZHEN_TELEMETRY_QUEUE_MAX", "2048")))
TELEMETRY_PRUNE_INTERVAL_SECONDS = max(
    300,
    int(os.getenv("JIANZHEN_TELEMETRY_PRUNE_INTERVAL_SECONDS", "21600")),
)
_TELEMETRY_QUEUE: asyncio.Queue | None = None
_TELEMETRY_WORKER: asyncio.Task | None = None
_TELEMETRY_MAINTENANCE: asyncio.Task | None = None


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    await start_telemetry_worker()
    try:
        yield
    finally:
        await stop_telemetry_worker()


app = FastAPI(title="慧鉴 AI 鉴伪工作台", version="0.3.0", lifespan=_app_lifespan)
PROTECTED_ENDPOINTS = [
    "/api/admin/health",
    "/api/history",
    "/api/history/{task_id}",
    "/api/history/{task_id}/artifacts",
    "/api/report/{report_id}",
    "/api/report/{report_id}/download",
    "/api/report/{report_id}/export",
    "/api/report/{report_id}/share",
    "/api/report/{report_id}/share/{share_id}",
    "/api/metrics",
]
DEVELOPER_PROTECTED_ENDPOINTS = [
    "/api/detect",
    "/api/forensics",
    "/api/provenance",
]


def _allowed_origins() -> list[str]:
    raw = os.getenv("JIANZHEN_ALLOWED_ORIGINS", "").strip()
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://124.221.92.85",
        "https://rrreal.cn",
        "https://www.rrreal.cn",
        "https://realguard.cn",
        "https://www.realguard.cn",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _enqueue_telemetry(func, *args, **kwargs) -> bool:
    queue = _TELEMETRY_QUEUE
    if queue is None:
        # ASGI servers normally run lifespan startup. Keep embedded/test hosts
        # compatible without ever allowing telemetry failure to replace the
        # business response.
        try:
            func(*args, **kwargs)
            return True
        except Exception:
            logger.exception("telemetry persistence failed without lifespan worker")
            return False
    try:
        queue.put_nowait((func, args, kwargs))
        return True
    except asyncio.QueueFull:
        logger.warning("dropping telemetry event because the queue is full")
        return False


async def _telemetry_worker() -> None:
    assert _TELEMETRY_QUEUE is not None
    while True:
        func, args, kwargs = await _TELEMETRY_QUEUE.get()
        try:
            await run_in_threadpool(func, *args, **kwargs)
        except Exception:
            logger.exception("telemetry persistence failed")
        finally:
            _TELEMETRY_QUEUE.task_done()


async def _telemetry_maintenance() -> None:
    _enqueue_telemetry(storage.prune_telemetry)
    while True:
        await asyncio.sleep(TELEMETRY_PRUNE_INTERVAL_SECONDS)
        _enqueue_telemetry(storage.prune_telemetry)


async def start_telemetry_worker() -> None:
    global _TELEMETRY_QUEUE, _TELEMETRY_WORKER, _TELEMETRY_MAINTENANCE
    _TELEMETRY_QUEUE = asyncio.Queue(maxsize=TELEMETRY_QUEUE_MAX)
    _TELEMETRY_WORKER = asyncio.create_task(_telemetry_worker())
    _TELEMETRY_MAINTENANCE = asyncio.create_task(_telemetry_maintenance())


async def stop_telemetry_worker() -> None:
    global _TELEMETRY_QUEUE, _TELEMETRY_WORKER, _TELEMETRY_MAINTENANCE
    if _TELEMETRY_MAINTENANCE:
        _TELEMETRY_MAINTENANCE.cancel()
    if _TELEMETRY_QUEUE:
        try:
            await asyncio.wait_for(_TELEMETRY_QUEUE.join(), timeout=2)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    if _TELEMETRY_WORKER:
        _TELEMETRY_WORKER.cancel()
    _TELEMETRY_QUEUE = None
    _TELEMETRY_WORKER = None
    _TELEMETRY_MAINTENANCE = None


@app.middleware("http")
async def prevent_sensitive_response_caching(request: Request, call_next):
    response = await call_next(request)
    sensitive_prefixes = (
        "/api/history",
        "/api/report",
        "/api/admin",
        "/api/metrics",
    )
    if request.url.path.startswith(sensitive_prefixes):
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Vary"] = "Cookie, Authorization"
    return response

EXT_TO_TYPE = {
    "jpg": "image", "jpeg": "image", "png": "image", "webp": "image", "bmp": "image", "gif": "image",
    "mp4": "video", "mov": "video", "avi": "video", "mkv": "video", "webm": "video",
    "mp3": "audio", "wav": "audio", "m4a": "audio", "flac": "audio", "aac": "audio",
    "txt": "document", "pdf": "document", "doc": "document", "docx": "document", "md": "document",
    "csv": "document", "json": "document", "log": "document",
}

def _detect_type(filename: str, declared: str | None) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    inferred = EXT_TO_TYPE.get(ext)
    if inferred is None:
        if not ext and declared in detector.DIMENSIONS_BY_TYPE:
            return declared
        raise HTTPException(status_code=415, detail="不支持的文件格式")
    if declared in detector.DIMENSIONS_BY_TYPE and declared != inferred:
        raise HTTPException(status_code=422, detail="文件类型与扩展名不一致")
    return inferred


def _trusted_proxy_request(request: Request) -> bool:
    if not request.client:
        return False
    try:
        peer = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    return any(peer in network for network in TRUSTED_PROXY_NETWORKS)


def _client_ip(request: Request) -> str | None:
    if _trusted_proxy_request(request):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            candidate = forwarded.split(",", 1)[0].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            try:
                return str(ipaddress.ip_address(real_ip.strip()))
            except ValueError:
                pass
    return request.client.host if request.client else None


def _request_token(request: Request) -> str:
    bearer = request.headers.get("authorization", "").strip()
    if bearer.lower().startswith("bearer "):
        return bearer[7:].strip()
    return request.headers.get("x-jianzhen-token", "").strip()


def _admin_access_granted(request: Request) -> bool:
    if not request.client:
        return False
    try:
        peer = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    if not peer.is_loopback:
        return False
    if any(
        request.headers.get(name)
        for name in ("x-forwarded-for", "x-real-ip", "x-forwarded-host", "x-forwarded-proto")
    ):
        return False
    return bool(ADMIN_ACCESS_TOKEN) and secrets.compare_digest(_request_token(request), ADMIN_ACCESS_TOKEN)


def _internal_access_granted(request: Request) -> bool:
    return bool(ACCESS_TOKEN) and secrets.compare_digest(_request_token(request), ACCESS_TOKEN)


def _require_admin_access(request: Request) -> dict:
    if _admin_access_granted(request):
        return {"mode": "admin"}
    if _session_access_granted(request) or _request_token(request):
        raise HTTPException(status_code=403, detail="仅管理员可访问该接口")
    raise HTTPException(status_code=401, detail="管理员认证信息缺失")


def _verify_session_user_sync(request: Request) -> dict | None:
    cookie = request.headers.get("cookie", "").strip()
    if not SESSION_AUTH_URL or not cookie:
        return None
    headers = {
        "Accept": "application/json",
        "Cookie": cookie,
        "User-Agent": request.headers.get("user-agent", ""),
        "X-Forwarded-For": _client_ip(request) or "",
    }
    session_request = urlrequest.Request(SESSION_AUTH_URL, headers=headers, method="GET")
    try:
        with urlrequest.urlopen(session_request, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
    except urlerror.HTTPError as exc:
        if exc.code in {401, 403}:
            return None
        return None
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None
    user = data.get("user")
    if data.get("status") != "success" or not isinstance(user, dict):
        return None
    user_id = user.get("Userid") or user.get("userId") or user.get("id") or user.get("phone") or user.get("openid")
    if not user_id:
        return None
    try:
        account_uuid = str(uuid.UUID(str(user.get("account_uuid") or user.get("accountUuid") or "")))
    except (ValueError, TypeError, AttributeError):
        return None
    return {
        "mode": "session",
        "userId": user_id,
        "accountUuid": account_uuid,
        "phone": user.get("phone"),
        "openid": user.get("openid"),
        "username": user.get("username"),
    }


def _session_access_granted(request: Request) -> dict | None:
    return _verify_session_user_sync(request)


def _require_protected_access(request: Request) -> dict:
    if _admin_access_granted(request):
        return {"mode": "admin"}
    actor = _session_access_granted(request)
    if actor:
        return actor
    api_key = _request_developer_key(request)
    if api_key:
        if not DEVELOPER_AUTH_CONFIGURED:
            raise HTTPException(status_code=503, detail="API Key 校验服务未配置")
        return _verify_developer_key_sync(api_key, request)
    raise HTTPException(status_code=401, detail="请先登录慧鉴 AI")


def _request_developer_key(request: Request) -> str:
    bearer = request.headers.get("authorization", "").strip()
    if bearer.lower().startswith("bearer "):
        bearer = bearer[7:].strip()
    else:
        bearer = ""
    legacy = request.headers.get("x-jianzhen-token", "").strip()
    if not legacy.startswith("rg_sk_"):
        legacy = ""
    return (
        request.headers.get("x-realguard-key", "").strip()
        or request.headers.get("x-realguard-api-key", "").strip()
        or request.headers.get("x-api-key", "").strip()
        or bearer
        or legacy
    )


def _verify_developer_key_sync(api_key: str, request: Request) -> dict:
    payload = json.dumps({"api_key": api_key}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-RealGuard-Internal-Secret": DEVELOPER_AUTH_SECRET,
        "X-Forwarded-For": _client_ip(request) or "",
    }
    verification_request = urlrequest.Request(
        DEVELOPER_AUTH_URL,
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urlrequest.urlopen(verification_request, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
    except urlerror.HTTPError as exc:
        if exc.code in {401, 403}:
            raise HTTPException(status_code=503, detail="API Key 内部校验配置无效") from exc
        raise HTTPException(status_code=503, detail="API Key 校验服务不可用") from exc
    except (urlerror.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="API Key 校验服务不可用") from exc

    if not data.get("valid"):
        raise HTTPException(status_code=401, detail="API Key 缺失或无效")
    try:
        account_uuid = str(uuid.UUID(str(data.get("accountUuid") or "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=503, detail="API Key 账号缺少稳定身份标识") from exc
    return {
        "mode": "developer",
        "keyId": data.get("keyId"),
        "userId": data.get("userId"),
        "accountUuid": account_uuid,
        "scopes": data.get("scopes") or [],
    }


async def _require_developer_access(request: Request) -> dict:
    if _admin_access_granted(request):
        return {"mode": "admin"}
    if _internal_access_granted(request):
        return {"mode": "internal"}
    actor = await run_in_threadpool(_session_access_granted, request)
    if actor:
        return actor
    api_key = _request_developer_key(request)
    if api_key:
        if not ALLOW_DIRECT_DEVELOPER_KEYS:
            raise HTTPException(
                status_code=410,
                detail="开发者 API 已迁移到 /api/openapi/v1/image-detections",
            )
        if not DEVELOPER_AUTH_CONFIGURED:
            raise HTTPException(status_code=503, detail="API Key 校验服务未配置")
        return await run_in_threadpool(_verify_developer_key_sync, api_key, request)
    if ALLOW_ANONYMOUS_DETECT:
        return {"mode": "public"}
    raise HTTPException(status_code=401, detail="请先登录慧鉴 AI")


def _forensics_cache_scope(actor: dict) -> str | None:
    if actor.get("accountUuid") is not None:
        raw_scope = f"account:{actor['accountUuid']}"
    elif actor.get("keyId") is not None:
        raw_scope = f"key:{actor['keyId']}"
    elif actor.get("mode") == "admin":
        raw_scope = "admin"
    else:
        return None
    return hashlib.sha256(raw_scope.encode("utf-8")).hexdigest()[:16]


def _require_internal_developer_auth(request: Request) -> None:
    if not DEVELOPER_AUTH_SECRET:
        raise HTTPException(status_code=503, detail="内部开发者接口未配置")
    submitted = request.headers.get("x-realguard-internal-secret", "").strip()
    if not secrets.compare_digest(submitted, DEVELOPER_AUTH_SECRET):
        raise HTTPException(status_code=403, detail="内部鉴权失败")


def _require_owned_item(request: Request, item: dict, *, missing_detail: str) -> dict:
    actor = _require_protected_access(request)
    return _require_actor_owns_item(actor, item, missing_detail=missing_detail)


def _require_actor_owns_item(actor: dict, item: dict, *, missing_detail: str) -> dict:
    if actor.get("mode") == "admin":
        return actor
    item_account_uuid = str(item.get("_developerAccountUuid") or "")
    actor_account_uuid = str(actor.get("accountUuid") or "")
    if (
        item_account_uuid
        and actor_account_uuid
        and secrets.compare_digest(item_account_uuid, actor_account_uuid)
    ):
        return actor
    # Hide object existence from other tenants and from users accessing legacy
    # unowned rows. Null ownership must be repaired explicitly by an admin.
    raise HTTPException(status_code=404, detail=missing_detail)


def _require_matching_history_upload(actor: dict, task_id: str, sha256: str) -> dict:
    item = storage.get_history(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    _require_actor_owns_item(actor, item, missing_detail="记录不存在")
    stored_sha256 = str((item.get("fileMeta") or {}).get("sha256") or "").strip().lower()
    if not stored_sha256 or not secrets.compare_digest(stored_sha256, sha256.lower()):
        raise HTTPException(status_code=409, detail="上传文件与原检测任务不一致")
    return item


def _require_report_access(request: Request, item: dict) -> dict:
    return _require_owned_item(request, item, missing_detail="报告不存在")


def _require_report_share_access(request: Request, item: dict) -> dict:
    return _require_report_access(request, item)


def _report_share_secret() -> str:
    secret = REPORT_SHARE_SECRET
    if len(secret.encode("utf-8")) < 32 or secret.lower().startswith(("change-", "replace-")):
        raise HTTPException(status_code=503, detail="报告分享签名密钥未安全配置")
    return secret


def _sign_report_share(report_id: str, expires: int) -> str:
    message = f"v1:{report_id}:{expires}".encode("utf-8")
    return hmac.new(_report_share_secret().encode("utf-8"), message, hashlib.sha256).hexdigest()


def _sign_persisted_report_share(share_id: str, report_id: str, expires: int) -> str:
    message = f"v2:{share_id}:{report_id}:{expires}".encode("utf-8")
    return hmac.new(_report_share_secret().encode("utf-8"), message, hashlib.sha256).hexdigest()


def _verify_report_share(report_id: str, expires: int, signature: str) -> None:
    if expires < int(time.time()):
        raise HTTPException(status_code=410, detail="报告分享链接已过期")
    expected = _sign_report_share(report_id, expires)
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=403, detail="报告分享链接签名无效")


def _audit_report_share_access(request: Request, share: dict, outcome: str) -> None:
    storage.record_report_share_access(
        share_id=str(share["shareId"]),
        report_id=str(share["reportId"]),
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        outcome=outcome,
    )


def _validate_persisted_report_share(
    request: Request,
    *,
    share: dict,
    report_id: str,
    expires: int,
    signature: str,
    legacy: bool = False,
) -> None:
    expected = (
        _sign_report_share(report_id, expires)
        if legacy
        else _sign_persisted_report_share(str(share["shareId"]), report_id, expires)
    )
    if not signature or not hmac.compare_digest(signature, expected):
        _audit_report_share_access(request, share, "invalid_signature")
        raise HTTPException(status_code=403, detail="报告分享链接签名无效")
    if str(share.get("reportId") or "") != report_id or int(share.get("expiresAt") or 0) != expires:
        _audit_report_share_access(request, share, "record_mismatch")
        raise HTTPException(status_code=403, detail="报告分享链接与服务端记录不匹配")
    if share.get("revokedAt"):
        _audit_report_share_access(request, share, "revoked")
        raise HTTPException(status_code=410, detail="报告分享链接已撤销")
    if expires < int(time.time()):
        _audit_report_share_access(request, share, "expired")
        raise HTTPException(status_code=410, detail="报告分享链接已过期")


def _request_origin(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    trust_forwarded = _trusted_proxy_request(request)
    untrusted_forwarded = not trust_forwarded and any(
        request.headers.get(name)
        for name in ("x-forwarded-proto", "x-forwarded-host", "x-forwarded-prefix")
    )
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip() if trust_forwarded else ""
    forwarded_host = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip() if trust_forwarded else ""
    proto = forwarded_proto or request.url.scheme
    host = forwarded_host or request.headers.get("host") or request.url.netloc
    origin = f"{proto}://{host}".rstrip("/")
    if origin in _allowed_origins() or (
        not untrusted_forwarded and host.startswith(("testserver", "127.0.0.1:", "localhost:"))
    ):
        return origin
    allowed = _allowed_origins()
    preferred = next((value for value in allowed if value.startswith("https://www.")), "")
    return preferred or (allowed[0] if allowed else "https://www.rrreal.cn")


def _public_api_prefix(request: Request) -> str:
    forwarded_prefix = request.headers.get("x-forwarded-prefix", "").strip() if _trusted_proxy_request(request) else ""
    if forwarded_prefix:
        return "/" + forwarded_prefix.strip("/")
    host = (request.headers.get("host") or request.url.netloc or "").lower()
    if host.startswith("testserver") or host.startswith("127.0.0.1:8848") or host.startswith("localhost:8848"):
        return "/api"
    return os.getenv("JIANZHEN_PUBLIC_API_PREFIX", "/v2-api").strip() or "/v2-api"


def _build_public_report_link(
    request: Request,
    report_id: str,
    expires: int,
    signature: str,
    *,
    share_id: str | None = None,
) -> dict:
    share_query = f"shareId={quote(share_id, safe='')}&" if share_id else ""
    query = f"{share_query}expires={expires}&sig={signature}"
    api_path = f"/api/report/{quote(report_id, safe='')}/public?{query}"
    public_path = f"{_public_api_prefix(request).rstrip('/')}/report/{quote(report_id, safe='')}/public?{query}"
    return {
        "url": f"{_request_origin(request)}{public_path}",
        "publicPath": public_path,
        "apiPath": api_path,
    }


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限")
    return data


async def _acquire_forensics_slot() -> None:
    try:
        await asyncio.wait_for(
            _FORENSICS_SEMAPHORE.acquire(),
            timeout=FORENSICS_QUEUE_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=429,
            detail="取证任务较多，请稍后重试",
            headers={"Retry-After": str(max(1, round(FORENSICS_QUEUE_TIMEOUT_SECONDS)))},
        ) from exc


async def _validate_image_pixels(data: bytes) -> tuple[int, int]:
    try:
        width, height = await run_in_threadpool(detector.image_dimensions, data)
    except Exception as exc:
        if "DecompressionBomb" in type(exc).__name__:
            raise HTTPException(status_code=413, detail="图片像素数量过高，无法安全处理") from exc
        # This preflight only enforces the resource ceiling. The detector keeps
        # ownership of malformed-file validation and its existing error shape.
        return 0, 0
    if width <= 0 or height <= 0:
        return 0, 0
    if width * height > FORENSICS_MAX_SOURCE_PIXELS:
        raise HTTPException(
            status_code=413,
            detail=f"图片像素不能超过 {FORENSICS_MAX_SOURCE_PIXELS}，当前为 {width}x{height}",
        )
    return width, height


def _public_capabilities() -> dict:
    synthid_status = synthid_detector.status()
    watermark_status = visible_watermark_detector.status()
    precheck_status = provenance_precheck.status()
    return {
        "status": "ok",
        "vlmEnabled": bool(detector.API_KEY),
        "accessProtectionEnabled": bool(ACCESS_TOKEN),
        "unifiedLoginEnabled": bool(SESSION_AUTH_URL),
        "sessionAuthEnabled": bool(SESSION_AUTH_URL),
        "capabilities": {
            "image": "available" if detector.API_KEY else "unavailable",
            "document": "limited" if detector.API_KEY else "unavailable",
            "video": "unavailable",
            "audio": "unavailable",
        },
        "synthid": {
            "enabled": bool(synthid_status.get("enabled")),
            "available": bool(synthid_status.get("available")),
            "modelProfiles": synthid_status.get("modelProfiles") or [],
            "profileCount": int(synthid_status.get("profileCount") or 0),
            "officialVerification": False,
        },
        "visibleWatermark": {
            "enabled": bool(watermark_status.get("enabled")),
            "available": bool(watermark_status.get("available")),
        },
        "provenancePrecheck": {
            "configured": bool(precheck_status.get("configured")),
            "available": precheck_status.get("available"),
            "lastElapsedMs": precheck_status.get("lastElapsedMs"),
        },
        "limits": {
            "maxUploadBytes": MAX_UPLOAD_BYTES,
        },
    }


@app.middleware("http")
async def collect_request_metrics(request: Request, call_next) -> Response:
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        _enqueue_telemetry(
            storage.record_event,
            "request",
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            method=request.method,
            path=request.url.path,
            status=status,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )


def _image_data_uri(data: bytes, file_type: str, *, max_side: int, quality: int) -> str | None:
    if file_type != "image":
        return None
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(data)) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in {"RGBA", "LA"} or "transparency" in im.info:
                rgba = im.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                im = background
            else:
                im = im.convert("RGB")
            im.thumbnail((max_side, max_side))
            out = io.BytesIO()
            im.save(out, format="WEBP", quality=quality, method=4)
        import base64

        return "data:image/webp;base64," + base64.b64encode(out.getvalue()).decode()
    except Exception:
        return None


def _usage_int(value) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _token_usage_from_payload(payload: dict | None, *, cache_hit: bool = False) -> dict:
    if cache_hit:
        return {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
    usage = (payload or {}).get("tokenUsage") or {}
    prompt_tokens = _usage_int(usage.get("promptTokens"))
    completion_tokens = _usage_int(usage.get("completionTokens"))
    total_tokens = _usage_int(usage.get("totalTokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "promptTokens": prompt_tokens,
        "completionTokens": completion_tokens,
        "totalTokens": total_tokens,
    }


def _analysis_risk_vector(analysis: dict) -> dict[str, float | None]:
    supplied = analysis.get("riskVector") if isinstance(analysis.get("riskVector"), dict) else {}
    dimensions = {
        str(item.get("key") or ""): item
        for item in analysis.get("dimensions") or []
        if isinstance(item, dict)
    }

    def dimension_score(*keys: str) -> float | None:
        for key in keys:
            if supplied.get(key) is not None:
                return round(min(max(float(supplied[key]), 0.0), 1.0), 4)
            item = dimensions.get(key)
            if item and item.get("score") is not None:
                return round(min(max(float(item["score"]), 0.0), 1.0), 4)
        return None

    generated = analysis.get("aiProbability")
    if generated is None:
        generated = dimension_score("aiGenerated", "aigc", "aigc_image", "aigc_text")
    if generated is None and analysis.get("source") == "provenance":
        generated = analysis.get("confidence")
    return {
        "aiGenerated": None if generated is None else round(min(max(float(generated), 0.0), 1.0), 4),
        "tampered": dimension_score("tampered", "tamper"),
        "deepfake": dimension_score("deepfake"),
    }


def _build_result(
    *,
    filename: str,
    ftype: str,
    data: bytes,
    analysis: dict,
    elapsed_ms: int,
    cache_hit: bool,
    sha256: str,
    thumbnail: str | None,
    preview: str | None,
    actor: dict | None = None,
    token_usage: dict | None = None,
    provenance_report: dict | None = None,
    provenance_precheck_report: dict | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y%m%d")
    seq = storage.next_sequence(day)
    task_id = f"rj-{day}-{seq:04d}"
    report_id = f"RJ-RPT-{day}-{seq:04d}"
    risk_vector = _analysis_risk_vector(analysis)

    result = {
        "taskId": task_id,
        "reportId": report_id,
        "createdAt": now.isoformat(),
        "fileMeta": {
            "name": filename,
            "type": ftype,
            "size": f"{len(data) / 1024:.1f}KB",
            "resolution": detector.image_size(data) if ftype == "image" else None,
            "sha256": sha256,
            "thumbnail": thumbnail,
            "preview": preview,
        },
        "verdict": analysis["verdict"],
        "confidence": analysis["confidence"],
        "riskScore": analysis.get("riskScore", analysis["confidence"]),
        "aiProbability": risk_vector.get("aiGenerated"),
        "riskVector": risk_vector,
        "modelVersion": analysis["modelVersion"],
        "source": analysis["source"],
        "cacheVersion": storage.ANALYSIS_CACHE_VERSION,
        "cacheHit": cache_hit,
        "elapsedMs": elapsed_ms,
        "tokenUsage": token_usage or {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0},
        "dimensions": analysis["dimensions"],
        "regions": analysis["regions"],
        "explanation": analysis["explanation"],
        "synthid": analysis.get("synthid"),
        "visibleWatermark": analysis.get("visibleWatermark"),
        "watermarkVerdictOverride": analysis.get("watermarkVerdictOverride"),
        "probabilityModel": analysis.get("probabilityModel"),
        "captureEvidence": (provenance_report or {}).get("captureEvidence"),
        "provenancePrecheck": provenance_precheck_report or analysis.get("provenancePrecheck"),
        "disclaimer": "本结果由自动化来源证据与检测模型生成，仅供专业复核参考，不构成司法鉴定结论。",
    }
    if provenance_report is not None:
        result["provenance"] = provenance_report
    watermark_verdict.apply(result, result.get("visibleWatermark"))
    result["unifiedForensics"] = unified_forensics.build(result)
    storage.put_history(result, sha256=sha256, file_size=len(data), thumbnail=thumbnail, actor=actor)
    return result


def _strip_internal_history_fields(item: dict) -> dict:
    clean = dict(item)
    clean.pop("_developerUserId", None)
    clean.pop("_developerAccountUuid", None)
    clean.pop("_developerKeyId", None)
    watermark_verdict.apply(clean, clean.get("visibleWatermark"))
    return clean


@app.get("/api/health")
def health() -> dict:
    return _public_capabilities()


@app.get("/api/ready")
def ready() -> Response:
    capabilities = _public_capabilities()
    storage_status = storage.healthcheck()
    checks = {
        "imageModelConfigured": capabilities["capabilities"]["image"] == "available",
        "storageAvailable": bool(storage_status.get("available")),
        "accessProtectionConfigured": bool(
            ACCESS_TOKEN or SESSION_AUTH_URL or DEVELOPER_AUTH_CONFIGURED
        ),
    }
    is_ready = all(checks.values())
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={
            "status": "ready" if is_ready else "not_ready",
            "checks": checks,
            "capabilities": capabilities["capabilities"],
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/admin/health")
def admin_health(request: Request) -> dict:
    _require_admin_access(request)
    return {
        **_public_capabilities(),
        "model": detector.VLM_MODEL,
        "calibration": detector.calibration_status(),
        "synthid": synthid_detector.status(),
        "visibleWatermark": visible_watermark_detector.status(),
        "provenancePrecheck": provenance_precheck.status(),
        "storage": str(storage.DB_PATH),
    }


@app.post("/api/detect")
async def detect(request: Request, file: UploadFile = File(...), fileType: str | None = Form(default=None)) -> dict:
    actor = await _require_developer_access(request)
    try:
        await asyncio.wait_for(
            _DETECTION_SEMAPHORE.acquire(),
            timeout=DETECTION_QUEUE_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=429,
            detail="当前检测任务较多，请稍后重试",
            headers={"Retry-After": "5"},
        ) from exc
    try:
        return await _detect_with_slot(request, file, fileType, actor)
    finally:
        _DETECTION_SEMAPHORE.release()


async def _detect_with_slot(
    request: Request,
    file: UploadFile,
    file_type: str | None,
    actor: dict,
) -> dict:
    data = await _read_upload(file)
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    filename = file.filename or "unknown"
    ftype = _detect_type(filename, file_type)
    if ftype in {"video", "audio"}:
        label = "视频" if ftype == "video" else "音频"
        raise HTTPException(status_code=422, detail=f"{label}检测能力尚未部署，本次不会生成模拟结论")
    if ftype == "image":
        await _validate_image_pixels(data)
    sha256 = hashlib.sha256(data).hexdigest()
    thumbnail = _image_data_uri(data, ftype, max_side=180, quality=44)
    preview = _image_data_uri(data, ftype, max_side=960, quality=72)

    started = time.perf_counter()
    precheck_report = None
    precheck_analysis = None
    local_provenance_report = None
    if ftype == "image":
        precheck_report = await run_in_threadpool(provenance_precheck.inspect, data, filename)
        local_provenance_report = precheck_report.pop("_provenanceReport", None)
        precheck_analysis = provenance_precheck.build_analysis(precheck_report)

    cached = None if precheck_analysis is not None else storage.get_cached_analysis(ftype, sha256)
    cache_hit = cached is not None
    if precheck_analysis is not None:
        analysis = precheck_analysis
    elif cached is not None:
        analysis = cached
    else:
        if not detector.API_KEY:
            raise HTTPException(status_code=503, detail="未发现可直接判定的来源标记，且真实模型服务未配置")
        try:
            analysis = await run_in_threadpool(detector.analyze, ftype, filename, data)
        except detector.DetectionUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not storage.is_publishable_analysis(analysis):
        raise HTTPException(status_code=503, detail="真实模型未返回可发布的明确结论")
    if not cache_hit:
        storage.put_cached_analysis(ftype, sha256, analysis)
    if ftype == "image":
        if precheck_analysis is None:
            decision = (precheck_report or {}).get("decision") or {}
            analysis = evidence_probability.fuse_with_analysis(analysis, decision.get("probabilityModel"))
        analysis = watermark_yolo.merge(analysis, precheck_report)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    token_usage = _token_usage_from_payload(analysis, cache_hit=cache_hit)
    provenance_report = None
    if ftype == "image":
        provenance_report = local_provenance_report or await run_in_threadpool(
            provenance.read_provenance,
            data,
            provenance.mime_for(filename),
            filename,
        )

    result = _build_result(
        filename=filename,
        ftype=ftype,
        data=data,
        analysis=analysis,
        elapsed_ms=elapsed_ms,
        cache_hit=cache_hit,
        sha256=sha256,
        thumbnail=thumbnail,
        preview=preview,
        actor=actor,
        token_usage=token_usage,
        provenance_report=provenance_report,
        provenance_precheck_report=precheck_report,
    )
    _enqueue_telemetry(
        storage.record_token_usage,
        actor=actor,
        endpoint="/api/detect",
        file_type=ftype,
        result=result,
        usage=token_usage,
        cache_hit=cache_hit,
    )
    _enqueue_telemetry(
        storage.record_event,
        "detect",
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        method="POST",
        path="/api/detect",
        status=200,
        elapsed_ms=elapsed_ms,
        file_type=ftype,
        verdict=result["verdict"],
        cache_hit=cache_hit,
    )
    return result


@app.post("/api/forensics")
async def forensics(
    request: Request,
    file: UploadFile = File(...),
    taskId: str | None = Form(default=None),
) -> dict:
    actor = await _require_developer_access(request)
    await _acquire_forensics_slot()
    try:
        data = await _read_upload(file)
        if not data:
            raise HTTPException(status_code=400, detail="空文件")
        filename = file.filename or "unknown"
        ftype = _detect_type(filename, None)
        if ftype != "image":
            raise HTTPException(status_code=400, detail="可解释性取证分析目前仅支持图像")
        await _validate_image_pixels(data)

        started = time.perf_counter()
        sha256 = hashlib.sha256(data).hexdigest()
        history_item = _require_matching_history_upload(actor, taskId, sha256) if taskId else None
        cache_scope = _forensics_cache_scope(actor)
        cache_type = (
            f"image-forensics:{detector.FORENSICS_PIPELINE_VERSION}:{detector.VLM_MODEL}:{cache_scope}"
            if cache_scope
            else None
        )
        cached = (
            await run_in_threadpool(storage.get_cached_analysis, cache_type, sha256, FORENSICS_CACHE_MAX_AGE_SECONDS)
            if cache_type
            else None
        )
        cache_hit = cached is not None
        if await request.is_disconnected():
            raise HTTPException(status_code=499, detail="客户端已停止等待取证结果")
        report = (
            await run_in_threadpool(detector.attach_forensic_images, data, cached)
            if cached
            else await run_in_threadpool(detector.explainable, data)
        )
    finally:
        _FORENSICS_SEMAPHORE.release()

    report["elapsedMs"] = int((time.perf_counter() - started) * 1000)
    report["fileMeta"] = {"name": filename, "type": ftype, "size": f"{len(data) / 1024:.1f}KB"}
    report["tokenUsage"] = (
        {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
        if cache_hit
        else _token_usage_from_payload(report)
    )
    if cache_type and not cache_hit:
        await run_in_threadpool(
            storage.prune_cached_analyses,
            "image-forensics:",
            FORENSICS_CACHE_MAX_AGE_SECONDS,
        )
        await run_in_threadpool(
            storage.put_cached_analysis,
            cache_type,
            sha256,
            detector.compact_explainable_for_cache(report),
        )
    await run_in_threadpool(
        storage.record_token_usage,
        actor=actor,
        endpoint="/api/forensics",
        file_type=ftype,
        result=report,
        usage=report["tokenUsage"],
        cache_hit=cache_hit,
    )
    await run_in_threadpool(
        storage.record_event,
        "forensics",
        client_ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        method="POST",
        path="/api/forensics",
        status=200,
        elapsed_ms=report["elapsedMs"],
        file_type=ftype,
        verdict=report.get("verdict"),
        cache_hit=cache_hit,
    )
    if history_item is not None:
        await run_in_threadpool(
            storage.put_history_artifacts,
            history_item["taskId"],
            forensics=report,
        )
    return report


@app.post("/api/provenance")
async def provenance_check(
    request: Request,
    file: UploadFile = File(...),
    taskId: str | None = Form(default=None),
) -> dict:
    actor = await _require_developer_access(request)
    await _acquire_forensics_slot()
    try:
        data = await _read_upload(file)
        if not data:
            raise HTTPException(status_code=400, detail="空文件")
        filename = file.filename or "unknown"
        sha256 = hashlib.sha256(data).hexdigest()
        history_item = _require_matching_history_upload(actor, taskId, sha256) if taskId else None
        started = time.perf_counter()
        report = await run_in_threadpool(provenance.read_provenance, data, provenance.mime_for(filename), filename)
    finally:
        _FORENSICS_SEMAPHORE.release()
    report["elapsedMs"] = int((time.perf_counter() - started) * 1000)
    report["fileMeta"] = {"name": filename, "size": f"{len(data) / 1024:.1f}KB"}
    if history_item is not None:
        await run_in_threadpool(
            storage.put_history_artifacts,
            history_item["taskId"],
            provenance=report,
        )
    return report


@app.get("/api/history")
def history(request: Request) -> dict:
    actor = _require_protected_access(request)
    try:
        limit = int(request.query_params.get("limit", "100"))
    except ValueError:
        raise HTTPException(status_code=400, detail="limit 必须是整数")
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=400, detail="limit 仅支持 1 到 500")
    try:
        offset = int(request.query_params.get("offset", "0"))
    except ValueError:
        raise HTTPException(status_code=400, detail="offset 必须是整数")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset 不能小于 0")

    def _parse_bool(name: str) -> bool | None:
        raw = request.query_params.get(name)
        if raw is None or raw == "":
            return None
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
        if normalized in {"0", "false", "no"}:
            return False
        raise HTTPException(status_code=400, detail=f"{name} 必须是布尔值")

    source = request.query_params.get("source")
    if source not in {None, "", "vlm", "mock", "maps-only", "unknown"}:
        raise HTTPException(status_code=400, detail="source 不受支持")
    verdict = request.query_params.get("verdict")
    if verdict not in {None, "", "real", "suspected_fake", "highly_suspected_fake", "unknown"}:
        raise HTTPException(status_code=400, detail="verdict 不受支持")

    items, total, filter_counts = storage.list_history(
        owner_account_uuid=(
            None if actor.get("mode") == "admin" else str(actor.get("accountUuid") or "")
        ),
        limit=limit,
        offset=offset,
        query=request.query_params.get("query"),
        source=source or None,
        verdict=verdict or None,
        has_cache=_parse_bool("hasCache"),
        has_forensics=_parse_bool("hasForensics"),
        has_provenance=_parse_bool("hasProvenance"),
        has_watermark=_parse_bool("hasWatermark"),
        has_synthid=_parse_bool("hasSynthid"),
    )
    return {"items": items, "total": total, "filterCounts": filter_counts}


@app.get("/api/history/{task_id}")
def history_item(task_id: str, request: Request) -> dict:
    item = storage.get_history(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    _require_owned_item(request, item, missing_detail="记录不存在")
    return _strip_internal_history_fields(item)


@app.post("/api/history/{task_id}/artifacts")
def history_artifacts(task_id: str, request: Request) -> dict:
    item = storage.get_history(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    _require_owned_item(request, item, missing_detail="记录不存在")
    raise HTTPException(
        status_code=410,
        detail="客户端证据归档接口已停用；请在服务端取证请求中提交 taskId",
    )


@app.delete("/api/history/{task_id}")
def delete_item(task_id: str, request: Request) -> dict:
    item = storage.get_history(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    _require_owned_item(request, item, missing_detail="记录不存在")
    storage.delete_history(task_id)
    return {"deleted": task_id}


@app.get("/api/report/{report_id}")
def report(report_id: str, request: Request) -> dict:
    item = storage.get_history(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    _require_report_access(request, item)
    return _strip_internal_history_fields(item)


@app.get("/api/report/{report_id}/download")
def report_download(report_id: str, request: Request) -> Response:
    item = storage.get_history(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    _require_report_access(request, item)
    clean_item = _strip_internal_history_fields(item)
    filename = reporting.download_filename(clean_item)
    pdf = reporting.build_report_pdf(
        clean_item,
        forensics=clean_item.get("forensics"),
        provenance=clean_item.get("provenance"),
    )
    _audit_report_share_access(
        request,
        {"shareId": f"direct:{report_id}", "reportId": report_id},
        "downloaded_authenticated",
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@app.post("/api/report/{report_id}/export")
def report_export(report_id: str, request: Request) -> Response:
    item = storage.get_history(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    _require_report_access(request, item)
    clean_item = _strip_internal_history_fields(item)
    filename = reporting.download_filename(clean_item)
    pdf = reporting.build_report_pdf(
        clean_item,
        forensics=clean_item.get("forensics"),
        provenance=clean_item.get("provenance"),
    )
    _audit_report_share_access(
        request,
        {"shareId": f"direct:{report_id}", "reportId": report_id},
        "exported_authenticated",
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@app.post("/api/report/{report_id}/share")
def report_share(report_id: str, request: Request, payload: dict | None = Body(default=None)) -> dict:
    item = storage.get_history(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    actor = _require_report_share_access(request, item)
    body = payload or {}
    try:
        requested_seconds = int(body.get("expiresInSeconds") or REPORT_SHARE_DEFAULT_SECONDS)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="expiresInSeconds 必须是整数秒")
    ttl_seconds = max(60, min(requested_seconds, REPORT_SHARE_MAX_SECONDS))
    expires = int(time.time()) + ttl_seconds
    share_id = f"rgs_{secrets.token_urlsafe(18)}"
    signature = _sign_persisted_report_share(share_id, report_id, expires)
    creator_user_id = str(
        actor.get("accountUuid") or actor.get("userId") or actor.get("mode") or "unknown"
    )
    share = storage.create_report_share(
        share_id=share_id,
        report_id=report_id,
        expires_at=expires,
        created_by_user_id=creator_user_id,
        created_by_key_id=str(actor.get("keyId") or "") or None,
        created_by_mode=str(actor.get("mode") or "unknown"),
    )
    _audit_report_share_access(request, share, "created")
    links = _build_public_report_link(request, report_id, expires, signature, share_id=share_id)
    return {
        **links,
        "shareId": share_id,
        "createdAt": share["createdAt"],
        "expiresAt": datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
        "expiresInSeconds": ttl_seconds,
    }


@app.delete("/api/report/{report_id}/share/{share_id}")
def report_share_revoke(report_id: str, share_id: str, request: Request) -> dict:
    item = storage.get_history(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    _require_report_share_access(request, item)
    share = storage.revoke_report_share(report_id, share_id)
    if not share:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    _audit_report_share_access(request, share, "revoked_by_owner")
    return {
        "shareId": share["shareId"],
        "reportId": share["reportId"],
        "revokedAt": share["revokedAt"],
    }


@app.get("/api/report/{report_id}/shares")
def report_share_list(report_id: str, request: Request) -> dict:
    item = storage.get_history(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    _require_report_share_access(request, item)
    now = int(time.time())
    return {
        "items": [
            {
                **share,
                "active": not share.get("revokedAt") and int(share["expiresAt"]) >= now,
                "expiresAt": datetime.fromtimestamp(
                    int(share["expiresAt"]), tz=timezone.utc
                ).isoformat(),
            }
            for share in storage.list_report_shares(report_id)
        ]
    }


@app.get("/api/report/{report_id}/public")
def report_public(report_id: str, request: Request) -> Response:
    try:
        expires = int(request.query_params.get("expires", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="expires 必须是整数秒时间戳")
    signature = request.query_params.get("sig", "")
    share_id = request.query_params.get("shareId", "").strip()
    if share_id:
        share = storage.get_report_share(share_id)
        if not share:
            raise HTTPException(status_code=404, detail="报告分享链接不存在")
        _validate_persisted_report_share(
            request,
            share=share,
            report_id=report_id,
            expires=expires,
            signature=signature,
        )
        item = storage.get_history(report_id)
        if not item:
            _audit_report_share_access(request, share, "report_missing")
            raise HTTPException(status_code=404, detail="报告不存在")
    else:
        # Existing v1 HMAC links cannot contain a database identifier. A valid
        # legacy link is imported deterministically on first access, after
        # which this and every later access is governed by the stored record.
        _verify_report_share(report_id, expires, signature)
        item = storage.get_history(report_id)
        if not item:
            raise HTTPException(status_code=404, detail="报告不存在")
        fingerprint = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        legacy_share_id = "rgs_legacy_" + hashlib.sha256(
            f"{report_id}:{expires}:{fingerprint}".encode("utf-8")
        ).hexdigest()[:24]
        share = storage.register_legacy_report_share(
            share_id=legacy_share_id,
            report_id=report_id,
            expires_at=expires,
            owner_user_id=str(item.get("_developerUserId") or "legacy-unknown"),
            signature_fingerprint=fingerprint,
        )
        _validate_persisted_report_share(
            request,
            share=share,
            report_id=report_id,
            expires=expires,
            signature=signature,
            legacy=True,
        )
    clean_item = _strip_internal_history_fields(item)
    html = reporting.build_report_html(
        clean_item,
        forensics=clean_item.get("forensics"),
        provenance=clean_item.get("provenance"),
    )
    current_share = storage.get_report_share(share["shareId"])
    if not current_share:
        raise HTTPException(status_code=404, detail="报告分享链接不存在")
    _validate_persisted_report_share(
        request,
        share=current_share,
        report_id=report_id,
        expires=expires,
        signature=signature,
        legacy=bool(current_share.get("legacy")),
    )
    _audit_report_share_access(request, current_share, "granted")
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "X-Robots-Tag": "noindex",
            "Referrer-Policy": "no-referrer",
        },
    )


@app.get("/api/metrics")
def metrics(request: Request) -> dict:
    _require_admin_access(request)
    try:
        days = int(request.query_params.get("days", "14"))
    except ValueError:
        raise HTTPException(status_code=400, detail="days 必须是整数")
    if days not in (7, 14, 30):
        raise HTTPException(status_code=400, detail="days 仅支持 7、14、30")
    return storage.metrics(days=days)


@app.get("/api/developer/token-usage")
def developer_token_usage(request: Request) -> dict:
    _require_internal_developer_auth(request)
    try:
        days = int(request.query_params.get("days", "30"))
    except ValueError:
        raise HTTPException(status_code=400, detail="days 必须是整数")
    if days not in (7, 14, 30, 90):
        raise HTTPException(status_code=400, detail="days 仅支持 7、14、30、90")
    developer_user_id = (request.query_params.get("developerUserId") or "").strip() or None
    developer_key_id = (request.query_params.get("developerKeyId") or "").strip() or None
    if not developer_user_id and not developer_key_id:
        raise HTTPException(status_code=400, detail="developerUserId 或 developerKeyId 必填")
    return storage.token_usage(days=days, developer_user_id=developer_user_id, developer_key_id=developer_key_id)
