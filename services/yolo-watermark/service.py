"""GPU-backed visible watermark detector using a pinned YOLO11x checkpoint."""
from __future__ import annotations

import hashlib
import hmac
import io
import os
import threading
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("YOLO_AUTOINSTALL", "false")
os.environ.setdefault("ULTRALYTICS_OFFLINE", "true")

import torch
from flask import Flask, jsonify, request
from PIL import Image, ImageOps, UnidentifiedImageError
from ultralytics import YOLO


MODEL_PATH = Path(os.getenv("YOLO_WATERMARK_MODEL", Path(__file__).parent / "models" / "best.pt"))
MODEL_REVISION = os.getenv("YOLO_WATERMARK_REVISION", "796a3b58a1121f20c5976d59314baea3db659a66")
EXPECTED_MODEL_SHA256 = os.getenv(
    "YOLO_WATERMARK_MODEL_SHA256",
    "6ac71b6ab8db27ec7928b5176e60a359c65e1579a5c1d58cf2f98df30cf3085e",
)
API_TOKEN = os.getenv("YOLO_WATERMARK_TOKEN", "")
DEVICE = os.getenv("YOLO_WATERMARK_DEVICE", "0" if torch.cuda.is_available() else "cpu")
INPUT_SIZE = int(os.getenv("YOLO_WATERMARK_IMAGE_SIZE", "1280"))
CONFIDENCE = float(os.getenv("YOLO_WATERMARK_CONFIDENCE", "0.35"))
IOU_THRESHOLD = float(os.getenv("YOLO_WATERMARK_IOU", "0.50"))
MAX_DETECTIONS = int(os.getenv("YOLO_WATERMARK_MAX_DETECTIONS", "100"))
MAX_UPLOAD_BYTES = int(os.getenv("YOLO_WATERMARK_MAX_BYTES", str(30 * 1024 * 1024)))
WARMUP_ENABLED = os.getenv("YOLO_WATERMARK_WARMUP", "true").lower() in {"1", "true", "yes"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
_inference_lock = threading.Lock()

if not MODEL_PATH.is_file():
    raise RuntimeError(f"YOLO watermark checkpoint not found: {MODEL_PATH}")

checkpoint_digest = hashlib.sha256()
with MODEL_PATH.open("rb") as checkpoint_file:
    for checkpoint_chunk in iter(lambda: checkpoint_file.read(1024 * 1024), b""):
        checkpoint_digest.update(checkpoint_chunk)
checkpoint_sha256 = checkpoint_digest.hexdigest()
if EXPECTED_MODEL_SHA256 and not hmac.compare_digest(checkpoint_sha256, EXPECTED_MODEL_SHA256):
    raise RuntimeError(f"YOLO watermark checkpoint checksum mismatch: {checkpoint_sha256}")

_model = YOLO(str(MODEL_PATH))
if WARMUP_ENABLED:
    with torch.inference_mode():
        _model.predict(
            source=Image.new("RGB", (64, 64), color=(127, 127, 127)),
            imgsz=INPUT_SIZE,
            conf=CONFIDENCE,
            iou=IOU_THRESHOLD,
            max_det=1,
            device=DEVICE,
            verbose=False,
        )


def _authorized() -> bool:
    if not API_TOKEN:
        return False
    header = request.headers.get("Authorization", "")
    supplied = header[7:] if header.startswith("Bearer ") else ""
    return bool(supplied) and hmac.compare_digest(supplied, API_TOKEN)


def _gpu_name() -> str | None:
    if DEVICE == "cpu" or not torch.cuda.is_available():
        return None
    try:
        index = int(DEVICE.split(":")[-1]) if ":" in DEVICE else int(DEVICE)
        return torch.cuda.get_device_name(index)
    except (TypeError, ValueError, RuntimeError):
        return None


def _decode_image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.load()
    return image


def _serialize_detections(result: Any, width: int, height: int) -> list[dict[str, Any]]:
    detections: list[dict[str, Any]] = []
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return detections

    xyxy = boxes.xyxy.detach().cpu().numpy()
    confidences = boxes.conf.detach().cpu().numpy()
    classes = boxes.cls.detach().cpu().numpy().astype(int)
    names = getattr(result, "names", {}) or {}
    for coordinates, confidence, class_id in zip(xyxy, confidences, classes):
        x1, y1, x2, y2 = (float(value) for value in coordinates)
        x1, y1 = max(0.0, x1), max(0.0, y1)
        x2, y2 = min(float(width), x2), min(float(height), y2)
        if x2 <= x1 or y2 <= y1:
            continue
        label = names.get(int(class_id), "watermark") if isinstance(names, dict) else "watermark"
        detections.append({
            "classId": int(class_id),
            "label": str(label),
            "confidence": round(float(confidence), 4),
            "xyxy": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            "bbox": {
                "x": round(x1 / max(width, 1), 4),
                "y": round(y1 / max(height, 1), 4),
                "w": round((x2 - x1) / max(width, 1), 4),
                "h": round((y2 - y1) / max(height, 1), 4),
            },
        })
    return detections


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": "detect-only",
        "engine": "ultralytics-yolo11x",
        "model": "corzent/yolo11x_watermark_detection",
        "modelRevision": MODEL_REVISION,
        "modelSha256": checkpoint_sha256,
        "modelPath": str(MODEL_PATH),
        "device": DEVICE,
        "gpu": _gpu_name(),
        "inputSize": INPUT_SIZE,
        "confidenceThreshold": CONFIDENCE,
        "warmupEnabled": WARMUP_ENABLED,
        "maxUploadBytes": MAX_UPLOAD_BYTES,
    }


@app.post("/v1/detect")
def detect():
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

    started = time.perf_counter()
    try:
        image = _decode_image(data)
    except (UnidentifiedImageError, OSError, ValueError):
        return jsonify({"detail": "unsupported or corrupt image"}), 415

    width, height = image.size
    try:
        with _inference_lock, torch.inference_mode():
            prediction = _model.predict(
                source=image,
                imgsz=INPUT_SIZE,
                conf=CONFIDENCE,
                iou=IOU_THRESHOLD,
                max_det=MAX_DETECTIONS,
                device=DEVICE,
                verbose=False,
            )[0]
        detections = _serialize_detections(prediction, width, height)
    except Exception as exc:
        app.logger.exception("YOLO watermark inference failed")
        return jsonify({"detail": "inference failed", "errorType": type(exc).__name__}), 500

    return {
        "status": "ok",
        "engine": "ultralytics-yolo11x",
        "model": "corzent/yolo11x_watermark_detection",
        "modelRevision": MODEL_REVISION,
        "confidenceThreshold": CONFIDENCE,
        "elapsedMs": int((time.perf_counter() - started) * 1000),
        "image": {"width": width, "height": height},
        "detected": bool(detections),
        "count": len(detections),
        "detections": detections,
    }


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"detail": "file too large"}), 413
