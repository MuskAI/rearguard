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
            })
    return factors


def fuse(experts: list[dict[str, Any]]) -> dict[str, Any]:
    baseline, baseline_experts = baseline_probability(experts)
    precheck = _precheck_model(experts) or {}
    factors = [dict(item) for item in precheck.get("factors") or [] if isinstance(item, dict)]
    occupied_groups = {str(item.get("group") or "") for item in factors}
    local_factors = _local_factors(experts, occupied_groups)
    factors.extend(local_factors)

    effective_lr = max(float(precheck.get("effectiveLikelihoodRatio") or 1.0), 1.0)
    for factor in local_factors:
        effective_lr *= max(float(factor.get("effectiveLikelihoodRatio") or 1.0), 1.0)

    groups = {str(item.get("group") or "") for item in factors}
    decisive = bool(precheck.get("decisive")) or any(
        item.get("kind") in {"known_invisible_ai_watermark", "ai_generation_metadata"}
        for item in local_factors
    )
    corroborated = bool(precheck.get("corroborated")) or len(groups) >= 2
    adjusted_baseline = baseline
    if corroborated:
        adjusted_baseline = max(adjusted_baseline, 0.35)
    elif decisive:
        adjusted_baseline = max(adjusted_baseline, BASE_RATE)

    posterior = adjusted_baseline
    if effective_lr > 1.0:
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
        "calibrationStatus": "policy_prior_pending_dataset_calibration",
        "note": "像素模型形成基线，独立来源证据以似然比更新；同源证据已降权，普通 Logo 不参与。",
        "logOdds": round(math.log(_odds(posterior)), 4),
    }
