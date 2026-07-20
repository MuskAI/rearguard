import hashlib
import hmac
import json
import math
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
    UnsupportedAnimatedImageError,
    acquire_inference_slot,
    analyze_image,
    get_model_status,
    release_inference_slot,
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
VISIBLE_PRECHECK_HEALTH_URL = os.environ.get(
    "REALGUARD_MODEL_VISIBLE_PRECHECK_HEALTH_URL",
    "http://127.0.0.1:5066/health",
).strip()
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
    "coordinateSpace",
    "displaySize",
    "encodedSize",
    "sourceOrientation",
    "decision",
    "report",
    "positiveEvidenceAvailable",
    "completeVisibleScan",
)
_REGISTRY_PROVIDERS = frozenset({"gemini", "doubao", "jimeng", "jimeng_pill", "samsung"})
_RESPONSE_INTEGRITY_SCHEMA = "cn.huijian.remote-inference-response-v1"
_DEFAULT_RESPONSE_HMAC_KEY_ID = "v1"


def _valid_normalized_box(value):
    if not isinstance(value, dict):
        return False
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
        width = float(value.get("w"))
        height = float(value.get("h"))
    except (TypeError, ValueError):
        return False
    return bool(
        all(math.isfinite(item) for item in (x, y, width, height))
        and 0.0 <= x <= 1.0
        and 0.0 <= y <= 1.0
        and 0.0 < width <= 1.0
        and 0.0 < height <= 1.0
        and x + width <= 1.0
        and y + height <= 1.0
    )


def _has_valid_registry_evidence(payload):
    hits = payload.get("visibleHits") if isinstance(payload, dict) else None
    return bool(
        isinstance(hits, list)
        and any(
            isinstance(hit, dict)
            and str(hit.get("provider") or "").strip().lower() in _REGISTRY_PROVIDERS
            and _valid_normalized_box(hit.get("bbox"))
            for hit in hits
        )
    )


def _configured_tokens():
    values = (
        os.environ.get("REALGUARD_MODEL_INTERNAL_TOKEN", "").strip(),
        os.environ.get("REALGUARD_MODEL_INTERNAL_TOKEN_PREVIOUS", "").strip(),
    )
    return tuple(dict.fromkeys(value for value in values if value))


def _valid_key_id(value):
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and value[0].isalnum()
        and all(char.isalnum() or char in "._:-" for char in value)
    )


def _response_hmac_key_id():
    value = os.environ.get(
        "REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID",
        _DEFAULT_RESPONSE_HMAC_KEY_ID,
    ).strip()
    return value if _valid_key_id(value) else None


def _response_hmac_key():
    raw = os.environ.get("REALGUARD_MODEL_RESPONSE_HMAC_KEY", "").strip().lower()
    if len(raw) != 64 or any(char not in "0123456789abcdef" for char in raw):
        return None
    return bytes.fromhex(raw)


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _request_nonce():
    nonce = request.headers.get("X-RealGuard-Request-Nonce", "").strip().lower()
    if not nonce:
        return ""
    if len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        raise ValueError("X-RealGuard-Request-Nonce must be 32 lowercase hex characters")
    return nonce


def _response_integrity(data, image_bytes, nonce):
    if not nonce:
        # Compatibility for an old Web process during the short GPU-first rollout window.
        return None
    key = _response_hmac_key()
    key_id = _response_hmac_key_id()
    if key is None or key_id is None:
        raise RuntimeError("Remote inference response HMAC key is not configured")
    signed = {
        "schema": _RESPONSE_INTEGRITY_SCHEMA,
        "keyId": key_id,
        "requestNonce": nonce,
        "imageSha256": hashlib.sha256(image_bytes).hexdigest(),
        "issuedAt": int(time.time()),
        "bodySha256": hashlib.sha256(_canonical_json(data)).hexdigest(),
    }
    signed["hmacSha256"] = hmac.new(
        key,
        _canonical_json(signed),
        hashlib.sha256,
    ).hexdigest()
    return signed


def _authorize():
    configured = _configured_tokens()
    if not configured:
        return jsonify({"code": 503, "msg": "Internal model token is not configured"}), 503
    provided = request.headers.get("X-RealGuard-Internal-Token", "").strip()
    token_matches = [hmac.compare_digest(provided, candidate) for candidate in configured]
    if not provided or not any(token_matches):
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
    display_size = payload.get("displaySize") if isinstance(payload, dict) else None
    generic = payload.get("genericVisibleWatermark") if isinstance(payload, dict) else None
    generic_ready = isinstance(generic, dict) and generic.get("available") is True
    registry_positive = _has_valid_registry_evidence(payload)
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "ok"
        or payload.get("coordinateSpace") != "display_normalized_v1"
        or not isinstance(display_size, dict)
        or int(display_size.get("width") or 0) <= 0
        or int(display_size.get("height") or 0) <= 0
        or (not generic_ready and not registry_positive)
    ):
        raise ValueError("Visible watermark precheck returned an invalid response")
    payload["positiveEvidenceAvailable"] = registry_positive
    payload["completeVisibleScan"] = generic_ready
    return {key: payload.get(key) for key in _PRECHECK_FIELDS if key in payload}


