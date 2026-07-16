"""Transparent likelihood-ratio fusion for provenance evidence.

The constants are conservative policy priors, not claims of empirical
calibration. They are versioned and surfaced in the API so they can later be
replaced by likelihood ratios fitted on a labeled validation set.
"""
from __future__ import annotations

import math
from typing import Any


MODEL_VERSION = "huijian-evidence-lr-v1"
BASE_RATE = 0.10
CORRELATED_EVIDENCE_EXPONENT = 0.65
REPEATED_WATERMARK_EXPONENT = 0.35
KNOWN_VISIBLE_PROVIDERS = frozenset({"gemini", "doubao", "jimeng", "jimeng_pill", "samsung"})


def _clamp_probability(value: Any, default: float = 0.0) -> float:
    try:
        return min(max(float(value), 0.0001), 0.9999)
    except (TypeError, ValueError):
        return default


def _odds(probability: float) -> float:
    value = _clamp_probability(probability, BASE_RATE)
    return value / (1.0 - value)


def _probability(odds: float) -> float:
    return min(max(odds / (1.0 + odds), 0.0001), 0.9999)


def _signal_names(report: dict[str, Any]) -> set[str]:
    return {
        str(signal.get("name") or "")
        for signal in report.get("signals") or []
        if isinstance(signal, dict)
    }


def _known_watermark_lr(confidence: float) -> float:
    confidence = _clamp_probability(confidence, 0.5)
    return round(min(240.0, max(60.0, 20.0 * _odds(confidence))), 3)


def _factor(
    kind: str,
    label: str,
    likelihood_ratio: float,
    group: str,
    *,
    source: str,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "source": source,
        "group": group,
        "likelihoodRatio": round(max(float(likelihood_ratio), 1.0), 3),
    }


def build_probability_model(
    report: dict[str, Any],
    known_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a versioned evidence model from positive provenance signals."""
    factors: list[dict[str, Any]] = []
    signal_names = _signal_names(report)
    clashes = [str(item) for item in report.get("integrityClashes") or [] if str(item)]
    has_c2pa_integrity_clash = any(
        "c2pa" in clash.lower() and any(token in clash.lower() for token in ("invalid", "signature", "tamper"))
        for clash in clashes
    )
    ai_from_metadata = bool(report.get("aiFromMetadata"))
    is_ai_generated = report.get("isAiGenerated") is True
    source_kind = str(report.get("aiSourceKind") or "")

    if ai_from_metadata and is_ai_generated:
        if clashes:
            factors.append(_factor(
                "unverified_ai_declaration",
                "签名异常的 AI 来源声明",
                1.5,
                "untrusted_provenance",
                source="c2pa" if "c2pa" in signal_names else "metadata",
            ))
        elif source_kind == "enhanced":
            factors.append(_factor(
                "ai_enhancement_declaration",
                "AI 合成编辑来源声明",
                150.0,
                "origin_declaration",
                source="c2pa" if "c2pa" in signal_names else "metadata",
            ))
        elif "c2pa" in signal_names:
            factors.append(_factor(
                "valid_ai_c2pa",
                "通过校验的 AI 生成内容凭证",
                1000.0,
                "origin_declaration",
                source="c2pa",
            ))
        else:
            factors.append(_factor(
                "ai_generation_metadata",
                "明确的 AI 生成元数据或参数",
                250.0,
                "origin_declaration",
                source="metadata",
            ))

    best_by_provider: dict[str, dict[str, Any]] = {}
    for hit in known_hits:
        provider = str(hit.get("provider") or "unknown")
        if provider not in KNOWN_VISIBLE_PROVIDERS or hit.get("decisive") is not True:
            continue
        previous = best_by_provider.get(provider)
        if previous is None or float(hit.get("confidence") or 0.0) > float(previous.get("confidence") or 0.0):
            best_by_provider[provider] = hit
    for provider, hit in best_by_provider.items():
        confidence = _clamp_probability(hit.get("confidence"), 0.5)
        label = str(hit.get("label") or provider)
        factors.append(_factor(
            "known_visible_ai_watermark",
            f"已知 AI 平台水印：{label}",
            _known_watermark_lr(confidence),
            "known_watermark",
            source=provider,
        ))

    if clashes:
        factors.append(_factor(
            "metadata_integrity_clash",
            "来源凭证或元数据完整性冲突",
            9.0,
            "untrusted_provenance" if has_c2pa_integrity_clash else "integrity",
            source="、".join(clashes[:3]),
        ))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for factor in factors:
        grouped.setdefault(str(factor["group"]), []).append(factor)

    effective_lr = 1.0
    effective_factors: list[dict[str, Any]] = []
    for group, group_factors in grouped.items():
        ranked = sorted(group_factors, key=lambda item: float(item["likelihoodRatio"]), reverse=True)
        for index, factor in enumerate(ranked):
            exponent = 1.0
            if index > 0:
                exponent = (
                    REPEATED_WATERMARK_EXPONENT
                    if group == "known_watermark"
                    else CORRELATED_EVIDENCE_EXPONENT
                )
            contribution = float(factor["likelihoodRatio"]) ** exponent
            effective_lr *= contribution
            effective_factors.append({
                **factor,
                "correlationExponent": exponent,
                "effectiveLikelihoodRatio": round(contribution, 3),
                "logOddsContribution": round(math.log(contribution), 4),
            })

    posterior = _probability(_odds(BASE_RATE) * effective_lr)
    decisive_kinds = {
        "known_visible_ai_watermark",
        "valid_ai_c2pa",
        "ai_generation_metadata",
        "ai_enhancement_declaration",
    }
    active_kinds = {str(item["kind"]) for item in effective_factors}
    return {
        "version": MODEL_VERSION,
        "method": "bayesian_likelihood_ratio",
        "baseRate": BASE_RATE,
        "posterior": round(posterior, 4),
        "effectiveLikelihoodRatio": round(effective_lr, 3),
        "factors": effective_factors,
        "decisive": bool(active_kinds.intersection(decisive_kinds)),
        "corroborated": len({item["group"] for item in effective_factors}) >= 2,
        "calibrationStatus": "policy_prior_pending_dataset_calibration",
        "note": (
            "当前数值为版本化证据似然比模型输出；上线后应使用标注集进行温度缩放或等距回归校准。"
        ),
    }
