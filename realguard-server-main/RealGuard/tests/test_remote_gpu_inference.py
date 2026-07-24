import base64
import hashlib
import hmac
from io import BytesIO
import json
import time

import detector_backend
import pytest
from imagedetection.Agent.tools.AIGC_Detection import inference_onnx


VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


class _Response:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _legacy_integrity_compatibility(monkeypatch):
    monkeypatch.setattr(inference_onnx, "REMOTE_REQUIRE_RESPONSE_INTEGRITY", False)


def _signed_remote_payload(data, response_key, nonce, image_bytes, key_id=None):
    canonical = lambda value: json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode()
    signed = {
        "schema": "cn.huijian.remote-inference-response-v1",
        "requestNonce": nonce,
        "imageSha256": hashlib.sha256(image_bytes).hexdigest(),
        "issuedAt": int(time.time()),
        "bodySha256": hashlib.sha256(canonical(data)).hexdigest(),
    }
    if key_id:
        signed["keyId"] = key_id
    return {
        "code": 200,
        "data": data,
        "integrity": {
            **signed,
            "hmacSha256": hmac.new(
                bytes.fromhex(response_key), canonical(signed), hashlib.sha256
            ).hexdigest(),
        },
    }


def test_remote_model_health_accepts_cuda_and_ignores_missing_local_artifact(monkeypatch):
    monkeypatch.setattr(detector_backend, "REMOTE_INFERENCE_URL", "http://127.0.0.1/internal/model/predict")
    monkeypatch.setattr(
        detector_backend,
        "_artifact_status",
        lambda: {
            "ready": False,
            "warnings": ["missing local artifact"],
            "artifact": {"exists": False},
            "externalData": {"exists": False},
        },
    )
    monkeypatch.setattr(
        detector_backend,
        "_dependency_status",
        lambda: {"ready": True, "missing": []},
    )
    monkeypatch.setattr(
        detector_backend,
        "_remote_inference_status",
        lambda: {
            "configured": True,
            "ready": True,
            "activeProvider": "CUDAExecutionProvider",
            "cudaDeviceId": 0,
            "latencyMs": 2.0,
            "verdictReady": False,
            "decisionMode": "review_only",
            "decisionGateReasons": ["calibration_manifest_missing"],
            "error": "",
        },
    )

    status = detector_backend._capability_status()

    assert status["capabilityReady"] is True
    assert status["artifactReady"] is False
    assert status["inferenceMode"] == "remote-cuda"
    assert status["verdictReady"] is False
    assert status["decisionMode"] == "review_only"
    assert status["warnings"] == [
        "remote model runtime is ready but automatic verdict calibration is not authorized"
    ]


def test_remote_predict_requires_cuda_provider(monkeypatch, tmp_path):
    image_path = tmp_path / "image.jpg"
    image_path.write_bytes(b"image")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_TOKEN", "test-token")
    monkeypatch.setattr(inference_onnx, "REMOTE_REQUIRE_CUDA", True)
    monkeypatch.setattr(
        inference_onnx.requests,
        "post",
        lambda *args, **kwargs: _Response({
            "code": 200,
            "data": {
                "fakeProbability": 0.75,
                "runtime": {"activeProvider": "CPUExecutionProvider"},
            },
        }),
    )

    try:
        inference_onnx.predict(image_path)
    except RuntimeError as exc:
        assert "not using CUDAExecutionProvider" in str(exc)
    else:
        raise AssertionError("CPU fallback must not be accepted")


