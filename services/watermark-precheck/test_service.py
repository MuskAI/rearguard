from pathlib import Path
import sys
import threading

from flask import g, has_request_context


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import service


def test_report_and_visible_scan_start_in_parallel(monkeypatch):
    rendezvous = threading.Barrier(2)

    def report(_path):
        rendezvous.wait(timeout=2)
        return {"isAiGenerated": None}

    def visible(_path):
        assert has_request_context()
        g.visible_status = "complete"
        rendezvous.wait(timeout=2)
        return []

    monkeypatch.setattr(service, "_report", report)
    monkeypatch.setattr(service, "_visible_hits", visible)

    with service.app.test_request_context("/v1/precheck"):
        collected_report, collected_hits = service._collect_evidence(Path("unused.png"))
        assert g.visible_status == "complete"

    assert collected_report == {"isAiGenerated": None}
    assert collected_hits == []
