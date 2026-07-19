from __future__ import annotations

from importlib import util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "skills" / "huijian-image-forensics" / "scripts" / "huijian_forensics.py"
SPEC = util.spec_from_file_location("huijian_forensics", SCRIPT)
MODULE = util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_retry_after_seconds_is_bounded_and_defaults_safely():
    assert MODULE.retry_after_seconds("3") == 3.0
    assert MODULE.retry_after_seconds("100") == 30.0
    assert MODULE.retry_after_seconds("invalid") == 2.0


def test_wait_for_task_retries_rate_limited_status_poll(monkeypatch):
    calls = []

    def fake_get_task(task, timeout):
        calls.append((task, timeout))
        if len(calls) == 1:
            raise MODULE.ApiError("rate limited", 429, retry_after=0.2)
        return {"id": "task-1", "status": "success"}

    monkeypatch.setattr(MODULE, "get_task", fake_get_task)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)

    result = MODULE.wait_for_task({"id": "task-1", "status": "queued"}, 5.0, 0.5)

    assert result["status"] == "success"
    assert len(calls) == 2
