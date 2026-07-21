"""Known-platform attribution plus generic visible-watermark localization.

Registry matches remain provenance evidence. Every valid visible-watermark box
is exposed for review; the final aggregator applies the configured high-risk
watermark policy after preserving the pixel model's original score.
"""
from __future__ import annotations

import os
import math
import time
from typing import Any, Dict, Optional

import requests


SERVICE_URL_ENV = "REALGUARD_VISIBLE_WATERMARK_URL"
SERVICE_TOKEN_ENV = "WATERMARK_PRECHECK_TOKEN"
LEGACY_SERVICE_TOKEN_ENV = "YOLO_WATERMARK_TOKEN"
SERVICE_TIMEOUT_ENV = "REALGUARD_VISIBLE_WATERMARK_TIMEOUT"
DEFAULT_SERVICE_URL = "http://127.0.0.1:15066/v1/precheck"
DEFAULT_TIMEOUT_SECONDS = 12.0
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PIPELINE_TRACE_SCHEMA = "watermark_pipeline_trace_v1"
PIPELINE_STAGE_IDS = (
    "decode", "metadata", "registry", "yolo", "ocr", "retrieval", "fusion", "verdict",
)
REGISTRY_MODEL = "wiltodelta/remove-ai-watermarks"
REGISTRY_PROVIDERS = frozenset({"gemini", "doubao", "jimeng", "jimeng_pill", "samsung"})
YOLO_PROVIDER = "yolo11x_watermark"
YOLO_METHOD = "yolo11x_watermark_detection"
YOLO_MODEL = "corzent/yolo11x_watermark_detection"


def _clamp01(value: Any) -> float:
    try:
        return round(min(max(float(value), 0.0), 1.0), 4)
    except (TypeError, ValueError):
        return 0.0


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _normalized_bbox(value: Any) -> Optional[Dict[str, float]]:
    if not isinstance(value, dict):
        return None
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
        width = float(value.get("w"))
        height = float(value.get("h"))
    except (TypeError, ValueError):
        return None
    values = (x, y, width, height)
    if (
        not all(math.isfinite(item) for item in values)
        or not 0.0 <= x <= 1.0
        or not 0.0 <= y <= 1.0
        or not 0.0 < width <= 1.0
        or not 0.0 < height <= 1.0
        or x + width > 1.0
        or y + height > 1.0
    ):
        return None
    return {
        "x": round(x, 4),
        "y": round(y, 4),
        "w": round(width, 4),
        "h": round(height, 4),
    }


