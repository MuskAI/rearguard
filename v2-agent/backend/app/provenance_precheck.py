"""Client and adapter for the provenance-first detector running on server 66."""
from __future__ import annotations

import json
import mimetypes
import os
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from PIL import Image, ImageOps

from . import evidence_probability
from . import provenance as provenance_reader
from . import watermark_verdict


BASE_URL = os.getenv("JIANZHEN_PROVENANCE_PRECHECK_URL", "").strip().rstrip("/")
API_TOKEN = os.getenv("JIANZHEN_PROVENANCE_PRECHECK_TOKEN", "").strip()
TIMEOUT_SECONDS = float(os.getenv("JIANZHEN_PROVENANCE_PRECHECK_TIMEOUT", "8"))
ORIGINAL_TIMEOUT_SECONDS = float(os.getenv("JIANZHEN_PROVENANCE_PRECHECK_ORIGINAL_TIMEOUT", "20"))
DIRECT_UPLOAD_MAX_BYTES = int(os.getenv("JIANZHEN_PROVENANCE_PRECHECK_DIRECT_UPLOAD_MAX_BYTES", "1572864"))
VISIBLE_SCAN_MAX_SIDE = int(os.getenv("JIANZHEN_PROVENANCE_PRECHECK_SCAN_MAX_SIDE", "1536"))
VISIBLE_SCAN_QUALITY = int(os.getenv("JIANZHEN_PROVENANCE_PRECHECK_SCAN_QUALITY", "94"))
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ALLOWED_REASONS = {
    "c2pa_ai_generated",
    "c2pa_ai_enhanced",
}
KNOWN_VISIBLE_PROVIDERS = frozenset({"gemini", "doubao", "jimeng", "jimeng_pill", "samsung"})
YOLO_PROVIDER = "yolo11x_watermark"

_last_state: dict[str, Any] = {
    "available": None,
    "lastError": None,
    "lastElapsedMs": None,
    "lastTransportMode": None,
}


def status() -> dict[str, Any]:
    return {
        "configured": bool(BASE_URL and API_TOKEN),
        "localEvidenceEnabled": True,
        "remoteVisibleConfigured": bool(BASE_URL and API_TOKEN),
        "url": BASE_URL or None,
        **_last_state,
    }


def _endpoint() -> str:
    return BASE_URL if BASE_URL.endswith("/v1/precheck") else f"{BASE_URL}/v1/precheck"


