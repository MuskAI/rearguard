from __future__ import annotations

import importlib.util
from pathlib import Path


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
            {"status": "success", "latencySeconds": 1.0},
            {"status": "success", "latencySeconds": 3.0},
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
