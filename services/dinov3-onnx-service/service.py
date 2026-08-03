from __future__ import annotations

import io
import os
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request
from PIL import Image, ImageOps, UnidentifiedImageError


PACKAGE_DIR = Path(
    os.environ.get(
        "DINOV3_PACKAGE_DIR",
        "/home/ymk/model_registry/dinov3_vit7b16_linear_onnx_fp16",
    )
).expanduser().resolve()
MODEL_PATH = PACKAGE_DIR / "model" / "dinov3_vit7b16_linear_fp16.onnx"
LABELS_PATH = PACKAGE_DIR / "model" / "labels.json"
PROVIDER = os.environ.get("DINOV3_PROVIDER", "cpu").strip().lower() or "cpu"
SPLIT_DIR = Path(
    os.environ.get("DINOV3_SPLIT_DIR", "/mnt/sda1/ymk/dinov3_split_fp16")
).expanduser().resolve()
STAGE1_DEVICE = int(os.environ.get("DINOV3_STAGE1_DEVICE", "1"))
STAGE2_DEVICE = int(os.environ.get("DINOV3_STAGE2_DEVICE", "0"))
INTRA_OP_THREADS = max(0, int(os.environ.get("DINOV3_INTRA_OP_THREADS", "16")))
MAX_UPLOAD_BYTES = max(1, int(os.environ.get("DINOV3_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))))
MAX_IMAGE_PIXELS = max(1, int(os.environ.get("DINOV3_MAX_IMAGE_PIXELS", "24000000")))
QUEUE_WAIT_SECONDS = max(1.0, float(os.environ.get("DINOV3_QUEUE_WAIT_SECONDS", "20")))
MODEL_ID = "dinov3-vit7b16-linear-fp16"
MODEL_VERSION = "2026-08-02"


class ModelRuntime:
    def __init__(self) -> None:
        self.detector: Any | None = None
        self.state = "not_loaded"
        self.error = ""
        self.loaded_at = ""
        self.load_seconds: float | None = None
        self._load_lock = threading.Lock()
        self._inference_slot = threading.BoundedSemaphore(1)

    def load(self) -> Any:
        if self.detector is not None:
            return self.detector
        with self._load_lock:
            if self.detector is not None:
                return self.detector
            self.state = "loading"
            self.error = ""
            started = time.monotonic()
            try:
                import sys

                package = str(PACKAGE_DIR)
                if package not in sys.path:
                    sys.path.insert(0, package)
                if PROVIDER == "split-cuda":
                    from split_runtime import SplitCudaDetector

                    self.detector = SplitCudaDetector(
                        SPLIT_DIR,
                        package_dir=PACKAGE_DIR,
                        stage1_device=STAGE1_DEVICE,
                        stage2_device=STAGE2_DEVICE,
                    )
                else:
                    from inference_onnx import OnnxDetector

                    self.detector = OnnxDetector(
                        MODEL_PATH,
                        provider=PROVIDER,
                        batch_size=1,
                        intra_op_threads=INTRA_OP_THREADS,
                        labels_path=LABELS_PATH,
                    )
            except Exception as exc:
                self.state = "error"
                self.error = f"{type(exc).__name__}: {exc}"
                raise
            self.load_seconds = round(time.monotonic() - started, 3)
            self.loaded_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self.state = "ready"
            return self.detector

    def start_background_load(self) -> None:
        if self.state in {"loading", "ready"}:
            return

        def target() -> None:
            try:
                self.load()
            except Exception:
                pass

        threading.Thread(target=target, name="dinov3-model-loader", daemon=True).start()

    def acquire(self) -> bool:
        return self._inference_slot.acquire(timeout=QUEUE_WAIT_SECONDS)

    def release(self) -> None:
        self._inference_slot.release()


runtime = ModelRuntime()
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _confidence_level(fake_probability: float) -> str:
    distance = abs(float(fake_probability) - 0.5)
    if distance >= 0.35:
        return "高"
    if distance >= 0.2:
        return "中"
    return "低"


def _health_payload() -> dict[str, Any]:
    split_ready = (SPLIT_DIR / "stage1.onnx").is_file() and (
        SPLIT_DIR / "stage2.onnx"
    ).is_file()
    artifact_ready = LABELS_PATH.is_file() and (
        split_ready if PROVIDER == "split-cuda" else MODEL_PATH.is_file()
    )
    detector = runtime.detector
    providers = list(getattr(detector, "active_providers", []) or [])
    ready = runtime.state == "ready" and detector is not None
    warnings = [
        "该模型尚未绑定独立签名校准清单，模型分数仅作为候选模型输出。",
    ]
    if PROVIDER == "cpu":
        warnings.append("66 服务器单卡显存为 12 GB，ViT-7B 无法单卡常驻，当前使用 CPUExecutionProvider。")
    elif PROVIDER == "split-cuda":
        warnings.append("ViT-7B 已按 20+20 层切分并常驻两张 GPU；当前仅允许单路串行推理。")
    return {
        "status": "ok" if ready else runtime.state,
        "service": "realguard-dinov3-vit7b16",
        "serviceOk": ready,
        "artifactReady": artifact_ready,
        "dependencyReady": runtime.state != "error",
        "capabilityReady": ready and artifact_ready,
        "verdictReady": False,
        "decisionMode": "review_only",
        "inferenceMode": f"dinov3-onnx-{PROVIDER}",
        "activeProvider": providers[0] if providers else "",
        "providers": providers,
        "model": MODEL_ID,
        "modelVersion": MODEL_VERSION,
        "modelPath": str(MODEL_PATH),
        "splitModelPath": str(SPLIT_DIR) if PROVIDER == "split-cuda" else "",
        "threshold": 0.5,
        "loadSeconds": runtime.load_seconds,
        "loadedAt": runtime.loaded_at,
        "error": runtime.error,
        "queueDepth": 0,
        "warnings": warnings,
    }


@app.get("/health")
def health():
    if runtime.state == "not_loaded":
        runtime.start_background_load()
    return jsonify(_health_payload())


@app.get("/ready")
def ready():
    payload = _health_payload()
    return jsonify(payload), 200 if payload["capabilityReady"] else 503


def _read_image() -> tuple[Image.Image, str]:
    uploaded = request.files.get("image_file") or request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise ValueError("image_file is required")
    raw = uploaded.stream.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise ValueError("uploaded image is empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise OverflowError("uploaded image exceeds the 25 MB limit")
    with Image.open(io.BytesIO(raw)) as source:
        source.load()
        width, height = source.size
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise OverflowError("decoded image exceeds the 24 megapixel limit")
        image = ImageOps.exif_transpose(source).convert("RGB")
    return image, uploaded.filename


@app.post("/image")
def predict_image():
    if not runtime.acquire():
        response = jsonify({"code": 429, "msg": "DINOv3 model queue is full"})
        response.status_code = 429
        response.headers["Retry-After"] = str(int(QUEUE_WAIT_SECONDS))
        return response
    try:
        image, filename = _read_image()
        started = time.monotonic()
        detector = runtime.load()
        result = detector.predict_pil(image)
        latency_ms = int((time.monotonic() - started) * 1000)
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc)}), 400
    except OverflowError as exc:
        return jsonify({"code": 413, "msg": str(exc)}), 413
    except (UnidentifiedImageError, OSError) as exc:
        return jsonify({"code": 415, "msg": f"unsupported image: {exc}"}), 415
    except Exception as exc:
        return jsonify({"code": 503, "msg": f"model inference failed: {type(exc).__name__}: {exc}"}), 503
    finally:
        runtime.release()

    fake_probability = max(0.0, min(1.0, float(result["fake_probability"])))
    real_probability = max(0.0, min(1.0, float(result["real_probability"])))
    is_fake = fake_probability >= 0.5
    final_label = "AI生成图像" if is_fake else "真实图像"
    confidence = _confidence_level(fake_probability)
    explanation = (
        f"DINOv3 ViT-7B 线性探针给出二元模型结果：{final_label}。"
        f"原始 AI 分数为 {fake_probability:.4f}，固定阈值为 0.5。"
        "该候选模型尚未完成慧鉴生产校准签名，结果需结合水印、元数据与来源证据解释。"
    )
    data = {
        "fake_percentage": round(fake_probability * 100.0, 4),
        "real_percentage": round(real_probability * 100.0, 4),
        "detector_probability": fake_probability,
        "probability": fake_probability,
        "final_label": final_label,
        "confidence": confidence,
        "clarity": confidence,
        "explanation": explanation,
        "explantation": explanation,
        "visual_issues": [],
        "filename": filename,
        "decisionStatus": "review_only",
        "decisionAuthority": "none",
        "reviewRequired": True,
        "modelDecisionReady": False,
        "model": {
            "id": MODEL_ID,
            "version": MODEL_VERSION,
            "runtime": f"onnxruntime-{PROVIDER}",
            "providers": list(getattr(detector, "active_providers", []) or []),
            "latencyMs": latency_ms,
        },
        "remote_evidence": {
            "modelDecision": {
                "ready": False,
                "mode": "review_only",
                "rawModelScore": fake_probability,
                "publishedProbability": None,
                "finalLabel": final_label,
                "gateReasons": ["production_calibration_manifest_missing"],
            }
        },
    }
    return jsonify({"code": 200, "msg": "success", "data": data})


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"code": 413, "msg": "uploaded image exceeds the 25 MB limit"}), 413


if _truthy(os.environ.get("DINOV3_EAGER_LOAD", "0")):
    runtime.start_background_load()
