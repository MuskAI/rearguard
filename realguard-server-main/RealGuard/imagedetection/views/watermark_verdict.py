from __future__ import annotations

import math
from typing import Any


MIN_WATERMARK_FAKE_PROBABILITY = 0.95
MIN_EXPLICIT_WATERMARK_CONFIDENCE = 0.80
MIN_EXPLICIT_VERDICT_CONFIDENCE = 0.80
DECISIVE_PROVIDERS = frozenset({"gemini", "doubao", "jimeng", "jimeng_pill", "samsung"})
DECISIVE_METHOD = "remove_ai_watermarks_registry"
MIN_LOCALIZED_AREA = 0.0001
PROVIDER_MIN_CONFIDENCE = {
    "gemini": 0.72,
    "doubao": 0.80,
    "jimeng": 0.80,
    "jimeng_pill": 0.80,
    "samsung": 0.80,
}

_PROVIDER_LABELS = {
    "gemini": "Google Gemini",
    "doubao": "豆包",
    "jimeng": "即梦",
    "jimeng_pill": "即梦",
    "samsung": "Samsung",
    "yolo11x_watermark": "通用可见水印",
}
_PROVIDER_ALIASES = {
    "gemini": "gemini",
    "google gemini": "gemini",
    "豆包": "doubao",
    "doubao": "doubao",
    "即梦": "jimeng",
    "即梦ai": "jimeng",
    "jimeng": "jimeng",
    "jimeng ai": "jimeng",
    "jimeng_pill": "jimeng_pill",
    "samsung": "samsung",
}


def _clamp01(value: Any) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _localized_hits(visible: Any) -> list[dict[str, Any]]:
    if not isinstance(visible, dict) or not visible.get("detected"):
        return []
    display_size = visible.get("displaySize") or {}
    try:
        display_width = int(display_size.get("width") or 0)
        display_height = int(display_size.get("height") or 0)
    except (TypeError, ValueError):
        return []
    if (
        visible.get("coordinateSpace") != "display_normalized_v1"
        or display_width <= 0
        or display_height <= 0
    ):
        return []
    complete_scan = visible.get("supported") is True
    registry_positive_supported = (
        visible.get("positiveEvidenceSupported") is True
        or visible.get("registrySupported") is True
    )
    localized = []
    for hit in visible.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        bbox = hit.get("bbox")
        if not _valid_normalized_bbox(bbox):
            continue
        is_registry_hit = (
            str(hit.get("method") or "").strip() == DECISIVE_METHOD
            and str(hit.get("provider") or "").strip().lower() in DECISIVE_PROVIDERS
        )
        if complete_scan or (registry_positive_supported and is_registry_hit):
            localized.append(hit)
    return localized


def _valid_normalized_bbox(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
        width = float(value.get("w"))
        height = float(value.get("h"))
    except (TypeError, ValueError):
        return False
    values = (x, y, width, height)
    return bool(
        all(math.isfinite(item) for item in values)
        and 0.0 <= x <= 1.0
        and 0.0 <= y <= 1.0
        and 0.0 < width <= 1.0
        and 0.0 < height <= 1.0
        and x + width <= 1.0
        and y + height <= 1.0
    )


def has_localized_watermark(visible: Any) -> bool:
    return bool(_localized_hits(visible))


def _provider_id(value: Any) -> str:
    return _PROVIDER_ALIASES.get(str(value or "").strip().lower(), "")


def _boxes_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    if not _valid_normalized_bbox(first) or not _valid_normalized_bbox(second):
        return False
    ax1, ay1 = float(first["x"]), float(first["y"])
    ax2, ay2 = ax1 + float(first["w"]), ay1 + float(first["h"])
    bx1, by1 = float(second["x"]), float(second["y"])
    bx2, by2 = bx1 + float(second["w"]), by1 + float(second["h"])
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0.0, min(ay2, by2) - max(ay1, by1)
    )
    smaller = min(float(first["w"]) * float(first["h"]), float(second["w"]) * float(second["h"]))
    return smaller > 0 and intersection / smaller >= 0.5


