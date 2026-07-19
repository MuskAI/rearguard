from __future__ import annotations

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


def _decisive_hits(visible: Any) -> list[dict[str, Any]]:
    return [
        hit
        for hit in _localized_hits(visible)
        if hit.get("decisive") is True
        and (provider := str(hit.get("provider") or "").strip().lower()) in DECISIVE_PROVIDERS
        and str(hit.get("method") or "").strip() == DECISIVE_METHOD
        and _clamp01(hit.get("confidence")) >= PROVIDER_MIN_CONFIDENCE[provider]
        and _clamp01((hit.get("bbox") or {}).get("w")) * _clamp01((hit.get("bbox") or {}).get("h")) >= MIN_LOCALIZED_AREA
        and (hit.get("localizationConfirmed") is True or hit.get("yoloCorroborated") is True)
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
    hits = _decisive_hits(visible)
    if not hits:
        return str(result.get("explanation") or "").strip()

    provider_names = []
    for hit in hits:
        provider = str(hit.get("provider") or "").strip()
        label = str(hit.get("label") or "").strip() or _PROVIDER_LABELS.get(provider) or "通用可见水印"
        if label not in provider_names:
            provider_names.append(label)
    highest_confidence = max((_clamp01(hit.get("confidence")) for hit in hits), default=0.0)

    override = result.get("watermark_verdict_override") or {}
    raw_probability = result.get("detector_probability")
    if raw_probability is None:
        raw_probability = override.get("model_probability")
    if raw_probability is None:
        raw_probability = result.get("probability")
    raw_probability = _clamp01(raw_probability)

    lines = [
        (
            f"决定性证据：已知 AI 平台水印注册表定位到 {len(hits)} 处有效区域"
            f"（{'、'.join(provider_names)}，最高置信度 {_percent(highest_confidence)}）。"
            f"经平台类型归属确认，该标记属于直接来源证据，综合 AI 风险提升至至少 "
            f"{round(MIN_WATERMARK_FAKE_PROBABILITY * 100)}%。"
        ),
        (
            f"主模型：原始 AI 风险为 {_percent(raw_probability)}，判断{_model_tendency(raw_probability)}；"
            "该分数保留作辅助参考，但不覆盖水印证据。"
        ),
    ]

    visual_issues = _positive_visual_issues(result.get("visual_issues"))
    swarm = result.get("swarm") or {}
    if isinstance(swarm, dict) and swarm.get("enabled"):
        effective = int(swarm.get("effectiveExperts") or 0)
        total = int(swarm.get("totalExperts") or 0)
        lines.append(f"多源复核：{effective}/{total} 路证据完成有效复核；其结果作为辅助证据参与解释。")
    elif visual_issues:
        lines.append(
            f"视觉复核：提取到 {len(visual_issues)} 项可复核线索"
            f"（{visual_issues[0]}）；这些线索不是本次决定性依据。"
        )
    elif result.get("llm_used") is False:
        lines.append("视觉复核：本次未完成多模态视觉复核，不生成替代性视觉结论。")
    else:
        lines.append("视觉复核：未提取到明确异常线索，本项未参与抬高风险。")

    metadata = result.get("all_metadata") or result.get("full_exif_info") or {}
    if isinstance(metadata, dict) and metadata:
        lines.append(f"元数据：已读取 {len(metadata)} 项，仅作辅助线索；本次不是决定性依据。")
    else:
        lines.append("元数据：未读取到可用元数据；元数据缺失本身不作为伪造证据。")
    lines.append("综合结论：本次由已确认的 AI 平台水印来源证据主导，判定为 AI 生成图像；当前置信度：高。")
    return "\n".join(lines)


def apply_to_result(result: dict[str, Any], visible: Any) -> bool:
    if not has_decisive_ai_watermark(visible):
        return False
    existing_override = result.get("watermark_verdict_override") or {}
    original_probability = result.get("detector_probability")
    if original_probability is None:
        original_probability = existing_override.get("model_probability")
    if original_probability is None:
        original_probability = result.get("probability")
    original_probability = _clamp01(original_probability)
    result["probability"] = round(max(original_probability, MIN_WATERMARK_FAKE_PROBABILITY), 4)
    result["final_label"] = "AI生成图像"
    result["confidence"] = "高"
    result["watermark_verdict_override"] = {
        "applied": True,
        "reason": "known_ai_platform_visible_watermark",
        "minimum_probability": MIN_WATERMARK_FAKE_PROBABILITY,
        "model_probability": round(original_probability, 4),
    }
    result["explanation"] = build_explanation(result, visible)
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
