"""Standalone manual-review UI for the explicit watermark detector."""
from __future__ import annotations

import base64
import tempfile
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import requests
from flask import Flask, jsonify, request, send_from_directory


ROOT = Path(__file__).resolve().parent
PRECHECK_URL = os.getenv("WATERMARK_LAB_PRECHECK_URL", "http://127.0.0.1:5066/v1/precheck")
PRECHECK_TOKEN = os.getenv("WATERMARK_LAB_PRECHECK_TOKEN", os.getenv("WATERMARK_PRECHECK_TOKEN", ""))
MAX_UPLOAD_BYTES = int(os.getenv("WATERMARK_LAB_MAX_UPLOAD_BYTES", str(30 * 1024 * 1024)))
MAX_VIDEO_UPLOAD_BYTES = int(os.getenv("WATERMARK_LAB_MAX_VIDEO_UPLOAD_BYTES", str(200 * 1024 * 1024)))
REQUEST_TIMEOUT = float(os.getenv("WATERMARK_LAB_REQUEST_TIMEOUT", "90"))
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = max(MAX_UPLOAD_BYTES, MAX_VIDEO_UPLOAD_BYTES)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/favicon.ico")
def favicon():
    return "", 204


@app.get("/health")
def health():
    return {
        "status": "ok" if PRECHECK_TOKEN else "degraded",
        "mode": "standalone-manual-review",
        "precheckConfigured": bool(PRECHECK_URL and PRECHECK_TOKEN),
        "upstream": PRECHECK_URL,
    }


def _error(message: str, status: int = 422, **extra: Any):
    return jsonify({"status": "error", "message": message, **extra}), status


