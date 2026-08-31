"""Expose the pinned explicit-watermark detector without retrieval or OCR fusion."""
from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Any

import requests
from flask import g

import service as base


YOLO_URL = os.getenv("YOLO_WATERMARK_URL", "http://127.0.0.1:5067/v1/detect")
YOLO_HEALTH_URL = os.getenv("YOLO_WATERMARK_HEALTH_URL", "http://127.0.0.1:5067/health")
YOLO_TOKEN = os.getenv("YOLO_WATERMARK_TOKEN", "")
YOLO_TIMEOUT_SECONDS = float(os.getenv("YOLO_WATERMARK_TIMEOUT_SECONDS", "20"))
YOLO_REQUIRE_CUDA = os.getenv("YOLO_WATERMARK_REQUIRE_CUDA", "true").lower() in {
    "1", "true", "yes", "on",
}
YOLO_EXPECTED_MODEL = os.getenv(
    "YOLO_WATERMARK_EXPECTED_MODEL",
    "huijian/yolo11x_explicit_watermark_binary",
)
YOLO_EXPECTED_REVISION = os.getenv(
    "YOLO_WATERMARK_REVISION", "2026-08-31-f527d8a75420"
)
YOLO_EXPECTED_SHA256 = os.getenv(
    "YOLO_WATERMARK_MODEL_SHA256",
    "f527d8a7542061eb58b0a2953ea86b66b0ecf0b16f3c84d64886e8104c341081",
)
DIRECT_DECISIVE_CONFIDENCE = min(
    1.0,
    max(0.0, float(os.getenv("YOLO_WATERMARK_DIRECT_DECISIVE_CONFIDENCE", "0.80"))),
)
YOLO_PROVIDER = "yolo11x_watermark"
DIRECT_MODE = "model_direct"
DIRECT_METHOD = "explicit_watermark_model_direct"

_base_health = base.app.view_functions["health"]
_base_precheck = base.app.view_functions["precheck"]


def _yolo_runtime_error(payload: dict[str, Any]) -> str:
    if payload.get("status") != "ok":
        return "service_not_ok"
    if payload.get("model") != YOLO_EXPECTED_MODEL:
        return "model_identity_mismatch"
    if YOLO_EXPECTED_REVISION and payload.get("modelRevision") != YOLO_EXPECTED_REVISION:
        return "model_revision_mismatch"
    if YOLO_EXPECTED_SHA256 and payload.get("modelSha256") != YOLO_EXPECTED_SHA256:
        return "model_checksum_mismatch"
    if YOLO_REQUIRE_CUDA and (
        payload.get("cudaReady") is not True
        or str(payload.get("device") or "").lower() == "cpu"
        or not payload.get("gpu")
    ):
        return "cuda_not_ready"
    return ""


