"""Detection-only watermark and provenance service for the 66 server."""
from __future__ import annotations

import hmac
import os
import tempfile
import time
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from policy import VISIBLE_ONLY_THRESHOLDS, build_decision, visible_hit_is_decisive


MAX_UPLOAD_BYTES = int(os.getenv("WATERMARK_PRECHECK_MAX_BYTES", str(30 * 1024 * 1024)))
API_TOKEN = os.getenv("WATERMARK_PRECHECK_TOKEN", "")
SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".heic", ".heif", ".tif", ".tiff"}

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


def _visible_hits(path: Path) -> list[dict[str, Any]]:
    from remove_ai_watermarks import visible_provenance
    from remove_ai_watermarks.image_io import imread
    from remove_ai_watermarks.watermark_registry import detect_marks

    image = imread(path)
    if image is None:
        return []
    height, width = image.shape[:2]
    provenance = visible_provenance(path)
    hits = []
    for detection in detect_marks(image, provenance=provenance):
        if not detection.detected:
            continue
        x, y, box_width, box_height = detection.region
        bbox = {
            "x": round(max(0.0, min(1.0, x / max(width, 1))), 4),
            "y": round(max(0.0, min(1.0, y / max(height, 1))), 4),
            "w": round(max(0.0, min(1.0, box_width / max(width, 1))), 4),
            "h": round(max(0.0, min(1.0, box_height / max(height, 1))), 4),
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


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": "detect-only",
        "engine": "remove-ai-watermarks.identify",
        "engineVersion": _package_version(),
        "visibleProviders": ["gemini", "doubao", "jimeng", "jimeng_pill", "samsung"],
        "visibleOnlyThresholds": VISIBLE_ONLY_THRESHOLDS,
        "maxUploadBytes": MAX_UPLOAD_BYTES,
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
        with tempfile.NamedTemporaryFile(suffix=suffix) as temporary:
            temporary.write(data)
            temporary.flush()
            path = Path(temporary.name)
            report = _report(path)
            visible_hits = _visible_hits(path)
        decision = build_decision(report, visible_hits)
    except Exception as exc:
        app.logger.exception("provenance precheck failed")
        return jsonify({"detail": "precheck failed", "errorType": type(exc).__name__}), 422

    return {
        "status": "ok",
        "engine": "remove-ai-watermarks.identify",
        "engineVersion": _package_version(),
        "elapsedMs": int((time.perf_counter() - started) * 1000),
        "decision": decision,
        "report": report,
        "visibleHits": visible_hits,
    }


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"detail": "file too large"}), 413
