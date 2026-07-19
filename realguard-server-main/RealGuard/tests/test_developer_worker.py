from pathlib import Path
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection.workers import developer_detection_worker as worker_module  # noqa: E402


def test_worker_enforces_configured_concurrency(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_module, "WORKER_CONCURRENCY", 2)
    monkeypatch.setattr(worker_module, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(worker_module, "HEARTBEAT_PATH", tmp_path / "worker.heartbeat")
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_spool_root", lambda: None)
    monkeypatch.setattr(
        worker_module.developer_platform,
        "_ensure_developer_platform_tables",
        lambda: True,
    )
    monkeypatch.setattr(
        worker_module.developer_platform,
        "_run_worker_maintenance",
        lambda: {"recovered": 0, "cleaned": 0},
    )
    release = threading.Event()
    lock = threading.Lock()
    started = threading.Event()
    claims = []
    active = 0
    peak = 0

    def claim(_instance):
        with lock:
            task_id = len(claims)
            claims.append(task_id)
        return {"task_id": task_id}

    def execute(_task):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                started.set()
        assert release.wait(timeout=2)
        with lock:
            active -= 1

    monkeypatch.setattr(worker_module.developer_platform, "_claim_next_task", claim)
    monkeypatch.setattr(worker_module.developer_platform, "_run_openapi_job", execute)
    worker = worker_module.DeveloperDetectionWorker()
    thread = threading.Thread(target=worker.run)
    thread.start()
    assert started.wait(timeout=2)

    time.sleep(0.05)
    with lock:
        assert len(claims) == 2
        assert peak == 2
    worker.stop_event.set()
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_deploy_backs_up_before_all_migrations_and_installs_worker_unit():
    root = ROOT.parents[1]
    activate = (root / "scripts" / "remote" / "activate_v1.sh").read_text(encoding="utf-8")
    backup = activate.index("backup_output=\"")
    identity = activate.index("identity-db-upgrade")
    admin = activate.index("admin-db-upgrade")
    developer = activate.index("developer-db-upgrade")

    assert backup < identity < admin < developer
    assert "REALGUARD_EVIDENCE_HMAC_KEY=" in activate
    assert "REALGUARD_EVIDENCE_HMAC_KEYS_JSON=" in activate
    assert "Existing operator-managed keyrings are never replaced" in activate
    assert "realguard-developer-worker.service" in activate
    assert "Pre-migration backup verified" in activate
