"""Decision policy for provenance-first AI image detection.

The detector reports evidence. This module decides whether that evidence is
specific enough to skip the statistical image model.
"""
from __future__ import annotations

from typing import Any

try:
    from .evidence_probability import build_probability_model
except ImportError:  # Service is also launched as a top-level Gunicorn module.
    from evidence_probability import build_probability_model


KNOWN_VISIBLE_PROVIDERS = frozenset({"gemini", "doubao", "jimeng", "jimeng_pill", "samsung"})
VISIBLE_ONLY_THRESHOLDS = {
    "gemini": 0.62,
    "doubao": 0.82,
    "jimeng": 0.82,
    "jimeng_pill": 0.88,
    "samsung": 0.82,
}


def visible_hit_is_decisive(
    provider: str,
    confidence: float,
    bbox: dict[str, Any],
    *,
    corroborated: bool,
) -> bool:
    """Visible marks are localizable evidence, never decision authority.

    A logo or platform mark can be copied onto unrelated content. Position,
    confidence and registry attribution improve review quality but cannot prove
    how the underlying pixels were created.
    """
    try:
        center_x = float(bbox["x"]) + float(bbox["w"]) / 2
        center_y = float(bbox["y"]) + float(bbox["h"]) / 2
    except (KeyError, TypeError, ValueError):
        return False

    if provider in {"gemini", "doubao", "jimeng"}:
        location_ok = center_x >= 0.72 and center_y >= 0.72
    elif provider == "samsung":
        location_ok = center_x <= 0.45 and center_y >= 0.68
    elif provider == "jimeng_pill":
        location_ok = center_x <= 0.35 and center_y <= 0.35
    else:
        return False
    _ = location_ok and (corroborated or confidence >= VISIBLE_ONLY_THRESHOLDS.get(provider, 1.0))
    return False


def build_decision(report: dict[str, Any], visible_hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the model-gating decision for one provenance scan.

    Only locally verified cryptographic AI provenance can bypass the model.
    Visible marks, generic logos, camera C2PA, cloud-manifest pointers,
    TrustMark by itself, and hosting hints remain non-decisive.
    """
    known_hits: list[dict[str, Any]] = []
    source_kind = report.get("aiSourceKind")
    probability_model = build_probability_model(report, known_hits)
    factor_kinds = {str(item.get("kind") or "") for item in probability_model.get("factors") or []}
    evidence_kinds: list[str] = []
    if "known_visible_ai_watermark" in factor_kinds:
        evidence_kinds.append("visible_watermark")
    if factor_kinds.intersection({"valid_ai_c2pa", "ai_enhancement_declaration"}):
        evidence_kinds.append("c2pa")
    elif factor_kinds.intersection({"ai_generation_metadata", "unverified_ai_declaration"}):
        evidence_kinds.append("metadata")
    if "metadata_integrity_clash" in factor_kinds:
        evidence_kinds.append("integrity_clash")

    has_authoritative_source = bool(
        factor_kinds.intersection({"valid_ai_c2pa", "ai_enhancement_declaration"})
    )
    if probability_model.get("decisive") is not True or not has_authoritative_source:
        summary = "未发现足以直接判定的 AI 来源标记，继续调用图像检测模型。"
        if "metadata_integrity_clash" in factor_kinds:
            summary = "发现来源凭证或元数据完整性冲突，但单项冲突不足以证明 AI 生成，继续调用图像检测模型。"
        return {
            "shortCircuit": False,
            "modelRequired": True,
            "verdict": None,
            "confidence": 0.0,
            "reason": "no_decisive_ai_provenance",
            "evidenceKinds": evidence_kinds,
            "summary": summary,
            "probabilityModel": probability_model,
        }

    confidence = float(probability_model.get("posterior") or 0.0)
    has_known_watermark = "known_visible_ai_watermark" in factor_kinds
    has_integrity_clash = "metadata_integrity_clash" in factor_kinds
    has_ai_declaration = bool(factor_kinds.intersection({
        "valid_ai_c2pa",
        "ai_enhancement_declaration",
        "ai_generation_metadata",
        "unverified_ai_declaration",
    }))

    if has_known_watermark and has_integrity_clash:
        reason = "watermark_metadata_conflict"
        summary = (
            f"已知 AI 平台水印与来源完整性冲突相互印证，证据融合风险概率为 {confidence * 100:.2f}%。"
            "该组合达到高置信等级，但仍保留原始证据供人工复核。"
        )
    elif has_known_watermark and has_ai_declaration:
        reason = "corroborated_ai_provenance"
        summary = (
            f"已知 AI 平台水印与文件中的 AI 来源声明相互印证，证据融合风险概率为 {confidence * 100:.2f}%。"
        )
    elif "valid_ai_c2pa" in factor_kinds:
        reason = "c2pa_ai_generated"
        summary = f"通过校验的内容凭证声明该文件为 AI 生成，证据融合风险概率为 {confidence * 100:.2f}%。"
    elif "ai_enhancement_declaration" in factor_kinds:
        reason = "c2pa_ai_enhanced"
        summary = f"内容凭证声明存在 AI 合成编辑，证据融合风险概率为 {confidence * 100:.2f}%。"
    else:
        reason = "known_visible_ai_watermark"
        providers = "、".join(dict.fromkeys(str(hit.get("label") or hit.get("provider")) for hit in known_hits))
        summary = f"检测到已知 AI 平台可见标记（{providers}），证据融合风险概率为 {confidence * 100:.2f}%。"

    verdict = "highly_suspected_fake" if confidence >= 0.98 else "suspected_fake"
    if source_kind == "enhanced" and not has_known_watermark:
        verdict = "suspected_fake"

    return {
        "shortCircuit": True,
        "modelRequired": False,
        "verdict": verdict,
        "confidence": round(confidence, 4),
        "reason": reason,
        "evidenceKinds": evidence_kinds,
        "summary": summary,
        "probabilityModel": probability_model,
    }
