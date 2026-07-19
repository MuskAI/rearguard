import hashlib
import hmac
import json
import os
import secrets
import threading
import time

import numpy as np
import onnxruntime as ort
import requests
from PIL import Image


current_dir = os.path.dirname(os.path.abspath(__file__))

session = None
input_name = None
output_name = None

REMOTE_INFERENCE_URL = os.environ.get("REALGUARD_REMOTE_INFERENCE_URL", "").strip()
REMOTE_INFERENCE_TOKEN = os.environ.get("REALGUARD_MODEL_INTERNAL_TOKEN", "").strip()
REMOTE_RESPONSE_HMAC_KEY = os.environ.get(
    "REALGUARD_MODEL_RESPONSE_HMAC_KEY", ""
).strip().lower()
REMOTE_RESPONSE_HMAC_KEY_ID = os.environ.get(
    "REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID", "v1"
).strip()
REMOTE_RESPONSE_HMAC_KEYS_JSON = os.environ.get(
    "REALGUARD_MODEL_RESPONSE_HMAC_KEYS_JSON", "{}"
).strip()
REMOTE_INFERENCE_TIMEOUT = float(os.environ.get("REALGUARD_REMOTE_INFERENCE_TIMEOUT", "120"))
REMOTE_INFERENCE_TOTAL_TIMEOUT = max(
    1.0,
    min(175.0, float(os.environ.get("REALGUARD_REMOTE_INFERENCE_TOTAL_TIMEOUT", "150"))),
)
REMOTE_INFERENCE_MAX_ATTEMPTS = max(
    1,
    min(5, int(os.environ.get("REALGUARD_REMOTE_INFERENCE_MAX_ATTEMPTS", "3"))),
)
REMOTE_INFERENCE_RETRY_MAX_DELAY = max(
    0.0,
    min(10.0, float(os.environ.get("REALGUARD_REMOTE_INFERENCE_RETRY_MAX_DELAY", "5"))),
)
# 429 is an explicit admission rejection, so the server guarantees inference
# did not start. Proxy 502/504 and generic connection resets have unknown
# outcomes and must not be replayed without an end-to-end idempotency key.
REMOTE_INFERENCE_RETRY_STATUSES = {429}
REMOTE_REQUIRE_CUDA = os.environ.get("REALGUARD_REMOTE_REQUIRE_CUDA", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
REMOTE_REQUIRE_RESPONSE_INTEGRITY = os.environ.get(
    "REALGUARD_REMOTE_REQUIRE_RESPONSE_INTEGRITY", "1"
).strip().lower() in {"1", "true", "yes", "on"}
REMOTE_RESPONSE_MAX_AGE_SECONDS = max(
    30,
    min(900, int(os.environ.get("REALGUARD_REMOTE_RESPONSE_MAX_AGE_SECONDS", "300"))),
)
_RESPONSE_INTEGRITY_SCHEMA = "cn.huijian.remote-inference-response-v1"
_REMOTE_EVIDENCE = threading.local()


class RemoteInferenceError(RuntimeError):
    def __init__(self, message, *, status_code=503, error_code="remote_inference_failed", retry_after=""):
        super().__init__(message)
        self.status_code = int(status_code or 503)
        self.error_code = str(error_code or "remote_inference_failed")
        self.retry_after = str(retry_after or "")


def consume_remote_evidence():
    evidence = getattr(_REMOTE_EVIDENCE, "value", None)
    if hasattr(_REMOTE_EVIDENCE, "value"):
        del _REMOTE_EVIDENCE.value
    return dict(evidence) if isinstance(evidence, dict) else {}


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _valid_key_id(value):
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and value[0].isalnum()
        and all(char.isalnum() or char in "._:-" for char in value)
    )


def _response_hmac_keyring():
    try:
        decoded = json.loads(REMOTE_RESPONSE_HMAC_KEYS_JSON or "{}")
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Remote inference response integrity keyring is malformed") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("Remote inference response integrity keyring is malformed")
    keyring = {}
    for raw_key_id, raw_key in decoded.items():
        key_id = str(raw_key_id or "").strip()
        key = str(raw_key or "").strip().lower()
        if (
            not _valid_key_id(key_id)
            or len(key) != 64
            or any(char not in "0123456789abcdef" for char in key)
        ):
            raise RuntimeError("Remote inference response integrity keyring is malformed")
        keyring[key_id] = key
    if REMOTE_RESPONSE_HMAC_KEY:
        if (
            not _valid_key_id(REMOTE_RESPONSE_HMAC_KEY_ID)
            or len(REMOTE_RESPONSE_HMAC_KEY) != 64
            or any(char not in "0123456789abcdef" for char in REMOTE_RESPONSE_HMAC_KEY)
        ):
            raise RuntimeError("Remote inference response integrity key is not configured")
        keyring[REMOTE_RESPONSE_HMAC_KEY_ID] = REMOTE_RESPONSE_HMAC_KEY
    return keyring


