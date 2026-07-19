import hmac
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename


CURRENT_DIR = Path(__file__).resolve().parent
IMAGEDETECTION_DIR = CURRENT_DIR.parent
AGENT_DIR = IMAGEDETECTION_DIR / "Agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.append(str(AGENT_DIR))

from tools.AIGC_Detection.inference_onnx import (  # noqa: E402
    ImagePixelsTooLargeError,
    InferenceBusyError,
    analyze_image,
    get_model_status,
)


model_inference_blueprint = Blueprint("model_inference", __name__)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "bmp", "gif"}
MAX_UPLOAD_BYTES = int(os.environ.get("REALGUARD_MODEL_MAX_UPLOAD_BYTES", str(32 * 1024 * 1024)))
VISIBLE_PRECHECK_URL = os.environ.get(
    "REALGUARD_MODEL_VISIBLE_PRECHECK_URL",
    "http://127.0.0.1:5066/v1/precheck",
).strip()
VISIBLE_PRECHECK_TOKEN = (
    os.environ.get("REALGUARD_MODEL_VISIBLE_PRECHECK_TOKEN")
    or os.environ.get("WATERMARK_PRECHECK_TOKEN")
    or ""
).strip()
VISIBLE_PRECHECK_TIMEOUT = float(os.environ.get("REALGUARD_MODEL_VISIBLE_PRECHECK_TIMEOUT", "12"))
VISIBLE_PRECHECK_WORKERS = max(
    1,
    min(8, int(os.environ.get("REALGUARD_MODEL_VISIBLE_PRECHECK_WORKERS", "4"))),
)
_PRECHECK_EXECUTOR = ThreadPoolExecutor(
    max_workers=VISIBLE_PRECHECK_WORKERS,
    thread_name_prefix="visible-precheck",
)
_PRECHECK_FIELDS = (
    "status",
    "elapsedMs",
    "engine",
    "engineVersion",
    "detections",
    "visibleHits",
    "genericVisibleWatermark",
    "decision",
    "report",
)


def _configured_token():
    return os.environ.get("REALGUARD_MODEL_INTERNAL_TOKEN", "").strip()


def _authorize():
    configured = _configured_token()
    if not configured:
        return jsonify({"code": 503, "msg": "Internal model token is not configured"}), 503
    provided = request.headers.get("X-RealGuard-Internal-Token", "").strip()
    if not provided or not hmac.compare_digest(provided, configured):
        return jsonify({"code": 401, "msg": "Unauthorized"}), 401
    return None


def _optional_int(name, minimum, maximum):
    raw = request.form.get(name)
    if raw in (None, ""):
        return None
    value = int(raw)
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _run_visible_precheck(image_bytes, filename, mimetype):
    if not VISIBLE_PRECHECK_URL or not VISIBLE_PRECHECK_TOKEN:
        return None
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            VISIBLE_PRECHECK_URL,
            headers={"Authorization": f"Bearer {VISIBLE_PRECHECK_TOKEN}"},
            files={"file": (filename, image_bytes, mimetype or "application/octet-stream")},
            timeout=(2, VISIBLE_PRECHECK_TIMEOUT),
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ValueError("Visible watermark precheck returned an invalid response")
    return {key: payload.get(key) for key in _PRECHECK_FIELDS if key in payload}


@model_inference_blueprint.get("/internal/model/health")
def model_health():
    unauthorized = _authorize()
    if unauthorized:
        return unauthorized
    status = get_model_status()
    status["visiblePrecheckWorkers"] = VISIBLE_PRECHECK_WORKERS
    ready = bool(status.get("initialized")) and status.get("activeProvider") == "CUDAExecutionProvider"
    return jsonify({"code": 200 if ready else 503, "data": status}), 200 if ready else 503


@model_inference_blueprint.post("/internal/model/predict")
def model_predict():
    unauthorized = _authorize()
    if unauthorized:
        return unauthorized

    image_file = request.files.get("image_file")
    if not image_file or not image_file.filename:
        return jsonify({"code": 400, "msg": "image_file is required"}), 400
    if request.content_length and request.content_length > MAX_UPLOAD_BYTES:
        return jsonify({"code": 413, "msg": "Image is too large"}), 413

    safe_name = secure_filename(image_file.filename)
    extension = Path(safe_name).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({"code": 400, "msg": "Unsupported image format"}), 400

    temp_path = None
    endpoint_started = time.perf_counter()
    try:
        chunk_size = _optional_int("chunk_size", 64, 4096)
        max_tiles = _optional_int("max_tiles", 1, 256)
        top_k = _optional_int("top_k", 1, 128)
        image_bytes = image_file.read()
        if not image_bytes:
            return jsonify({"code": 400, "msg": "Image is empty"}), 400
        if len(image_bytes) > MAX_UPLOAD_BYTES:
            return jsonify({"code": 413, "msg": "Image is too large"}), 413

        with tempfile.NamedTemporaryFile(
            prefix="realguard-model-",
            suffix=f".{extension}",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            temp_file.write(image_bytes)

        precheck_future = _PRECHECK_EXECUTOR.submit(
            _run_visible_precheck,
            image_bytes,
            safe_name,
            image_file.mimetype,
        )
        result = analyze_image(
            temp_path,
            chunk_size=chunk_size,
            max_tiles=max_tiles,
            top_k=top_k,
        )
        precheck_payload = None
        precheck_status = "disabled"
        try:
            precheck_payload = precheck_future.result(timeout=VISIBLE_PRECHECK_TIMEOUT + 2)
            precheck_status = "success" if precheck_payload else "disabled"
        except Exception as exc:
            precheck_status = f"failed:{type(exc).__name__}"
        runtime = dict(result.get("runtime") or {})
        runtime["endpointMs"] = round((time.perf_counter() - endpoint_started) * 1000.0, 2)
        runtime["visiblePrecheckStatus"] = precheck_status
        data = {
            "model": result.get("model"),
            "fakeProbability": result.get("fakeProbability"),
            "realProbability": result.get("realProbability"),
            "finalLabel": result.get("finalLabel"),
            "originalSize": result.get("originalSize"),
            "processedSize": result.get("processedSize"),
            "downsample": result.get("downsample"),
            "chunkCount": result.get("chunkCount"),
            "parameters": result.get("parameters"),
            "processing": result.get("processing"),
            "runtime": runtime,
        }
        if precheck_payload:
            data["visibleWatermarkPrecheck"] = precheck_payload
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": data,
        })
    except ImagePixelsTooLargeError as exc:
        return jsonify({"code": 413, "msg": str(exc)}), 413
    except InferenceBusyError as exc:
        response = jsonify({"code": 429, "msg": str(exc)})
        response.headers["Retry-After"] = "5"
        return response, 429
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc)}), 400
    except Exception as exc:
        return jsonify({"code": 500, "msg": f"Model inference failed: {exc}"}), 500
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
