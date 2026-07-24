from __future__ import annotations

from typing import Any


REAL_VERDICT = "real"
FAKE_VERDICT = "highly_suspected_fake"


def _normalized_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score != score:
        return None
    if score > 1:
        score /= 100.0
    return max(0.0, min(score, 1.0))


def binary_verdict(result: dict | None) -> str:
    payload = result or {}
    label = str(payload.get("final_label") or payload.get("verdict") or "").strip().lower()
    if label == REAL_VERDICT or "真实" in label or "实拍" in label:
        return REAL_VERDICT
    if (
        label in {"suspected_fake", FAKE_VERDICT}
        or "ai" in label
        or "生成" in label
        or "伪造" in label
        or "篡改" in label
        or "深伪" in label
        or "翻拍" in label
        or "fake" in label
    ):
        return FAKE_VERDICT

    vector = payload.get("riskVector") if isinstance(payload.get("riskVector"), dict) else {}
    candidates = [
        payload.get("riskScore"),
        payload.get("aiProbability"),
        payload.get("confidence"),
        vector.get("aiGenerated"),
        vector.get("tampered"),
        vector.get("deepfake"),
    ]
    scores = [score for value in candidates if (score := _normalized_score(value)) is not None]
    return FAKE_VERDICT if scores and max(scores) >= 0.5 else REAL_VERDICT


def binary_label(result: dict | None) -> str:
    return "AI生成图像" if binary_verdict(result) == FAKE_VERDICT else "真实图像"
