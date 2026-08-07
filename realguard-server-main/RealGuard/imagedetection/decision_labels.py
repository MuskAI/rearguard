"""Shared binary verdict labels for user-facing detection results."""

from __future__ import annotations

import math
from typing import Any


AI_GENERATED_LABEL = "AI生成图像"
REAL_IMAGE_LABEL = "真实图像"

_AI_LABELS = {
    "ai",
    "ai生成",
    "ai生成图像",
    "ai生成视频",
    "fake",
    "highly_suspected_fake",
    "suspected_fake",
    "高风险",
    "生成图像",
    "疑似ai生成",
    "疑似伪造",
    "疑似篡改图像",
    "疑似深伪图像",
}
_REAL_LABELS = {
    "real",
    "低风险",
    "原生拍摄",
    "实拍",
    "真实",
    "真实图像",
    "真实视频",
}
_AI_MARKERS = ("ai生成", "疑似ai", "伪造", "篡改", "深伪", "翻拍", "fake")
_REAL_MARKERS = ("真实", "实拍", "原生拍摄")


def normalized_fake_probability(value: Any, default: float = 0.5) -> float:
    try:
        probability = float(value)
    except (TypeError, ValueError):
        probability = float(default)
    if not math.isfinite(probability):
        probability = float(default)
    if probability > 1.0:
        probability /= 100.0
    return max(0.0, min(1.0, probability))


def binary_final_label(label: Any = "", fake_probability: Any = None) -> str:
    text = str(label or "").strip().lower()
    if text in _AI_LABELS or "高风险" in text:
        return AI_GENERATED_LABEL
    if text in _REAL_LABELS or "低风险" in text:
        return REAL_IMAGE_LABEL
    if any(marker in text for marker in _AI_MARKERS):
        return AI_GENERATED_LABEL
    if any(marker in text for marker in _REAL_MARKERS):
        return REAL_IMAGE_LABEL
    return (
        AI_GENERATED_LABEL
        if normalized_fake_probability(fake_probability) >= 0.5
        else REAL_IMAGE_LABEL
    )