def _run_precheck(data: bytes, filename: str, mimetype: str) -> dict[str, Any]:
    response = requests.post(
        PRECHECK_URL,
        headers={"Authorization": f"Bearer {PRECHECK_TOKEN}"},
        files={"file": (filename, data, mimetype or "application/octet-stream")},
        timeout=(2, REQUEST_TIMEOUT),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("上游返回格式无效")
    return payload


def _is_video(filename: str, mimetype: str) -> bool:
    return mimetype.startswith("video/") or Path(filename).suffix.lower() in VIDEO_EXTENSIONS


def _verdict_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    explicit = payload.get("explicitWatermark") or {}
    verdict = explicit.get("aiWatermarkVerdict") or {}
    return {
        "verdict": verdict.get("verdict", "inconclusive"),
        "confidence": float(verdict.get("confidence") or 0),
        "sourcePlatform": explicit.get("sourcePlatform") or "",
        "explicitWatermark": explicit,
    }


def _jimeng_evidence(explicit: dict[str, Any]) -> dict[str, Any]:
    """Expose the concrete signals behind a Jimeng attribution for review."""
    hits = explicit.get("hits") if isinstance(explicit.get("hits"), list) else []
    registry_entries: list[str] = []
    ocr_texts: list[str] = []
    retrieval_scores: list[float] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        provider = str(hit.get("providerHint") or "")
        if hit.get("registryMatched") is True and provider in {"jimeng", "jimeng_pill"}:
            registry_entries.append(provider)
        text_analysis = hit.get("textAnalysis") or {}
        if text_analysis.get("platformMatch") == "即梦AI" and hit.get("text"):
            ocr_texts.append(str(hit["text"]))
        similarity = float(hit.get("retrievalSimilarity") or 0)
        if hit.get("retrievalAccepted") is True and hit.get("sourcePlatform") == "即梦AI":
            retrieval_scores.append(round(similarity, 4))
    text_signal = explicit.get("aiGenerationTextSignal") or {}
    if text_signal.get("platformMatch") == "即梦AI" and text_signal.get("text"):
        ocr_texts.append(str(text_signal["text"]))
    registry_entries = list(dict.fromkeys(registry_entries))
    ocr_texts = list(dict.fromkeys(ocr_texts))
    retrieval_scores = sorted(set(retrieval_scores), reverse=True)
    sources: list[str] = []
    if registry_entries:
        sources.append("registry")
    if ocr_texts:
        sources.append("ocr")
    if retrieval_scores:
        sources.append("retrieval")
    return {
        "matched": bool(sources),
        "sources": sources,
        "registryEntries": registry_entries,
        "registryLabels": ["即梦AI" for _ in registry_entries],
        "ocrTexts": ocr_texts,
        "retrievalScores": retrieval_scores,
    }


def _format_timestamp(milliseconds: float) -> str:
    total_seconds = max(0, milliseconds) / 1000
    minutes = int(total_seconds // 60)
    seconds = total_seconds - minutes * 60
    return f"{minutes:02d}:{seconds:05.2f}"


def _aggregate_video(frames: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [frame for frame in frames if not frame.get("error")]
    positives = [frame for frame in valid if frame.get("verdict") == "yes"]
    inconclusive = [frame for frame in valid if frame.get("verdict") == "inconclusive"]
    if positives:
        strongest = max(positives, key=lambda frame: frame.get("confidence", 0))
        if len(positives) >= 2:
            verdict = "yes"
            reason = f"{len(positives)}/{len(valid)} 个抽取帧出现 AI 生成水印证据。"
        else:
            verdict = "inconclusive"
            reason = "仅有一个抽取帧出现候选证据，不足以代表整段视频，建议回看对应时间点。"
        confidence = strongest.get("confidence", 0)
    elif inconclusive:
        verdict = "inconclusive"
        confidence = max(frame.get("confidence", 0) for frame in inconclusive)
        reason = "抽取帧中存在未能确定的候选证据，建议回看标记帧。"
    else:
        verdict = "no"
        confidence = 0.0
        reason = "抽取帧中未发现支持 AI 生成水印的证据。"
    return {
        "verdict": verdict,
        "isAiGeneratedWatermark": verdict == "yes",
        "confidence": round(confidence, 4),
        "reason": reason,
        "sampledFrames": len(frames),
        "positiveFrames": len(positives),
        "inconclusiveFrames": len(inconclusive),
        "persistenceRatio": round(len(positives) / len(valid), 4) if valid else 0,
    }


@app.post("/api/analyze")
def analyze():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return _error("请先选择一张图片。", 400)
    if not PRECHECK_TOKEN:
        return _error("实验室尚未配置上游检测令牌。", 503)
    data = uploaded.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        return _error("图片内容为空。", 400)
    if len(data) > MAX_UPLOAD_BYTES:
        return _error("图片超过 30 MB 限制。", 413)
    try:
        payload = _run_precheck(data, uploaded.filename, uploaded.mimetype or "image/jpeg")
        return jsonify({"status": "ok", "filename": uploaded.filename, "result": payload})
    except requests.RequestException as exc:
        return _error("检测服务暂时不可用。", 502, errorType=type(exc).__name__)
    except (ValueError, TypeError) as exc:
        return _error("检测服务返回了无法解析的结果。", 502, errorType=type(exc).__name__)


@app.post("/api/analyze-video")
def analyze_video():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return _error("请先选择一个视频文件。", 400)
    if not _is_video(uploaded.filename, uploaded.mimetype or ""):
        return _error("当前文件不是受支持的视频格式。", 415)
    if not PRECHECK_TOKEN:
        return _error("实验室尚未配置上游检测令牌。", 503)
    try:
        requested = int(request.form.get("sample_count", "8"))
    except ValueError:
        requested = 8
    sample_count = max(3, min(requested, 24))
    data = uploaded.read(MAX_VIDEO_UPLOAD_BYTES + 1)
    if not data:
        return _error("视频内容为空。", 400)
    if len(data) > MAX_VIDEO_UPLOAD_BYTES:
        return _error("视频超过 200 MB 限制。", 413)

    started = time.perf_counter()
    suffix = Path(uploaded.filename).suffix.lower() or ".mp4"
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="watermark-lab-", suffix=suffix, delete=False) as temp:
            temp.write(data)
            temp_path = temp.name
        capture = cv2.VideoCapture(temp_path)
        if not capture.isOpened():
            return _error("视频无法读取，请使用 MP4、MOV 或 WEBM 文件。", 422)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if frame_count <= 0 or width <= 0 or height <= 0:
            capture.release()
            return _error("视频没有可读取的画面帧。", 422)
        fps = fps if fps > 0 else 25.0
        indices = np.unique(np.linspace(0, frame_count - 1, min(sample_count, frame_count)).round().astype(int)).tolist()
        extracted: list[tuple[int, float, bytes]] = []
        for index in indices:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            encoded_ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
            if encoded_ok:
                extracted.append((index, index / fps * 1000, encoded.tobytes()))
        capture.release()
        if not extracted:
            return _error("视频帧提取失败，请更换文件后重试。", 422)

        def inspect(item: tuple[int, float, bytes]) -> dict[str, Any]:
            index, timestamp_ms, frame_data = item
            try:
                payload = _run_precheck(frame_data, f"frame-{index:06d}.jpg", "image/jpeg")
                verdict = _verdict_from_payload(payload)
                return {
                    "frameIndex": index,
                    "timestampMs": round(timestamp_ms, 1),
                    "timestamp": _format_timestamp(timestamp_ms),
                    "preview": "data:image/jpeg;base64," + base64.b64encode(frame_data).decode("ascii"),
                    **verdict,
                    "pipelineTrace": payload.get("pipelineTrace") or {},
                    "jimengEvidence": _jimeng_evidence(verdict["explicitWatermark"]),
                }
            except (requests.RequestException, ValueError, TypeError) as exc:
                return {
                    "frameIndex": index,
                    "timestampMs": round(timestamp_ms, 1),
                    "timestamp": _format_timestamp(timestamp_ms),
                    "preview": "data:image/jpeg;base64," + base64.b64encode(frame_data).decode("ascii"),
                    "verdict": "inconclusive",
                    "confidence": 0,
                    "sourcePlatform": "",
                    "explicitWatermark": {},
                    "pipelineTrace": {},
                    "jimengEvidence": {"matched": False, "sources": [], "registryEntries": [], "registryLabels": [], "ocrTexts": [], "retrievalScores": []},
                    "error": type(exc).__name__,
                }

        frames: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(4, len(extracted))) as executor:
            futures = [executor.submit(inspect, item) for item in extracted]
            for future in as_completed(futures):
                frames.append(future.result())
        frames.sort(key=lambda frame: frame["frameIndex"])
        aggregate = _aggregate_video(frames)
        positive_frames = [frame for frame in frames if frame.get("verdict") == "yes"]
        best_frame = max(positive_frames, key=lambda frame: frame.get("confidence", 0), default=frames[0])
        explicit = best_frame.get("explicitWatermark") or {}
        explicit = {**explicit, "type": "video", "aiWatermarkVerdict": aggregate, "hits": []}
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return jsonify({
            "status": "ok",
            "filename": uploaded.filename,
            "result": {
                "mediaType": "video",
                "video": {
                    "width": width,
                    "height": height,
                    "fps": round(fps, 3),
                    "frameCount": frame_count,
                    "durationMs": round(frame_count / fps * 1000),
                    "requestedSampleCount": sample_count,
                },
                "elapsedMs": elapsed_ms,
                "aiWatermarkVerdict": aggregate,
                "explicitWatermark": explicit,
                "pipelineTrace": best_frame.get("pipelineTrace") or {},
                "frames": frames,
            },
        })
    except (cv2.error, OSError, ValueError, TypeError) as exc:
        return _error("视频处理失败，请确认文件编码和格式。", 422, errorType=type(exc).__name__)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


@app.errorhandler(413)
def too_large(_error):
    return _error("上传文件超过实验室限制：图片 30 MB，视频 200 MB。", 413)
