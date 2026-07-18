import json
from datetime import datetime, timezone
from io import BytesIO

from PIL import Image

from app import capture_evidence, metadata, reporting


def _camera_metadata():
    return {
        "exif": {
            "EXIF:Make": "Apple",
            "EXIF:Model": "iPhone 16 Pro",
            "EXIF:ExposureTime": "1/120",
            "EXIF:FNumber": "1.8",
            "EXIF:ISO": "80",
            "EXIF:FocalLength": "6.8 mm",
            "EXIF:DateTimeOriginal": "2026-06-12T10:21:33+08:00",
            "EXIF:BodySerialNumber": "private-serial-8899",
            "EXIF:GPSLatitude": "30.111111",
            "EXIF:GPSLongitude": "120.222222",
            "EXIF:MakerNote": {"HDR": "On"},
        }
    }


def test_camera_chain_is_structured_and_privacy_safe():
    result = capture_evidence.analyze_capture_evidence(
        _camera_metadata(),
        now=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )

    assert result["level"] == "medium"
    assert result["supportsRealCapture"] is True
    assert result["privacy"] == {
        "gpsRedacted": True,
        "serialRedacted": True,
        "captureTimeRedacted": True,
    }
    rendered = json.dumps(result, ensure_ascii=False)
    assert "private-serial-8899" not in rendered
    assert "30.111111" not in rendered
    assert "2026-06-12T10:21:33+08:00" not in rendered


def test_future_timestamp_and_ai_marker_create_conflict():
    metadata = _camera_metadata()
    metadata["exif"]["EXIF:DateTimeOriginal"] = "2036-01-01T00:00:00+08:00"
    result = capture_evidence.analyze_capture_evidence(
        metadata,
        ai_markers=["Midjourney parameters"],
        now=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )

    assert result["level"] == "conflict"
    assert result["supportsRealCapture"] is False
    assert {item["key"] for item in result["conflicts"]} == {"future_time", "ai_declaration"}


def test_signed_camera_capture_upgrades_only_consistent_evidence():
    clean = capture_evidence.analyze_capture_evidence(_camera_metadata())
    signed = capture_evidence.add_verified_camera_credential(clean, issuer="Camera Trust List")
    conflicted = capture_evidence.analyze_capture_evidence(_camera_metadata(), ai_markers=["ComfyUI"])

    assert signed["level"] == "strong"
    assert signed["supportsRealCapture"] is True
    assert signed["likelihoodRatio"] == 0.08
    assert capture_evidence.add_verified_camera_credential(conflicted)["level"] == "conflict"


def test_jpeg_exif_rationals_flow_into_capture_evidence():
    image = Image.new("RGB", (32, 32), "white")
    exif = Image.Exif()
    for tag, value in {
        271: "Canon",
        272: "EOS R5",
        33434: (1, 250),
        33437: (28, 10),
        34855: 200,
        37386: (50, 1),
        36867: "2025:06:12 10:21:33",
    }.items():
        exif[tag] = value
    output = BytesIO()
    image.save(output, format="JPEG", exif=exif)

    report = metadata.inspect_metadata(
        output.getvalue(),
        filename="camera-sample.jpg",
        mime="image/jpeg",
    )

    capture = report["captureEvidence"]
    assert capture["level"] == "medium"
    parameters = next(item for item in capture["evidence"] if item["key"] == "exposure")
    assert parameters["value"] == "1/250s · f/2.8 · ISO 200 · 50mm"


def test_html_report_redacts_sensitive_metadata_preview_values():
    html = reporting._render_provenance_block({
        "hasCredentials": False,
        "metadataSummary": {
            "preview": [
                {"path": "image.gps.GPSLatitude", "value": "30.111111"},
                {"path": "image.exif.BodySerialNumber", "value": "private-serial"},
            ],
        },
        "captureEvidence": capture_evidence.analyze_capture_evidence(_camera_metadata()),
        "synthid": {},
    })

    assert "30.111111" not in html
    assert "private-serial" not in html
    assert html.count("[已隐藏敏感值]") == 2