def _visible_precheck_ready():
    if not VISIBLE_PRECHECK_HEALTH_URL or not VISIBLE_PRECHECK_TOKEN:
        return False
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(VISIBLE_PRECHECK_HEALTH_URL, timeout=(1, 4))
        response.raise_for_status()
        payload = response.json()
        return bool(
            isinstance(payload, dict)
            and payload.get("status") == "ok"
            and payload.get("registryReady") is True
            and payload.get("tokenReady") is True
            and payload.get("coordinateSpace") == "display_normalized_v1"
            and isinstance(payload.get("genericVisibleWatermark"), dict)
            and payload["genericVisibleWatermark"].get("available") is True
        )
    except (requests.RequestException, TypeError, ValueError):
        return False


@model_inference_blueprint.get("/internal/model/health")
def model_health():
    unauthorized = _authorize()
    if unauthorized:
        return unauthorized
    status = get_model_status()
    status["visiblePrecheckWorkers"] = VISIBLE_PRECHECK_WORKERS
    status["visiblePrecheckReady"] = _visible_precheck_ready()
    status["responseIntegrityKeyId"] = _response_hmac_key_id()
    status["responseIntegrityReady"] = (
        _response_hmac_key() is not None
        and status["responseIntegrityKeyId"] is not None
    )
    runtime_ready = (
        bool(status.get("initialized"))
        and status.get("activeProvider") == "CUDAExecutionProvider"
        and status["visiblePrecheckReady"]
        and status["responseIntegrityReady"]
    )
    decision_policy = status.get("modelDecisionPolicy") if isinstance(status.get("modelDecisionPolicy"), dict) else {}
    status["runtimeReady"] = runtime_ready
    status["verdictReady"] = bool(decision_policy.get("ready"))
    status["decisionMode"] = str(decision_policy.get("mode") or "review_only")
    status["decisionGateReasons"] = [str(item) for item in decision_policy.get("gateReasons") or []]
    return jsonify({"code": 200 if runtime_ready else 503, "data": status}), 200 if runtime_ready else 503


@model_inference_blueprint.post("/internal/model/predict")
def model_predict():
    unauthorized = _authorize()
    if unauthorized:
        return unauthorized

    try:
        request_nonce = _request_nonce()
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc)}), 400

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
    precheck_future = None
    inference_slot_acquired = False
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

        queue_wait_ms = acquire_inference_slot()
        inference_slot_acquired = True
        precheck_started = time.perf_counter()
        precheck_future = _PRECHECK_EXECUTOR.submit(
            _run_visible_precheck,
            image_bytes,
            safe_name,
            image_file.mimetype,
        )
        try:
            result = analyze_image(
                temp_path,
                chunk_size=chunk_size,
                max_tiles=max_tiles,
                top_k=top_k,
                admission_queue_wait_ms=queue_wait_ms,
            )
        finally:
            release_inference_slot()
            inference_slot_acquired = False
        precheck_payload = None
        precheck_status = "unavailable"
        try:
            precheck_payload = precheck_future.result(timeout=VISIBLE_PRECHECK_TIMEOUT + 2)
            if precheck_payload:
                precheck_status = "success"
            else:
                precheck_payload = {
                    "status": "unavailable",
                    "errorCode": "visible_watermark_not_configured",
                    "message": "可见水印检测服务未配置，本次证据不完整。",
                    "elapsedMs": int((time.perf_counter() - precheck_started) * 1000),
                }
        except Exception as exc:
            precheck_future.cancel()
            precheck_status = "failed"
            precheck_payload = {
                "status": "failed",
                "errorCode": "visible_watermark_unavailable",
                "message": "可见水印检测暂不可用，本次证据不完整。",
                "elapsedMs": int((time.perf_counter() - precheck_started) * 1000),
                "failureType": type(exc).__name__,
            }
        runtime = dict(result.get("runtime") or {})
        runtime["endpointMs"] = round((time.perf_counter() - endpoint_started) * 1000.0, 2)
        runtime["visiblePrecheckStatus"] = precheck_status
        data = {
            "model": result.get("model"),
            "fakeProbability": result.get("fakeProbability"),
            "realProbability": result.get("realProbability"),
            "rawModelScore": result.get("rawModelScore"),
            "finalLabel": result.get("finalLabel"),
            "modelDecision": result.get("modelDecision"),
            "originalSize": result.get("originalSize"),
            "processedSize": result.get("processedSize"),
            "downsample": result.get("downsample"),
            "chunkCount": result.get("chunkCount"),
            "parameters": result.get("parameters"),
            "processing": result.get("processing"),
            "runtime": runtime,
        }
        if isinstance(precheck_payload, dict):
            data["visibleWatermarkPrecheck"] = precheck_payload
        response_payload = {
            "code": 200,
            "msg": "success",
            "data": data,
        }
        integrity = _response_integrity(data, image_bytes, request_nonce)
        if integrity:
            response_payload["integrity"] = integrity
        return jsonify(response_payload)
    except ImagePixelsTooLargeError as exc:
        return jsonify({"code": 413, "msg": str(exc)}), 413
    except UnsupportedAnimatedImageError as exc:
        return jsonify({"code": 415, "msg": str(exc)}), 415
    except InferenceBusyError as exc:
        response = jsonify({"code": 429, "msg": str(exc)})
        response.headers["Retry-After"] = "5"
        return response, 429
    except ValueError as exc:
        return jsonify({"code": 400, "msg": str(exc)}), 400
    except Exception as exc:
        return jsonify({"code": 500, "msg": f"Model inference failed: {exc}"}), 500
    finally:
        if inference_slot_acquired:
            release_inference_slot()
        if precheck_future is not None and not precheck_future.done():
            precheck_future.cancel()
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
