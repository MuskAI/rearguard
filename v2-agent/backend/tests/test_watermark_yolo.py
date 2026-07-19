from app import watermark_yolo


def _analysis():
    return {
        "verdict": "real",
        "confidence": 0.82,
        "source": "vlm",
        "dimensions": [],
        "regions": [],
        "explanation": "model result",
    }


def test_merge_keeps_unmatched_yolo_watermark_as_non_decisive_context():
    analysis = _analysis()
    merged = watermark_yolo.merge(analysis, {
        "status": "ok",
        "available": True,
        "engineVersion": "0.15.3",
        "genericVisibleWatermark": {
            "available": True,
            "elapsedMs": 108,
            "roundTripMs": 114,
            "model": "corzent/yolo11x_watermark_detection",
            "modelRevision": "revision-1",
            "confidenceThreshold": 0.35,
        },
        "visibleHits": [{
            "provider": "yolo11x_watermark",
            "label": "通用可见水印或 Logo",
            "confidence": 0.9237,
            "bbox": {"x": 0.04, "y": 0.84, "w": 0.14, "h": 0.11},
            "model": "corzent/yolo11x_watermark_detection",
            "modelRevision": "revision-1",
            "decisive": False,
        }],
    })

    assert merged["verdict"] == "real"
    assert merged["confidence"] == 0.82
    assert "watermarkVerdictOverride" not in merged
    assert analysis.get("visibleWatermark") is None
    visible = merged["visibleWatermark"]
    assert visible["supported"] is True
    assert visible["detected"] is True
    assert visible["confidence"] == 0.9237
    assert visible["evidenceLevel"] == "medium"
    assert len(visible["hits"]) == 1
    assert visible["hits"][0]["provider"] == "yolo11x_watermark"
    assert visible["hits"][0]["decisive"] is False
    assert "平台归属尚未确认" in visible["note"]
    assert "不单独影响 AI 生成结论" in visible["note"]


def test_merge_exposes_registry_and_yolo_as_distinct_engines():
    merged = watermark_yolo.merge(_analysis(), {
        "status": "ok",
        "available": True,
        "engineVersion": "0.15.3",
        "elapsedMs": 182,
        "genericVisibleWatermark": {
            "available": True,
            "model": "corzent/yolo11x_watermark_detection",
            "modelRevision": "revision-1",
        },
        "visibleHits": [
            {
                "provider": "gemini",
                "label": "Google Gemini sparkle",
                "confidence": 0.86,
                "bbox": {"x": 0.91, "y": 0.89, "w": 0.06, "h": 0.07},
                "decisive": True,
                "yoloCorroborated": True,
                "yoloConfidence": 0.92,
                "localizationModel": "corzent/yolo11x_watermark_detection",
                "localizationModelRevision": "revision-1",
            },
            {
                "provider": "yolo11x_watermark",
                "confidence": 0.92,
                "bbox": {"x": 0.9, "y": 0.88, "w": 0.08, "h": 0.1},
                "decisive": False,
            },
        ],
    })

    visible = merged["visibleWatermark"]
    assert merged["verdict"] == "highly_suspected_fake"
    assert merged["confidence"] == 0.95
    assert visible["provider"] == "gemini"
    assert visible["confidence"] == 0.86
    assert len(visible["hits"]) == 1
    assert visible["hits"][0]["method"] == "remove_ai_watermarks_registry"
    assert visible["hits"][0]["localizationConfirmed"] is True
    engines = {engine["id"]: engine for engine in visible["detector"]["engines"]}
    assert engines["known_ai_registry"]["model"] == "wiltodelta/remove-ai-watermarks"
    assert engines["known_ai_registry"]["version"] == "0.15.3"
    assert engines["yolo_visible_watermark"]["model"] == "corzent/yolo11x_watermark_detection"
    assert engines["yolo_visible_watermark"]["count"] == 1
    assert "来源证据规则" in visible["note"]


def test_merge_exposes_completed_scan_when_no_watermark_is_found():
    merged = watermark_yolo.merge(_analysis(), {
        "status": "ok",
        "available": True,
        "engineVersion": "0.15.3",
        "genericVisibleWatermark": {
            "available": True,
            "detected": False,
            "count": 0,
            "elapsedMs": 91,
        },
        "visibleHits": [],
    })

    visible = merged["visibleWatermark"]
    assert visible["supported"] is True
    assert visible["detected"] is False
    assert visible["evidenceLevel"] == "none"
    assert visible["elapsedMs"] == 91


def test_merge_marks_detector_unavailable_without_changing_analysis():
    merged = watermark_yolo.merge(_analysis(), {
        "genericVisibleWatermark": {
            "available": False,
            "error": "Timeout",
        },
        "visibleHits": [],
    })

    assert merged["verdict"] == "real"
    assert merged["visibleWatermark"]["supported"] is False
    assert merged["visibleWatermark"]["evidenceLevel"] == "unavailable"
