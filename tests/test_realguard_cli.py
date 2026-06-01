from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = ROOT / "scripts" / "realguard_cli.py"


spec = importlib.util.spec_from_file_location("realguard_cli", CLI_PATH)
realguard_cli = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(realguard_cli)


def test_build_url_handles_prefix_slashes():
    assert (
        realguard_cli.build_url("http://realguard.cn/", "/v2-api/", "detect")
        == "http://realguard.cn/v2-api/detect"
    )
    assert realguard_cli.build_url("http://127.0.0.1:8848", "", "/health") == (
        "http://127.0.0.1:8848/health"
    )


def test_encode_multipart_includes_file_and_file_type(tmp_path):
    sample = tmp_path / "sample.png"
    sample.write_bytes(b"fake-image")

    body, content_type = realguard_cli.encode_multipart(
        {"fileType": "image"}, {"file": sample}
    )

    assert content_type.startswith("multipart/form-data; boundary=----realguard-")
    assert b'name="fileType"' in body
    assert b"image" in body
    assert b'name="file"; filename="sample.png"' in body
    assert b"fake-image" in body


def test_compact_result_strips_large_media_and_adds_agent_summary():
    result = {
        "taskId": "task-1",
        "reportId": "report-1",
        "verdict": "likely_ai_generated",
        "confidence": 0.87,
        "source": "vlm",
        "modelVersion": "v2.1",
        "cacheVersion": "v2-cache",
        "cacheHit": False,
        "fileMeta": {
            "thumbnail": "data:image/png;base64,AAA",
            "preview": "data:image/png;base64,BBB",
        },
        "visibleWatermark": {
            "detected": True,
            "hits": [{"text": "AI", "crop": "data:image/png;base64,CCC"}],
        },
        "synthid": {"detected": False},
        "explanation": {"reasons": ["watermark signal"]},
        "disclaimer": "Reference only.",
    }

    compact = realguard_cli.compact_result(result)

    assert compact["fileMeta"]["thumbnail"].startswith("[stripped data URI")
    assert compact["fileMeta"]["preview"].startswith("[stripped data URI")
    assert compact["visibleWatermark"]["hits"][0]["crop"].startswith("[stripped data URI")
    assert compact["agentSummary"] == {
        "verdict": "likely_ai_generated",
        "confidence": 0.87,
        "confidencePercent": 87,
        "reportId": "report-1",
        "taskId": "task-1",
        "source": "vlm",
        "modelVersion": "v2.1",
        "cacheVersion": "v2-cache",
        "cacheHit": False,
        "topReasons": ["watermark signal"],
        "evidenceFlags": {
            "synthidDetected": False,
            "visibleWatermarkDetected": True,
            "visibleWatermarkHits": 1,
        },
        "disclaimer": "Reference only.",
    }
