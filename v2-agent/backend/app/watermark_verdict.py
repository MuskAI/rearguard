from __future__ import annotations

import math
from typing import Any


MIN_WATERMARK_FAKE_CONFIDENCE = 0.95
MIN_EXPLICIT_WATERMARK_CONFIDENCE = 0.80
MIN_EXPLICIT_VERDICT_CONFIDENCE = 0.80
DECISIVE_PROVIDERS = frozenset({"gemini", "doubao", "jimeng", "jimeng_pill", "samsung"})
DECISIVE_METHOD = "remove_ai_watermarks_registry"
EXPLICIT_DECISIVE_METHOD = "explicit_ai_watermark_fusion"
DECISIVE_METHODS = frozenset({DECISIVE_METHOD, EXPLICIT_DECISIVE_METHOD})
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
    "samsung galaxy ai": "samsung",
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


def _provider_id(value: Any) -> str:
    return _PROVIDER_ALIASES.get(str(value or "").strip().lower(), "")


def _boxes_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    try:
        ax1, ay1 = float(first["x"]), float(first["y"])
        ax2, ay2 = ax1 + float(first["w"]), ay1 + float(first["h"])
        bx1, by1 = float(second["x"]), float(second["y"])
        bx2, by2 = bx1 + float(second["w"]), by1 + float(second["h"])
    except (KeyError, TypeError, ValueError):
        return False
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0.0, min(ay2, by2) - max(ay1, by1)
    )
    smaller = min(max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1), max(0.0, bx2 - bx1) * max(0.0, by2 - by1))
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
        if not isinstance(hit, dict):
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
            hit.get("yoloCorroborated") is True or hit.get("detectorLocalized") is True,
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
        and str(hit.get("method") or "").strip() in DECISIVE_METHODS
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
            "按当前规则，该证据可独立授权“AI生成图像”结论。"
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


def apply(result: dict[str, Any], visible: Any) -> dict[str, Any]:
    decisive_hits = _decisive_hits(visible)
    if not decisive_hits:
        return result
    explicit = visible.get("explicitWatermark") or {}
    explicit_verdict = explicit.get("aiWatermarkVerdict") or {}
    original_confidence = _clamp01(result.get("confidence"))
    published_confidence = max(
        MIN_WATERMARK_FAKE_CONFIDENCE,
        _clamp01(explicit.get("confidence")),
        _clamp01(explicit_verdict.get("confidence")),
        max((_clamp01(hit.get("confidence")) for hit in decisive_hits), default=0.0),
    )
    providers = sorted({
        str(hit.get("provider") or "").strip().lower()
        for hit in decisive_hits
        if str(hit.get("provider") or "").strip()
    })
    result.update({
        "verdict": "highly_suspected_fake",
        "confidence": round(published_confidence, 4),
        "riskScore": round(published_confidence, 4),
        "aiProbability": round(published_confidence, 4),
        "riskVector": {
            **(result.get("riskVector") if isinstance(result.get("riskVector"), dict) else {}),
            "aiGenerated": round(published_confidence, 4),
        },
        "decisionStatus": "verdict",
        "decisionAuthority": "decisive_provenance",
        "reviewRequired": False,
        "watermarkVerdictOverride": {
            "applied": True,
            "policyVersion": "explicit-ai-watermark-v2",
            "decisionAuthority": "decisive_provenance",
            "providers": providers,
            "hitCount": len(decisive_hits),
            "originalConfidence": round(original_confidence, 4),
            "publishedConfidence": round(published_confidence, 4),
            "watermarkConfidence": round(_clamp01(explicit.get("confidence")), 4),
            "verdictConfidence": round(_clamp01(explicit_verdict.get("confidence")), 4),
        },
    })
    result["explanation"] = build_explanation(result, visible)
    return result
