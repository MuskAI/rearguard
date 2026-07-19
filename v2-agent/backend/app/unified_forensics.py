"""Unified research-facing forensic output adapter.

The production API already exposes stable product fields such as verdict,
confidence, dimensions and regions.  This module adds a research-oriented
wrapper that follows the AIGC image/video forensics roadmap interface without
forcing the product UI to change at the same pace as the research protocol.
"""
from __future__ import annotations

from typing import Any


INTERFACE_VERSION = "aigc-forensics-unified-v0.1"


def _float01(value: Any, default: float = 0.0) -> float:
    try:
        return round(min(max(float(value), 0.0), 1.0), 3)
    except (TypeError, ValueError):
        return default


def _cost(result: dict[str, Any]) -> dict[str, Any]:
    usage = result.get("tokenUsage") or {}
    return {
        "elapsed_ms": int(result.get("elapsedMs") or 0),
        "cache_hit": bool(result.get("cacheHit")),
        "source": result.get("source") or "unknown",
        "model_version": result.get("modelVersion") or "unknown",
        "cache_version": result.get("cacheVersion") or "unknown",
        "prompt_tokens": int(usage.get("promptTokens") or 0),
        "completion_tokens": int(usage.get("completionTokens") or 0),
        "total_tokens": int(usage.get("totalTokens") or 0),
    }


def _region_from_detector(region: dict[str, Any], file_type: str) -> dict[str, Any] | None:
    try:
        return {
            "modality": "video" if file_type == "video" else "image",
            "source": "detector_region",
            "x": _float01(region["x"]),
            "y": _float01(region["y"]),
            "w": _float01(region["w"]),
            "h": _float01(region["h"]),
            "label": str(region.get("label") or "suspicious_region"),
            "confidence": _float01(region.get("score"), 0.0),
        }
    except KeyError:
        return None


def _region_from_watermark_hit(hit: dict[str, Any], file_type: str) -> dict[str, Any] | None:
    bbox = hit.get("bbox") or {}
    try:
        region = {
            "modality": "video" if file_type == "video" else "image",
            "source": "visible_watermark",
            "x": _float01(bbox["x"]),
            "y": _float01(bbox["y"]),
            "w": _float01(bbox["w"]),
            "h": _float01(bbox["h"]),
            "label": f"{hit.get('provider') or 'unknown'} visible watermark",
            "confidence": _float01(hit.get("confidence"), 0.0),
        }
    except KeyError:
        return None
    frame = hit.get("frame")
    if frame is not None:
        try:
            region["frame"] = int(frame)
        except (TypeError, ValueError):
            pass
    return region


def _temporal_segments(result: dict[str, Any]) -> list[dict[str, Any]]:
    file_type = (result.get("fileMeta") or {}).get("type")
    if file_type != "video":
        return []
    visible = result.get("visibleWatermark") or {}
    segments = []
    for hit in visible.get("hits") or []:
        frame = hit.get("frame")
        if frame is None:
            continue
        try:
            frame_id = int(frame)
        except (TypeError, ValueError):
            continue
        segments.append({
            "source": "visible_watermark",
            "start_frame": frame_id,
            "end_frame": frame_id,
            "label": f"{hit.get('provider') or 'unknown'} visible watermark",
            "confidence": _float01(hit.get("confidence"), 0.0),
        })
    return segments


def _generator_attribution(result: dict[str, Any]) -> dict[str, Any]:
    synthid = result.get("synthid") or {}
    visible = result.get("visibleWatermark") or {}
    provenance = result.get("provenance") or {}
    ai_metadata = provenance.get("aiMetadata") or {}
    evidence: list[str] = []
    candidates: list[dict[str, Any]] = []

    if provenance.get("isAiGenerated") is True:
        evidence.append("c2pa_ai_declaration")
        candidates.append({
            "family": provenance.get("generator") or "c2pa-ai-declaration",
            "model": provenance.get("generator") or "c2pa-ai-declaration",
            "confidence": 0.95 if provenance.get("hasCredentials") else 0.75,
        })
    if ai_metadata.get("isAiLikely"):
        evidence.append("metadata_ai_signal")
        matched = ai_metadata.get("matchedTools") or []
        candidates.append({
            "family": matched[0] if matched else "metadata-ai-signal",
            "model": matched[0] if matched else "metadata-ai-signal",
            "confidence": _float01((ai_metadata.get("score") or 0) / 100, 0.0),
        })
    if synthid.get("detected"):
        evidence.append("synthid")
        candidates.append({
            "family": "google-gemini",
            "model": synthid.get("attributedModelProfile") or "google-synthid-unattributed",
            "confidence": _float01(synthid.get("confidence"), 0.0),
        })
    if visible.get("detected"):
        evidence.append("visible_watermark")
        provider = visible.get("provider") or "unknown"
        candidates.append({
            "family": provider,
            "model": provider,
            "confidence": _float01(visible.get("confidence"), 0.0),
        })

    if not candidates:
        return {
            "status": "unknown",
            "family": None,
            "model": None,
            "confidence": 0.0,
            "evidence": [],
        }

    best = max(candidates, key=lambda item: item["confidence"])
    return {
        "status": "known_signal",
        "family": best.get("family"),
        "model": best.get("model"),
        "confidence": best["confidence"],
        "evidence": evidence,
    }