def test_remote_predict_returns_probability(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")
    captured = {}

    def fake_post(url, files, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["filename"] = files["image_file"][0]
        return _Response({
            "code": 200,
            "data": {
                "fakeProbability": 0.625,
                "runtime": {"activeProvider": "CUDAExecutionProvider"},
            },
        })

    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_TOKEN", "test-token")
    monkeypatch.setattr(inference_onnx, "REMOTE_REQUIRE_CUDA", True)
    monkeypatch.setattr(inference_onnx.requests, "post", fake_post)

    assert inference_onnx.predict(image_path) == 0.625
    assert captured == {
        "url": "http://model/predict",
        "headers": {
            "X-RealGuard-Internal-Token": "test-token",
            "X-RealGuard-Request-Nonce": captured["headers"]["X-RealGuard-Request-Nonce"],
        },
        "filename": "image.png",
    }


def test_remote_predict_verifies_response_integrity(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(VALID_PNG_BYTES)
    token = "test-integrity-token"
    response_key = "ab" * 32
    data = {
        "fakeProbability": 0.625,
        "runtime": {"activeProvider": "CUDAExecutionProvider"},
    }

    def fake_post(_url, files, headers, timeout):
        assert timeout
        assert files["image_file"][0] == "image.png"
        return _Response(_signed_remote_payload(
            data,
            response_key,
            headers["X-RealGuard-Request-Nonce"],
            VALID_PNG_BYTES,
        ))

    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_TOKEN", token)
    monkeypatch.setattr(inference_onnx, "REMOTE_RESPONSE_HMAC_KEY", response_key)
    monkeypatch.setattr(inference_onnx, "REMOTE_REQUIRE_RESPONSE_INTEGRITY", True)
    monkeypatch.setattr(inference_onnx.requests, "post", fake_post)

    assert inference_onnx.predict(image_path) == 0.625


def test_remote_predict_verifies_historical_response_key(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(VALID_PNG_BYTES)
    old_key = "ab" * 32
    data = {
        "fakeProbability": 0.625,
        "runtime": {"activeProvider": "CUDAExecutionProvider"},
    }

    def fake_post(_url, files, headers, timeout):
        return _Response(_signed_remote_payload(
            data,
            old_key,
            headers["X-RealGuard-Request-Nonce"],
            VALID_PNG_BYTES,
            key_id="v1",
        ))

    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_TOKEN", "test-token")
    monkeypatch.setattr(inference_onnx, "REMOTE_RESPONSE_HMAC_KEY", "cd" * 32)
    monkeypatch.setattr(inference_onnx, "REMOTE_RESPONSE_HMAC_KEY_ID", "v2")
    monkeypatch.setattr(
        inference_onnx,
        "REMOTE_RESPONSE_HMAC_KEYS_JSON",
        json.dumps({"v1": old_key}),
    )
    monkeypatch.setattr(inference_onnx, "REMOTE_REQUIRE_RESPONSE_INTEGRITY", True)
    monkeypatch.setattr(inference_onnx.requests, "post", fake_post)

    assert inference_onnx.predict(image_path) == 0.625


def test_remote_predict_rejects_unknown_response_key_id(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(VALID_PNG_BYTES)
    response_key = "ab" * 32
    data = {
        "fakeProbability": 0.625,
        "runtime": {"activeProvider": "CUDAExecutionProvider"},
    }

    def fake_post(_url, files, headers, timeout):
        return _Response(_signed_remote_payload(
            data,
            response_key,
            headers["X-RealGuard-Request-Nonce"],
            VALID_PNG_BYTES,
            key_id="retired",
        ))

    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_TOKEN", "test-token")
    monkeypatch.setattr(inference_onnx, "REMOTE_RESPONSE_HMAC_KEY", response_key)
    monkeypatch.setattr(inference_onnx, "REMOTE_RESPONSE_HMAC_KEY_ID", "v1")
    monkeypatch.setattr(inference_onnx, "REMOTE_RESPONSE_HMAC_KEYS_JSON", "{}")
    monkeypatch.setattr(inference_onnx, "REMOTE_REQUIRE_RESPONSE_INTEGRITY", True)
    monkeypatch.setattr(inference_onnx.requests, "post", fake_post)

    with pytest.raises(RuntimeError, match="integrity key is unknown"):
        inference_onnx.predict(image_path)


def test_remote_predict_rejects_tampered_signed_body(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(VALID_PNG_BYTES)
    token = "test-integrity-token"
    response_key = "ab" * 32

    def fake_post(_url, files, headers, timeout):
        data = {
            "fakeProbability": 0.125,
            "runtime": {"activeProvider": "CUDAExecutionProvider"},
        }
        payload = _signed_remote_payload(
            data,
            response_key,
            headers["X-RealGuard-Request-Nonce"],
            VALID_PNG_BYTES,
        )
        payload["data"]["fakeProbability"] = 0.999
        return _Response(payload)

    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_TOKEN", token)
    monkeypatch.setattr(inference_onnx, "REMOTE_RESPONSE_HMAC_KEY", response_key)
    monkeypatch.setattr(inference_onnx, "REMOTE_REQUIRE_RESPONSE_INTEGRITY", True)
    monkeypatch.setattr(inference_onnx.requests, "post", fake_post)

    with pytest.raises(RuntimeError, match="integrity verification failed"):
        inference_onnx.predict(image_path)


def test_remote_predict_retries_transient_overload(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")
    responses = [
        _Response({"code": 429, "msg": "GPU busy"}, 429, {"Retry-After": "0"}),
        _Response({
            "code": 200,
            "data": {
                "fakeProbability": 0.625,
                "runtime": {"activeProvider": "CUDAExecutionProvider"},
            },
        }),
    ]
    sleeps = []
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(inference_onnx.requests, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(inference_onnx.time, "sleep", sleeps.append)

    assert inference_onnx.predict(image_path) == 0.625
    assert responses == []
    assert sleeps == [0.0]


def test_remote_predict_preserves_exhausted_gpu_overload(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(
        inference_onnx.requests,
        "post",
        lambda *args, **kwargs: _Response(
            {"code": 429, "msg": "GPU busy"}, 429, {"Retry-After": "7"}
        ),
    )

    with pytest.raises(inference_onnx.RemoteInferenceError) as captured:
        inference_onnx.predict(image_path)

    assert captured.value.status_code == 429
    assert captured.value.error_code == "gpu_queue_full"
    assert captured.value.retry_after == "7"


def test_remote_predict_preserves_non_json_gpu_overload(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")
    response = _Response(None, 429, {"Retry-After": "9"})
    response.json = lambda: (_ for _ in ()).throw(ValueError("empty response"))
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(inference_onnx.requests, "post", lambda *args, **kwargs: response)

    with pytest.raises(inference_onnx.RemoteInferenceError) as captured:
        inference_onnx.predict(image_path)

    assert captured.value.status_code == 429
    assert captured.value.error_code == "gpu_queue_full"
    assert captured.value.retry_after == "9"


def test_remote_predict_limits_each_attempt_to_remaining_deadline(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")
    clock = {"now": 0.0}
    timeouts = []
    responses = [
        _Response({"code": 429, "msg": "busy"}, 429, {"Retry-After": "0"}),
        _Response({
            "code": 200,
            "data": {
                "fakeProbability": 0.25,
                "runtime": {"activeProvider": "CUDAExecutionProvider"},
            },
        }),
    ]

    def fake_post(*_args, **kwargs):
        timeouts.append(kwargs["timeout"])
        clock["now"] += 40.0
        return responses.pop(0)

    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_TOTAL_TIMEOUT", 50.0)
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(inference_onnx.requests, "post", fake_post)
    monkeypatch.setattr(inference_onnx.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(inference_onnx.time, "sleep", lambda delay: clock.__setitem__("now", clock["now"] + delay))

    assert inference_onnx.predict(image_path) == 0.25
    assert len(timeouts) == 2
    assert sum(timeouts[0]) <= 50.0
    assert sum(timeouts[1]) <= 10.0


def test_remote_predict_does_not_retry_read_timeout(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")
    calls = []
    sleeps = []

    def fail_after_dispatch(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise inference_onnx.requests.ReadTimeout("server outcome unknown")

    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(inference_onnx.requests, "post", fail_after_dispatch)
    monkeypatch.setattr(inference_onnx.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="server outcome unknown"):
        inference_onnx.predict(image_path)

    assert len(calls) == 1
    assert sleeps == []


def test_remote_predict_does_not_retry_connection_reset_with_unknown_outcome(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")
    calls = []

    def reset_after_upload(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise inference_onnx.requests.ConnectionError("peer closed after upload")

    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(inference_onnx.requests, "post", reset_after_upload)

    with pytest.raises(RuntimeError, match="peer closed after upload"):
        inference_onnx.predict(image_path)

    assert len(calls) == 1


def test_remote_predict_preserves_shared_precheck_evidence(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")
    precheck = {"status": "ok", "elapsedMs": 21, "visibleHits": []}
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_TOKEN", "test-token")
    monkeypatch.setattr(inference_onnx, "REMOTE_REQUIRE_CUDA", True)
    monkeypatch.setattr(
        inference_onnx.requests,
        "post",
        lambda *args, **kwargs: _Response({
            "code": 200,
            "data": {
                "fakeProbability": 0.625,
                "runtime": {"activeProvider": "CUDAExecutionProvider"},
                "visibleWatermarkPrecheck": precheck,
            },
        }),
    )

    assert inference_onnx.predict(image_path) == 0.625
    evidence = inference_onnx.consume_remote_evidence()
    assert evidence["visibleWatermarkPrecheck"] == precheck
    assert evidence["modelRun"]["fakeProbability"] == pytest.approx(0.625)
    assert evidence["modelRun"]["runtime"]["activeProvider"] == "CUDAExecutionProvider"
    assert inference_onnx.consume_remote_evidence() == {}


def test_remote_predict_preserves_failed_precheck_evidence(monkeypatch, tmp_path):
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")
    precheck = {
        "status": "failed",
        "errorCode": "visible_watermark_unavailable",
        "message": "可见水印检测暂不可用，本次证据不完整。",
        "elapsedMs": 12000,
    }
    monkeypatch.setattr(inference_onnx, "REMOTE_INFERENCE_URL", "http://model/predict")
    monkeypatch.setattr(inference_onnx, "REMOTE_REQUIRE_CUDA", True)
    monkeypatch.setattr(
        inference_onnx.requests,
        "post",
        lambda *args, **kwargs: _Response({
            "code": 200,
            "data": {
                "fakeProbability": 0.125,
                "runtime": {"activeProvider": "CUDAExecutionProvider"},
                "visibleWatermarkPrecheck": precheck,
            },
        }),
    )

    assert inference_onnx.predict(image_path) == 0.125
    evidence = inference_onnx.consume_remote_evidence()
    assert evidence["visibleWatermarkPrecheck"] == precheck
    assert evidence["modelRun"]["fakeProbability"] == pytest.approx(0.125)


def test_detector_backend_forwards_remote_evidence(monkeypatch):
    monkeypatch.setattr(detector_backend, "_ensure_capability_ready", lambda: None)
    monkeypatch.setattr(
        detector_backend,
        "_run_v1_detect",
        lambda image_path: {
            "final_label": "真实图像",
            "probability": 0.16,
            "detector_probability": 0.18,
            "confidence": "高",
            "explanation": "V1 evidence summary",
            "visual_issues": [],
            "all_metadata": {},
        },
    )
    monkeypatch.setattr(
        detector_backend,
        "_consume_remote_inference_evidence",
        lambda: {"visibleWatermarkPrecheck": {"status": "ok", "visibleHits": []}},
    )
    monkeypatch.setattr(
        detector_backend,
        "_save_upload",
        lambda image_bytes, folder, filename: ("stored-demo.png", "/tmp/stored-demo.png"),
    )
    monkeypatch.setattr(detector_backend, "get_image_info", lambda path: ("PNG", "320x240"))
    monkeypatch.setattr(detector_backend, "get_file_size_str", lambda path: "1KB")
    monkeypatch.setattr(detector_backend, "excute_detection_sql_lastid", lambda sql, params=None: 91)
    monkeypatch.setattr(detector_backend, "DETECTOR_INTERNAL_TOKEN", "detector-test-token")
    app = detector_backend.create_app()
    app.config.update(TESTING=True)

    response = app.test_client().post(
        "/image",
        headers={"X-RealGuard-Detector-Token": "detector-test-token"},
        data={
            "image_file": (BytesIO(VALID_PNG_BYTES), "demo.png"),
            "openid": "openid-1",
            "phone": "13800000000",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    remote = response.get_json()["data"]["remote_evidence"]
    assert remote["visibleWatermarkPrecheck"] == {"status": "ok", "visibleHits": []}
    assert remote["modelDecision"]["ready"] is False
    assert remote["modelDecision"]["mode"] == "review_only"


def test_detector_backend_can_defer_visual_llm(monkeypatch):
    calls = []
    monkeypatch.setattr(detector_backend, "_ensure_capability_ready", lambda: None)

    def fake_detect(image_path, *, use_llm=True):
        calls.append({"image_path": image_path, "use_llm": use_llm})
        return {
            "final_label": "真实图像",
            "probability": 0.16,
            "detector_probability": 0.18,
            "confidence": "高",
            "explanation": "GPU 主模型已完成。",
            "visual_issues": [],
            "all_metadata": {},
        }

    monkeypatch.setattr(detector_backend, "_run_v1_detect", fake_detect)
    monkeypatch.setattr(
        detector_backend,
        "_save_upload",
        lambda image_bytes, folder, filename: ("stored-demo.png", "/tmp/stored-demo.png"),
    )
    monkeypatch.setattr(detector_backend, "_consume_remote_inference_evidence", lambda: {})
    monkeypatch.setattr(detector_backend, "get_image_info", lambda path: ("PNG", "320x240"))
    monkeypatch.setattr(detector_backend, "get_file_size_str", lambda path: "1KB")
    monkeypatch.setattr(detector_backend, "excute_detection_sql_lastid", lambda sql, params=None: 91)
    monkeypatch.setattr(detector_backend, "DETECTOR_INTERNAL_TOKEN", "detector-test-token")
    app = detector_backend.create_app()
    app.config.update(TESTING=True)

    response = app.test_client().post(
        "/image",
        headers={"X-RealGuard-Detector-Token": "detector-test-token"},
        data={
            "image_file": (BytesIO(VALID_PNG_BYTES), "demo.png"),
            "defer_visual_llm": "1",
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert calls == [{"image_path": "/tmp/stored-demo.png", "use_llm": False}]


def test_detector_backend_preserves_gpu_overload_status(monkeypatch):
    error = RuntimeError("GPU inference queue is full")
    error.status_code = 429
    error.error_code = "gpu_queue_full"
    error.retry_after = "9"
    monkeypatch.setattr(detector_backend, "_ensure_capability_ready", lambda: None)
    monkeypatch.setattr(detector_backend, "_run_v1_detect", lambda _path: (_ for _ in ()).throw(error))
    monkeypatch.setattr(
        detector_backend,
        "_save_upload",
        lambda image_bytes, folder, filename: ("stored-demo.png", "/tmp/stored-demo.png"),
    )
    monkeypatch.setattr(detector_backend, "DETECTOR_INTERNAL_TOKEN", "detector-test-token")
    app = detector_backend.create_app()
    app.config.update(TESTING=True)

    response = app.test_client().post(
        "/image",
        headers={"X-RealGuard-Detector-Token": "detector-test-token"},
        data={"image_file": (BytesIO(VALID_PNG_BYTES), "demo.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "9"
    assert response.get_json()["errorCode"] == "gpu_queue_full"


def test_detector_readiness_requires_internal_token(monkeypatch):
    monkeypatch.setattr(
        detector_backend,
        "_capability_status",
        lambda: {"capabilityReady": True, "activeProvider": "CUDAExecutionProvider"},
    )
    monkeypatch.setattr(detector_backend, "DETECTOR_INTERNAL_TOKEN", "")
    app = detector_backend.create_app()
    app.config.update(TESTING=True)

    response = app.test_client().get("/ready")

    assert response.status_code == 503
    assert response.get_json()["tokenReady"] is False


def test_detector_internal_readiness_verifies_callers_token(monkeypatch):
    monkeypatch.setattr(
        detector_backend,
        "_capability_status",
        lambda: {"capabilityReady": True, "activeProvider": "CUDAExecutionProvider"},
    )
    monkeypatch.setattr(detector_backend, "DETECTOR_INTERNAL_TOKEN", "x" * 64)
    app = detector_backend.create_app()
    app.config.update(TESTING=True)

    rejected = app.test_client().get("/internal/ready")
    accepted = app.test_client().get(
        "/internal/ready",
        headers={"X-RealGuard-Detector-Token": "x" * 64},
    )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted.get_json()["tokenReady"] is True


def test_detector_deployment_probe_does_not_persist_history(monkeypatch):
    monkeypatch.setattr(detector_backend, "_ensure_capability_ready", lambda: None)
    monkeypatch.setattr(
        detector_backend,
        "_run_v1_detect",
        lambda _path: {
            "final_label": "真实图像",
            "detector_probability": 0.1,
            "probability": 0.1,
        },
    )
    monkeypatch.setattr(detector_backend, "_consume_remote_inference_evidence", lambda: {})
    monkeypatch.setattr(
        detector_backend,
        "_save_upload",
        lambda image_bytes, folder, filename: ("stored-demo.png", "/tmp/stored-demo.png"),
    )
    monkeypatch.setattr(
        detector_backend,
        "_persist_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("probe must not persist")),
    )
    monkeypatch.setattr(detector_backend, "DETECTOR_INTERNAL_TOKEN", "x" * 64)
    app = detector_backend.create_app()
    app.config.update(TESTING=True)

    response = app.test_client().post(
        "/image",
        headers={"X-RealGuard-Detector-Token": "x" * 64},
        data={
            "internal_probe": "1",
            "openid": "deployment-probe",
            "image_file": (BytesIO(VALID_PNG_BYTES), "demo.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["probe"] is True


def test_detector_backend_rejects_missing_internal_token(monkeypatch):
    monkeypatch.setattr(detector_backend, "DETECTOR_INTERNAL_TOKEN", "detector-test-token")
    app = detector_backend.create_app()
    app.config.update(TESTING=True)

    response = app.test_client().post(
        "/image",
        data={"image_file": (BytesIO(VALID_PNG_BYTES), "demo.png")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 401
