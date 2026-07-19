from pathlib import Path
import sys
import threading


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yolo_adapter


def test_merge_corroborates_platform_hit_and_keeps_unmatched_watermark():
    registry_hits = [{
        "provider": "gemini",
        "confidence": 0.86,
        "bbox": {"x": 0.91, "y": 0.89, "w": 0.06, "h": 0.07},
    }]
    candidates = [
        {
            "provider": "yolo11x_watermark",
            "confidence": 0.92,
            "bbox": {"x": 0.90, "y": 0.88, "w": 0.08, "h": 0.10},
            "model": "corzent/yolo11x_watermark_detection",
            "modelRevision": "revision-1",
        },
        {
            "provider": "yolo11x_watermark",
            "confidence": 0.99,
            "bbox": {"x": 0.05, "y": 0.05, "w": 0.20, "h": 0.12},
        },
    ]

    hits = yolo_adapter._merge_visible_hits(registry_hits, candidates)

    assert len(hits) == 2
    assert hits[0]["provider"] == "gemini"
    assert hits[0]["yoloCorroborated"] is True
    assert hits[0]["yoloConfidence"] == 0.92
    assert hits[0]["localizationModelRevision"] == "revision-1"
    assert hits[1]["provider"] == "yolo11x_watermark"
    assert hits[1]["confidence"] == 0.99


def test_unmatched_watermark_candidate_is_returned_as_non_decisive():
    candidates = [{
        "provider": "yolo11x_watermark",
        "confidence": 0.99,
        "bbox": {"x": 0.05, "y": 0.05, "w": 0.20, "h": 0.12},
        "decisive": False,
    }]

    assert yolo_adapter._merge_visible_hits([], candidates) == candidates
    assert yolo_adapter._merge_visible_hits([], candidates)[0]["decisive"] is False


def test_registry_and_yolo_branches_start_in_parallel(monkeypatch):
    rendezvous = threading.Barrier(2)

    def registry(_path, provenance_path=None):
        rendezvous.wait(timeout=2)
        return []

    def yolo(_path):
        rendezvous.wait(timeout=2)
        return [], {"available": True, "detected": False, "count": 0}

    monkeypatch.setattr(yolo_adapter, "_registry_visible_hits", registry)
    monkeypatch.setattr(yolo_adapter, "_generic_yolo_hits", yolo)

    with yolo_adapter.base.app.test_request_context("/v1/precheck"):
        assert yolo_adapter._visible_hits_with_yolo(Path("unused.png")) == []
        status = yolo_adapter.g.generic_visible_watermark_status

    assert status["branchesParallel"] is True
    assert status["registryElapsedMs"] >= 0


def test_health_is_degraded_when_yolo_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        yolo_adapter,
        "_base_health",
        lambda: {
            "status": "ok",
            "registryReady": True,
            "tokenReady": True,
            "coordinateSpace": "display_normalized_v1",
        },
    )
    monkeypatch.setattr(
        yolo_adapter.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(yolo_adapter.requests.ConnectionError()),
    )

    payload = yolo_adapter.health_with_yolo()

    assert payload["status"] == "degraded"
    assert payload["genericVisibleWatermark"]["available"] is False


def test_yolo_runtime_validation_rejects_cpu_fallback(monkeypatch):
    monkeypatch.setattr(yolo_adapter, "YOLO_REQUIRE_CUDA", True)
    payload = {
        "status": "ok",
        "model": yolo_adapter.YOLO_EXPECTED_MODEL,
        "modelRevision": yolo_adapter.YOLO_EXPECTED_REVISION,
        "modelSha256": yolo_adapter.YOLO_EXPECTED_SHA256,
        "device": "cpu",
        "gpu": None,
        "cudaReady": False,
    }

    assert yolo_adapter._yolo_runtime_error(payload) == "cuda_not_ready"


def test_yolo_runtime_validation_accepts_pinned_cuda_runtime(monkeypatch):
    monkeypatch.setattr(yolo_adapter, "YOLO_REQUIRE_CUDA", True)
    payload = {
        "status": "ok",
        "model": yolo_adapter.YOLO_EXPECTED_MODEL,
        "modelRevision": yolo_adapter.YOLO_EXPECTED_REVISION,
        "modelSha256": yolo_adapter.YOLO_EXPECTED_SHA256,
        "device": "0",
        "gpu": "NVIDIA L20",
        "cudaReady": True,
    }

    assert yolo_adapter._yolo_runtime_error(payload) == ""


def test_yolo_detection_validation_rejects_empty_payload():
    assert yolo_adapter._yolo_detection_error({}) == "service_not_ok"


def test_yolo_detection_validation_rejects_invalid_box(monkeypatch):
    monkeypatch.setattr(yolo_adapter, "YOLO_REQUIRE_CUDA", True)
    payload = {
        "status": "ok",
        "model": yolo_adapter.YOLO_EXPECTED_MODEL,
        "modelRevision": yolo_adapter.YOLO_EXPECTED_REVISION,
        "modelSha256": yolo_adapter.YOLO_EXPECTED_SHA256,
        "device": "0",
        "gpu": "NVIDIA L20",
        "cudaReady": True,
        "image": {"width": 640, "height": 480},
        "detected": True,
        "count": 1,
        "detections": [{"bbox": {"x": 0.9, "y": 0.2, "w": 0.3, "h": 0.2}}],
    }

    assert yolo_adapter._yolo_detection_error(payload) == "detection_box_invalid"
