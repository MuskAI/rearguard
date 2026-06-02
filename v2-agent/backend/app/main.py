"""鉴真 AI 鉴伪智能体后端。

图像/文本检测调用真实视觉语言模型（qwen3-vl-flash via DashScope），
视频/音频及任何模型调用失败时回退到确定性 Mock。检测逻辑见 detector.py。
"""
from __future__ import annotations

import json
import os
import secrets
import time
import hashlib
import io
from datetime import datetime, timezone
from urllib import error as urlerror
from urllib.parse import quote
from urllib import request as urlrequest

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from . import detector, provenance, reporting, storage, synthid_detector, visible_watermark_detector

app = FastAPI(title="鉴真 AI 鉴伪智能体", version="0.2.0")
ACCESS_TOKEN = os.getenv("JIANZHEN_ACCESS_TOKEN", "").strip()
MAX_UPLOAD_BYTES = int(os.getenv("JIANZHEN_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
DEVELOPER_AUTH_URL = os.getenv("JIANZHEN_DEVELOPER_AUTH_URL", "http://127.0.0.1:5000/api/developer/keys/verify").strip()
DEVELOPER_AUTH_SECRET = (
    os.getenv("REALGUARD_DEVELOPER_AUTH_SECRET")
    or os.getenv("JIANZHEN_DEVELOPER_AUTH_SECRET")
    or ""
).strip()
REQUIRE_DEVELOPER_API_KEY = str(os.getenv("JIANZHEN_REQUIRE_DEVELOPER_API_KEY", "0")).lower() in {"1", "true", "yes"}
DEVELOPER_AUTH_CONFIGURED = bool(DEVELOPER_AUTH_URL and DEVELOPER_AUTH_SECRET)
DEVELOPER_API_KEY_REQUIRED = REQUIRE_DEVELOPER_API_KEY
PROTECTED_ENDPOINTS = [
    "/api/admin/health",
    "/api/history",
    "/api/history/{task_id}",
    "/api/history/{task_id}/artifacts",
    "/api/report/{report_id}",
    "/api/report/{report_id}/download",
    "/api/report/{report_id}/export",
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
        "http://124.222.3.205",
        "https://realguard.cn",
        "https://www.realguard.cn",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

EXT_TO_TYPE = {
    "jpg": "image", "jpeg": "image", "png": "image", "webp": "image", "bmp": "image", "gif": "image",
    "mp4": "video", "mov": "video", "avi": "video", "mkv": "video", "webm": "video",
    "mp3": "audio", "wav": "audio", "m4a": "audio", "flac": "audio", "aac": "audio",
    "txt": "document", "pdf": "document", "doc": "document", "docx": "document", "md": "document",
}

def _detect_type(filename: str, declared: str | None) -> str:
    if declared in detector.DIMENSIONS_BY_TYPE:
        return declared
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXT_TO_TYPE.get(ext, "image")


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else None


def _request_token(request: Request) -> str:
    bearer = request.headers.get("authorization", "").strip()
    if bearer.lower().startswith("bearer "):
        return bearer[7:].strip()
    return request.headers.get("x-jianzhen-token", "").strip()


def _admin_access_granted(request: Request) -> bool:
    return bool(ACCESS_TOKEN) and secrets.compare_digest(_request_token(request), ACCESS_TOKEN)


def _require_protected_access(request: Request) -> None:
    if not ACCESS_TOKEN:
        return
    if _admin_access_granted(request):
        return
    raise HTTPException(status_code=401, detail="访问令牌缺失或无效")


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
    return {
        "mode": "developer",
        "keyId": data.get("keyId"),
        "userId": data.get("userId"),
        "scopes": data.get("scopes") or [],
    }


async def _require_developer_access(request: Request) -> dict:
    if _admin_access_granted(request):
        return {"mode": "admin"}
    if not DEVELOPER_API_KEY_REQUIRED:
        return {"mode": "public"}
    if not DEVELOPER_AUTH_CONFIGURED:
        raise HTTPException(status_code=503, detail="API Key 校验服务未配置")
    api_key = _request_developer_key(request)
    if not api_key:
        raise HTTPException(status_code=401, detail="请在 X-RealGuard-Key 中提供 API Key")
    return await run_in_threadpool(_verify_developer_key_sync, api_key, request)


def _require_internal_developer_auth(request: Request) -> None:
    if not DEVELOPER_AUTH_SECRET:
        raise HTTPException(status_code=503, detail="内部开发者接口未配置")
    submitted = request.headers.get("x-realguard-internal-secret", "").strip()
    if not secrets.compare_digest(submitted, DEVELOPER_AUTH_SECRET):
        raise HTTPException(status_code=403, detail="内部鉴权失败")


def _require_report_access(request: Request, item: dict) -> dict:
    if _admin_access_granted(request):
        return {"mode": "admin"}
    if not DEVELOPER_API_KEY_REQUIRED:
        return {"mode": "public"}
    if not DEVELOPER_AUTH_CONFIGURED:
        raise HTTPException(status_code=503, detail="API Key 校验服务未配置")
    api_key = _request_developer_key(request)
    if not api_key:
        raise HTTPException(status_code=401, detail="请在 X-RealGuard-Key 中提供 API Key")
    actor = _verify_developer_key_sync(api_key, request)
    item_user_id = str(item.get("_developerUserId") or "")
    if item_user_id and item_user_id == str(actor.get("userId") or ""):
        return actor
    raise HTTPException(status_code=403, detail="当前 API Key 无权访问该报告")


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限")
    return data


def _public_capabilities() -> dict:
    synthid_status = synthid_detector.status()
    watermark_status = visible_watermark_detector.status()
    return {
        "status": "ok",
        "version": app.version,
        "vlmEnabled": bool(detector.API_KEY),
        "accessProtectionEnabled": bool(ACCESS_TOKEN),
        "developerKeyAuthEnabled": DEVELOPER_API_KEY_REQUIRED,
        "developerKeyAuthConfigured": DEVELOPER_AUTH_CONFIGURED,
        "protectedEndpoints": PROTECTED_ENDPOINTS if ACCESS_TOKEN else [],
        "developerProtectedEndpoints": DEVELOPER_PROTECTED_ENDPOINTS if DEVELOPER_API_KEY_REQUIRED else [],
        "analysisCacheVersion": storage.ANALYSIS_CACHE_VERSION,
        "capabilities": {
            "image": "vlm",
            "document": "vlm-text",
            "video": "demo-fallback",
            "audio": "demo-fallback",
        },
        "synthid": {
            "enabled": bool(synthid_status.get("enabled")),
            "available": bool(synthid_status.get("available")),
        },
        "visibleWatermark": {
            "enabled": bool(watermark_status.get("enabled")),
            "available": bool(watermark_status.get("available")),
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
        storage.record_event(
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
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
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
) -> dict:
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y%m%d")
    seq = storage.next_sequence(day)
    task_id = f"rj-{day}-{seq:04d}"
    report_id = f"RJ-RPT-{day}-{seq:04d}"

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
        "disclaimer": (
            "本结果由演示用 Mock 算法生成，不构成权威鉴定结论。"
            if analysis["source"] == "mock"
            else "本结果由视觉语言模型分析生成，仅供参考，不构成权威鉴定结论。"
        ),
    }
    storage.put_history(result, sha256=sha256, file_size=len(data), thumbnail=thumbnail, actor=actor)
    return result


def _strip_internal_history_fields(item: dict) -> dict:
    clean = dict(item)
    clean.pop("_developerUserId", None)
    clean.pop("_developerKeyId", None)
    return clean


@app.get("/api/health")
def health() -> dict:
    return _public_capabilities()


@app.get("/api/admin/health")
def admin_health(request: Request) -> dict:
    _require_protected_access(request)
    return {
        **_public_capabilities(),
        "model": detector.VLM_MODEL,
        "calibration": detector.calibration_status(),
        "synthid": synthid_detector.status(),
        "visibleWatermark": visible_watermark_detector.status(),
        "storage": str(storage.DB_PATH),
    }


@app.post("/api/detect")
async def detect(request: Request, file: UploadFile = File(...), fileType: str | None = Form(default=None)) -> dict:
    actor = await _require_developer_access(request)
    data = await _read_upload(file)
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    filename = file.filename or "unknown"
    ftype = _detect_type(filename, fileType)
    sha256 = hashlib.sha256(data).hexdigest()
    thumbnail = _image_data_uri(data, ftype, max_side=180, quality=44)
    preview = _image_data_uri(data, ftype, max_side=960, quality=72)

    started = time.perf_counter()
    cached = storage.get_cached_analysis(ftype, sha256)
    cache_hit = cached is not None
    if cached is not None:
        analysis = cached
    else:
        analysis = await run_in_threadpool(detector.analyze, ftype, filename, data)
        storage.put_cached_analysis(ftype, sha256, analysis)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    token_usage = _token_usage_from_payload(analysis, cache_hit=cache_hit)

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
    )
    storage.record_token_usage(
        actor=actor,
        endpoint="/api/detect",
        file_type=ftype,
        result=result,
        usage=token_usage,
        cache_hit=cache_hit,
    )
    storage.record_event(
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
async def forensics(request: Request, file: UploadFile = File(...)) -> dict:
    actor = await _require_developer_access(request)
    data = await _read_upload(file)
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    filename = file.filename or "unknown"
    ftype = _detect_type(filename, None)
    if ftype != "image":
        raise HTTPException(status_code=400, detail="可解释性取证分析目前仅支持图像")

    started = time.perf_counter()
    report = await run_in_threadpool(detector.explainable, data)
    report["elapsedMs"] = int((time.perf_counter() - started) * 1000)
    report["fileMeta"] = {"name": filename, "type": ftype, "size": f"{len(data) / 1024:.1f}KB"}
    report["tokenUsage"] = _token_usage_from_payload(report)
    storage.record_token_usage(
        actor=actor,
        endpoint="/api/forensics",
        file_type=ftype,
        result=report,
        usage=report["tokenUsage"],
        cache_hit=False,
    )
    return report


@app.post("/api/provenance")
async def provenance_check(request: Request, file: UploadFile = File(...)) -> dict:
    await _require_developer_access(request)
    data = await _read_upload(file)
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    filename = file.filename or "unknown"
    started = time.perf_counter()
    report = await run_in_threadpool(provenance.read_provenance, data, provenance.mime_for(filename))
    report["elapsedMs"] = int((time.perf_counter() - started) * 1000)
    report["fileMeta"] = {"name": filename, "size": f"{len(data) / 1024:.1f}KB"}
    return report


@app.get("/api/history")
def history(request: Request) -> dict:
    _require_protected_access(request)
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
    _require_protected_access(request)
    item = storage.get_history(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    return _strip_internal_history_fields(item)


@app.post("/api/history/{task_id}/artifacts")
def history_artifacts(task_id: str, request: Request, payload: dict | None = Body(default=None)) -> dict:
    _require_protected_access(request)
    item = storage.get_history(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    body = payload or {}
    storage.put_history_artifacts(
        item["taskId"],
        forensics=body.get("forensics"),
        provenance=body.get("provenance"),
    )
    return {"ok": True, "taskId": item["taskId"]}


@app.delete("/api/history/{task_id}")
def delete_item(task_id: str, request: Request) -> dict:
    _require_protected_access(request)
    item = storage.delete_history(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
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
    html = reporting.build_report_html(
        clean_item,
        forensics=clean_item.get("forensics"),
        provenance=clean_item.get("provenance"),
    )
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@app.post("/api/report/{report_id}/export")
def report_export(report_id: str, request: Request, payload: dict | None = Body(default=None)) -> Response:
    item = storage.get_history(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    _require_report_access(request, item)
    clean_item = _strip_internal_history_fields(item)
    filename = reporting.download_filename(clean_item)
    body = payload or {}
    html = reporting.build_report_html(
        clean_item,
        forensics=body.get("forensics") or clean_item.get("forensics"),
        provenance=body.get("provenance") or clean_item.get("provenance"),
    )
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@app.get("/api/metrics")
def metrics(request: Request) -> dict:
    _require_protected_access(request)
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
