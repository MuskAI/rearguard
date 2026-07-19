"""Expose visible watermarks and attribute known AI-platform marks."""
from __future__ import annotations

import copy
import math
from typing import Any

from . import watermark_verdict


REGISTRY_METHOD = "remove_ai_watermarks_registry"
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


def _bbox(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        x = float(value["x"])
        y = float(value["y"])
        width = float(value["w"])
        height = float(value["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in (x, y, width, height)):
        return None
    if (
        x < 0.0
        or y < 0.0
        or width <= 0.0
        or height <= 0.0
        or x + width > 1.0
        or y + height > 1.0
    ):
        return None
    return {"x": round(x, 4), "y": round(y, 4), "w": round(width, 4), "h": round(height, 4)}


def _registry_hits(precheck: dict[str, Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for raw in precheck.get("visibleHits") or []:
        if not isinstance(raw, dict) or raw.get("provider") not in REGISTRY_PROVIDERS:
            continue
        bbox = _bbox(raw.get("bbox"))
        if bbox is None:
            continue
        hits.append({
            "provider": str(raw.get("provider")),
            "label": str(raw.get("label") or raw.get("provider") or "已知 AI 平台标记"),
            "confidence": _clamp01(raw.get("confidence")),
            "bbox": bbox,
            "method": REGISTRY_METHOD,
            "frame": None,
            "scores": {},
            "crop": None,
            "model": REGISTRY_MODEL,
            "modelRevision": precheck.get("engineVersion"),
            "decisive": False,
            "evidenceRole": "visual_attribution",
            "registryCorroborated": bool(raw.get("corroborated")),
            "localizationConfirmed": bool(raw.get("yoloCorroborated")),
            "localizationConfidence": _clamp01(raw.get("yoloConfidence")),
            "localizationModel": raw.get("localizationModel"),
            "localizationModelRevision": raw.get("localizationModelRevision"),
        })
    return hits[:12]


def _generic_hits(precheck: dict[str, Any]) -> list[dict[str, Any]]:
    detector = precheck.get("genericVisibleWatermark") or {}
    hits: list[dict[str, Any]] = []
    for raw in precheck.get("visibleHits") or []:
        if not isinstance(raw, dict) or raw.get("provider") != YOLO_PROVIDER:
            continue
        bbox = _bbox(raw.get("bbox"))
        if bbox is None:
            continue
        hits.append({
            "provider": YOLO_PROVIDER,
            "label": "可见水印（平台待确认）",
            "confidence": _clamp01(raw.get("confidence")),
            "bbox": bbox,
            "method": YOLO_METHOD,
            "frame": None,
            "scores": {},
            "crop": None,
            "model": raw.get("model") or detector.get("model") or YOLO_MODEL,
            "modelRevision": raw.get("modelRevision") or detector.get("modelRevision"),
            "decisive": False,
            "evidenceRole": "localization",
            "localizationConfirmed": False,
        })
    return hits[:12]


def _boxes_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
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


def _deduplicate(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
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


def merge(analysis: dict[str, Any], precheck: dict[str, Any] | None) -> dict[str, Any]:
    """Attach visible-watermark detections and apply the configured verdict policy."""
    if not isinstance(precheck, dict):
        return analysis
    detector = precheck.get("genericVisibleWatermark")
    registry_hits = _registry_hits(precheck)
    generic_hits = [
        hit
        for hit in _generic_hits(precheck)
        if not any(_boxes_overlap(hit.get("bbox") or {}, registry.get("bbox") or {}) for registry in registry_hits)
    ]
    if not isinstance(detector, dict) and not registry_hits and not generic_hits:
        return analysis

    merged = copy.deepcopy(analysis)
    existing = merged.get("visibleWatermark")
    existing = dict(existing) if isinstance(existing, dict) else {}
    existing_hits = []
    for raw in existing.get("hits") or []:
        if not isinstance(raw, dict):
            continue
        hit = copy.deepcopy(raw)
        hit["decisive"] = False
        hit["evidenceRole"] = "untrusted_context"
        hit["registryCorroborated"] = False
        hit["localizationConfirmed"] = False
        existing_hits.append(hit)
    hits = _deduplicate([*registry_hits, *generic_hits, *existing_hits])
    detected = bool(hits)
    top_pool = registry_hits or hits
    top = max(top_pool, key=lambda item: _clamp01(item.get("confidence"))) if top_pool else None
    confidence = _clamp01(top.get("confidence")) if top else 0.0
    engine_version = str(precheck.get("engineVersion") or "")
    registry_available = precheck.get("status") == "ok" and not engine_version.startswith("local-")
    yolo_available = bool(detector.get("available")) if isinstance(detector, dict) else False
    confirmed_hits = [hit for hit in registry_hits if hit.get("localizationConfirmed") is True]
    available = registry_available or yolo_available

    if generic_hits and confirmed_hits:
        yolo_note = (
            f"YOLO11x 检测到 {len(generic_hits)} 处平台待确认的可见水印，"
            f"并对 {len(confirmed_hits)} 处已知平台标记完成区域复核；"
            "通用水印与平台标记均仅作视觉定位和归属辅助，不单独决定真伪。"
        )
    elif generic_hits:
        yolo_note = (
            f"YOLO11x 检测到 {len(generic_hits)} 处可见水印，平台归属尚未确认；"
            "该结果可能是 Logo、台标或版权标记，不单独影响 AI 生成结论。"
        )
    elif confirmed_hits:
        yolo_note = f"YOLO11x 已对 {len(confirmed_hits)} 处平台标记完成区域复核。"
    elif yolo_available:
        yolo_note = "YOLO11x 已完成可见水印扫描，本次未检出。"
    else:
        yolo_note = "YOLO11x 区域复核本次不可用，平台水印识别仍可独立工作。"

    if registry_hits:
        providers = "、".join(dict.fromkeys(str(hit.get("label") or hit.get("provider")) for hit in registry_hits))
        registry_note = (
            f"remove-ai-watermarks 识别到 {len(registry_hits)} 处已知 AI 平台标记（{providers}），"
            "该信号仅作视觉归属辅助，不单独决定真伪。"
        )
    elif registry_available:
        registry_note = "remove-ai-watermarks 已完成已知 AI 平台标记扫描，本次未命中。"
    else:
        registry_note = "remove-ai-watermarks 平台标记识别本次不可用。"

    note_parts = [part for part in (registry_note, yolo_note) if part]
    note = " ".join(dict.fromkeys(note_parts))
    merged["visibleWatermark"] = {
        **existing,
        "enabled": True,
        "supported": available,
        "detected": detected,
        "provider": top.get("provider") if top else None,
        "confidence": confidence,
        "coordinateSpace": str(precheck.get("coordinateSpace") or ""),
        "displaySize": dict(precheck.get("displaySize") or {}) if isinstance(precheck.get("displaySize"), dict) else {},
        "registrySupported": registry_available,
        "positiveEvidenceSupported": registry_available,
        "evidenceLevel": (
            "unavailable"
            if not available
            else "strong" if registry_hits and confidence >= 0.8 else "medium" if detected else "none"
        ),
        "hits": hits,
        "temporal": existing.get("temporal") or {
            "sampledFrames": 1,
            "positiveFrames": 1 if detected else 0,
            "moving": False,
        },
        "note": note,
        "elapsedMs": int(
            precheck.get("elapsedMs")
            or (detector or {}).get("elapsedMs")
            or existing.get("elapsedMs")
            or 0
        ),
        "detector": {
            "available": available,
            "model": REGISTRY_MODEL if registry_available else YOLO_MODEL,
            "modelRevision": precheck.get("engineVersion") if registry_available else (detector or {}).get("modelRevision"),
            "confidenceThreshold": (detector or {}).get("confidenceThreshold"),
            "roundTripMs": (detector or {}).get("roundTripMs"),
            "engines": [
                {
                    "id": "known_ai_registry",
                    "label": "AI 平台标记识别",
                    "available": registry_available,
                    "detected": bool(registry_hits),
                    "count": len(registry_hits),
                    "model": REGISTRY_MODEL,
                    "version": precheck.get("engineVersion"),
                    "role": "provenance",
                },
                {
                    "id": "yolo_visible_watermark",
                    "label": "YOLO 可见水印检测",
                    "available": yolo_available,
                    "detected": bool(generic_hits or confirmed_hits or (detector or {}).get("detected")),
                    "count": max(
                        len(generic_hits) + len(confirmed_hits),
                        _nonnegative_int((detector or {}).get("count")),
                    ),
                    "model": (detector or {}).get("model") or YOLO_MODEL,
                    "version": (detector or {}).get("modelRevision"),
                    "role": "localization",
                },
            ],
        },
    }
    return watermark_verdict.apply(merged, merged["visibleWatermark"])