def _strong_explicit_hits(visible: Any) -> list[dict[str, Any]]:
    if not isinstance(visible, dict):
        return []
    explicit = visible.get("explicitWatermark")
    if not isinstance(explicit, dict):
        return []
    verdict = explicit.get("aiWatermarkVerdict")
    if not isinstance(verdict, dict):
        return []
    provider = _provider_id(explicit.get("provider") or explicit.get("sourcePlatform"))
    if (
        explicit.get("available") is not True
        or explicit.get("detected") is not True
        or provider not in DECISIVE_PROVIDERS
        or _clamp01(explicit.get("confidence")) < MIN_EXPLICIT_WATERMARK_CONFIDENCE
        or verdict.get("verdict") != "yes"
        or verdict.get("isAiGeneratedWatermark") is not True
        or _clamp01(verdict.get("confidence")) < MIN_EXPLICIT_VERDICT_CONFIDENCE
        or _nonnegative_int(verdict.get("relevantHitCount")) < 1
    ):
        return []
    strong = []
    for hit in explicit.get("hits") or []:
        if not isinstance(hit, dict) or not _valid_normalized_bbox(hit.get("bbox")):
            continue
        hit_provider = _provider_id(hit.get("provider") or hit.get("sourcePlatform"))
        if hit_provider != provider or _clamp01(hit.get("confidence")) < PROVIDER_MIN_CONFIDENCE[provider]:
            continue
        text = hit.get("textAnalysis") if isinstance(hit.get("textAnalysis"), dict) else {}
        text_supported = (
            text.get("verdict") == "supports_ai_generation"
            and text.get("likelyAIgenerated") is True
            and _clamp01(text.get("aiGenerationConfidence")) >= MIN_EXPLICIT_VERDICT_CONFIDENCE
        )
        signal_count = sum((
            text_supported,
            hit.get("retrievalAccepted") is True,
            hit.get("registryMatched") is True,
            hit.get("yoloCorroborated") is True,
        ))
        if signal_count >= 2:
            strong.append(hit)
    return strong


def _decisive_hits(visible: Any) -> list[dict[str, Any]]:
    explicit_hits = _strong_explicit_hits(visible)
    if not explicit_hits:
        return []
    return [
        hit
        for hit in _localized_hits(visible)
        if hit.get("decisive") is True
        and (provider := str(hit.get("provider") or "").strip().lower()) in DECISIVE_PROVIDERS
        and str(hit.get("method") or "").strip() == DECISIVE_METHOD
        and (
            hit.get("registryCorroborated") is True
            or _clamp01(hit.get("confidence")) >= PROVIDER_MIN_CONFIDENCE[provider]
        )
        and _clamp01((hit.get("bbox") or {}).get("w")) * _clamp01((hit.get("bbox") or {}).get("h")) >= MIN_LOCALIZED_AREA
        and any(_boxes_overlap(hit.get("bbox") or {}, item.get("bbox") or {}) for item in explicit_hits)
    ]


def has_decisive_ai_watermark(visible: Any) -> bool:
    return bool(_decisive_hits(visible))


def _percent(value: Any) -> str:
    return f"{_clamp01(value) * 100:.1f}%"


def _model_tendency(probability: float) -> str:
    if probability < 0.35:
        return "偏向真实"
    if probability < 0.75:
        return "处于边界区间"
    return "偏向 AI 生成"


