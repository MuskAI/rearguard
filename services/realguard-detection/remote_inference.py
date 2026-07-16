import hmac
import os
import sys
import tempfile
import time
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename


CURRENT_DIR = Path(__file__).resolve().parent
IMAGEDETECTION_DIR = CURRENT_DIR.parent
AGENT_DIR = IMAGEDETECTION_DIR / "Agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.append(str(AGENT_DIR))

from tools.AIGC_Detection.inference_onnx import analyze_image, get_model_status  # noqa: E402


model_inference_blueprint = Blueprint("model_inference", __name__)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "bmp", "gif"}
MAX_UPLOAD_BYTES = int(os.environ.get("REALGUARD_MODEL_MAX_UPLOAD_BYTES", str(32 * 1024 * 1024)))


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


@model_inference_blueprint.get("/internal/model/health")
def model_health():
    unauthorized = _authorize()
    if unauthorized:
        return unauthorized
    status = get_model_status()
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
        with tempfile.NamedTemporaryFile(
            prefix="realguard-model-",
            suffix=f".{extension}",
            delete=False,
        ) as temp_file:
            temp_path = temp_file.name
            image_file.save(temp_file)

        result = analyze_image(
            temp_path,
            chunk_size=chunk_size,
            max_tiles=max_tiles,
            top_k=top_k,
        )
        runtime = dict(result.get("runtime") or {})
        runtime["endpointMs"] = round((time.perf_counter() - endpoint_started) * 1000.0, 2)
        return jsonify({
            "code": 200,
            "msg": "success",
            "data": {
                "model": result.get("model"),
                "fakeProbability": result.get("fakeProbability"),
                "realProbability": result.get("realProbability"),
                "finalLabel": result.get("finalLabel"),
                "originalSize": result.get("originalSize"),
                "chunkCount": result.get("chunkCount"),
                "parameters": result.get("parameters"),
                "runtime": runtime,
            },
        })
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
