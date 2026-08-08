from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from PIL import Image
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import primary_image_detector


class _Response:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


def _png(width: int = 64, height: int = 48) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (30, 80, 140)).save(output, "PNG")
    return output.getvalue()


def test_primary_detector_normalizes_fast_model_response(monkeypatch):
    captured = {}
    monkeypatch.setattr(primary_image_detector, "ENDPOINT", "http://detector.local/image")
    monkeypatch.setattr(primary_image_detector, "TOKEN", "x" * 40)

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _Response({
            "code": 200,
            "data": {
                "fake_percentage": 82.5,
                "final_label": "AI生成图像",
                "explanation": "主模型证据",
                "meta": {"model": "dinov3-v7b-fp16"},
                "remote_evidence": {
                    "modelDecision": {"ready": True},
                    "visibleWatermarkPrecheck": {"status": "ok", "visibleHits": []},
                },
            },
        })

    monkeypatch.setattr(primary_image_detector.urlrequest, "urlopen", fake_urlopen)
    result = primary_image_detector.analyze(
        "sample.png",
        _png(),
        account_uuid="11111111-1111-4111-8111-111111111111",
    )

    assert result["verdict"] == "highly_suspected_fake"
    assert result["aiProbability"] == pytest.approx(0.825)
    assert result["modelVersion"] == "dinov3-v7b-fp16"
    assert result["decisionStatus"] == "verdict"
    assert captured["request"].get_header("X-realguard-detector-token") == "x" * 40
    assert b'defer_visual_llm\"\r\n\r\n1' in captured["request"].data
    assert b'persist_result\"\r\n\r\n0' in captured["request"].data
    assert result["visibleWatermarkPrecheck"] == {"status": "ok", "visibleHits": []}


def test_primary_detector_downsamples_large_transport(monkeypatch):
    monkeypatch.setattr(primary_image_detector, "MAX_TRANSPORT_SIDE", 96)
    filename, mime, data = primary_image_detector._prepare_transport("large.png", _png(300, 180))

    assert filename.endswith(".jpg")
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as image:
        assert max(image.size) == 96


def test_primary_detector_fails_closed_without_internal_token(monkeypatch):
    monkeypatch.setattr(primary_image_detector, "TOKEN", "")
    with pytest.raises(primary_image_detector.PrimaryDetectorError, match="尚未配置"):
        primary_image_detector.analyze("sample.png", _png())
