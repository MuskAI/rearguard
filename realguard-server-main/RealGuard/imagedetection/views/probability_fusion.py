"""Fuse Swarm pixel scores with provenance likelihood ratios."""
from __future__ import annotations

import math
from typing import Any, Iterable


MODEL_VERSION = "huijian-evidence-lr-v1"
CROSS_MODAL_EXPONENT = 0.75
BASE_RATE = 0.10


def _clamp(value: Any, default: float = 0.5) -> float:
    try:
        return min(max(float(value), 0.0001), 0.9999)
    except (TypeError, ValueError):
        return default


def _odds(probability: float) -> float:
    probability = _clamp(probability)
    return probability / (1.0 - probability)


def _probability(odds: float) -> float:
    return min(max(odds / (1.0 + odds), 0.0001), 0.9999)


def _weight(expert: dict[str, Any]) -> float:
    try:
        return max(float(expert.get("weight") or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def baseline_probability(experts: Iterable[dict[str, Any]]) -> tuple[float, list[str]]:
    """Pool only statistical/image experts; absence of metadata is neutral."""
    candidates = [
        expert for expert in experts
        if expert.get("status") == "success"
        and expert.get("score") is not None
        and expert.get("id") != "metadata"
        and expert.get("provenance_kind") not in {"c2pa", "watermark", "wam"}
    ]
    if not candidates:
        candidates = [
            expert for expert in experts
            if expert.get("status") == "success" and expert.get("score") is not None
        ]
    total_weight = sum(_weight(expert) for expert in candidates)
    if total_weight <= 0:
        total_weight = float(len(candidates) or 1)
        weights = [1.0 for _ in candidates]
    else:
        weights = [_weight(expert) for expert in candidates]
    score = sum(_clamp(expert.get("score")) * weight for expert, weight in zip(candidates, weights)) / total_weight
    return round(score, 6), [str(expert.get("id") or "unknown") for expert in candidates]


def _precheck_model(experts: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    for expert in experts:
        model = expert.get("probabilityModel")
        if expert.get("id") == "visible_watermark" and isinstance(model, dict):
            return model
    return None


def _local_factors(experts: Iterable[dict[str, Any]], occupied_groups: set[str]) -> list[dict[str, Any]]:
    factors: list[dict[str, Any]] = []
    capture_factors: list[dict[str, Any]] = []
    for expert in experts:
        if expert.get("status") != "success" or expert.get("score") is None:
            continue
        score = _clamp(expert.get("score"))
        expert_id = str(expert.get("id") or "")
        details = expert.get("details") if isinstance(expert.get("details"), dict) else {}
        if expert_id == "watermark" and score >= 0.9 and details.get("attribution"):
            factors.append({
                "kind": "known_invisible_ai_watermark",
                "label": f"已知生成器隐式水印：{details['attribution']}",
                "group": "invisible_watermark",
                "source": "swarm_watermark",
                "likelihoodRatio": 120.0,
                "effectiveLikelihoodRatio": 120.0,
                "direction": "fake",
            })
        elif (
            expert_id == "metadata"
            and score >= 0.85
            and details.get("verifiedAiMetadata") is True
            and "origin_declaration" not in occupied_groups
        ):
            factors.append({
                "kind": "ai_generation_metadata",
                "label": "元数据中的生成工具标识",
                "group": "origin_declaration",
                "source": "swarm_metadata",
                "likelihoodRatio": 80.0,
                "effectiveLikelihoodRatio": 80.0,
                "direction": "fake",
            })
        elif expert_id == "metadata" and details.get("verifiedAiMetadata") is not True:
            capture = details.get("captureEvidence") if isinstance(details.get("captureEvidence"), dict) else {}
            level = str(capture.get("level") or "")
            if capture.get("supportsRealCapture") is True and level in {"medium", "weak"}:
                ratio = min(max(float(capture.get("likelihoodRatio") or 1.0), 0.05), 1.0)
                capture_factors.append({
                    "kind": "camera_capture_metadata",
                    "label": "一致的相机拍摄元数据" if level == "medium" else "部分相机拍摄元数据",
                    "group": "camera_capture",
                    "source": "swarm_metadata",
                    "likelihoodRatio": ratio,
                    "effectiveLikelihoodRatio": ratio,
                    "direction": "real",
                })
        elif expert_id == "c2pa":
            chain_sources = set(details.get("chain_sources") or [])
            if (
                score <= 0.15
                and details.get("validation_severity") == "ok"
                and "camera" in chain_sources
                and "ai" not in chain_sources
                and not details.get("chain_conflict")
            ):
                capture_factors.append({
                    "kind": "valid_camera_c2pa",
                    "label": "通过校验的相机捕获内容凭证",
                    "group": "camera_capture",
                    "source": "c2pa",
                    "likelihoodRatio": 0.08,
                    "effectiveLikelihoodRatio": 0.08,
                    "direction": "real",
                })
    if capture_factors:
        factors.append(min(capture_factors, key=lambda item: float(item["likelihoodRatio"])))
    return factors


def fuse(experts: list[dict[str, Any]]) -> dict[str, Any]:
    baseline, baseline_experts = baseline_probability(experts)
    precheck = _precheck_model(experts) or {}
    factors = [dict(item) for item in precheck.get("factors") or [] if isinstance(item, dict)]
    occupied_groups = {str(item.get("group") or "") for item in factors}
    local_factors = _local_factors(experts, occupied_groups)
    factors.extend(local_factors)

    effective_lr = max(float(precheck.get("effectiveLikelihoodRatio") or 1.0), 0.01)
    for factor in local_factors:
        effective_lr *= max(float(factor.get("effectiveLikelihoodRatio") or 1.0), 0.01)

    fake_groups = {
        str(item.get("group") or "")
        for item in factors
        if float(item.get("effectiveLikelihoodRatio") or item.get("likelihoodRatio") or 1.0) > 1.0
    }
    real_groups = {
        str(item.get("group") or "")
        for item in factors
        if float(item.get("effectiveLikelihoodRatio") or item.get("likelihoodRatio") or 1.0) < 1.0
    }
    decisive = bool(precheck.get("decisive")) or any(
        item.get("kind") in {"known_invisible_ai_watermark", "ai_generation_metadata"}
        for item in local_factors
    )
    corroborated = bool(precheck.get("corroborated")) or len(fake_groups) >= 2
    conflicting = bool(fake_groups and real_groups)
    adjusted_baseline = baseline
    if corroborated:
        adjusted_baseline = max(adjusted_baseline, 0.35)
    elif decisive:
        adjusted_baseline = max(adjusted_baseline, BASE_RATE)

    posterior = adjusted_baseline
    if abs(effective_lr - 1.0) > 0.0001:
        posterior = _probability(_odds(adjusted_baseline) * (effective_lr ** CROSS_MODAL_EXPONENT))
    return {
        "version": MODEL_VERSION,
        "method": "weighted_pixel_baseline_plus_bayesian_likelihood_ratio",
        "pixelBaseline": round(baseline, 4),
        "adjustedBaseline": round(adjusted_baseline, 4),
        "baselineExperts": baseline_experts,
        "crossModalExponent": CROSS_MODAL_EXPONENT,
        "effectiveLikelihoodRatio": round(effective_lr, 3),
        "posterior": round(posterior, 4),
        "factors": factors,
        "decisive": decisive,
        "corroborated": corroborated,
        "conflicting": conflicting,
        "calibrationStatus": "policy_prior_pending_dataset_calibration",
        "note": "像素模型形成基线；AI 来源证据抬高风险，一致的实拍来源证据适度降低风险；同源证据已降权。",
        "logOdds": round(math.log(_odds(posterior)), 4),
    }