def _boxes_overlap(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    ax1, ay1 = _clamp01(first.get("x")), _clamp01(first.get("y"))
    ax2 = ax1 + _clamp01(first.get("w"))
    ay2 = ay1 + _clamp01(first.get("h"))
    bx1, by1 = _clamp01(second.get("x")), _clamp01(second.get("y"))
    bx2 = bx1 + _clamp01(second.get("w"))
    by2 = by1 + _clamp01(second.get("h"))
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    smaller = min(area_a, area_b)
    iou = intersection / union if union > 0 else 0.0
    smaller_coverage = intersection / smaller if smaller > 0 else 0.0
    return iou >= 0.08 or smaller_coverage >= 0.5


def _deduplicate(hits: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    unique = []
    seen = set()
    for hit in hits:
        bbox = hit.get("bbox") or {}
        key = (
            hit.get("provider"),
            round(_clamp01(bbox.get("x")), 3),
            round(_clamp01(bbox.get("y")), 3),
            round(_clamp01(bbox.get("w")), 3),
            round(_clamp01(bbox.get("h")), 3),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique[:24]


def _timeout() -> float:
    try:
        return max(1.0, float(os.environ.get(SERVICE_TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def _pipeline_trace(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Expose only the bounded, versioned trace emitted by the trusted service."""
    trace = payload.get("pipelineTrace")
    if not isinstance(trace, dict) or trace.get("schemaVersion") != PIPELINE_TRACE_SCHEMA:
        return None
    stages = trace.get("stages")
    if not isinstance(stages, list) or not stages or len(stages) > len(PIPELINE_STAGE_IDS):
        return None
    normalized = []
    seen = set()
    for raw in stages:
        if not isinstance(raw, dict):
            return None
        stage_id = str(raw.get("id") or "")
        if stage_id not in PIPELINE_STAGE_IDS or stage_id in seen:
            return None
        seen.add(stage_id)
        normalized.append({
            "id": stage_id,
            "label": str(raw.get("label") or stage_id)[:80],
            "status": str(raw.get("status") or "unknown")[:24],
            "elapsedMs": _nonnegative_int(raw.get("elapsedMs")),
            "summary": str(raw.get("summary") or "")[:500],
            "parallelGroup": str(raw.get("parallelGroup") or "")[:48] or None,
            "details": raw.get("details") if isinstance(raw.get("details"), dict) else {},
        })
    parallel_groups = trace.get("parallelGroups")
    return {
        "schemaVersion": PIPELINE_TRACE_SCHEMA,
        "totalElapsedMs": _nonnegative_int(trace.get("totalElapsedMs")),
        "parallelGroups": parallel_groups if isinstance(parallel_groups, dict) else {},
        "stages": normalized,
    }


def _unavailable(note: str) -> Dict[str, Any]:
    return {
        "enabled": True,
        "supported": False,
        "detected": False,
        "provider": None,
        "confidence": 0.0,
        "evidenceLevel": "unavailable",
        "hits": [],
        "temporal": {"sampledFrames": 1, "positiveFrames": 0, "moving": False},
        "note": note,
        "elapsedMs": 0,
        "detector": {
            "available": False,
            "model": REGISTRY_MODEL,
            "engines": [
                {
                    "id": "known_ai_registry",
                    "label": "AI 平台标记识别",
                    "available": False,
                    "model": REGISTRY_MODEL,
                    "role": "provenance",
                },
                {
                    "id": "yolo_platform_corroboration",
                    "label": "YOLO 区域复核",
                    "available": False,
                    "model": "corzent/yolo11x_watermark_detection",
                    "role": "corroboration",
                },
            ],
        },
    }


def _visible_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    if payload.get("status") != "ok":
        note = str(payload.get("message") or "").strip()
        visible = _unavailable(
            note or "可见水印检测暂不可用，本次未完成该必检项；不能据此判断未检出水印。"
        )
        visible["elapsedMs"] = _nonnegative_int(payload.get("elapsedMs"))
        return visible
    detector = payload.get("genericVisibleWatermark")
    detector = detector if isinstance(detector, dict) else {}
    raw_detections = []
    for key in ("visibleHits", "detections"):
        values = payload.get(key)
        if isinstance(values, list):
            raw_detections.extend(values)
    registry_hits = []
    generic_hits = []
    for detection in raw_detections:
        if not isinstance(detection, dict) or not isinstance(detection.get("bbox"), dict):
            continue
        provider = str(detection.get("provider") or "")
        bbox = _normalized_bbox(detection.get("bbox"))
        if bbox is None:
            continue
        if provider in REGISTRY_PROVIDERS:
            registry_hits.append({
                "provider": provider,
                "label": str(detection.get("label") or provider),
                "confidence": _clamp01(detection.get("confidence")),
                "bbox": bbox,
                "method": "remove_ai_watermarks_registry",
                "frame": None,
                "scores": {},
                "crop": None,
                "model": REGISTRY_MODEL,
                "modelRevision": payload.get("engineVersion"),
                "decisive": False,
                "registryCorroborated": bool(detection.get("corroborated")),
                "evidenceRole": "visual_attribution",
                "localizationConfirmed": bool(detection.get("yoloCorroborated")),
                "localizationConfidence": _clamp01(detection.get("yoloConfidence")),
                "localizationModel": detection.get("localizationModel"),
                "localizationModelRevision": detection.get("localizationModelRevision"),
            })
            continue
        if provider != YOLO_PROVIDER:
            continue
        generic_hits.append({
            "provider": YOLO_PROVIDER,
            "label": str(detection.get("label") or "可见水印（平台待确认）"),
            "confidence": _clamp01(detection.get("confidence")),
            "bbox": bbox,
            "method": YOLO_METHOD,
            "frame": None,
            "scores": {},
            "crop": None,
            "model": detection.get("model") or detector.get("model") or YOLO_MODEL,
            "modelRevision": detection.get("modelRevision") or detector.get("modelRevision"),
            "decisive": False,
            "evidenceRole": "localization",
            "localizationConfirmed": False,
        })

    registry_hits = _deduplicate(registry_hits)[:12]
    generic_hits = [
        hit
        for hit in _deduplicate(generic_hits)
        if not any(_boxes_overlap(hit.get("bbox") or {}, known.get("bbox") or {}) for known in registry_hits)
    ][:12]
    hits = _deduplicate([*registry_hits, *generic_hits])
    top_pool = registry_hits or hits
    top = max(top_pool, key=lambda hit: hit["confidence"]) if top_pool else None
    top_confidence = top.get("confidence", 0.0) if top else 0.0
    confirmed_hits = [hit for hit in registry_hits if hit.get("localizationConfirmed") is True]
    notes = []
    if registry_hits:
        providers = "、".join(dict.fromkeys(hit["label"] for hit in registry_hits))
        notes.append(
            f"remove-ai-watermarks 识别到 {len(registry_hits)} 处已知 AI 平台标记（{providers}），"
            "作为来源证据参与概率融合。"
        )
    else:
        notes.append("remove-ai-watermarks 已完成已知 AI 平台标记扫描，本次未命中。")
    if generic_hits and confirmed_hits:
        notes.append(
            f"YOLO11x 检测到 {len(generic_hits)} 处平台待确认的可见水印，"
            f"并对 {len(confirmed_hits)} 处已知平台标记完成区域复核；"
            "通用水印仅作定位线索，已确认的平台标记按来源证据规则参与融合。"
        )
    elif generic_hits:
        notes.append(
            f"YOLO11x 检测到 {len(generic_hits)} 处可见水印，平台归属尚未确认；"
            "该结果可能是 Logo、台标或版权标记，不单独影响 AI 生成结论。"
        )
    elif confirmed_hits:
        notes.append(f"YOLO11x 已对其中 {len(confirmed_hits)} 处平台标记完成区域复核。")
    elif detector.get("available"):
        notes.append("YOLO11x 已完成可见水印扫描，本次未检出。")
    else:
        notes.append("YOLO11x 本次不可用，已知平台标记扫描仍可独立工作。")
    coordinate_space = str(payload.get("coordinateSpace") or "").strip()
    display_size = payload.get("displaySize") if isinstance(payload.get("displaySize"), dict) else {}
    coordinate_protocol_valid = (
        coordinate_space == "display_normalized_v1"
        and _nonnegative_int(display_size.get("width")) > 0
        and _nonnegative_int(display_size.get("height")) > 0
    )
    registry_available = payload.get("status") == "ok"
    yolo_available = bool(detector.get("available"))
    # Fast detection promises both known-platform provenance scanning and
    # generic visible-watermark localization. A partial run may still provide
    # positive evidence, but it cannot prove that no watermark was present.
    available = registry_available and yolo_available and coordinate_protocol_valid
    positive_evidence_supported = registry_available and coordinate_protocol_valid
    if not coordinate_protocol_valid:
        notes.append("水印服务未提供受支持的显示坐标协议，本次定位框不进入完整证据链。")
        hits = []
        top = None
        top_confidence = 0.0
    return {
        "enabled": True,
        "supported": available,
        "positiveEvidenceSupported": positive_evidence_supported,
        "registrySupported": registry_available,
        "genericVisibleSupported": yolo_available,
        "coordinateSpace": coordinate_space if coordinate_protocol_valid else "unknown",
        "displaySize": display_size if coordinate_protocol_valid else {},
        "encodedSize": payload.get("encodedSize") or {},
        "sourceOrientation": _nonnegative_int(payload.get("sourceOrientation") or 1),
        "detected": bool(hits),
        "provider": top.get("provider") if top else None,
        "confidence": top_confidence,
        "evidenceLevel": "strong" if coordinate_protocol_valid and registry_hits and top_confidence >= 0.8 else "medium" if hits else "none",
        "hits": hits,
        "temporal": {"sampledFrames": 1, "positiveFrames": 1 if hits else 0, "moving": False},
        "note": " ".join(notes),
        "elapsedMs": int(payload.get("elapsedMs") or detector.get("elapsedMs") or 0),
        "pipelineTrace": _pipeline_trace(payload),
        "detector": {
            "available": available,
            "model": REGISTRY_MODEL if registry_available else YOLO_MODEL,
            "modelRevision": payload.get("engineVersion") if registry_available else detector.get("modelRevision"),
            "confidenceThreshold": detector.get("confidenceThreshold"),
            "roundTripMs": detector.get("roundTripMs"),
            "engines": [
                {
                    "id": "known_ai_registry",
                    "label": "AI 平台标记识别",
                    "available": registry_available,
                    "detected": bool(registry_hits),
                    "count": len(registry_hits),
                    "model": REGISTRY_MODEL,
                    "version": payload.get("engineVersion"),
                    "role": "provenance",
                },
                {
                    "id": "yolo_visible_watermark",
                    "label": "YOLO 可见水印检测",
                    "available": yolo_available,
                    "detected": bool(generic_hits or confirmed_hits or detector.get("detected")),
                    "count": max(
                        len(generic_hits) + len(confirmed_hits),
                        _nonnegative_int(detector.get("count")),
                    ),
                    "model": detector.get("model") or YOLO_MODEL,
                    "version": detector.get("modelRevision"),
                    "role": "localization",
                },
            ],
        },
    }


def _expert_verdict(visible: Dict[str, Any]) -> str:
    hits = [hit for hit in visible.get("hits") or [] if isinstance(hit, dict)]
    platform_count = sum(1 for hit in hits if hit.get("provider") in REGISTRY_PROVIDERS)
    generic_count = len(hits) - platform_count
    if platform_count:
        return f"检出 {platform_count} 处 AI 平台水印"
    if generic_count:
        return f"定位 {generic_count} 处可见水印（平台待确认）"
    return "未检出可见水印"


def run_visible_watermark_expert(
    image_bytes: bytes,
    filename: Optional[str],
    mimetype: Optional[str],
) -> Dict[str, Any]:
    started = time.perf_counter()

    def finish(update: Dict[str, Any]) -> Dict[str, Any]:
        update.setdefault("latencyMs", int((time.perf_counter() - started) * 1000))
        update.setdefault("evidence_kind", "visible_watermark")
        return update

    token = (
        os.environ.get(SERVICE_TOKEN_ENV)
        or os.environ.get(LEGACY_SERVICE_TOKEN_ENV)
        or ""
    ).strip()
    if not token:
        return finish({
            "status": "skipped",
            "score": None,
            "verdict": "AI 平台水印识别未配置",
            "confidence": "",
            "evidence": [],
            "message": f"环境变量 {SERVICE_TOKEN_ENV} 未设置",
            "visibleWatermark": _unavailable("AI 平台水印识别服务尚未配置。"),
        })
    if not image_bytes:
        return finish({
            "status": "failed",
            "score": None,
            "verdict": "无图像",
            "confidence": "",
            "evidence": [],
            "message": "未收到图像字节",
            "visibleWatermark": _unavailable("未收到可用于 AI 平台水印识别的图像。"),
        })
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        return finish({
            "status": "failed",
            "score": None,
            "verdict": "图像超限",
            "confidence": "",
            "evidence": [],
            "message": f"图像大小超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
            "visibleWatermark": _unavailable("图像超过 AI 平台水印识别服务的大小限制。"),
        })

    url = (os.environ.get(SERVICE_URL_ENV) or DEFAULT_SERVICE_URL).strip()
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (filename or "upload.bin", image_bytes, mimetype or "application/octet-stream")},
            timeout=_timeout(),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise ValueError("invalid YOLO watermark response")
    except (requests.RequestException, ValueError) as exc:
        return finish({
            "status": "failed",
            "score": None,
            "verdict": "AI 平台水印识别不可用",
            "confidence": "",
            "evidence": [],
            "message": type(exc).__name__,
            "visibleWatermark": _unavailable("AI 平台水印识别服务本次不可用。"),
        })

    visible = _visible_result(payload)
    count = len(visible["hits"])
    provenance_decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    provenance_report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    return finish({
        "status": "success",
        "score": None,
        "verdict": _expert_verdict(visible),
        "confidence": "高" if visible["confidence"] >= 0.8 else "中" if count else "无",
        "evidence": [visible["note"]],
        "message": f"detected={str(bool(count)).lower()}|count={count}",
        "watermarkDetected": bool(count),
        "watermarkCount": count,
        "visibleWatermark": visible,
        "provenanceDecision": provenance_decision,
        "provenanceReport": provenance_report,
        "probabilityModel": provenance_decision.get("probabilityModel"),
    })
