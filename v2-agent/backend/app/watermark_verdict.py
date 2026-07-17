from __future__ import annotations

from typing import Any


MIN_WATERMARK_FAKE_CONFIDENCE = 0.95


def _clamp01(value: Any) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def has_localized_watermark(visible: Any) -> bool:
    if not isinstance(visible, dict) or not visible.get("detected"):
        return False
    for hit in visible.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        bbox = hit.get("bbox")
        if not isinstance(bbox, dict):
            continue
        if _clamp01(bbox.get("w")) > 0 and _clamp01(bbox.get("h")) > 0:
            return True
    return False


def apply(result: dict[str, Any], visible: Any) -> dict[str, Any]:
    if not has_localized_watermark(visible):
        return result

    original_confidence = _clamp01(result.get("confidence"))
    result["confidence"] = round(max(original_confidence, MIN_WATERMARK_FAKE_CONFIDENCE), 2)
    result["verdict"] = "highly_suspected_fake"
    result["watermarkVerdictOverride"] = {
        "applied": True,
        "reason": "localized_visible_watermark",
        "minimumConfidence": MIN_WATERMARK_FAKE_CONFIDENCE,
        "modelConfidence": round(original_confidence, 4),
    }
    policy_note = (
        "可见水印检测返回了有效定位框，系统按当前策略将其视为直接伪造证据，"
        f"最终 AI 风险置信度不低于 {round(MIN_WATERMARK_FAKE_CONFIDENCE * 100)}%。"
    )
    explanation = str(result.get("explanation") or "").strip()
    if policy_note not in explanation:
        result["explanation"] = f"{explanation}\n{policy_note}".strip()
    return result
