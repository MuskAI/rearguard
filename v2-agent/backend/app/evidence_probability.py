"""Evidence-aware probability fusion shared by the V2 detection path."""
from __future__ import annotations

import copy
import math
from typing import Any


MODEL_VERSION = "huijian-evidence-lr-v1"
BASE_RATE = 0.10
CORRELATED_EVIDENCE_EXPONENT = 0.65
REPEATED_WATERMARK_EXPONENT = 0.35
CROSS_MODAL_EXPONENT = 0.75
KNOWN_VISIBLE_PROVIDERS = frozenset({"gemini", "doubao", "jimeng", "jimeng_pill", "samsung"})
KNOWN_VISIBLE_MIN_CONFIDENCE = {
    "gemini": 0.72,
    "doubao": 0.80,
    "jimeng": 0.80,
    "jimeng_pill": 0.80,
    "samsung": 0.80,
}
KNOWN_VISIBLE_MIN_AREA = 0.0001


def clamp_probability(value: Any, default: float = 0.0) -> float:
    try:
        return min(max(float(value), 0.0001), 0.9999)
    except (TypeError, ValueError):
        return default


def _odds(probability: float) -> float:
    value = clamp_probability(probability, BASE_RATE)
    return value / (1.0 - value)


def _probability(odds: float) -> float:
    return min(max(odds / (1.0 + odds), 0.0001), 0.9999)


def _signal_names(report: dict[str, Any]) -> set[str]:
    return {
        str(signal.get("name") or "")
        for signal in report.get("signals") or []
        if isinstance(signal, dict)
    }


def _c2pa_validation_state(report: dict[str, Any], clashes: list[str]) -> str:
    state = str(
        report.get("c2paValidationState")
        or report.get("validationState")
        or ""
    ).strip().lower()
    if state:
        return state
    for clash in clashes:
        normalized = clash.strip().lower()
        if normalized.startswith("c2pa_not_trusted:"):
            return normalized.rsplit(":", 1)[-1]
    return ""


def _is_integrity_failure(clash: str) -> bool:
    normalized = clash.strip().lower()
    if "c2pa" not in normalized:
        return True
    return any(token in normalized for token in (
        "invalid",
        "hard_binding",
        "hard-binding",
        "hard binding",
        "hardbinding",
        "binding_mismatch",
        "hash_mismatch",
        "tamper",
    ))


def _known_watermark_lr(confidence: float) -> float:
    confidence = clamp_probability(confidence, 0.5)
    return round(min(240.0, max(60.0, 20.0 * _odds(confidence))), 3)


def _confirmed_known_watermark(hit: dict[str, Any]) -> bool:
    provider = str(hit.get("provider") or "")
    bbox = hit.get("bbox") if isinstance(hit.get("bbox"), dict) else {}
    area = max(float(bbox.get("w") or 0.0), 0.0) * max(float(bbox.get("h") or 0.0), 0.0)
    return (
        provider in KNOWN_VISIBLE_PROVIDERS
        and hit.get("decisive") is True
        and float(hit.get("confidence") or 0.0) >= KNOWN_VISIBLE_MIN_CONFIDENCE[provider]
        and area >= KNOWN_VISIBLE_MIN_AREA
        and (hit.get("localizationConfirmed") is True or hit.get("yoloCorroborated") is True)
    )


def _factor(kind: str, label: str, likelihood_ratio: float, group: str, *, source: str) -> dict[str, Any]:
    ratio = min(max(float(likelihood_ratio), 0.01), 10_000.0)
    return {
        "kind": kind,
        "label": label,
        "source": source,
        "group": group,
        "likelihoodRatio": round(ratio, 3),
        "direction": "fake" if ratio > 1.0 else "real" if ratio < 1.0 else "neutral",
    }