def _positive_visual_issues(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    ignored = ("无明显", "暂未提取", "未提取到明确", "未发现明确")
    return [
        str(item).strip()
        for item in value
        if str(item).strip() and not any(marker in str(item) for marker in ignored)
    ]


def build_explanation(result: dict[str, Any], visible: Any) -> str:
    hits = _localized_hits(visible)
    existing = str(result.get("explanation") or "").strip()
    if not hits:
        return existing

    provider_names = []
    for hit in hits:
        provider = str(hit.get("provider") or "").strip()
        label = str(hit.get("label") or "").strip() or _PROVIDER_LABELS.get(provider) or "通用可见水印"
        if label not in provider_names:
            provider_names.append(label)
    if has_decisive_ai_watermark(visible):
        line = (
            f"强 AI 水印证据：定位到 {len(_decisive_hits(visible))} 处"
            f"（{'、'.join(provider_names)}），已通过平台匹配、区域定位与 OCR/检索融合复核。"
            "按当前决策规则，该证据可独立授权“AI生成图像”结论。"
        )
    else:
        line = (
            f"可见标记线索：定位到 {len(hits)} 处区域"
            f"（{'、'.join(provider_names)}）。当前证据未通过强水印授权门槛，"
            "仅供人工核对来源，不单独决定真伪。"
        )
    if line in existing:
        return existing
    return "\n".join(part for part in (existing, line) if part)


def apply_to_result(result: dict[str, Any], visible: Any) -> bool:
    decisive_hits = _decisive_hits(visible)
    if not decisive_hits:
        return False
    explicit = visible.get("explicitWatermark") or {}
    verdict = explicit.get("aiWatermarkVerdict") or {}
    original_probability = _clamp01(result.get("probability"))
    published_probability = max(
        MIN_WATERMARK_FAKE_PROBABILITY,
        _clamp01(explicit.get("confidence")),
        _clamp01(verdict.get("confidence")),
        max((_clamp01(hit.get("confidence")) for hit in decisive_hits), default=0.0),
    )
    providers = sorted({
        str(hit.get("provider") or "").strip().lower()
        for hit in decisive_hits
        if str(hit.get("provider") or "").strip()
    })
    result.update({
        "probability": round(published_probability, 4),
        "final_label": "AI生成图像",
        "confidence": "高",
        "reviewRequired": False,
        "decisionStatus": "verdict",
        "decisionAuthority": "decisive_provenance",
        "scorePublished": True,
        "watermark_verdict_override": {
            "applied": True,
            "policyVersion": "explicit-ai-watermark-v1",
            "decisionAuthority": "decisive_provenance",
            "providers": providers,
            "hitCount": len(decisive_hits),
            "originalProbability": round(original_probability, 4),
            "publishedProbability": round(published_probability, 4),
            "watermarkConfidence": round(_clamp01(explicit.get("confidence")), 4),
            "verdictConfidence": round(_clamp01(verdict.get("confidence")), 4),
        },
    })
    result["explanation"] = build_explanation(result, visible)
    swarm = result.get("swarm")
    if isinstance(swarm, dict):
        swarm.update({
            "finalLabel": "AI生成图像",
            "score": round(published_probability, 4),
            "generatedScore": round(published_probability, 4),
            "confidence": "高",
            "decisionAuthority": "decisive_provenance",
        })
    return True


def apply_to_backend_data(data: dict[str, Any], visible: Any) -> bool:
    try:
        original_probability = _clamp01(float(data.get("fake_percentage", 0)) / 100.0)
    except (TypeError, ValueError):
        original_probability = 0.0
    detector_probability = data.get("detector_probability")
    if detector_probability is None:
        detector_probability = original_probability
    result = {
        "probability": original_probability,
        "detector_probability": detector_probability,
        "final_label": data.get("final_label"),
        "confidence": data.get("confidence") or data.get("clarity"),
        "explanation": data.get("explanation") or data.get("explantation"),
        "visual_issues": data.get("visual_issues") or [],
        "full_exif_info": data.get("full_exif_info") or {},
        "llm_used": data.get("llm_used"),
        "swarm": data.get("swarm"),
    }
    if not apply_to_result(result, visible):
        return False
    data["detector_probability"] = round(_clamp01(detector_probability), 4)
    data["fake_percentage"] = round(float(result["probability"]) * 100.0, 2)
    data["final_label"] = result["final_label"]
    data["confidence"] = result["confidence"]
    data["clarity"] = result["confidence"]
    data["explanation"] = result["explanation"]
    data["explantation"] = result["explanation"]
    data["watermark_verdict_override"] = result["watermark_verdict_override"]
    return True