def _valid_normalized_box(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
        width = float(value.get("w"))
        height = float(value.get("h"))
    except (TypeError, ValueError):
        return False
    return (
        all(math.isfinite(item) for item in (x, y, width, height))
        and 0.0 <= x <= 1.0
        and 0.0 <= y <= 1.0
        and 0.0 < width <= 1.0
        and 0.0 < height <= 1.0
        and x + width <= 1.0
        and y + height <= 1.0
    )


def _yolo_detection_error(payload: dict[str, Any]) -> str:
    runtime_error = _yolo_runtime_error(payload)
    if runtime_error:
        return runtime_error
    image = payload.get("image")
    detections = payload.get("detections")
    if not isinstance(image, dict) or not isinstance(detections, list):
        return "response_schema_invalid"
    try:
        if int(image.get("width") or 0) <= 0 or int(image.get("height") or 0) <= 0:
            return "image_dimensions_invalid"
    except (TypeError, ValueError):
        return "image_dimensions_invalid"
    for detection in detections:
        if not isinstance(detection, dict) or not _valid_normalized_box(detection.get("bbox")):
            return "detection_box_invalid"
    if bool(payload.get("detected")) != bool(detections):
        return "detection_count_inconsistent"
    try:
        if int(payload.get("count")) != len(detections):
            return "detection_count_inconsistent"
    except (TypeError, ValueError):
        return "detection_count_inconsistent"
    return ""


def _generic_yolo_hits(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not YOLO_URL or not YOLO_TOKEN:
        return [], {
            "available": False,
            "error": "not_configured",
            "model": YOLO_EXPECTED_MODEL,
            "mode": DIRECT_MODE,
        }
    started = time.perf_counter()
    with path.open("rb") as image_file:
        response = requests.post(
            YOLO_URL,
            headers={"Authorization": f"Bearer {YOLO_TOKEN}"},
            files={"file": (path.name, image_file, "application/octet-stream")},
            timeout=(2, YOLO_TIMEOUT_SECONDS),
        )
    response.raise_for_status()
    payload = response.json()
    detection_error = _yolo_detection_error(payload)
    if detection_error:
        raise ValueError(detection_error)

    candidates = []
    for item in payload.get("detections") or []:
        confidence = round(float(item.get("confidence") or 0.0), 4)
        decisive = confidence >= DIRECT_DECISIVE_CONFIDENCE
        candidates.append({
            "provider": YOLO_PROVIDER,
            "label": "显式水印",
            "location": "localized",
            "confidence": confidence,
            "decisive": decisive,
            "evidenceRole": "decisive_provenance" if decisive else "model_detection",
            "bbox": item.get("bbox") or {},
            "model": payload.get("model"),
            "modelRevision": payload.get("modelRevision"),
            "method": DIRECT_METHOD,
        })

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    status = {
        "available": True,
        "detected": bool(candidates),
        "count": len(candidates),
        "maxConfidence": max((item["confidence"] for item in candidates), default=0.0),
        "mode": DIRECT_MODE,
        "resultSource": "model",
        "directDecisionThreshold": DIRECT_DECISIVE_CONFIDENCE,
        "elapsedMs": int(payload.get("elapsedMs") or elapsed_ms),
        "roundTripMs": elapsed_ms,
    }
    for key in (
        "model", "modelRevision", "modelSha256", "device", "gpu", "cudaRequired",
        "cudaReady", "confidenceThreshold", "modelResident", "modelLoadCount",
        "modelLoadedAt", "warmupCompleted", "inputSize",
    ):
        status[key] = payload.get(key)
    return candidates, status


def _confidence_band(confidence: float) -> str:
    if confidence >= DIRECT_DECISIVE_CONFIDENCE:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def _direct_result(
    candidates: list[dict[str, Any]],
    status: dict[str, Any],
) -> dict[str, Any]:
    detected = bool(candidates)
    confidence = max((float(item.get("confidence") or 0.0) for item in candidates), default=0.0)
    strong_count = sum(1 for item in candidates if item.get("decisive") is True)
    reason = (
        f"显式水印模型直接定位到 {len(candidates)} 处水印区域，最高置信度 {confidence * 100:.1f}%。"
        if detected
        else "显式水印模型已完成扫描，未输出水印区域。"
    )
    return {
        "available": status.get("available") is True,
        "detected": detected,
        "type": "unknown" if detected else "none",
        "sourcePlatform": None,
        "provider": YOLO_PROVIDER if detected else None,
        "confidence": round(confidence, 4),
        "confidenceBand": _confidence_band(confidence),
        "mode": DIRECT_MODE,
        "resultSource": "model",
        "decisionThreshold": DIRECT_DECISIVE_CONFIDENCE,
        "aiWatermarkVerdict": {
            "verdict": "yes" if detected else "no",
            "isAiGeneratedWatermark": True if detected else False,
            "confidence": round(confidence, 4),
            "reason": reason,
            "relevantHitCount": len(candidates),
            "strongHitCount": strong_count,
        },
        "hits": [
            {
                "bbox": dict(item.get("bbox") or {}),
                "type": "unknown",
                "text": None,
                "sourcePlatform": None,
                "provider": YOLO_PROVIDER,
                "confidence": round(float(item.get("confidence") or 0.0), 4),
                "detectionConfidence": round(float(item.get("confidence") or 0.0), 4),
                "model": item.get("model"),
                "modelRevision": item.get("modelRevision"),
                "decisive": item.get("decisive") is True,
                "evidenceRole": item.get("evidenceRole"),
                "method": DIRECT_METHOD,
            }
            for item in candidates
        ],
        "elapsedMs": status.get("elapsedMs", 0),
        "roundTripMs": status.get("roundTripMs", 0),
    }


def _visible_hits_with_yolo(
    path: Path,
    provenance_path: Path | None = None,
) -> list[dict[str, Any]]:
    del provenance_path
    try:
        candidates, status = _generic_yolo_hits(path)
        g.generic_visible_watermark_status = status
        g.explicit_watermark_result = _direct_result(candidates, status)
        g.watermark_pipeline_state = {"candidates": candidates, "status": status}
        return candidates
    except (requests.RequestException, ValueError, TypeError) as exc:
        base.app.logger.warning("explicit watermark model unavailable: %s", type(exc).__name__)
        status = {
            "available": False,
            "detected": False,
            "count": 0,
            "error": type(exc).__name__,
            "model": YOLO_EXPECTED_MODEL,
            "modelRevision": YOLO_EXPECTED_REVISION,
            "mode": DIRECT_MODE,
            "resultSource": "model",
        }
        g.generic_visible_watermark_status = status
        g.explicit_watermark_result = {
            "available": False,
            "detected": False,
            "type": "none",
            "sourcePlatform": None,
            "provider": None,
            "confidence": 0.0,
            "confidenceBand": "low",
            "mode": DIRECT_MODE,
            "resultSource": "model",
            "hits": [],
            "aiWatermarkVerdict": {
                "verdict": "inconclusive",
                "isAiGeneratedWatermark": None,
                "confidence": 0.0,
                "reason": "显式水印模型本次不可用，未生成替代结果。",
                "relevantHitCount": 0,
                "strongHitCount": 0,
            },
            "error": type(exc).__name__,
        }
        g.watermark_pipeline_state = {"candidates": [], "status": status}
        return []


def _pipeline_stage(
    stage_id: str,
    label: str,
    status: str,
    elapsed_ms: int,
    summary: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": stage_id,
        "label": label,
        "status": status,
        "elapsedMs": max(0, int(elapsed_ms or 0)),
        "summary": summary,
        "parallelGroup": None,
        "details": details,
    }


def _build_pipeline_trace(response: dict[str, Any]) -> dict[str, Any]:
    timings = response.get("pipelineTimings") or {}
    state = getattr(g, "watermark_pipeline_state", {}) or {}
    candidates = state.get("candidates") or []
    model_status = state.get("status") or response.get("genericVisibleWatermark") or {}
    explicit = response.get("explicitWatermark") or {}
    verdict = explicit.get("aiWatermarkVerdict") or {}
    model_available = model_status.get("available") is True
    detected = bool(candidates)
    verdict_value = verdict.get("verdict") or "inconclusive"
    max_confidence = max(
        (float(item.get("confidence") or 0.0) for item in candidates),
        default=0.0,
    )
    stages = [
        _pipeline_stage(
            "decode", "文件读取", "success",
            int(timings.get("decodeMs") or 0) + int(timings.get("normalizeMs") or 0),
            (
                f"{(response.get('encodedSize') or {}).get('width', 0)}×"
                f"{(response.get('encodedSize') or {}).get('height', 0)}"
            ),
            {
                "input": response.get("input") or {},
                "encodedSize": response.get("encodedSize") or {},
                "displaySize": response.get("displaySize") or {},
            },
        ),
        _pipeline_stage(
            "yolo", "显式水印模型",
            "error" if not model_available else "hit" if detected else "clean",
            model_status.get("roundTripMs") or model_status.get("elapsedMs") or 0,
            (
                f"模型检出 {len(candidates)} 处水印，最高置信度 {max_confidence * 100:.1f}%"
                if detected
                else "模型已完成扫描，未检出显式水印"
                if model_available
                else "显式水印模型本次不可用"
            ),
            {
                "count": len(candidates),
                "candidates": candidates,
                "decisionThreshold": DIRECT_DECISIVE_CONFIDENCE,
                "runtime": {key: model_status.get(key) for key in (
                    "model", "modelRevision", "modelSha256", "device", "gpu",
                    "cudaReady", "confidenceThreshold", "elapsedMs", "roundTripMs",
                    "modelResident", "modelLoadCount", "warmupCompleted",
                )},
            },
        ),
        _pipeline_stage(
            "verdict", "模型结果",
            "hit" if verdict_value == "yes" else "clean" if verdict_value == "no" else "warning",
            0,
            verdict.get("reason") or "本次未形成水印结果",
            {
                "verdict": verdict,
                "confidence": explicit.get("confidence"),
                "decisionThreshold": DIRECT_DECISIVE_CONFIDENCE,
                "resultSource": "model",
            },
        ),
    ]
    return {
        "schemaVersion": "watermark_pipeline_trace_v1",
        "totalElapsedMs": int(model_status.get("roundTripMs") or response.get("elapsedMs") or 0),
        "parallelGroups": {},
        "stages": stages,
    }


def precheck_with_yolo():
    response = _base_precheck()
    if isinstance(response, dict):
        status = getattr(g, "generic_visible_watermark_status", {
            "available": False,
            "error": "not_run",
            "model": YOLO_EXPECTED_MODEL,
            "modelRevision": YOLO_EXPECTED_REVISION,
            "mode": DIRECT_MODE,
        })
        explicit = getattr(g, "explicit_watermark_result", {
            "available": False,
            "detected": False,
            "type": "none",
            "confidence": 0.0,
            "hits": [],
            "mode": DIRECT_MODE,
            "error": "not_run",
        })
        response.update({
            "engine": "huijian.explicit-watermark-model",
            "engineVersion": YOLO_EXPECTED_REVISION,
            "mode": DIRECT_MODE,
            "genericVisibleWatermark": status,
            "explicitWatermark": explicit,
            "decision": {
                "shortCircuit": False,
                "modelRequired": True,
                "reason": "explicit_watermark_model_direct",
                "evidenceKinds": ["visible_watermark_model"] if explicit.get("detected") else [],
                "summary": (explicit.get("aiWatermarkVerdict") or {}).get("reason"),
            },
        })
        response["pipelineTrace"] = _build_pipeline_trace(response)
    return response


def health_with_yolo():
    payload = dict(_base_health())
    yolo = {
        "available": False,
        "model": YOLO_EXPECTED_MODEL,
        "modelRevision": YOLO_EXPECTED_REVISION,
        "mode": DIRECT_MODE,
    }
    try:
        response = requests.get(YOLO_HEALTH_URL, timeout=(1, 4))
        response.raise_for_status()
        yolo.update(response.json())
        runtime_error = _yolo_runtime_error(yolo)
        yolo["available"] = not runtime_error
        if runtime_error:
            yolo["validationError"] = runtime_error
        yolo["mode"] = DIRECT_MODE
        yolo["resultSource"] = "model"
        yolo["directDecisionThreshold"] = DIRECT_DECISIVE_CONFIDENCE
    except (requests.RequestException, ValueError, TypeError) as exc:
        yolo["error"] = type(exc).__name__
    payload.update({
        "status": "ok" if yolo.get("available") and payload.get("tokenReady") else "degraded",
        "mode": DIRECT_MODE,
        "engine": "huijian.explicit-watermark-model",
        "engineVersion": YOLO_EXPECTED_REVISION,
        "registryReady": False,
        "visibleProviders": ["explicit_watermark"],
        "genericVisibleWatermark": yolo,
        "explicitWatermarkEnsemble": {
            "available": False,
            "disabled": True,
            "mode": "disabled",
            "reason": "model_direct_enabled",
        },
    })
    return payload


base._visible_hits = _visible_hits_with_yolo
base.app.view_functions["health"] = health_with_yolo
base.app.view_functions["precheck"] = precheck_with_yolo
app = base.app
