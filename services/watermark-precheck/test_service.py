from pathlib import Path
import sys
import threading


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
        rendezvous.wait(timeout=2)
        return []

    monkeypatch.setattr(service, "_report", report)
    monkeypatch.setattr(service, "_visible_hits", visible)

    collected_report, collected_hits = service._collect_evidence(Path("unused.png"))

    assert collected_report == {"isAiGenerated": None}
    assert collected_hits == []
