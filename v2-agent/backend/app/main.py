"""鉴真 AI 鉴伪智能体后端。

图像/文本检测调用真实视觉语言模型（qwen3-vl-flash via DashScope），
视频/音频及任何模型调用失败时回退到确定性 Mock。检测逻辑见 detector.py。
"""
from __future__ import annotations

import time
import hashlib
import io
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from . import detector, provenance, storage, synthid_detector, visible_watermark_detector

app = FastAPI(title="鉴真 AI 鉴伪智能体", version="0.2.0")

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


def _thumbnail_data_uri(data: bytes, file_type: str) -> str | None:
    if file_type != "image":
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            im.thumbnail((220, 220))
            out = io.BytesIO()
            im.save(out, format="WEBP", quality=46, method=4)
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
    thumbnail = _thumbnail_data_uri(data, ftype)

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
def history() -> dict:
    summary = storage.list_history()
    return {"items": summary, "total": len(summary)}


@app.get("/api/history/{task_id}")
def history_item(task_id: str) -> dict:
    item = storage.get_history(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    return item


@app.delete("/api/history/{task_id}")
def delete_item(task_id: str) -> dict:
    item = storage.delete_history(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"deleted": task_id}


@app.get("/api/report/{report_id}")
def report(report_id: str) -> dict:
    item = storage.get_history(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    return item


@app.get("/api/metrics")
def metrics() -> dict:
    return storage.metrics()
