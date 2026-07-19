"""Combine generic YOLO watermark boxes with known AI-platform marks."""
from __future__ import annotations

import os
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests
from flask import g

import service as base


YOLO_URL = os.getenv("YOLO_WATERMARK_URL", "http://127.0.0.1:5067/v1/detect")
YOLO_HEALTH_URL = os.getenv("YOLO_WATERMARK_HEALTH_URL", "http://127.0.0.1:5067/health")
YOLO_TOKEN = os.getenv("YOLO_WATERMARK_TOKEN", "")
YOLO_TIMEOUT_SECONDS = float(os.getenv("YOLO_WATERMARK_TIMEOUT_SECONDS", "20"))
YOLO_REQUIRE_CUDA = os.getenv("YOLO_WATERMARK_REQUIRE_CUDA", "true").lower() in {
    "1", "true", "yes", "on",
}
YOLO_EXPECTED_MODEL = "corzent/yolo11x_watermark_detection"
YOLO_EXPECTED_REVISION = os.getenv(
    "YOLO_WATERMARK_REVISION", "796a3b58a1121f20c5976d59314baea3db659a66"
)
YOLO_EXPECTED_SHA256 = os.getenv(
    "YOLO_WATERMARK_MODEL_SHA256",
    "6ac71b6ab8db27ec7928b5176e60a359c65e1579a5c1d58cf2f98df30cf3085e",
)
VISIBLE_BRANCH_WORKERS = max(
    2,
    min(16, int(os.getenv("WATERMARK_VISIBLE_BRANCH_WORKERS", "8"))),
)
_VISIBLE_EXECUTOR = ThreadPoolExecutor(
    max_workers=VISIBLE_BRANCH_WORKERS,
    thread_name_prefix="visible-watermark",
)
_registry_visible_hits = base._visible_hits
_base_health = base.app.view_functions["health"]
_base_precheck = base.app.view_functions["precheck"]


def _yolo_runtime_error(payload: dict[str, Any]) -> str:
    if payload.get("status") != "ok":
        return "service_not_ok"
    if payload.get("model") != YOLO_EXPECTED_MODEL:
        return "model_identity_mismatch"
    if YOLO_EXPECTED_REVISION and payload.get("modelRevision") != YOLO_EXPECTED_REVISION:
        return "model_revision_mismatch"
    if YOLO_EXPECTED_SHA256 and payload.get("modelSha256") != YOLO_EXPECTED_SHA256:
        return "model_checksum_mismatch"
    if YOLO_REQUIRE_CUDA and (
        payload.get("cudaReady") is not True
        or str(payload.get("device") or "").lower() == "cpu"
        or not payload.get("gpu")
    ):
        return "cuda_not_ready"
    return ""


