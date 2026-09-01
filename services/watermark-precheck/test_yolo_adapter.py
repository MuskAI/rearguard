from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yolo_adapter


def _runtime_payload(*, confidence: float | None = None, bbox: dict | None = None) -> dict:
    detections = [] if confidence is None else [{
        "confidence": confidence,
        "bbox": bbox or {"x": 0.75, "y": 0.82, "w": 0.18, "h": 0.10},
    }]
    return {
        "status": "ok",
        "model": yolo_adapter.YOLO_EXPECTED_MODEL,
        "modelRevision": yolo_adapter.YOLO_EXPECTED_REVISION,
        "modelSha256": yolo_adapter.YOLO_EXPECTED_SHA256,
        "device": "0",
        "gpu": "NVIDIA TITAN Xp",
        "cudaRequired": True,
        "cudaReady": True,
        "confidenceThreshold": 0.25,
        "elapsedMs": 9,
        "modelResident": True,
        "modelLoadCount": 1,
        "image": {"width": 640, "height": 480},
        "detected": bool(detections),
        "count": len(detections),
        "detections": detections,
    }


def _mock_post(monkeypatch, payload: dict) -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(yolo_adapter.requests, "post", lambda *_args, **_kwargs: Response())


def test_model_direct_hit_preserves_score_and_authority(monkeypatch, tmp_path):
    image_path = tmp_path / "probe.png"
    image_path.write_bytes(b"image")
    monkeypatch.setattr(yolo_adapter, "YOLO_TOKEN", "test-token")
    _mock_post(monkeypatch, _runtime_payload(confidence=0.9477))

    hits, status = yolo_adapter._generic_yolo_hits(image_path)
    result = yolo_adapter._direct_result(hits, status)

    assert status["mode"] == "model_direct"
    assert status["modelResident"] is True
    assert len(hits) == 1
    assert hits[0]["confidence"] == 0.9477
    assert hits[0]["decisive"] is True
    assert hits[0]["method"] == "explicit_watermark_model_direct"
    assert result["confidence"] == 0.9477
    assert result["aiWatermarkVerdict"]["verdict"] == "yes"
    assert result["hits"][0]["sourcePlatform"] is None
    assert "textAnalysis" not in result["hits"][0]
    assert "retrievalSimilarity" not in result["hits"][0]


def test_model_direct_low_score_is_visible_but_not_decisive(monkeypatch, tmp_path):
    image_path = tmp_path / "probe.png"
    image_path.write_bytes(b"image")
    monkeypatch.setattr(yolo_adapter, "YOLO_TOKEN", "test-token")
    _mock_post(monkeypatch, _runtime_payload(confidence=0.61))

    hits, status = yolo_adapter._generic_yolo_hits(image_path)
    result = yolo_adapter._direct_result(hits, status)

    assert hits[0]["decisive"] is False
    assert result["detected"] is True
    assert result["confidence"] == 0.61
    assert result["aiWatermarkVerdict"]["verdict"] == "inconclusive"
    assert result["aiWatermarkVerdict"]["isAiGeneratedWatermark"] is None
    assert result["aiWatermarkVerdict"]["strongHitCount"] == 0


def test_news_headline_false_positive_remains_visible_without_authority(monkeypatch, tmp_path):
    image_path = tmp_path / "news.jpg"
    image_path.write_bytes(b"image")
    monkeypatch.setattr(yolo_adapter, "YOLO_TOKEN", "test-token")
    _mock_post(monkeypatch, _runtime_payload(
        confidence=0.8361,
        bbox={"x": 0.2229, "y": 0.0755, "w": 0.2208, "h": 0.0415},
    ))

    hits, status = yolo_adapter._generic_yolo_hits(image_path)
    result = yolo_adapter._direct_result(hits, status)

    assert hits[0]["decisive"] is False
    assert hits[0]["decisionEligible"] is False
    assert hits[0]["label"] == "疑似标记区域（待核验）"
    assert status["directDecisionThreshold"] == 0.92
    assert result["aiWatermarkVerdict"]["verdict"] == "inconclusive"


def test_high_confidence_non_corner_text_cannot_authorize_verdict(monkeypatch, tmp_path):
    image_path = tmp_path / "headline.jpg"
    image_path.write_bytes(b"image")
    monkeypatch.setattr(yolo_adapter, "YOLO_TOKEN", "test-token")
    _mock_post(monkeypatch, _runtime_payload(
        confidence=0.98,
        bbox={"x": 0.28, "y": 0.04, "w": 0.44, "h": 0.08},
    ))

    hits, status = yolo_adapter._generic_yolo_hits(image_path)

    assert hits[0]["decisive"] is False
    assert status["strongCount"] == 0


