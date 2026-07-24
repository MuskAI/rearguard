"""Shared binary verdict labels for user-facing detection results."""

from __future__ import annotations

import math
from typing import Any


AI_GENERATED_LABEL = "AI生成图像"
REAL_IMAGE_LABEL = "真实图像"

_AI_MARKERS = ("ai", "生成", "伪造", "篡改", "深伪", "翻拍", "风险", "fake")
_REAL_MARKERS = ("真实", "实拍", "原生拍摄", "real")


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
    if any(marker in text for marker in _AI_MARKERS):
        return AI_GENERATED_LABEL
    if any(marker in text for marker in _REAL_MARKERS):
        return REAL_IMAGE_LABEL
    return (
        AI_GENERATED_LABEL
        if normalized_fake_probability(fake_probability) >= 0.5
        else REAL_IMAGE_LABEL
    )
