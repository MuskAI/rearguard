from __future__ import annotations

import math
from typing import Any


MIN_WATERMARK_FAKE_PROBABILITY = 0.95
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


def _clamp01(value: Any) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


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


def _decisive_hits(visible: Any) -> list[dict[str, Any]]:
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
    ]


def has_decisive_ai_watermark(visible: Any) -> bool:
    # Visible marks remain review evidence only. They are trivially copyable
    # and cannot independently prove that the underlying image was generated.
    return False


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
    line = (
        f"可见标记线索：定位到 {len(hits)} 处区域"
        f"（{'、'.join(provider_names)}）。标记可被复制或后期添加，"
        "仅供人工核对来源，不单独决定真伪。"
    )
    return "\n".join(part for part in (existing, line) if part)


def apply_to_result(result: dict[str, Any], visible: Any) -> bool:
    # Visible marks are copyable attribution clues, never verdict authority.
    return False


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