def build_probability_model(report: dict[str, Any], known_hits: list[dict[str, Any]]) -> dict[str, Any]:
    factors: list[dict[str, Any]] = []
    signal_names = _signal_names(report)
    raw_clashes = [str(item) for item in report.get("integrityClashes") or [] if str(item)]
    c2pa_validation_state = _c2pa_validation_state(report, raw_clashes)
    clashes = [clash for clash in raw_clashes if _is_integrity_failure(clash)]
    if (
        c2pa_validation_state == "invalid"
        and not any("c2pa" in clash.lower() for clash in clashes)
    ):
        clashes.append("c2pa_validation_invalid")
    has_c2pa_integrity_clash = any(
        "c2pa" in clash.lower()
        for clash in clashes
    )
    ai_from_metadata = bool(report.get("aiFromMetadata"))
    is_ai_generated = report.get("isAiGenerated") is True
    source_kind = str(report.get("aiSourceKind") or "")
    c2pa_trusted = report.get("c2paTrusted") is True
    capture = report.get("captureEvidence") if isinstance(report.get("captureEvidence"), dict) else {}

    if ai_from_metadata and is_ai_generated:
        if "c2pa" in signal_names and c2pa_trusted and source_kind == "enhanced":
            factors.append(_factor(
                "ai_enhancement_declaration", "AI 合成编辑来源声明", 150.0,
                "origin_declaration", source="c2pa",
            ))
        elif "c2pa" in signal_names and c2pa_trusted:
            factors.append(_factor(
                "valid_ai_c2pa", "通过校验的 AI 生成内容凭证", 1000.0,
                "origin_declaration", source="c2pa",
            ))
        elif "c2pa" in signal_names and c2pa_validation_state == "valid":
            # A valid signature from an untrusted signer is readable provenance,
            # not evidence for or against AI generation.
            pass
        elif "c2pa" in signal_names:
            factors.append(_factor(
                "unverified_ai_declaration", "未通过可信校验的 AI 来源声明", 1.5,
                "untrusted_provenance", source="c2pa",
            ))
        else:
            factors.append(_factor(
                "editable_ai_metadata", "可编辑的 AI 生成元数据或参数", 1.25,
                "metadata_context", source="metadata",
            ))

    best_by_provider: dict[str, dict[str, Any]] = {}
    for hit in known_hits:
        provider = str(hit.get("provider") or "unknown")
        if not _confirmed_known_watermark(hit):
            continue
        previous = best_by_provider.get(provider)
        if previous is None or float(hit.get("confidence") or 0.0) > float(previous.get("confidence") or 0.0):
            best_by_provider[provider] = hit
    for provider, hit in best_by_provider.items():
        confidence = clamp_probability(hit.get("confidence"), 0.5)
        label = str(hit.get("label") or provider)
        factors.append(_factor(
            "known_visible_ai_watermark", f"已知 AI 平台水印：{label}",
            _known_watermark_lr(confidence), "known_watermark", source=provider,
        ))

    if clashes:
        factors.append(_factor(
            "metadata_integrity_clash", "来源凭证或元数据完整性冲突", 9.0,
            "untrusted_provenance" if has_c2pa_integrity_clash else "integrity",
            source="、".join(clashes[:3]),
        ))

    capture_level = str(capture.get("level") or "")
    if capture.get("supportsRealCapture") is True and capture_level in {"strong", "medium", "weak"}:
        default_ratio = {"strong": 0.08, "medium": 0.65, "weak": 0.84}[capture_level]
        ratio = min(max(float(capture.get("likelihoodRatio") or default_ratio), 0.05), 1.0)
        factors.append(_factor(
            "valid_camera_c2pa" if capture_level == "strong" else "camera_capture_metadata",
            "通过校验的相机捕获内容凭证" if capture_level == "strong" else "一致的相机拍摄元数据" if capture_level == "medium" else "部分相机拍摄元数据",
            ratio,
            "camera_capture",
            source="c2pa" if capture_level == "strong" else "metadata",
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
                exponent = REPEATED_WATERMARK_EXPONENT if group == "known_watermark" else CORRELATED_EVIDENCE_EXPONENT
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
        "valid_ai_c2pa", "ai_enhancement_declaration",
    }
    active_kinds = {str(item["kind"]) for item in effective_factors}
    fake_groups = {str(item["group"]) for item in effective_factors if float(item["effectiveLikelihoodRatio"]) > 1.0}
    real_groups = {str(item["group"]) for item in effective_factors if float(item["effectiveLikelihoodRatio"]) < 1.0}
    return {
        "version": MODEL_VERSION,
        "method": "bayesian_likelihood_ratio",
        "baseRate": BASE_RATE,
        "posterior": round(posterior, 4),
        "effectiveLikelihoodRatio": round(effective_lr, 3),
        "factors": effective_factors,
        "decisive": bool(active_kinds.intersection(decisive_kinds)),
        "corroborated": len(fake_groups) >= 2,
        "conflicting": bool(fake_groups and real_groups),
        "calibrationStatus": "policy_prior_pending_dataset_calibration",
        "note": "可见标记只作诊断归属线索；只有通过校验的内容凭证可短路模型，所有融合权重仍需使用标注集校准。",
    }


def fuse_with_analysis(analysis: dict[str, Any], probability_model: dict[str, Any] | None) -> dict[str, Any]:
    """Fuse provenance likelihood ratios with a pixel-model baseline."""
    if not isinstance(probability_model, dict):
        return analysis
    effective_lr = max(float(probability_model.get("effectiveLikelihoodRatio") or 1.0), 0.01)
    if abs(effective_lr - 1.0) <= 0.0001:
        return analysis

    merged = copy.deepcopy(analysis)
    baseline = clamp_probability(merged.get("confidence"), 0.5)
    if probability_model.get("corroborated"):
        baseline = max(baseline, 0.35)
    elif probability_model.get("decisive"):
        baseline = max(baseline, BASE_RATE)
    fused = _probability(_odds(baseline) * (effective_lr ** CROSS_MODAL_EXPONENT))
    factors = probability_model.get("factors") or []
    kinds = {str(item.get("kind") or "") for item in factors if isinstance(item, dict)}

    merged["confidence"] = round(fused, 4)
    merged["verdict"] = "highly_suspected_fake" if fused >= 0.98 else "suspected_fake" if fused >= 0.62 else "real"
    merged["probabilityModel"] = {
        **probability_model,
        "pixelBaseline": round(baseline, 4),
        "crossModalExponent": CROSS_MODAL_EXPONENT,
        "posterior": round(fused, 4),
    }
    if kinds:
        has_real_support = any(
            float(item.get("effectiveLikelihoodRatio") or item.get("likelihoodRatio") or 1.0) < 1.0
            for item in factors if isinstance(item, dict)
        )
        has_fake_support = any(
            float(item.get("effectiveLikelihoodRatio") or item.get("likelihoodRatio") or 1.0) > 1.0
            for item in factors if isinstance(item, dict)
        )
        merged.setdefault("dimensions", []).append({
            "key": "evidence_probability",
            "label": "来源证据概率融合",
            "score": round(fused, 2),
            "result": "、".join(str(item.get("label") or "") for item in factors[:3] if isinstance(item, dict)),
        })
        if has_real_support and has_fake_support:
            impact = "支持实拍与支持生成的来源证据相互制衡"
        elif has_real_support:
            impact = "一致的实拍来源证据对像素模型风险作了适度下调"
        else:
            impact = "AI 来源证据提高了像素模型风险"
        merged["explanation"] = f"{str(merged.get('explanation') or '').strip()}\n{impact}，融合后风险概率为 {fused * 100:.2f}%。".strip()
    return merged