def _verify_response_integrity(payload, data, nonce, image_sha256):
    integrity = payload.get("integrity") if isinstance(payload, dict) else None
    if not REMOTE_REQUIRE_RESPONSE_INTEGRITY:
        return None
    if not isinstance(integrity, dict):
        raise RuntimeError("Remote inference response is missing integrity evidence")
    key_id = str(integrity.get("keyId") or REMOTE_RESPONSE_HMAC_KEY_ID).strip()
    keyring = _response_hmac_keyring()
    response_key = keyring.get(key_id) if _valid_key_id(key_id) else None
    if response_key is None:
        raise RuntimeError("Remote inference response integrity key is unknown")
    signed = {key: value for key, value in integrity.items() if key != "hmacSha256"}
    supplied_hmac = str(integrity.get("hmacSha256") or "").strip().lower()
    try:
        issued_at = int(integrity.get("issuedAt"))
        body_sha256 = hashlib.sha256(_canonical_json(data)).hexdigest()
        expected_hmac = hmac.new(
            bytes.fromhex(response_key),
            _canonical_json(signed),
            hashlib.sha256,
        ).hexdigest()
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Remote inference response integrity is malformed") from exc
    now = int(time.time())
    if (
        integrity.get("schema") != _RESPONSE_INTEGRITY_SCHEMA
        or not hmac.compare_digest(str(integrity.get("requestNonce") or ""), nonce)
        or not hmac.compare_digest(str(integrity.get("imageSha256") or ""), image_sha256)
        or not hmac.compare_digest(str(integrity.get("bodySha256") or ""), body_sha256)
        or len(supplied_hmac) != 64
        or not hmac.compare_digest(supplied_hmac, expected_hmac)
        or issued_at > now + 30
        or now - issued_at > REMOTE_RESPONSE_MAX_AGE_SECONDS
    ):
        raise RuntimeError("Remote inference response integrity verification failed")
    return dict(integrity)


def _lazy_init():
    global session, input_name, output_name
    if session is not None:
        return

    onnx_path = os.path.join(current_dir, "model_deploy.onnx")
    providers = ["CPUExecutionProvider"]

    print("  [模型加载] 正在加载 ONNX 模型...")
    session = ort.InferenceSession(onnx_path, providers=providers)
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    print("  [模型加载] 完成。")


