from __future__ import annotations

import math
from typing import Any


MIN_WATERMARK_FAKE_CONFIDENCE = 0.95
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
    if visible.get("coordinateSpace") != "display_normalized_v1":
        return []
    display_size = visible.get("displaySize")
    if not isinstance(display_size, dict):
        return []
    try:
        display_width = int(display_size["width"])
        display_height = int(display_size["height"])
    except (KeyError, TypeError, ValueError):
        return []
    if display_width <= 0 or display_height <= 0:
        return []
    localized = []
    for hit in visible.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        bbox = hit.get("bbox")
        if not isinstance(bbox, dict):
            continue
        try:
            x = float(bbox["x"])
            y = float(bbox["y"])
            width = float(bbox["w"])
            height = float(bbox["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            continue
        if (
            0.0 <= x <= 1.0
            and 0.0 <= y <= 1.0
            and width > 0.0
            and height > 0.0
            and x + width <= 1.0
            and y + height <= 1.0
        ):
            localized.append(hit)
    return localized


def has_localized_watermark(visible: Any) -> bool:
    return bool(_localized_hits(visible))


def _decisive_hits(visible: Any) -> list[dict[str, Any]]:
    if not isinstance(visible, dict):
        return []
    if visible.get("registrySupported") is not True or visible.get("positiveEvidenceSupported") is not True:
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
    ]


def has_decisive_ai_watermark(visible: Any) -> bool:
    # A localized platform mark is attribution evidence, not cryptographic
    # provenance. Keep it visible to reviewers without granting verdict power.
    return False


def _percent(value: Any) -> str:
    return f"{_clamp01(value) * 100:.1f}%"


def _model_tendency(probability: float) -> str:
    if probability < 0.35:
        return "偏向真实"
    if probability < 0.75:
        return "处于边界区间"
    return "偏向 AI 生成"


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


def apply(result: dict[str, Any], visible: Any) -> dict[str, Any]:
    # Visible marks are copyable attribution clues, never verdict authority.
    return result
