import base64
import hashlib
import hmac
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import sys
import types

from flask import Flask
import pytest


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def _load_remote_inference(monkeypatch):
    inference = types.ModuleType("tools.AIGC_Detection.inference_onnx")
    inference.ImagePixelsTooLargeError = type("ImagePixelsTooLargeError", (RuntimeError,), {})
    inference.InferenceBusyError = type("InferenceBusyError", (RuntimeError,), {})
    inference.UnsupportedAnimatedImageError = type("UnsupportedAnimatedImageError", (RuntimeError,), {})
    inference.get_model_status = lambda: {
        "initialized": True,
        "activeProvider": "CUDAExecutionProvider",
    }
    inference.acquire_inference_slot = lambda: 0.0
    inference.release_inference_slot = lambda: None
    inference.analyze_image = lambda *_args, **_kwargs: {
        "model": "test-model",
        "fakeProbability": 0.25,
        "realProbability": 0.75,
        "rawModelScore": 0.98,
        "finalLabel": "真实图像",
        "modelDecision": {
            "ready": False,
            "mode": "review_only",
            "rawModelScore": 0.98,
            "publishedProbability": 0.5,
            "finalLabel": "需人工复核",
        },
        "originalSize": [1, 1],
        "processedSize": [1, 1],
        "downsample": {},
        "chunkCount": 1,
        "parameters": {},
        "processing": {},
        "runtime": {"activeProvider": "CUDAExecutionProvider"},
    }
    tools_package = types.ModuleType("tools")
    tools_package.__path__ = []
    aigc_package = types.ModuleType("tools.AIGC_Detection")
    aigc_package.__path__ = []
    monkeypatch.setitem(sys.modules, "tools", tools_package)
    monkeypatch.setitem(sys.modules, "tools.AIGC_Detection", aigc_package)
    monkeypatch.setitem(sys.modules, "tools.AIGC_Detection.inference_onnx", inference)

    path = Path(__file__).with_name("remote_inference.py")
    spec = importlib.util.spec_from_file_location("realguard_remote_inference_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_predict_preserves_failed_visible_watermark_precheck(monkeypatch):
    module = _load_remote_inference(monkeypatch)
    monkeypatch.setenv("REALGUARD_MODEL_INTERNAL_TOKEN", "internal-test-token")
    monkeypatch.setenv("REALGUARD_MODEL_RESPONSE_HMAC_KEY", "ab" * 32)

    class FailedFuture:
        cancelled = False

        def result(self, timeout=None):
            raise TimeoutError("watermark service timed out")

        def cancel(self):
            self.cancelled = True
            return True

        def done(self):
            return False

    future = FailedFuture()

    class Executor:
        @staticmethod
        def submit(*_args, **_kwargs):
            return future

    monkeypatch.setattr(module, "_PRECHECK_EXECUTOR", Executor())
    app = Flask(__name__)
    app.register_blueprint(module.model_inference_blueprint)

    response = app.test_client().post(
        "/internal/model/predict",
        headers={
            "X-RealGuard-Internal-Token": "internal-test-token",
            "X-RealGuard-Request-Nonce": "a" * 32,
        },
        data={"image_file": (BytesIO(PNG_BYTES), "sample.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    data = payload["data"]
    assert data["runtime"]["visiblePrecheckStatus"] == "failed"
    assert data["visibleWatermarkPrecheck"]["status"] == "failed"
    assert data["visibleWatermarkPrecheck"]["errorCode"] == "visible_watermark_unavailable"
    assert data["rawModelScore"] == 0.98
    assert data["modelDecision"]["mode"] == "review_only"
    assert future.cancelled is True
    integrity = payload["integrity"]
    signed = {key: value for key, value in integrity.items() if key != "hmacSha256"}
    canonical = lambda value: json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode()
    assert integrity["requestNonce"] == "a" * 32
    assert integrity["keyId"] == "v1"
    assert integrity["imageSha256"] == hashlib.sha256(PNG_BYTES).hexdigest()
    assert integrity["bodySha256"] == hashlib.sha256(canonical(data)).hexdigest()
    assert hmac.compare_digest(
        integrity["hmacSha256"],
        hmac.new(bytes.fromhex("ab" * 32), canonical(signed), hashlib.sha256).hexdigest(),
    )


def test_previous_internal_token_is_accepted_during_rotation(monkeypatch):
    module = _load_remote_inference(monkeypatch)
    monkeypatch.setenv("REALGUARD_MODEL_INTERNAL_TOKEN", "new-internal-token-0123456789abcdef")
    monkeypatch.setenv("REALGUARD_MODEL_INTERNAL_TOKEN_PREVIOUS", "old-internal-token-0123456789abcdef")
    monkeypatch.setenv("REALGUARD_MODEL_RESPONSE_HMAC_KEY", "ab" * 32)
    monkeypatch.setattr(module, "_visible_precheck_ready", lambda: True)
    app = Flask(__name__)
    app.register_blueprint(module.model_inference_blueprint)

    response = app.test_client().get(
        "/internal/model/health",
        headers={"X-RealGuard-Internal-Token": "old-internal-token-0123456789abcdef"},
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["responseIntegrityKeyId"] == "v1"


def test_busy_gpu_does_not_start_visible_watermark_work(monkeypatch):
    module = _load_remote_inference(monkeypatch)
    monkeypatch.setenv("REALGUARD_MODEL_INTERNAL_TOKEN", "internal-test-token")
    submissions = []

    class Executor:
        @staticmethod
        def submit(*args, **kwargs):
            submissions.append((args, kwargs))
            raise AssertionError("watermark work must not start before GPU admission")

    def reject_admission():
        raise module.InferenceBusyError("GPU inference queue is full")

    monkeypatch.setattr(module, "_PRECHECK_EXECUTOR", Executor())
    monkeypatch.setattr(module, "acquire_inference_slot", reject_admission)
    app = Flask(__name__)
    app.register_blueprint(module.model_inference_blueprint)

    response = app.test_client().post(
        "/internal/model/predict",
        headers={"X-RealGuard-Internal-Token": "internal-test-token"},
        data={"image_file": (BytesIO(PNG_BYTES), "sample.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "5"
    assert submissions == []


def test_model_health_requires_visible_watermark_chain(monkeypatch):
    module = _load_remote_inference(monkeypatch)
    monkeypatch.setenv("REALGUARD_MODEL_INTERNAL_TOKEN", "internal-test-token")
    monkeypatch.setenv("REALGUARD_MODEL_RESPONSE_HMAC_KEY", "ab" * 32)
    monkeypatch.setattr(module, "_visible_precheck_ready", lambda: False)
    app = Flask(__name__)
    app.register_blueprint(module.model_inference_blueprint)

    response = app.test_client().get(
        "/internal/model/health",
        headers={"X-RealGuard-Internal-Token": "internal-test-token"},
    )

    assert response.status_code == 503
    assert response.get_json()["data"]["visiblePrecheckReady"] is False


def test_partial_visible_scan_accepts_valid_registry_positive(monkeypatch):
    module = _load_remote_inference(monkeypatch)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ok",
                "coordinateSpace": "display_normalized_v1",
                "displaySize": {"width": 640, "height": 480},
                "genericVisibleWatermark": {"available": False},
                "explicitWatermark": {
                    "detected": True,
                    "sourcePlatform": "doubao",
                },
                "pipelineTrace": {
                    "schemaVersion": "watermark_pipeline_trace_v1",
                    "totalElapsedMs": 321,
                    "stages": [
                        {
                            "id": "decode",
                            "label": "图像解码",
                            "status": "success",
                            "elapsedMs": 4,
                            "summary": "640 x 480",
                            "details": {},
                        }
                    ],
                },
                "visibleHits": [{
                    "provider": "gemini",
                    "confidence": 0.95,
                    "bbox": {"x": 0.8, "y": 0.8, "w": 0.1, "h": 0.1},
                }],
            }

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(module, "VISIBLE_PRECHECK_TOKEN", "token")
    monkeypatch.setattr(module.requests, "Session", Session)

    payload = module._run_visible_precheck(b"image", "sample.jpg", "image/jpeg")

    assert payload["positiveEvidenceAvailable"] is True
    assert payload["completeVisibleScan"] is False
    assert payload["explicitWatermark"]["sourcePlatform"] == "doubao"
    assert payload["pipelineTrace"]["schemaVersion"] == "watermark_pipeline_trace_v1"
    assert payload["pipelineTrace"]["stages"][0]["id"] == "decode"


def test_partial_visible_scan_rejects_invalid_registry_box(monkeypatch):
    module = _load_remote_inference(monkeypatch)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ok",
                "coordinateSpace": "display_normalized_v1",
                "displaySize": {"width": 640, "height": 480},
                "genericVisibleWatermark": {"available": False},
                "visibleHits": [{
                    "provider": "gemini",
                    "confidence": 0.95,
                    "bbox": {"x": 0.9, "y": 0.8, "w": 0.2, "h": 0.1},
                }],
            }

    class Session:
        trust_env = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(module, "VISIBLE_PRECHECK_TOKEN", "token")
    monkeypatch.setattr(module.requests, "Session", Session)

    with pytest.raises(ValueError):
        module._run_visible_precheck(b"image", "sample.jpg", "image/jpeg")