def _provenance_signals(result: dict[str, Any]) -> dict[str, Any]:
    synthid = result.get("synthid") or {}
    visible = result.get("visibleWatermark") or {}
    provenance = result.get("provenance") or {}
    ai_metadata = provenance.get("aiMetadata") or {}
    metadata_summary = provenance.get("metadataSummary") or {}
    precheck = result.get("provenancePrecheck") or {}
    c2pa_status = "not_checked_in_detect"
    c2pa_note = "Call /api/provenance for signed credential validation."
    if provenance:
        if provenance.get("hasCredentials"):
            c2pa_status = "credentials_present"
            c2pa_note = "C2PA content credentials were checked during detection."
        elif provenance.get("error") == "no_manifest":
            c2pa_status = "no_manifest"
            c2pa_note = "No embedded C2PA manifest was found; metadata was still inspected."
        elif provenance.get("error") == "c2pa_unavailable":
            c2pa_status = "c2pa_unavailable"
            c2pa_note = "C2PA library unavailable; metadata was still inspected."
        else:
            c2pa_status = "checked"
            c2pa_note = "C2PA and metadata checks ran during detection."
    return {
        "c2pa": {
            "status": c2pa_status,
            "has_credentials": bool(provenance.get("hasCredentials")) if provenance else None,
            "validation_state": provenance.get("validationState") if provenance else None,
            "is_ai_generated": provenance.get("isAiGenerated") if provenance else None,
            "generator": provenance.get("generator") if provenance else None,
            "issuer": provenance.get("issuer") if provenance else None,
            "error": provenance.get("error") if provenance else None,
            "note": c2pa_note,
        },
        "metadata_ai": {
            "detected": bool(ai_metadata.get("isAiLikely")),
            "score": int(ai_metadata.get("score") or 0),
            "confidence": ai_metadata.get("confidence") or "none",
            "signal_count": int(ai_metadata.get("signalCount") or 0),
            "matched_tools": ai_metadata.get("matchedTools") or [],
            "top_signals": (ai_metadata.get("signals") or [])[:5],
            "metadata_field_count": int(metadata_summary.get("fieldCount") or 0),
            "embedded_section_count": int(metadata_summary.get("embeddedSectionCount") or 0),
        } if provenance else None,
        "synthid": {
            "supported": bool(synthid.get("supported")),
            "detected": synthid.get("detected"),
            "possibly_detected": synthid.get("possiblyDetected"),
            "detection_state": synthid.get("detectionState"),
            "confidence": _float01(synthid.get("confidence"), 0.0),
            "evidence_level": synthid.get("evidenceLevel") or "unknown",
            "candidate_models": synthid.get("candidateModelProfiles") or [],
            "attributed_model": synthid.get("attributedModelProfile"),
            "model_results": synthid.get("modelResults") or [],
            "method": synthid.get("method"),
            "official_verification": bool(synthid.get("officialVerification")),
            "note": synthid.get("note"),
        } if synthid else None,
        "visible_watermark": {
            "supported": bool(visible.get("supported")),
            "detected": bool(visible.get("detected")),
            "provider": visible.get("provider"),
            "confidence": _float01(visible.get("confidence"), 0.0),
            "evidence_level": visible.get("evidenceLevel") or "unknown",
            "note": visible.get("note"),
        } if visible else None,
        "precheck": {
            "available": bool(precheck.get("available")),
            "engine": precheck.get("engine"),
            "engine_version": precheck.get("engineVersion"),
            "elapsed_ms": int(precheck.get("elapsedMs") or 0),
            "round_trip_ms": int(precheck.get("roundTripMs") or 0),
            "decision": precheck.get("decision"),
        } if precheck else None,
    }


def build(result: dict[str, Any]) -> dict[str, Any]:
    """Build the unified output block from a persisted detect result."""
    file_meta = result.get("fileMeta") or {}
    file_type = file_meta.get("type") or "unknown"
    confidence = _float01(result.get("confidence"), 0.0)
    attribution = _generator_attribution(result)
    regions = [
        converted
        for converted in (_region_from_detector(region, file_type) for region in result.get("regions") or [])
        if converted is not None
    ]
    visible = result.get("visibleWatermark") or {}
    regions.extend(
        converted
        for converted in (_region_from_watermark_hit(hit, file_type) for hit in visible.get("hits") or [])
        if converted is not None
    )

    # `confidence` is the probability/risk of synthetic content, not
    # confidence in the categorical verdict. Values near either 0 or 1 are
    # low-uncertainty; values near the decision boundary are high-uncertainty.
    uncertainty_score = round(1.0 - abs(confidence - 0.5) * 2.0, 3)
    uncertainty_factors = []
    if result.get("source") == "mock":
        uncertainty_factors.append("mock_fallback")
    if attribution["status"] == "unknown":
        uncertainty_factors.append("no_generator_attribution")
    if not regions:
        uncertainty_factors.append("no_localized_evidence")
    if result.get("cacheHit"):
        uncertainty_factors.append("cached_analysis")

    open_set_score = uncertainty_score
    if attribution["status"] == "unknown":
        open_set_score = min(open_set_score + 0.15, 1.0)
    if result.get("source") == "mock":
        open_set_score = min(open_set_score + 0.2, 1.0)

    return {
        "interface_version": INTERFACE_VERSION,
        "verdict": result.get("verdict") or "unknown",
        "confidence": confidence,
        "generator_attribution": attribution,
        "open_set_score": round(open_set_score, 3),
        "evidence_regions": regions,
        "temporal_segments": _temporal_segments(result),
        "provenance_signals": _provenance_signals(result),
        "explanation": result.get("explanation") or "",
        "uncertainty": {
            "score": uncertainty_score,
            "factors": uncertainty_factors,
        },
        "compute_cost": _cost(result),
    }