def _center_pad_and_crop(img, size=224):
    width, height = img.size
    canvas_width = max(width, size)
    canvas_height = max(height, size)
    if canvas_width != width or canvas_height != height:
        canvas = Image.new("RGB", (canvas_width, canvas_height), (0, 0, 0))
        canvas.paste(img, ((canvas_width - width) // 2, (canvas_height - height) // 2))
        img = canvas

    left = max((img.size[0] - size) // 2, 0)
    top = max((img.size[1] - size) // 2, 0)
    return img.crop((left, top, left + size, top + size))


def preprocess(img_path):
    img = Image.open(img_path).convert("RGB")
    img = _center_pad_and_crop(img, size=224)
    arr = np.asarray(img, dtype=np.float32) / 255.0
    mean = np.array((0.485, 0.456, 0.406), dtype=np.float32)
    std = np.array((0.229, 0.224, 0.225), dtype=np.float32)
    arr = (arr - mean) / std
    return arr.transpose(2, 0, 1)[None, :, :, :].astype(np.float32)


def _predict_remote(img_path):
    consume_remote_evidence()
    nonce = secrets.token_hex(16)
    image_sha256 = hashlib.sha256()
    with open(img_path, "rb") as image_source:
        while chunk := image_source.read(1024 * 1024):
            image_sha256.update(chunk)
    image_sha256 = image_sha256.hexdigest()
    headers = {"X-RealGuard-Request-Nonce": nonce}
    if REMOTE_INFERENCE_TOKEN:
        headers["X-RealGuard-Internal-Token"] = REMOTE_INFERENCE_TOKEN

    filename = os.path.basename(str(img_path)) or "image.png"
    response = None
    last_error = None
    retryable_error = False
    deadline = time.monotonic() + REMOTE_INFERENCE_TOTAL_TIMEOUT
    for attempt in range(1, REMOTE_INFERENCE_MAX_ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last_error = requests.Timeout("remote inference deadline exhausted")
            response = None
            break
        connect_timeout = min(5.0, max(0.1, remaining / 4.0))
        read_timeout = min(
            REMOTE_INFERENCE_TIMEOUT,
            max(0.1, remaining - connect_timeout),
        )
        try:
            with open(img_path, "rb") as image_file:
                response = requests.post(
                    REMOTE_INFERENCE_URL,
                    files={"image_file": (filename, image_file, "application/octet-stream")},
                    headers=headers,
                    timeout=(connect_timeout, read_timeout),
                )
            last_error = None
            retryable_error = False
        except requests.ConnectTimeout as exc:
            last_error = exc
            response = None
            retryable_error = True
        except requests.ConnectionError as exc:
            last_error = exc
            response = None
            retryable_error = False
        except requests.RequestException as exc:
            # Read timeouts have an unknown server-side outcome. Retrying can
            # duplicate an already-running GPU job, so only connect failures retry.
            last_error = exc
            response = None
            retryable_error = False

        should_retry = (
            attempt < REMOTE_INFERENCE_MAX_ATTEMPTS
            and (
                retryable_error
                or (response is not None and response.status_code in REMOTE_INFERENCE_RETRY_STATUSES)
            )
        )
        if not should_retry:
            break

        retry_after = ""
        if response is not None:
            retry_after = str(getattr(response, "headers", {}).get("Retry-After") or "").strip()
        try:
            delay = float(retry_after)
        except ValueError:
            delay = 0.25 * (2 ** (attempt - 1))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(remaining, REMOTE_INFERENCE_RETRY_MAX_DELAY, max(0.0, delay)))

    if response is None:
        raise RuntimeError(f"Remote inference request failed: {last_error}") from last_error

    payload = None
    try:
        payload = response.json()
    except (TypeError, ValueError):
        payload = None
    if response.status_code != 200:
        message = payload.get("msg") if isinstance(payload, dict) else None
        status_code = response.status_code
        retry_after = str(getattr(response, "headers", {}).get("Retry-After") or "").strip()
        raise RemoteInferenceError(
            f"Remote inference failed: {message or f'HTTP {status_code}'}",
            status_code=status_code,
            error_code="gpu_queue_full" if status_code == 429 else "remote_inference_failed",
            retry_after=retry_after,
        )
    if not isinstance(payload, dict):
        raise RuntimeError("Remote inference returned HTTP 200 without JSON")
    if payload.get("code") != 200:
        message = payload.get("msg") or f"HTTP {payload.get('code') or 503}"
        try:
            status_code = int(payload.get("code") or 503)
        except (TypeError, ValueError):
            status_code = 503
        raise RemoteInferenceError(
            f"Remote inference failed: {message}",
            status_code=status_code,
            error_code="gpu_queue_full" if status_code == 429 else "remote_inference_failed",
        )

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError("Remote inference returned an invalid data payload")
    verified_integrity = _verify_response_integrity(payload, data, nonce, image_sha256)
    runtime = data.get("runtime") or {}
    if REMOTE_REQUIRE_CUDA and runtime.get("activeProvider") != "CUDAExecutionProvider":
        raise RuntimeError(
            "Remote inference is not using CUDAExecutionProvider: "
            f"{runtime.get('activeProvider') or 'unknown'}"
        )

    remote_evidence = {}
    precheck = data.get("visibleWatermarkPrecheck")
    if isinstance(precheck, dict):
        remote_evidence["visibleWatermarkPrecheck"] = precheck
    model_decision = data.get("modelDecision")
    if isinstance(model_decision, dict):
        remote_evidence["modelDecision"] = model_decision
    remote_evidence["modelRun"] = {
        key: data.get(key)
        for key in (
            "model",
            "rawModelScore",
            "fakeProbability",
            "realProbability",
            "finalLabel",
            "originalSize",
            "processedSize",
            "downsample",
            "chunkCount",
            "parameters",
            "runtime",
        )
        if data.get(key) is not None
    }
    if isinstance(verified_integrity, dict):
        remote_evidence["modelRun"]["inputImageSha256"] = verified_integrity.get("imageSha256")
        remote_evidence["modelRun"]["responseIntegrity"] = verified_integrity
    if remote_evidence:
        _REMOTE_EVIDENCE.value = remote_evidence

    try:
        probability = float(data["fakeProbability"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Remote inference response has no valid fakeProbability") from exc
    if not np.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise RuntimeError(f"Remote inference returned an invalid probability: {probability}")
    return probability


def predict(img_path):
    if REMOTE_INFERENCE_URL:
        return _predict_remote(img_path)

    _lazy_init()
    input_tensor = preprocess(img_path)

    logits = session.run([output_name], {input_name: input_tensor})[0]

    def softmax(x):
        e_x = np.exp(x - np.max(x))
        return e_x / e_x.sum(axis=1, keepdims=True)

    probs = softmax(logits)
    prob_fake = float(probs[0, 1])
    return prob_fake


if __name__ == "__main__":
    img_path = "/media/disk2/gdq/Agent/test_photo/原像素.jpg"

    prob = predict(img_path)
    print(f"ONNX Prob: {prob}")
