from PIL import Image

import service


def test_unknown_candidate_is_not_forced_to_platform(monkeypatch):
    monkeypatch.setattr(service, "_ocr", lambda _crop: (None, 0.0, []))
    monkeypatch.setattr(service, "_retrieve", lambda _crop, _position=None: {
        "accepted": False, "platform": None, "similarity": 0.71,
        "threshold": 0.84, "reason": "below_platform_threshold",
    })
    image = Image.new("RGB", (100, 100), "white")
    result = service.analyze(
        image,
        [{"provider": "yolo11x_watermark", "confidence": 0.92, "bbox": {"x": 0.02, "y": 0.02, "w": 0.12, "h": 0.08}}],
        [],
    )
    assert result["detected"] is True
    assert result["type"] == "unknown"
    assert result["sourcePlatform"] is None


def test_text_candidate_uses_ocr_and_platform_keyword(monkeypatch):
    monkeypatch.setattr(service, "_ocr", lambda _crop: ("即梦AI", 0.91, [{"text": "即梦AI", "confidence": 0.91}]))
    image = Image.new("RGB", (100, 100), "white")
    result = service.analyze(
        image,
        [{"provider": "yolo11x_watermark", "confidence": 0.82, "bbox": {"x": 0.76, "y": 0.86, "w": 0.18, "h": 0.08}}],
        [],
    )
    assert result["type"] == "text"
    assert result["sourcePlatform"] == "即梦AI"
    assert result["hits"][0]["text"] == "即梦AI"


def test_registry_hit_is_logo_without_ocr(monkeypatch):
    monkeypatch.setattr(service, "_ocr", lambda _crop: (None, 0.0, []))
    monkeypatch.setattr(service, "_retrieve", lambda _crop, _position=None: {
        "accepted": False, "platform": None, "similarity": 0.0,
        "threshold": 0.84, "reason": "not_needed",
    })
    image = Image.new("RGB", (100, 100), "white")
    result = service.analyze(
        image,
        [{"provider": "yolo11x_watermark", "confidence": 0.90, "bbox": {"x": 0.88, "y": 0.88, "w": 0.08, "h": 0.08}}],
        [{"provider": "gemini", "confidence": 0.86, "bbox": {"x": 0.88, "y": 0.88, "w": 0.08, "h": 0.08}}],
    )
    assert result["type"] == "logo"
    assert result["sourcePlatform"] == "Google Gemini"
    assert result["hits"][0]["registryMatched"] is True


def test_uncorroborated_registry_candidate_is_not_platform_evidence(monkeypatch):
    monkeypatch.setattr(service, "_ocr", lambda _crop: (None, 0.0, []))
    monkeypatch.setattr(service, "_retrieve", lambda _crop, _position=None: {
        "accepted": False, "platform": None, "similarity": 0.78,
        "threshold": 0.84, "reason": "below_platform_threshold",
    })
    image = Image.new("RGB", (100, 100), "white")
    result = service.analyze(
        image,
        [],
        [{"provider": "jimeng_pill", "confidence": 0.93, "bbox": {"x": 0.02, "y": 0.02, "w": 0.18, "h": 0.08}}],
    )
    assert result["sourcePlatform"] is None
    assert result["aiWatermarkVerdict"]["verdict"] != "yes"
    assert result["hits"][0]["registryMatched"] is False


def test_template_retrieval_requires_high_similarity(monkeypatch):
    monkeypatch.setattr(service, "_ocr", lambda _crop: (None, 0.0, []))
    monkeypatch.setattr(service, "_retrieve", lambda _crop, _position=None: {
        "accepted": False, "platform": None, "candidatePlatform": "即梦AI",
        "similarity": 0.84, "threshold": 0.93, "reason": "below_platform_threshold",
    })
    monkeypatch.setattr(service, "RETRIEVAL_BACKEND", "template")
    image = Image.new("RGB", (100, 100), "white")
    result = service.analyze(
        image,
        [{"provider": "yolo11x_watermark", "confidence": 0.90, "bbox": {"x": 0.75, "y": 0.82, "w": 0.20, "h": 0.10}}],
        [],
    )
    assert result["sourcePlatform"] is None
    assert result["type"] == "unknown"


def test_calibrated_retrieval_can_attribute_logo(monkeypatch):
    monkeypatch.setattr(service, "_ocr", lambda _crop: (None, 0.0, []))
    monkeypatch.setattr(service, "_retrieve", lambda _crop, _position=None: {
        "accepted": True, "platform": "Google Gemini", "candidatePlatform": "Google Gemini",
        "similarity": 0.91, "threshold": 0.86, "margin": 0.08,
        "minimumMargin": 0.03, "referenceId": "gemini-black",
        "referenceSource": "public-capture", "reason": "accepted", "backend": "faiss_clip",
    })
    image = Image.new("RGB", (100, 100), "white")
    result = service.analyze(
        image,
        [{"provider": "yolo11x_watermark", "confidence": 0.90, "bbox": {"x": 0.86, "y": 0.86, "w": 0.12, "h": 0.12}}],
        [],
    )
    assert result["type"] == "logo"
    assert result["sourcePlatform"] == "Google Gemini"
    assert result["hits"][0]["retrievalAccepted"] is True
    assert result["hits"][0]["retrievalReferenceId"] == "gemini-black"


def test_ocr_can_resolve_close_retrieval_platforms(monkeypatch):
    monkeypatch.setattr(service, "_ocr", lambda _crop: ("豆包AI生成", 0.96, [{"text": "豆包AI生成", "confidence": 0.96}]))
    monkeypatch.setattr(service, "_retrieve", lambda _crop, _position=None: {
        "accepted": False, "platform": None, "candidatePlatform": "即梦AI",
        "similarity": 0.931, "threshold": 0.845, "margin": 0.003,
        "minimumMargin": 0.024, "reason": "ambiguous_platform_margin",
        "platformScores": {
            "即梦AI": {"similarity": 0.931, "threshold": 0.845, "positionMatch": True},
            "豆包": {"similarity": 0.928, "threshold": 0.855, "positionMatch": True,
                     "referenceId": "doubao-black", "referenceSource": "public-capture"},
        },
    })
    image = Image.new("RGB", (100, 100), "black")
    result = service.analyze(
        image,
        [{"provider": "yolo11x_watermark", "confidence": 0.92, "bbox": {"x": 0.77, "y": 0.92, "w": 0.22, "h": 0.07}}],
        [],
    )
    hit = result["hits"][0]
    assert hit["sourcePlatform"] == "豆包"
    assert hit["retrievalAccepted"] is True
    assert hit["retrievalReason"] == "accepted_with_ocr_corroboration"


def test_ai_generation_wording_is_reported_as_supporting_text_evidence():
    signal = service._analyze_text_signal("豆包AI生成", 0.92, 1.0, 0.88, "豆包")
    assert signal["verdict"] == "supports_ai_generation"
    assert signal["likelyAIgenerated"] is True
    assert "ai生成" in signal["matchedKeywords"]


def test_ordinary_text_is_not_called_ai_generated():
    signal = service._analyze_text_signal("夏日湖畔", 0.96, 1.0, 0.90, None)
    assert signal["verdict"] == "not_supported"
    assert signal["likelyAIgenerated"] is False


def test_plain_text_hit_is_filtered_from_ai_watermark_answer():
    result = service._ai_watermark_verdict([{
        "type": "text",
        "confidence": 0.91,
        "textAnalysis": {"verdict": "not_supported", "aiGenerationConfidence": 0.08},
    }])
    assert result["verdict"] == "no"
    assert result["isAiGeneratedWatermark"] is False
    assert result["relevantHitCount"] == 0