def _valid_normalized_box(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
        width = float(value.get("w"))
        height = float(value.get("h"))
    except (TypeError, ValueError):
        return False
    return (
        all(math.isfinite(item) for item in (x, y, width, height))
        and 0.0 <= x <= 1.0
        and 0.0 <= y <= 1.0
        and 0.0 < width <= 1.0
        and 0.0 < height <= 1.0
        and x + width <= 1.0
        and y + height <= 1.0
    )


def _yolo_detection_error(payload: dict[str, Any]) -> str:
    runtime_error = _yolo_runtime_error(payload)
    if runtime_error:
        return runtime_error
    image = payload.get("image")
    detections = payload.get("detections")
    if not isinstance(image, dict) or not isinstance(detections, list):
        return "response_schema_invalid"
    try:
        if int(image.get("width") or 0) <= 0 or int(image.get("height") or 0) <= 0:
            return "image_dimensions_invalid"
    except (TypeError, ValueError):
        return "image_dimensions_invalid"
    for detection in detections:
        if not isinstance(detection, dict) or not _valid_normalized_box(detection.get("bbox")):
            return "detection_box_invalid"
    if bool(payload.get("detected")) != bool(detections):
        return "detection_count_inconsistent"
    try:
        if int(payload.get("count")) != len(detections):
            return "detection_count_inconsistent"
    except (TypeError, ValueError):
        return "detection_count_inconsistent"
    return ""


def _bbox_metrics(first: dict[str, Any], second: dict[str, Any]) -> tuple[float, float]:
    """Return IoU and coverage of the smaller box for normalized boxes."""
    try:
        ax1, ay1 = float(first["x"]), float(first["y"])
        ax2 = ax1 + float(first["w"])
        ay2 = ay1 + float(first["h"])
        bx1, by1 = float(second["x"]), float(second["y"])
        bx2 = bx1 + float(second["w"])
        by2 = by1 + float(second["h"])
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


def _corroborate_registry_hits(
    registry_hits: list[dict[str, Any]],
    yolo_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Annotate registry hits with an overlapping YOLO candidate."""
    corroborated: list[dict[str, Any]] = []
    for raw_hit in registry_hits:
        hit = dict(raw_hit)
        best: tuple[float, dict[str, Any]] | None = None
        for candidate in yolo_candidates:
            iou, smaller_coverage = _bbox_metrics(hit.get("bbox") or {}, candidate.get("bbox") or {})
            if iou < 0.08 and smaller_coverage < 0.5:
                continue
            match_score = max(iou, smaller_coverage)
            if best is None or match_score > best[0]:
                best = (match_score, candidate)
        if best is not None:
            candidate = best[1]
            hit.update({
                "yoloCorroborated": True,
                "yoloConfidence": round(float(candidate.get("confidence") or 0.0), 4),
                "yoloBbox": candidate.get("bbox") or {},
                "localizationModel": candidate.get("model") or "corzent/yolo11x_watermark_detection",
                "localizationModelRevision": candidate.get("modelRevision"),
            })
        else:
            hit["yoloCorroborated"] = False
        corroborated.append(hit)
    return corroborated


def _merge_visible_hits(
    registry_hits: list[dict[str, Any]],
    yolo_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep unmatched YOLO detections as non-decisive visible-watermark hits."""
    corroborated = _corroborate_registry_hits(registry_hits, yolo_candidates)
    unmatched_candidates = []
    for candidate in yolo_candidates:
        overlaps_registry = any(
            _boxes_overlap(hit.get("bbox") or {}, candidate.get("bbox") or {})
            for hit in registry_hits
        )
        if not overlaps_registry:
            unmatched_candidates.append(candidate)
    return [*corroborated, *unmatched_candidates]


def _generic_yolo_hits(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not YOLO_URL or not YOLO_TOKEN:
        return [], {
            "available": False,
            "error": "not_configured",
            "model": "corzent/yolo11x_watermark_detection",
            "mode": "visible_watermark_detection_with_platform_attribution",
        }
    started = time.perf_counter()
    with path.open("rb") as image_file:
        response = requests.post(
            YOLO_URL,
            headers={"Authorization": f"Bearer {YOLO_TOKEN}"},
            files={"file": (path.name, image_file, "application/octet-stream")},
            timeout=(2, YOLO_TIMEOUT_SECONDS),
        )
    response.raise_for_status()
    payload = response.json()
    detection_error = _yolo_detection_error(payload)
    if detection_error:
        raise ValueError(detection_error)
    candidates = [
        {
            "provider": "yolo11x_watermark",
            "label": "可见水印（平台待确认）",
            "location": "localized",
            "confidence": round(float(item.get("confidence") or 0.0), 4),
            "corroborated": False,
            "decisive": False,
            "evidenceRole": "localization",
            "bbox": item.get("bbox") or {},
            "model": payload.get("model"),
            "modelRevision": payload.get("modelRevision"),
        }
        for item in payload.get("detections") or []
        if isinstance(item, dict)
    ]
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return candidates, {
        "available": True,
        "detected": bool(candidates),
        "count": len(candidates),
        "mode": "visible_watermark_detection_with_platform_attribution",
        "elapsedMs": int(payload.get("elapsedMs") or elapsed_ms),
        "roundTripMs": elapsed_ms,
        "model": payload.get("model") or "corzent/yolo11x_watermark_detection",
        "modelRevision": payload.get("modelRevision"),
        "modelSha256": payload.get("modelSha256"),
        "device": payload.get("device"),
        "gpu": payload.get("gpu"),
        "cudaRequired": payload.get("cudaRequired"),
        "cudaReady": payload.get("cudaReady"),
        "confidenceThreshold": payload.get("confidenceThreshold"),
    }


def _timed_registry_hits(
    path: Path,
    provenance_path: Path | None = None,
) -> tuple[list[dict[str, Any]], int]:
    started = time.perf_counter()
    hits = _registry_visible_hits(path, provenance_path=provenance_path)
    return hits, int((time.perf_counter() - started) * 1000)


def _visible_hits_with_yolo(
    path: Path,
    provenance_path: Path | None = None,
) -> list[dict[str, Any]]:
    registry_future = _VISIBLE_EXECUTOR.submit(_timed_registry_hits, path, provenance_path)
    yolo_future = _VISIBLE_EXECUTOR.submit(_generic_yolo_hits, path)
    registry_hits, registry_elapsed_ms = registry_future.result()
    try:
        yolo_candidates, status = yolo_future.result()
        hits = _merge_visible_hits(registry_hits, yolo_candidates)
        confirmed = sum(1 for hit in hits if hit.get("yoloCorroborated") is True)
        status["knownPlatformCount"] = len(registry_hits)
        status["platformConfirmedCount"] = confirmed
        status["registryElapsedMs"] = registry_elapsed_ms
        status["branchesParallel"] = True
        status["branchWorkerLimit"] = VISIBLE_BRANCH_WORKERS
        g.generic_visible_watermark_status = status
    except (requests.RequestException, ValueError, TypeError) as exc:
        base.app.logger.warning("YOLO watermark detector unavailable: %s", type(exc).__name__)
        g.generic_visible_watermark_status = {
            "available": False,
            "error": type(exc).__name__,
            "model": "corzent/yolo11x_watermark_detection",
            "mode": "visible_watermark_detection_with_platform_attribution",
            "registryElapsedMs": registry_elapsed_ms,
            "branchesParallel": True,
            "branchWorkerLimit": VISIBLE_BRANCH_WORKERS,
        }
        hits = [dict(hit, yoloCorroborated=False) for hit in registry_hits]
    return hits


def precheck_with_yolo():
    response = _base_precheck()
    if isinstance(response, dict):
        response["genericVisibleWatermark"] = getattr(
            g,
            "generic_visible_watermark_status",
            {
                "available": False,
                "error": "not_run",
                "model": "corzent/yolo11x_watermark_detection",
                "mode": "visible_watermark_detection_with_platform_attribution",
            },
        )
    return response


def health_with_yolo():
    payload = dict(_base_health())
    yolo = {
        "available": False,
        "model": "corzent/yolo11x_watermark_detection",
        "mode": "visible_watermark_detection_with_platform_attribution",
    }
    try:
        response = requests.get(YOLO_HEALTH_URL, timeout=(1, 4))
        response.raise_for_status()
        yolo.update(response.json())
        runtime_error = _yolo_runtime_error(yolo)
        yolo["available"] = not runtime_error
        if runtime_error:
            yolo["validationError"] = runtime_error
        yolo["mode"] = "visible_watermark_detection_with_platform_attribution"
    except (requests.RequestException, ValueError, TypeError) as exc:
        yolo["error"] = type(exc).__name__
    payload["genericVisibleWatermark"] = yolo
    payload["visibleBranchWorkers"] = VISIBLE_BRANCH_WORKERS
    if not yolo.get("available") or payload.get("status") != "ok":
        payload["status"] = "degraded"
    return payload


base._visible_hits = _visible_hits_with_yolo
base.app.view_functions["health"] = health_with_yolo
base.app.view_functions["precheck"] = precheck_with_yolo
app = base.app