def test_visible_path_calls_only_the_model(monkeypatch):
    candidates = [{
        "provider": yolo_adapter.YOLO_PROVIDER,
        "confidence": 0.9,
        "bbox": {"x": 0.7, "y": 0.8, "w": 0.2, "h": 0.1},
        "decisive": True,
        "method": yolo_adapter.DIRECT_METHOD,
    }]
    status = {"available": True, "mode": "model_direct", "elapsedMs": 7}
    monkeypatch.setattr(yolo_adapter, "_generic_yolo_hits", lambda _path: (candidates, status))

    with yolo_adapter.base.app.test_request_context("/v1/precheck"):
        assert yolo_adapter._visible_hits_with_yolo(Path("unused.png")) == candidates
        assert yolo_adapter.g.explicit_watermark_result["resultSource"] == "model"

    assert not hasattr(yolo_adapter, "_registry_visible_hits")
    assert not hasattr(yolo_adapter, "_explicit_ensemble")


def test_health_is_degraded_when_model_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        yolo_adapter,
        "_base_health",
        lambda: {"status": "ok", "tokenReady": True, "coordinateSpace": "display_normalized_v1"},
    )
    monkeypatch.setattr(
        yolo_adapter.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(yolo_adapter.requests.ConnectionError()),
    )

    payload = yolo_adapter.health_with_yolo()

    assert payload["status"] == "degraded"
    assert payload["genericVisibleWatermark"]["available"] is False
    assert payload["explicitWatermarkEnsemble"]["disabled"] is True


def test_health_reports_direct_model_without_ensemble(monkeypatch):
    monkeypatch.setattr(yolo_adapter, "_base_health", lambda: {"status": "ok", "tokenReady": True})
    payload = _runtime_payload()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(yolo_adapter.requests, "get", lambda *_args, **_kwargs: Response())

    health = yolo_adapter.health_with_yolo()

    assert health["status"] == "ok"
    assert health["mode"] == "model_direct"
    assert health["registryReady"] is False
    assert health["genericVisibleWatermark"]["modelResident"] is True
    assert health["explicitWatermarkEnsemble"] == {
        "available": False,
        "disabled": True,
        "mode": "disabled",
        "reason": "model_direct_enabled",
    }


def test_yolo_runtime_validation_rejects_cpu_fallback(monkeypatch):
    monkeypatch.setattr(yolo_adapter, "YOLO_REQUIRE_CUDA", True)
    payload = _runtime_payload()
    payload.update({"device": "cpu", "gpu": None, "cudaReady": False})

    assert yolo_adapter._yolo_runtime_error(payload) == "cuda_not_ready"


def test_yolo_runtime_validation_accepts_pinned_cuda_runtime(monkeypatch):
    monkeypatch.setattr(yolo_adapter, "YOLO_REQUIRE_CUDA", True)
    assert yolo_adapter._yolo_runtime_error(_runtime_payload()) == ""


def test_yolo_detection_validation_rejects_invalid_box(monkeypatch):
    monkeypatch.setattr(yolo_adapter, "YOLO_REQUIRE_CUDA", True)
    payload = _runtime_payload(confidence=0.9)
    payload["detections"][0]["bbox"] = {"x": 0.9, "y": 0.2, "w": 0.3, "h": 0.2}

    assert yolo_adapter._yolo_detection_error(payload) == "detection_box_invalid"


def test_pipeline_trace_contains_only_direct_model_stages():
    response = {
        "elapsedMs": 20,
        "pipelineTimings": {"decodeMs": 2, "normalizeMs": 3},
        "encodedSize": {"width": 100, "height": 80},
        "displaySize": {"width": 100, "height": 80},
        "explicitWatermark": {
            "available": True,
            "detected": False,
            "confidence": 0.0,
            "aiWatermarkVerdict": {"verdict": "no", "reason": "模型未检出水印"},
        },
    }
    with yolo_adapter.base.app.test_request_context("/v1/precheck"):
        yolo_adapter.g.watermark_pipeline_state = {
            "candidates": [],
            "status": {"available": True, "mode": "model_direct", "roundTripMs": 11},
        }
        trace = yolo_adapter._build_pipeline_trace(response)

    assert trace["schemaVersion"] == "watermark_pipeline_trace_v1"
    assert [stage["id"] for stage in trace["stages"]] == ["decode", "yolo", "verdict"]
    assert trace["stages"][1]["status"] == "clean"
    assert trace["stages"][-1]["status"] == "clean"
