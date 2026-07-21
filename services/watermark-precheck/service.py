"""Detection-only watermark and provenance service for the 66 server."""
from __future__ import annotations

import hmac
import math
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from PIL import Image, ImageOps

from policy import VISIBLE_ONLY_THRESHOLDS, build_decision, visible_hit_is_decisive


MAX_UPLOAD_BYTES = int(os.getenv("WATERMARK_PRECHECK_MAX_BYTES", str(30 * 1024 * 1024)))
API_TOKEN = os.getenv("WATERMARK_PRECHECK_TOKEN", "")
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".heic", ".heif", ".tif", ".tiff"}
PRECHECK_BRANCH_WORKERS = max(
    2,
    min(8, int(os.getenv("WATERMARK_PRECHECK_BRANCH_WORKERS", "4"))),
)
_PRECHECK_EXECUTOR = ThreadPoolExecutor(
    max_workers=PRECHECK_BRANCH_WORKERS,
    thread_name_prefix="watermark-precheck",
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


def _package_version() -> str:
    try:
        return version("remove-ai-watermarks")
    except PackageNotFoundError:
        return "unknown"


def _authorized() -> bool:
    if not API_TOKEN:
        return False
    header = request.headers.get("Authorization", "")
    supplied = header[7:] if header.startswith("Bearer ") else ""
    return bool(supplied) and hmac.compare_digest(supplied, API_TOKEN)


def _keep_visible_detection(
    provider: str,
    detected_keys: set[str],
    provenance: frozenset[str],
) -> bool:
    if provider != "jimeng_pill":
        return True
    return "jimeng" in detected_keys or "jimeng" in provenance


def _visible_hits(path: Path, provenance_path: Path | None = None) -> list[dict[str, Any]]:
    from remove_ai_watermarks import visible_provenance
    from remove_ai_watermarks.image_io import imread
    from remove_ai_watermarks.watermark_registry import detect_marks

    image = imread(path)
    if image is None:
        return []
    height, width = image.shape[:2]
    provenance = visible_provenance(provenance_path or path)
    hits = []
    detections = [
        detection
        for detection in detect_marks(image, provenance=provenance)
        if detection.detected
    ]
    detected_keys = {detection.key for detection in detections}
    for detection in detections:
        # The capture-less Jimeng pill detector has a documented raw false-fire
        # rate and must not be used without same-product or provenance evidence.
        if not _keep_visible_detection(detection.key, detected_keys, provenance):
            continue
        x, y, box_width, box_height = detection.region
        normalized = (
            float(x) / max(width, 1),
            float(y) / max(height, 1),
            float(box_width) / max(width, 1),
            float(box_height) / max(height, 1),
        )
        norm_x, norm_y, norm_width, norm_height = normalized
        if (
            not all(math.isfinite(value) for value in normalized)
            or not 0.0 <= norm_x <= 1.0
            or not 0.0 <= norm_y <= 1.0
            or not 0.0 < norm_width <= 1.0
            or not 0.0 < norm_height <= 1.0
            or norm_x + norm_width > 1.0
            or norm_y + norm_height > 1.0
        ):
            continue
        bbox = {
            "x": round(norm_x, 4),
            "y": round(norm_y, 4),
            "w": round(norm_width, 4),
            "h": round(norm_height, 4),
        }
        corroborated = detection.key in provenance
        decisive = visible_hit_is_decisive(
            detection.key,
            float(detection.confidence),
            bbox,
            corroborated=corroborated,
        )
        hits.append(
            {
                "provider": detection.key,
                "label": detection.label,
                "location": detection.location,
                "confidence": round(float(detection.confidence), 3),
                "corroborated": corroborated,
                "decisive": decisive,
                "bbox": bbox,
            }
        )
    return hits


def _report(path: Path) -> dict[str, Any]:
    from remove_ai_watermarks.identify import identify

    result = identify(path, check_visible=False, check_invisible=False)
    return {
        "isAiGenerated": result.is_ai_generated,
        "platform": result.platform,
        "confidence": result.confidence,
        "aiSourceKind": result.ai_source_kind,
        "aiFromMetadata": result.ai_from_metadata,
        "watermarks": list(result.watermarks),
        "signals": [asdict(signal) for signal in result.signals],
        "caveats": list(result.caveats),
        "integrityClashes": list(result.integrity_clashes),
    }


def _collect_evidence(
    path: Path,
    visible_path: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    def timed_report() -> tuple[dict[str, Any], int]:
        started = time.perf_counter()
        return _report(path), int((time.perf_counter() - started) * 1000)

    report_future = _PRECHECK_EXECUTOR.submit(timed_report)
    visible_started = time.perf_counter()
    visible_hits = _visible_hits(visible_path or path, provenance_path=path)
    visible_elapsed_ms = int((time.perf_counter() - visible_started) * 1000)
    report, metadata_elapsed_ms = report_future.result()
    return report, visible_hits, {
        "metadataMs": metadata_elapsed_ms,
        "visiblePipelineMs": visible_elapsed_ms,
    }


@app.get("/health")
def health():
    engine_version = _package_version()
    registry_ready = engine_version != "unknown"
    return {
        "status": "ok" if registry_ready and bool(API_TOKEN) else "degraded",
        "mode": "detect-only",
        "engine": "remove-ai-watermarks.identify",
        "engineVersion": engine_version,
        "registryReady": registry_ready,
        "tokenReady": bool(API_TOKEN),
        "coordinateSpace": "display_normalized_v1",
        "visibleProviders": ["gemini", "doubao", "jimeng", "jimeng_pill", "samsung"],
        "visibleOnlyThresholds": VISIBLE_ONLY_THRESHOLDS,
        "maxUploadBytes": MAX_UPLOAD_BYTES,
        "precheckBranchWorkers": PRECHECK_BRANCH_WORKERS,
    }


@app.post("/v1/precheck")
def precheck():
    if not _authorized():
        return jsonify({"detail": "unauthorized"}), 401
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"detail": "file is required"}), 400

    data = uploaded.stream.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        return jsonify({"detail": "empty file"}), 400
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify({"detail": "file too large"}), 413

    suffix = Path(uploaded.filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return jsonify({"detail": "unsupported image type"}), 415

    started = time.perf_counter()
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix) as temporary, tempfile.NamedTemporaryFile(
            suffix=".png"
        ) as normalized_temporary:
            temporary.write(data)
            temporary.flush()
            path = Path(temporary.name)
            decode_started = time.perf_counter()
            with Image.open(path) as encoded_image:
                if bool(getattr(encoded_image, "is_animated", False)) and int(
                    getattr(encoded_image, "n_frames", 1)
                ) > 1:
                    return jsonify({"detail": "animated images are not supported"}), 415
                encoded_size = {
                    "width": int(encoded_image.width),
                    "height": int(encoded_image.height),
                }
                source_orientation = int(encoded_image.getexif().get(274, 1) or 1)
                decode_elapsed_ms = int((time.perf_counter() - decode_started) * 1000)
                normalize_started = time.perf_counter()
                normalized_image = ImageOps.exif_transpose(encoded_image).convert("RGB")
                display_size = {
                    "width": int(normalized_image.width),
                    "height": int(normalized_image.height),
                }
                normalized_image.save(normalized_temporary, format="PNG")
                normalized_temporary.flush()
                normalize_elapsed_ms = int((time.perf_counter() - normalize_started) * 1000)
            normalized_path = Path(normalized_temporary.name)
            report, visible_hits, evidence_timings = _collect_evidence(path, normalized_path)
        decision_started = time.perf_counter()
        decision = build_decision(report, visible_hits)
        decision_elapsed_ms = int((time.perf_counter() - decision_started) * 1000)
    except Exception as exc:
        app.logger.exception("provenance precheck failed")
        return jsonify({"detail": "precheck failed", "errorType": type(exc).__name__}), 422

    total_elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "status": "ok",
        "engine": "remove-ai-watermarks.identify",
        "engineVersion": _package_version(),
        "elapsedMs": total_elapsed_ms,
        "decision": decision,
        "report": report,
        "visibleHits": visible_hits,
        "coordinateSpace": "display_normalized_v1",
        "displaySize": display_size,
        "encodedSize": encoded_size,
        "sourceOrientation": source_orientation,
        "input": {
            "filename": uploaded.filename,
            "bytes": len(data),
            "suffix": suffix,
        },
        "parallelism": {
            "enabled": True,
            "workers": PRECHECK_BRANCH_WORKERS,
        },
        "pipelineTimings": {
            "decodeMs": decode_elapsed_ms,
            "normalizeMs": normalize_elapsed_ms,
            "metadataMs": evidence_timings["metadataMs"],
            "visiblePipelineMs": evidence_timings["visiblePipelineMs"],
            "decisionMs": decision_elapsed_ms,
            "totalMs": total_elapsed_ms,
        },
    }


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"detail": "file too large"}), 413
