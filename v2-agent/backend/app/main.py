"""鉴真 AI 鉴伪智能体后端。

图像/文本检测调用真实视觉语言模型（qwen3-vl-flash via DashScope），
视频/音频及任何模型调用失败时回退到确定性 Mock。检测逻辑见 detector.py。
"""
from __future__ import annotations

import os
import secrets
import time
import hashlib
import io
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from . import detector, provenance, reporting, storage, synthid_detector, visible_watermark_detector

app = FastAPI(title="鉴真 AI 鉴伪智能体", version="0.2.0")
ACCESS_TOKEN = os.getenv("JIANZHEN_ACCESS_TOKEN", "").strip()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


def _require_protected_access(request: Request) -> None:
    if not ACCESS_TOKEN:
        return
    if secrets.compare_digest(_request_token(request), ACCESS_TOKEN):
        return
    raise HTTPException(status_code=401, detail="访问令牌缺失或无效")


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
        "cacheHit": cache_hit,
        "elapsedMs": elapsed_ms,
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
    storage.put_history(result, sha256=sha256, file_size=len(data), thumbnail=thumbnail)
    return result


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": detector.VLM_MODEL,
        "vlmEnabled": bool(detector.API_KEY),
        "accessProtectionEnabled": bool(ACCESS_TOKEN),
        "protectedEndpoints": [
            "/api/history",
            "/api/history/{task_id}/artifacts",
            "/api/report/{report_id}",
            "/api/report/{report_id}/download",
            "/api/report/{report_id}/export",
            "/api/metrics",
        ] if ACCESS_TOKEN else [],
        "calibration": detector.calibration_status(),
        "synthid": synthid_detector.status(),
        "visibleWatermark": visible_watermark_detector.status(),
        "storage": str(storage.DB_PATH),
    }


@app.post("/api/detect")
async def detect(request: Request, file: UploadFile = File(...), fileType: str | None = Form(default=None)) -> dict:
    data = await file.read()
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
async def forensics(file: UploadFile = File(...)) -> dict:
    data = await file.read()
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
    return report


@app.post("/api/provenance")
async def provenance_check(file: UploadFile = File(...)) -> dict:
    data = await file.read()
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

    items, total = storage.list_history(
        limit=limit,
        query=request.query_params.get("query"),
        source=source or None,
        verdict=verdict or None,
        has_forensics=_parse_bool("hasForensics"),
        has_provenance=_parse_bool("hasProvenance"),
        has_watermark=_parse_bool("hasWatermark"),
        has_synthid=_parse_bool("hasSynthid"),
    )
    return {"items": items, "total": total}


@app.get("/api/history/{task_id}")
def history_item(task_id: str, request: Request) -> dict:
    _require_protected_access(request)
    item = storage.get_history(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    return item


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
    _require_protected_access(request)
    item = storage.get_history(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    return item


@app.get("/api/report/{report_id}/download")
def report_download(report_id: str, request: Request) -> Response:
    _require_protected_access(request)
    item = storage.get_history(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    filename = reporting.download_filename(item)
    html = reporting.build_report_html(
        item,
        forensics=item.get("forensics"),
        provenance=item.get("provenance"),
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
    _require_protected_access(request)
    item = storage.get_history(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    filename = reporting.download_filename(item)
    body = payload or {}
    html = reporting.build_report_html(
        item,
        forensics=body.get("forensics") or item.get("forensics"),
        provenance=body.get("provenance") or item.get("provenance"),
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
