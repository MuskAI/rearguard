import detector_backend
from imagedetection.Agent.tools.AIGC_Detection import inference_onnx


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


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
            "error": "",
        },
    )

    status = detector_backend._capability_status()

    assert status["capabilityReady"] is True
    assert status["artifactReady"] is False
    assert status["inferenceMode"] == "remote-cuda"
    assert status["warnings"] == []


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
        "headers": {"X-RealGuard-Internal-Token": "test-token"},
        "filename": "image.png",
    }
