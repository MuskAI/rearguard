from pathlib import Path
from io import BytesIO
import sys
import threading

from flask import g, has_request_context
from PIL import Image


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import service


def test_jimeng_pill_requires_same_product_or_provenance():
    assert service._keep_visible_detection("jimeng_pill", {"jimeng_pill"}, frozenset()) is False
    assert service._keep_visible_detection("jimeng_pill", {"jimeng", "jimeng_pill"}, frozenset()) is True
    assert service._keep_visible_detection("jimeng_pill", {"jimeng_pill"}, frozenset({"jimeng"})) is True
    assert service._keep_visible_detection("doubao", {"doubao"}, frozenset()) is True


def test_report_and_visible_scan_start_in_parallel(monkeypatch):
    rendezvous = threading.Barrier(2)

    def report(_path):
        rendezvous.wait(timeout=2)
        return {"isAiGenerated": None}

    def visible(_path, provenance_path=None):
        assert provenance_path == Path("unused.png")
        assert has_request_context()
        g.visible_status = "complete"
        rendezvous.wait(timeout=2)
        return []

    monkeypatch.setattr(service, "_report", report)
    monkeypatch.setattr(service, "_visible_hits", visible)

    with service.app.test_request_context("/v1/precheck"):
        collected_report, collected_hits, timings = service._collect_evidence(Path("unused.png"))
        assert g.visible_status == "complete"

    assert collected_report == {"isAiGenerated": None}
    assert collected_hits == []
    assert timings["metadataMs"] >= 0
    assert timings["visiblePipelineMs"] >= 0


def test_precheck_normalizes_exif_orientation_for_all_visible_boxes(monkeypatch):
    encoded = Image.new("RGB", (4, 2), "white")
    exif = Image.Exif()
    exif[274] = 6
    payload = BytesIO()
    encoded.save(payload, format="JPEG", exif=exif)
    observed = {}

    def collect(_original_path, visible_path=None):
        with Image.open(visible_path) as normalized:
            observed["size"] = normalized.size
            observed["orientation"] = normalized.getexif().get(274)
        return {}, [], {"metadataMs": 1, "visiblePipelineMs": 2}

    monkeypatch.setattr(service, "API_TOKEN", "test-token")
    monkeypatch.setattr(service, "_collect_evidence", collect)
    monkeypatch.setattr(service, "build_decision", lambda *_args: {})

    response = service.app.test_client().post(
        "/v1/precheck",
        headers={"Authorization": "Bearer test-token"},
        data={"file": (BytesIO(payload.getvalue()), "oriented.jpg")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["coordinateSpace"] == "display_normalized_v1"
    assert data["encodedSize"] == {"width": 4, "height": 2}
    assert data["displaySize"] == {"width": 2, "height": 4}
    assert data["sourceOrientation"] == 6
    assert observed == {"size": (2, 4), "orientation": None}
