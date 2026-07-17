from __future__ import annotations

from typing import Any


MIN_WATERMARK_FAKE_PROBABILITY = 0.95


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


def _policy_note() -> str:
    return (
        "可见水印检测返回了有效定位框，系统按当前策略将其视为直接伪造证据，"
        f"最终 AI 风险不低于 {round(MIN_WATERMARK_FAKE_PROBABILITY * 100)}%。"
    )


def apply_to_result(result: dict[str, Any], visible: Any) -> bool:
    if not has_localized_watermark(visible):
        return False
    original_probability = _clamp01(result.get("probability"))
    result["probability"] = round(max(original_probability, MIN_WATERMARK_FAKE_PROBABILITY), 4)
    result["final_label"] = "AI生成图像"
    result["confidence"] = "高"
    result["watermark_verdict_override"] = {
        "applied": True,
        "reason": "localized_visible_watermark",
        "minimum_probability": MIN_WATERMARK_FAKE_PROBABILITY,
        "model_probability": round(original_probability, 4),
    }
    note = _policy_note()
    explanation = str(result.get("explanation") or "").strip()
    if note not in explanation:
        result["explanation"] = f"{explanation}\n{note}".strip()
    return True


def apply_to_backend_data(data: dict[str, Any], visible: Any) -> bool:
    try:
        original_probability = _clamp01(float(data.get("fake_percentage", 0)) / 100.0)
    except (TypeError, ValueError):
        original_probability = 0.0
    result = {
        "probability": original_probability,
        "final_label": data.get("final_label"),
        "confidence": data.get("confidence") or data.get("clarity"),
        "explanation": data.get("explanation") or data.get("explantation"),
    }
    if not apply_to_result(result, visible):
        return False
    data.setdefault("detector_probability", round(original_probability, 4))
    data["fake_percentage"] = round(float(result["probability"]) * 100.0, 2)
    data["final_label"] = result["final_label"]
    data["confidence"] = result["confidence"]
    data["clarity"] = result["confidence"]
    data["explanation"] = result["explanation"]
    data["explantation"] = result["explanation"]
    data["watermark_verdict_override"] = result["watermark_verdict_override"]
    return True
