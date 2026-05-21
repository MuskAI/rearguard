"""鉴真 AI 鉴伪智能体后端。

图像/文本检测调用真实视觉语言模型（qwen3-vl-flash via DashScope），
视频/音频及任何模型调用失败时回退到确定性 Mock。检测逻辑见 detector.py。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from . import detector, provenance

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

HISTORY: list[dict] = []
HISTORY_BY_ID: dict[str, dict] = {}
_counter = {"n": 0}


def _detect_type(filename: str, declared: str | None) -> str:
    if declared in detector.DIMENSIONS_BY_TYPE:
        return declared
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXT_TO_TYPE.get(ext, "image")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": detector.VLM_MODEL,
        "vlmEnabled": bool(detector.API_KEY),
    }


@app.post("/api/detect")
async def detect(file: UploadFile = File(...), fileType: str | None = Form(default=None)) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    filename = file.filename or "unknown"
    ftype = _detect_type(filename, fileType)

    started = time.perf_counter()
    analysis = await run_in_threadpool(detector.analyze, ftype, filename, data)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    _counter["n"] += 1
    now = datetime.now(timezone.utc)
    task_id = f"rj-{now.strftime('%Y%m%d')}-{_counter['n']:04d}"
    report_id = f"RJ-RPT-{now.strftime('%Y%m%d')}-{_counter['n']:04d}"

    size = f"{len(data) / 1024:.1f}KB"
    resolution = detector.image_size(data) if ftype == "image" else None

    result = {
        "taskId": task_id,
        "reportId": report_id,
        "createdAt": now.isoformat(),
        "fileMeta": {"name": filename, "type": ftype, "size": size, "resolution": resolution},
        "verdict": analysis["verdict"],
        "confidence": analysis["confidence"],
        "modelVersion": analysis["modelVersion"],
        "source": analysis["source"],
        "elapsedMs": elapsed_ms,
        "dimensions": analysis["dimensions"],
        "regions": analysis["regions"],
        "explanation": analysis["explanation"],
        "disclaimer": (
            "本结果由演示用 Mock 算法生成，不构成权威鉴定结论。"
            if analysis["source"] == "mock"
            else "本结果由视觉语言模型分析生成，仅供参考，不构成权威鉴定结论。"
        ),
    }

    HISTORY.insert(0, result)
    HISTORY_BY_ID[task_id] = result
    HISTORY_BY_ID[report_id] = result
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
    summary = [
        {
            "taskId": h["taskId"],
            "reportId": h["reportId"],
            "name": h["fileMeta"]["name"],
            "type": h["fileMeta"]["type"],
            "verdict": h["verdict"],
            "confidence": h["confidence"],
            "createdAt": h["createdAt"],
        }
        for h in HISTORY
    ]
    return {"items": summary, "total": len(summary)}


@app.get("/api/history/{task_id}")
def history_item(task_id: str) -> dict:
    item = HISTORY_BY_ID.get(task_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    return item


@app.delete("/api/history/{task_id}")
def delete_item(task_id: str) -> dict:
    item = HISTORY_BY_ID.pop(task_id, None)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    HISTORY_BY_ID.pop(item["reportId"], None)
    HISTORY[:] = [h for h in HISTORY if h["taskId"] != item["taskId"]]
    return {"deleted": task_id}


@app.get("/api/report/{report_id}")
def report(report_id: str) -> dict:
    item = HISTORY_BY_ID.get(report_id)
    if not item:
        raise HTTPException(status_code=404, detail="报告不存在")
    return item
