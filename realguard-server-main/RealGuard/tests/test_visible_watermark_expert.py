from imagedetection.views import swarm_visible_watermark_expert as expert


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise expert.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_visible_watermark_expert_localizes_without_ai_score(monkeypatch):
    monkeypatch.setenv("WATERMARK_PRECHECK_TOKEN", "test-token")

    def fake_post(url, headers, files, timeout):
        assert url == "http://127.0.0.1:15066/v1/precheck"
        assert headers["Authorization"] == "Bearer test-token"
        assert "file" in files
        assert timeout == 12.0
        return _Response({
            "status": "ok",
            "engineVersion": "0.15.3",
            "elapsedMs": 164,
            "genericVisibleWatermark": {
                "available": True,
                "model": "corzent/yolo11x_watermark_detection",
                "modelRevision": "revision-1",
                "confidenceThreshold": 0.35,
                "elapsedMs": 96,
            },
            "decision": {
                "shortCircuit": True,
                "confidence": 0.997,
                "probabilityModel": {
                    "version": "huijian-evidence-lr-v1",
                    "posterior": 0.997,
                    "effectiveLikelihoodRatio": 2700.0,
                },
            },
            "report": {"aiFromMetadata": True},
            "visibleHits": [
                {
                    "provider": "gemini",
                    "label": "Google Gemini sparkle",
                    "confidence": 0.86,
                    "decisive": True,
                    "bbox": {"x": 0.91, "y": 0.89, "w": 0.06, "h": 0.07},
                    "yoloCorroborated": True,
                    "yoloConfidence": 0.91,
                    "localizationModel": "corzent/yolo11x_watermark_detection",
                    "localizationModelRevision": "revision-1",
                },
                {
                    "provider": "yolo11x_watermark",
                    "confidence": 0.91,
                    "bbox": {"x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1},
                },
            ],
        })

    monkeypatch.setattr(expert.requests, "post", fake_post)
    result = expert.run_visible_watermark_expert(b"image", "sample.jpg", "image/jpeg")

    assert result["status"] == "success"
    assert result["score"] is None
    assert result["watermarkCount"] == 2
    assert result["visibleWatermark"]["detected"] is True
    assert result["visibleWatermark"]["provider"] == "gemini"
    assert {hit["method"] for hit in result["visibleWatermark"]["hits"]} == {
        "remove_ai_watermarks_registry",
        "yolo11x_watermark_detection",
    }
    assert result["visibleWatermark"]["hits"][0]["decisive"] is True
    assert result["visibleWatermark"]["hits"][0]["localizationConfirmed"] is True
    assert result["visibleWatermark"]["detector"]["engines"][0]["model"] == "wiltodelta/remove-ai-watermarks"
    assert result["visibleWatermark"]["detector"]["engines"][0]["version"] == "0.15.3"
    assert result["visibleWatermark"]["detector"]["engines"][1]["id"] == "yolo_visible_watermark"
    assert result["visibleWatermark"]["detector"]["engines"][1]["count"] == 2
    assert result["probabilityModel"]["posterior"] == 0.997
    assert result["provenanceReport"]["aiFromMetadata"] is True


def test_visible_watermark_expert_reports_clean_scan(monkeypatch):
    monkeypatch.setenv("WATERMARK_PRECHECK_TOKEN", "test-token")
    monkeypatch.setattr(
        expert.requests,
        "post",
        lambda *_args, **_kwargs: _Response({"status": "ok", "elapsedMs": 88, "detections": []}),
    )

    result = expert.run_visible_watermark_expert(b"image", "clean.jpg", "image/jpeg")

    assert result["status"] == "success"
    assert result["score"] is None
    assert result["visibleWatermark"]["supported"] is True
    assert result["visibleWatermark"]["detected"] is False


def test_visible_watermark_expert_exposes_generic_yolo_box_without_ai_score(monkeypatch):
    monkeypatch.setenv("WATERMARK_PRECHECK_TOKEN", "test-token")
    monkeypatch.setattr(
        expert.requests,
        "post",
        lambda *_args, **_kwargs: _Response({
            "status": "ok",
            "engineVersion": "0.15.3",
            "genericVisibleWatermark": {"available": True},
            "visibleHits": [{
                "provider": "yolo11x_watermark",
                "confidence": 0.97,
                "bbox": {"x": 0.1, "y": 0.8, "w": 0.2, "h": 0.1},
            }],
        }),
    )

    result = expert.run_visible_watermark_expert(b"image", "logo.jpg", "image/jpeg")

    assert result["score"] is None
    assert result["watermarkDetected"] is True
    assert result["watermarkCount"] == 1
    assert result["verdict"] == "定位 1 处可见水印（平台待确认）"
    hit = result["visibleWatermark"]["hits"][0]
    assert hit["provider"] == "yolo11x_watermark"
    assert hit["method"] == "yolo11x_watermark_detection"
    assert hit["decisive"] is False
    assert hit["evidenceRole"] == "localization"
    assert result["visibleWatermark"]["evidenceLevel"] == "medium"
    assert "提升最终 AI 风险" in result["visibleWatermark"]["note"]


def test_visible_watermark_expert_skips_without_token(monkeypatch):
    monkeypatch.delenv("WATERMARK_PRECHECK_TOKEN", raising=False)
    monkeypatch.delenv("YOLO_WATERMARK_TOKEN", raising=False)
    result = expert.run_visible_watermark_expert(b"image", "sample.jpg", "image/jpeg")
    assert result["status"] == "skipped"
    assert result["visibleWatermark"]["evidenceLevel"] == "unavailable"
