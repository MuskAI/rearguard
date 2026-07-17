from __future__ import annotations

from typing import Any


MIN_WATERMARK_FAKE_CONFIDENCE = 0.95

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
    localized = []
    for hit in visible.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        bbox = hit.get("bbox")
        if not isinstance(bbox, dict):
            continue
        if _clamp01(bbox.get("w")) > 0 and _clamp01(bbox.get("h")) > 0:
            localized.append(hit)
    return localized


def has_localized_watermark(visible: Any) -> bool:
    return bool(_localized_hits(visible))


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
    if not hits:
        return str(result.get("explanation") or "").strip()

    provider_names = []
    for hit in hits:
        provider = str(hit.get("provider") or "").strip()
        label = str(hit.get("label") or "").strip() or _PROVIDER_LABELS.get(provider) or "通用可见水印"
        if label not in provider_names:
            provider_names.append(label)
    highest_confidence = max((_clamp01(hit.get("confidence")) for hit in hits), default=0.0)

    override = result.get("watermarkVerdictOverride") or {}
    model_confidence = _clamp01(override.get("modelConfidence", result.get("confidence")))
    lines = [
        (
            f"决定性证据：可见水印检测定位到 {len(hits)} 处有效区域"
            f"（{'、'.join(provider_names)}，最高置信度 {_percent(highest_confidence)}）。"
            f"按当前规则，有效水印定位框属于直接伪造证据，综合 AI 风险提升至至少 "
            f"{round(MIN_WATERMARK_FAKE_CONFIDENCE * 100)}%。"
        ),
        (
            f"主模型：原始 AI 风险为 {_percent(model_confidence)}，判断{_model_tendency(model_confidence)}；"
            "该分数保留作辅助参考，但不覆盖水印证据。"
        ),
    ]

    dimensions = [
        item for item in (result.get("dimensions") or [])
        if isinstance(item, dict) and item.get("key") != "visible_watermark"
    ]
    positive_dimensions = [
        str(item.get("label") or "").strip()
        for item in dimensions
        if _clamp01(item.get("score")) >= 0.5 and str(item.get("label") or "").strip()
    ]
    if positive_dimensions:
        lines.append(
            f"辅助分析：已完成 {len(dimensions)} 个证据维度，其中"
            f"{'、'.join(positive_dimensions[:2])}提示风险；这些线索不是本次决定性依据。"
        )
    else:
        lines.append(f"辅助分析：已完成 {len(dimensions)} 个证据维度，未出现可替代水印证据的独立强结论。")

    provenance = result.get("provenance") or {}
    if isinstance(provenance, dict) and provenance.get("hasCredentials"):
        validation = str(provenance.get("validationState") or "待验证")
        lines.append(f"来源凭证：检测到内容凭证，签名状态为{validation}；作为来源链辅助证据。")
    else:
        lines.append("来源凭证：未发现可验证的来源凭证；凭证缺失本身不作为伪造证据。")
    lines.append("综合结论：本次由有效水印定位证据主导，判定为高度疑似 AI 生成；当前置信度：高。")
    return "\n".join(lines)


def apply(result: dict[str, Any], visible: Any) -> dict[str, Any]:
    if not has_localized_watermark(visible):
        return result

    existing_override = result.get("watermarkVerdictOverride") or {}
    original_confidence = _clamp01(existing_override.get("modelConfidence", result.get("confidence")))
    result["confidence"] = round(max(original_confidence, MIN_WATERMARK_FAKE_CONFIDENCE), 2)
    result["verdict"] = "highly_suspected_fake"
    result["watermarkVerdictOverride"] = {
        "applied": True,
        "reason": "localized_visible_watermark",
        "minimumConfidence": MIN_WATERMARK_FAKE_CONFIDENCE,
        "modelConfidence": round(original_confidence, 4),
    }
    result["explanation"] = build_explanation(result, visible)
    return result