def _multipart(filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = f"huijian-{uuid.uuid4().hex}"
    safe_name = Path(filename).name.replace('"', "") or "image.bin"
    content_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    return prefix + data + suffix, boundary


def _bbox_metrics(first: dict[str, Any], second: dict[str, Any]) -> tuple[float, float]:
    try:
        ax1, ay1 = float(first["x"]), float(first["y"])
        ax2, ay2 = ax1 + float(first["w"]), ay1 + float(first["h"])
        bx1, by1 = float(second["x"]), float(second["y"])
        bx2, by2 = bx1 + float(second["w"]), by1 + float(second["h"])
    except (KeyError, TypeError, ValueError):
        return 0.0, 0.0
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    smaller = min(area_a, area_b)
    return (
        intersection / union if union > 0 else 0.0,
        intersection / smaller if smaller > 0 else 0.0,
    )


def _boxes_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    iou, smaller_coverage = _bbox_metrics(first, second)
    return iou >= 0.08 or smaller_coverage >= 0.5


def _normalize_visible_hits(result: dict[str, Any]) -> None:
    """Expose generic watermarks without treating them as AI provenance."""
    raw_hits = [item for item in result.get("visibleHits") or [] if isinstance(item, dict)]
    registry_hits = [dict(item) for item in raw_hits if item.get("provider") in KNOWN_VISIBLE_PROVIDERS]
    yolo_candidates = [dict(item) for item in raw_hits if item.get("provider") == YOLO_PROVIDER]
    for hit in registry_hits:
        if hit.get("yoloCorroborated") is True:
            continue
        best: tuple[float, dict[str, Any]] | None = None
        for candidate in yolo_candidates:
            iou, coverage = _bbox_metrics(hit.get("bbox") or {}, candidate.get("bbox") or {})
            if iou < 0.08 and coverage < 0.5:
                continue
            score = max(iou, coverage)
            if best is None or score > best[0]:
                best = (score, candidate)
        hit["yoloCorroborated"] = best is not None
        if best is not None:
            candidate = best[1]
            hit["yoloConfidence"] = round(float(candidate.get("confidence") or 0.0), 4)
            hit["yoloBbox"] = candidate.get("bbox") or {}
            hit["localizationModel"] = candidate.get("model") or "corzent/yolo11x_watermark_detection"
            hit["localizationModelRevision"] = candidate.get("modelRevision")

    generic_hits = []
    for candidate in yolo_candidates:
        if any(_boxes_overlap(hit.get("bbox") or {}, candidate.get("bbox") or {}) for hit in registry_hits):
            continue
        candidate.update({
            "provider": YOLO_PROVIDER,
            "label": "可见水印（平台待确认）",
            "decisive": False,
            "corroborated": False,
            "evidenceRole": "localization",
        })
        generic_hits.append(candidate)
    result["visibleHits"] = [*registry_hits, *generic_hits]

    detector = result.get("genericVisibleWatermark")
    if isinstance(detector, dict) or yolo_candidates:
        normalized = dict(detector) if isinstance(detector, dict) else {}
        confirmed = sum(1 for hit in registry_hits if hit.get("yoloCorroborated") is True)
        try:
            detector_count = max(0, int(normalized.get("count") or 0))
        except (TypeError, ValueError):
            detector_count = 0
        normalized.update({
            "detected": bool(normalized.get("detected")) or bool(yolo_candidates),
            "count": max(detector_count, len(yolo_candidates), confirmed),
            "genericCount": len(generic_hits),
            "knownPlatformCount": len(registry_hits),
            "platformConfirmedCount": confirmed,
            "mode": "visible_watermark_detection_with_platform_attribution",
        })
        result["genericVisibleWatermark"] = normalized


def _remote_inspect(data: bytes, filename: str, *, timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    body, boundary = _multipart(filename, data)
    req = urlrequest.Request(
        _endpoint(),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {API_TOKEN}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError("precheck response too large")
        result = json.loads(payload.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("precheck response is not an object")
        _normalize_visible_hits(result)
        elapsed = int((time.perf_counter() - started) * 1000)
        result["available"] = True
        result["roundTripMs"] = elapsed
        return result
    except (urlerror.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        error_name = type(exc).__name__
        return {
            "status": "unavailable",
            "available": False,
            "error": error_name,
            "roundTripMs": elapsed,
            "decision": {"shortCircuit": False, "modelRequired": True},
        }


def _local_source_decision(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Gate only explicit AI declarations from the original file."""
    if str(report.get("error") or "").strip().lower().startswith("parse_error"):
        return None

    source_types = [
        str(action.get("digitalSourceType") or "")
        for action in report.get("actions") or []
        if isinstance(action, dict)
    ]
    source_kind = (
        "enhanced"
        if any("compositeWithTrainedAlgorithmicMedia" in value for value in source_types)
        else "generated"
    )
    validation_state = str(report.get("validationState") or "").strip().lower()
    c2pa_trusted = (
        report.get("credentialTrusted") is True
        and validation_state == "trusted"
    )
    c2pa_integrity_invalid = validation_state == "invalid"

    if report.get("isAiGenerated") is True:
        enhanced = source_kind == "enhanced"
        decision = {
            "shortCircuit": c2pa_trusted,
            "modelRequired": not c2pa_trusted,
            "verdict": None if not c2pa_trusted else "suspected_fake" if enhanced else "highly_suspected_fake",
            "confidence": 0.0 if not c2pa_trusted else 0.94 if enhanced else 0.99,
            "reason": "untrusted_ai_provenance" if not c2pa_trusted else "c2pa_ai_enhanced" if enhanced else "c2pa_ai_generated",
            "evidenceKinds": [
                "c2pa",
                *(["integrity_clash"] if c2pa_integrity_invalid else []),
            ],
            "summary": (
                "C2PA 签名结构有效，但签名者信任链未建立；其中的 AI 来源声明保持中性并继续运行像素模型。"
                if validation_state == "valid"
                else "C2PA 校验失败；凭证内容不受信，仅将校验失败作为完整性异常并继续运行像素模型。"
                if c2pa_integrity_invalid
                else "C2PA 包含 AI 来源声明，但凭证未处于可信校验状态；该线索仅作上下文并继续运行像素模型。"
                if not c2pa_trusted
                else "C2PA 内容凭证声明该文件包含 AI 生成或合成内容，已直接形成结论。"
            ),
        }
        compact = {
            "aiFromMetadata": True,
            "isAiGenerated": True,
            "aiSourceKind": source_kind,
            "c2paTrusted": c2pa_trusted,
            "c2paValidationState": validation_state or "unknown",
            "platform": report.get("generator"),
            "signals": [
                {
                    "name": "c2pa",
                    "confidence": "high" if c2pa_trusted else "unverified",
                    "detail": report.get("generator") or "AI digitalSourceType",
                }
            ],
            "integrityClashes": ["c2pa_validation_invalid"] if c2pa_integrity_invalid else [],
        }
        return decision, compact

    ai_metadata = report.get("aiMetadata") or {}
    score = int(ai_metadata.get("score") or 0)
    if report.get("metadataAiGenerated") is True and score >= 70:
        tools = [str(item) for item in ai_metadata.get("matchedTools") or []]
        compact = {
            "aiFromMetadata": True,
            "isAiGenerated": True,
            "aiSourceKind": "generated",
            "c2paTrusted": False,
            "platform": "、".join(tools) or None,
            "signals": [
                {
                    "name": "metadata",
                    "confidence": "high",
                    "detail": "、".join(tools) or f"AI metadata score {score}",
                }
            ],
            "integrityClashes": [],
        }
        return (
            {
                "shortCircuit": False,
                "modelRequired": True,
                "verdict": None,
                "confidence": 0.0,
                "reason": "ai_metadata_context",
                "evidenceKinds": ["metadata"],
                "summary": "文件包含可编辑的 AI 生成元数据或参数；该线索仅作上下文并继续运行像素模型。",
            },
            compact,
        )
    return None


def _visible_scan(data: bytes, filename: str) -> tuple[bytes, str, dict[str, Any]]:
    """Create a compact, high-detail raster used only for visible-mark matching."""
    with Image.open(BytesIO(data)) as opened:
        image = ImageOps.exif_transpose(opened)
        if getattr(image, "is_animated", False):
            image.seek(0)
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, "white")
            flattened.paste(rgba, mask=rgba.getchannel("A"))
            image = flattened
        else:
            image = image.convert("RGB")
        original_dimensions = {"width": image.width, "height": image.height}
        image.thumbnail((VISIBLE_SCAN_MAX_SIDE, VISIBLE_SCAN_MAX_SIDE), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(
            output,
            "JPEG",
            quality=max(80, min(98, VISIBLE_SCAN_QUALITY)),
            subsampling=0,
            optimize=True,
        )
        scan = output.getvalue()
        return scan, f"{Path(filename).stem or 'image'}.visible-scan.jpg", {
            "mode": "visible_scan",
            "originalBytes": len(data),
            "scanBytes": len(scan),
            "originalDimensions": original_dimensions,
            "scanDimensions": {"width": image.width, "height": image.height},
        }


def _needs_original_metadata_pass(report: dict[str, Any]) -> bool:
    ai_metadata = report.get("aiMetadata") or {}
    return bool(
        report.get("hasCredentials")
        or report.get("metadataAiGenerated")
        or int(ai_metadata.get("score") or 0) >= 25
    )


def _is_decisive(result: dict[str, Any]) -> bool:
    decision = result.get("decision") or {}
    return result.get("available") is True and decision.get("shortCircuit") is True


def _decision_from_probability_model(
    probability_model: dict[str, Any],
    report: dict[str, Any],
    known_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    factors = [item for item in probability_model.get("factors") or [] if isinstance(item, dict)]
    kinds = {str(item.get("kind") or "") for item in factors}
    evidence_kinds: list[str] = []
    if "known_visible_ai_watermark" in kinds:
        evidence_kinds.append("visible_watermark")
    if kinds.intersection({"valid_ai_c2pa", "ai_enhancement_declaration"}):
        evidence_kinds.append("c2pa")
    elif kinds.intersection({"ai_generation_metadata", "unverified_ai_declaration"}):
        evidence_kinds.append("metadata")
    if "metadata_integrity_clash" in kinds:
        evidence_kinds.append("integrity_clash")

    if probability_model.get("decisive") is not True:
        summary = "未发现足以直接判定的 AI 来源标记，继续调用图像检测模型。"
        if "metadata_integrity_clash" in kinds:
            summary = "发现来源凭证或元数据完整性冲突，继续调用图像检测模型进行交叉核验。"
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
    if "valid_ai_c2pa" in kinds:
        reason = "c2pa_ai_generated"
        summary = f"通过校验的内容凭证声明该文件为 AI 生成，证据融合风险概率为 {confidence * 100:.2f}%。"
    elif "ai_enhancement_declaration" in kinds:
        reason = "c2pa_ai_enhanced"
        summary = f"内容凭证声明存在 AI 合成编辑，证据融合风险概率为 {confidence * 100:.2f}%。"
    elif "ai_generation_metadata" in kinds:
        reason = "ai_metadata"
        summary = f"文件包含明确的 AI 生成元数据或参数，证据融合风险概率为 {confidence * 100:.2f}%。"
    else:
        return {
            "shortCircuit": False,
            "modelRequired": True,
            "verdict": None,
            "confidence": 0.0,
            "reason": "no_decisive_ai_provenance",
            "evidenceKinds": evidence_kinds,
            "summary": "可见标记与可编辑元数据仅作为人工复核线索，继续调用图像检测模型。",
            "probabilityModel": probability_model,
        }
    enhanced_only = report.get("aiSourceKind") == "enhanced"
    return {
        "shortCircuit": True,
        "modelRequired": False,
        "verdict": "suspected_fake" if enhanced_only or confidence < 0.98 else "highly_suspected_fake",
        "confidence": round(confidence, 4),
        "reason": reason,
        "evidenceKinds": evidence_kinds,
        "summary": summary,
        "probabilityModel": probability_model,
    }


def _reconcile_probability(
    result: dict[str, Any],
    local: tuple[dict[str, Any], dict[str, Any]] | None,
    capture_evidence: dict[str, Any] | None = None,
) -> None:
    if result.get("available") is not True:
        return
    report = dict(result.get("report") or {})
    if isinstance(capture_evidence, dict):
        report["captureEvidence"] = capture_evidence
    # Never accept a trust assertion from the remote visual precheck. Trust is
    # established only by local validation over the original upload bytes.
    report["c2paTrusted"] = False
    if local is not None:
        _, compact = local
        report["aiFromMetadata"] = bool(report.get("aiFromMetadata") or compact.get("aiFromMetadata"))
        if report.get("isAiGenerated") is not True and compact.get("isAiGenerated") is True:
            report["isAiGenerated"] = True
        report["aiSourceKind"] = report.get("aiSourceKind") or compact.get("aiSourceKind")
        report["c2paTrusted"] = compact.get("c2paTrusted") is True
        report["c2paValidationState"] = compact.get("c2paValidationState")
        report["platform"] = report.get("platform") or compact.get("platform")
        report["signals"] = [*(report.get("signals") or []), *(compact.get("signals") or [])]
        report["integrityClashes"] = list(dict.fromkeys([
            *(report.get("integrityClashes") or []),
            *(compact.get("integrityClashes") or []),
        ]))
    # Remote visual attribution is deliberately non-decisive. Only locally
    # verified provenance from the original bytes may short-circuit a model.
    probability_model = evidence_probability.build_probability_model(report, [])
    result["report"] = report
    result["decision"] = _decision_from_probability_model(probability_model, report, [])


def _local_result(
    local: tuple[dict[str, Any], dict[str, Any]],
    local_report: dict[str, Any],
    data_size: int,
    elapsed: int,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    _, compact_report = local
    capture = local_report.get("captureEvidence")
    if isinstance(capture, dict):
        compact_report["captureEvidence"] = capture
    probability_model = evidence_probability.build_probability_model(compact_report, [])
    decision = _decision_from_probability_model(probability_model, compact_report, [])
    return {
        "status": "ok",
        "available": True,
        "engineVersion": "local-provenance-v2",
        "report": compact_report,
        "visibleHits": [],
        "decision": decision,
        "elapsedMs": elapsed,
        "roundTripMs": elapsed,
        "transport": {
            "mode": "local_source_evidence" if error is None else "local_source_fallback",
            "originalBytes": data_size,
            "remoteAttempts": [],
            "remoteError": error,
        },
        "_provenanceReport": local_report,
    }


def _local_capture_result(
    capture: dict[str, Any],
    local_report: dict[str, Any],
    data_size: int,
    elapsed: int,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    compact_report = {
        "aiFromMetadata": False,
        "isAiGenerated": None,
        "aiSourceKind": None,
        "platform": None,
        "signals": [],
        "integrityClashes": [],
        "captureEvidence": capture,
    }
    probability_model = evidence_probability.build_probability_model(compact_report, [])
    decision = _decision_from_probability_model(probability_model, compact_report, [])
    return {
        "status": "ok",
        "available": True,
        "engineVersion": "local-capture-evidence-v1",
        "report": compact_report,
        "visibleHits": [],
        "decision": decision,
        "elapsedMs": elapsed,
        "roundTripMs": elapsed,
        "transport": {
            "mode": "local_capture_evidence" if error is None else "local_capture_fallback",
            "originalBytes": data_size,
            "remoteAttempts": [],
            "remoteError": error,
        },
        "_provenanceReport": local_report,
    }


def inspect(data: bytes, filename: str) -> dict[str, Any]:
    """Run provenance locally, then use server 66 for visible-mark matching."""
    started = time.perf_counter()
    local_report = provenance_reader.read_provenance(
        data,
        provenance_reader.mime_for(filename),
        filename,
    )
    local = _local_source_decision(local_report)
    capture = local_report.get("captureEvidence") if isinstance(local_report.get("captureEvidence"), dict) else {}
    has_capture_support = capture.get("supportsRealCapture") is True

    if not BASE_URL or not API_TOKEN:
        elapsed = int((time.perf_counter() - started) * 1000)
        if local is not None:
            result = _local_result(local, local_report, len(data), elapsed)
            _last_state.update({
                "available": True,
                "lastError": None,
                "lastElapsedMs": elapsed,
                "lastTransportMode": "local_source_evidence",
            })
            return result
        if has_capture_support:
            result = _local_capture_result(capture, local_report, len(data), elapsed)
            _last_state.update({
                "available": True,
                "lastError": None,
                "lastElapsedMs": elapsed,
                "lastTransportMode": "local_capture_evidence",
            })
            return result
        result = {
            "status": "unavailable",
            "available": False,
            "error": "not_configured",
            "roundTripMs": elapsed,
            "decision": {"shortCircuit": False, "modelRequired": True},
            "_provenanceReport": local_report,
        }
        _last_state.update(
            {
                "available": False,
                "lastError": "not_configured",
                "lastElapsedMs": elapsed,
                "lastTransportMode": "local_only",
            }
        )
        return result

    attempts: list[dict[str, Any]] = []
    if len(data) <= DIRECT_UPLOAD_MAX_BYTES:
        transport: dict[str, Any] = {
            "mode": "original",
            "originalBytes": len(data),
            "scanBytes": None,
        }
        result = _remote_inspect(data, filename, timeout=ORIGINAL_TIMEOUT_SECONDS)
        attempts.append(
            {
                "mode": "original",
                "bytes": len(data),
                "elapsedMs": result.get("roundTripMs"),
                "available": result.get("available"),
            }
        )
    else:
        try:
            scan, scan_name, transport = _visible_scan(data, filename)
            result = _remote_inspect(scan, scan_name, timeout=TIMEOUT_SECONDS)
            attempts.append(
                {
                    "mode": "visible_scan",
                    "bytes": len(scan),
                    "elapsedMs": result.get("roundTripMs"),
                    "available": result.get("available"),
                }
            )
        except Exception as exc:
            transport = {
                "mode": "original_fallback",
                "originalBytes": len(data),
                "scanBytes": None,
                "scanError": type(exc).__name__,
            }
            result = _remote_inspect(data, filename, timeout=ORIGINAL_TIMEOUT_SECONDS)
            attempts.append(
                {
                    "mode": "original_fallback",
                    "bytes": len(data),
                    "elapsedMs": result.get("roundTripMs"),
                    "available": result.get("available"),
                }
            )

        if (
            (local is not None or not _is_decisive(result))
            and _needs_original_metadata_pass(local_report)
            and transport["mode"] == "visible_scan"
        ):
            original_result = _remote_inspect(data, filename, timeout=ORIGINAL_TIMEOUT_SECONDS)
            attempts.append(
                {
                    "mode": "original_metadata",
                    "bytes": len(data),
                    "elapsedMs": original_result.get("roundTripMs"),
                    "available": original_result.get("available"),
                }
            )
            if original_result.get("available") or not result.get("available"):
                result = original_result

    elapsed = int((time.perf_counter() - started) * 1000)
    if result.get("available") is not True and local is not None:
        error_name = str(result.get("error") or "remote_unavailable")
        result = _local_result(local, local_report, len(data), elapsed, error=error_name)
        transport = dict(result["transport"])
    elif result.get("available") is not True and has_capture_support:
        error_name = str(result.get("error") or "remote_unavailable")
        result = _local_capture_result(capture, local_report, len(data), elapsed, error=error_name)
        transport = dict(result["transport"])
    else:
        _reconcile_probability(result, local, capture)
    transport["remoteAttempts"] = attempts
    result["transport"] = transport
    result["roundTripMs"] = elapsed
    result["_provenanceReport"] = local_report
    error_name = result.get("error") if not result.get("available") else None
    _last_state.update(
        {
            "available": bool(result.get("available")),
            "lastError": error_name,
            "lastElapsedMs": elapsed,
            "lastTransportMode": transport.get("mode"),
        }
    )
    return result


def _visible_result(
    hits: list[dict[str, Any]],
    *,
    engine_version: str = "unknown",
    elapsed_ms: int = 0,
    coordinate_space: str = "",
    display_size: dict[str, Any] | None = None,
    registry_supported: bool = False,
) -> dict[str, Any] | None:
    if not hits:
        return None
    top = max(hits, key=lambda item: float(item.get("confidence") or 0.0))
    confidence = float(top.get("confidence") or 0.0)
    return {
        "enabled": True,
        "supported": True,
        "detected": True,
        "provider": top.get("provider"),
        "confidence": round(confidence, 3),
        "evidenceLevel": "strong" if confidence >= 0.8 else "medium",
        "coordinateSpace": coordinate_space,
        "displaySize": dict(display_size or {}),
        "registrySupported": registry_supported,
        "positiveEvidenceSupported": registry_supported,
        "hits": [
            {
                "provider": hit.get("provider") or "unknown",
                "label": hit.get("label") or hit.get("provider") or "已知 AI 平台标记",
                "confidence": round(float(hit.get("confidence") or 0.0), 3),
                "bbox": hit.get("bbox") or {},
                "method": "remove_ai_watermarks_registry",
                "frame": None,
                "scores": {},
                "crop": None,
                "model": "wiltodelta/remove-ai-watermarks",
                "modelRevision": engine_version,
                "decisive": False,
                "evidenceRole": "visual_attribution",
                "registryCorroborated": bool(hit.get("corroborated")),
                "localizationConfirmed": bool(hit.get("yoloCorroborated")),
                "localizationConfidence": round(float(hit.get("yoloConfidence") or 0.0), 3),
            }
            for hit in hits[:8]
        ],
        "temporal": {"sampledFrames": 1, "positiveFrames": 1, "moving": False},
        "note": "已识别出已知 AI 平台可见标记，前置证据门控已跳过像素模型。",
        "elapsedMs": elapsed_ms,
        "detector": {
            "available": True,
            "model": "wiltodelta/remove-ai-watermarks",
            "modelRevision": engine_version,
            "engines": [
                {
                    "id": "known_ai_registry",
                    "label": "AI 平台标记识别",
                    "available": True,
                    "detected": True,
                    "count": len(hits),
                    "model": "wiltodelta/remove-ai-watermarks",
                    "version": engine_version,
                    "role": "provenance",
                }
            ],
        },
    }


def build_analysis(precheck: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a decisive precheck response into the standard analysis schema."""
    if not precheck.get("available"):
        return None
    decision = precheck.get("decision") or {}
    reason = str(decision.get("reason") or "")
    verdict = decision.get("verdict")
    confidence = float(decision.get("confidence") or 0.0)
    if (
        decision.get("shortCircuit") is not True
        or reason not in ALLOWED_REASONS
        or verdict not in {"suspected_fake", "highly_suspected_fake"}
        or confidence < 0.8
    ):
        return None

    report = precheck.get("report") or {}
    hits: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    if hits:
        top = max(float(hit.get("confidence") or 0.0) for hit in hits)
        providers = "、".join(dict.fromkeys(str(hit.get("label") or hit.get("provider")) for hit in hits))
        dimensions.append({
            "key": "visible_watermark",
            "label": "可见AI水印检测",
            "score": round(top, 2),
            "result": f"命中已知 AI 平台标记：{providers}",
        })
    if report.get("aiFromMetadata"):
        signal_names = {str(item.get("name") or "") for item in report.get("signals") or [] if isinstance(item, dict)}
        is_c2pa = "c2pa" in signal_names
        dimensions.append({
            "key": "c2pa" if is_c2pa else "ai_metadata",
            "label": "C2PA内容凭证" if is_c2pa else "AI元数据检测",
            "score": 0.99 if is_c2pa else 0.96,
            "result": str(decision.get("summary") or "检测到高可信 AI 来源标记"),
        })

    regions = []
    for hit in hits[:8]:
        bbox = hit.get("bbox") or {}
        try:
            regions.append({
                "x": round(float(bbox["x"]), 3),
                "y": round(float(bbox["y"]), 3),
                "w": round(float(bbox["w"]), 3),
                "h": round(float(bbox["h"]), 3),
                "label": "已知AI平台可见水印",
                "score": round(float(hit.get("confidence") or confidence), 2),
            })
        except (KeyError, TypeError, ValueError):
            continue

    explanation = str(decision.get("summary") or "检测到直接 AI 来源证据，已跳过像素模型。")
    explanation += " 本结论来自文件自身的来源标记，不消耗视觉模型调用。"
    engine_version = str(precheck.get("engineVersion") or "unknown")
    visible = _visible_result(
        hits,
        engine_version=engine_version,
        elapsed_ms=int(precheck.get("elapsedMs") or 0),
        coordinate_space=str(precheck.get("coordinateSpace") or ""),
        display_size=(precheck.get("displaySize") if isinstance(precheck.get("displaySize"), dict) else {}),
        registry_supported=bool(
            precheck.get("status") == "ok" and not engine_version.startswith("local-")
        ),
    )
    if reason in {
        "known_visible_ai_watermark",
        "corroborated_ai_provenance",
        "watermark_metadata_conflict",
    }:
        return None
    if reason in {"c2pa_ai_generated", "c2pa_ai_enhanced"} and report.get("c2paTrusted") is not True:
        return None
    engine_label = (
        engine_version
        if engine_version.startswith("local-")
        else f"remove-ai-watermarks-{engine_version}"
    )
    analysis = {
        "verdict": verdict,
        "confidence": round(confidence, 4),
        "dimensions": dimensions,
        "regions": regions,
        "explanation": explanation,
        "modelVersion": f"provenance-gate/{engine_label}",
        "source": "provenance",
        "decisionStatus": "verdict",
        "decisionAuthority": "decisive_provenance",
        "reviewRequired": False,
        "tokenUsage": {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0},
        "provenancePrecheck": precheck,
        "probabilityModel": decision.get("probabilityModel"),
    }
    if visible is not None:
        analysis["visibleWatermark"] = visible
    return analysis
