from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "load_test_realguard.py"
SPEC = importlib.util.spec_from_file_location("load_test_realguard", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_percentile_uses_nearest_rank():
    values = [float(value) for value in range(1, 101)]

    assert MODULE.percentile(values, 0.50) == 50.0
    assert MODULE.percentile(values, 0.95) == 95.0
    assert MODULE.percentile([], 0.95) is None


def test_summary_keeps_failures_out_of_latency_distribution():
    summary = MODULE.summarize(
        [
            {"status": "success", "latencySeconds": 1.0, "throttledResponses": 2},
            {"status": "success", "latencySeconds": 3.0, "throttledResponses": 1},
            {"status": "failed", "error": "task_timeout", "latencySeconds": 20.0},
        ],
        wall_seconds=4.0,
    )

    assert summary["attempted"] == 3
    assert summary["succeeded"] == 2
    assert summary["errorRate"] == 0.3333
    assert summary["latencySeconds"]["mean"] == 2.0
    assert summary["latencySeconds"]["max"] == 3.0
    assert summary["errors"] == {"task_timeout": 1}
    assert summary["throttledResponses"] == 3


def test_retry_after_seconds_is_bounded_and_has_a_safe_default():
    assert MODULE.retry_after_seconds(SimpleNamespace(headers={"Retry-After": "3"})) == 3.0
    assert MODULE.retry_after_seconds(SimpleNamespace(headers={"Retry-After": "100"})) == 30.0
    assert MODULE.retry_after_seconds(SimpleNamespace(headers={"Retry-After": "invalid"})) == 2.0
