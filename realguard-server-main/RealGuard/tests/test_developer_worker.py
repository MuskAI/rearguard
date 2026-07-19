from pathlib import Path
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection.workers import developer_detection_worker as worker_module  # noqa: E402


def test_worker_does_not_claim_when_detector_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_module, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(worker_module, "HEARTBEAT_PATH", tmp_path / "worker.heartbeat")
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_spool_root", lambda: None)
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_web_spool_root", lambda: None)
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(worker_module.developer_platform, "_run_worker_maintenance", lambda: {})
    monkeypatch.setattr(worker_module.developer_platform, "_detector_ready_for_worker", lambda: False)
    claims = []
    monkeypatch.setattr(
        worker_module.developer_platform,
        "_claim_next_task",
        lambda _instance: claims.append("developer"),
    )
    monkeypatch.setattr(
        worker_module.developer_platform,
        "_claim_next_web_task",
        lambda _instance: claims.append("web"),
    )
    worker = worker_module.DeveloperDetectionWorker()
    thread = threading.Thread(target=worker.run)
    thread.start()
    time.sleep(0.05)
    worker.stop_event.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert claims == []


def test_worker_enforces_configured_concurrency(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_module, "WORKER_CONCURRENCY", 2)
    monkeypatch.setattr(worker_module, "DEVELOPER_WORKER_CONCURRENCY", 2)
    monkeypatch.setattr(worker_module, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(worker_module, "HEARTBEAT_PATH", tmp_path / "worker.heartbeat")
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_spool_root", lambda: None)
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_web_spool_root", lambda: None)
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
    monkeypatch.setattr(worker_module.developer_platform, "_detector_ready_for_worker", lambda: True)
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
    monkeypatch.setattr(worker_module.developer_platform, "_claim_next_web_task", lambda _instance: None)
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


def test_worker_shares_capacity_fairly_between_web_and_developer_tasks(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_module, "WORKER_CONCURRENCY", 2)
    monkeypatch.setattr(worker_module, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(worker_module, "HEARTBEAT_PATH", tmp_path / "worker.heartbeat")
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_spool_root", lambda: None)
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_web_spool_root", lambda: None)
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(worker_module.developer_platform, "_run_worker_maintenance", lambda: {})
    monkeypatch.setattr(worker_module.developer_platform, "_detector_ready_for_worker", lambda: True)
    remaining = {"web": 1, "developer": 1}
    release = threading.Event()
    both_started = threading.Event()
    lock = threading.Lock()
    started = []

    def claim(kind):
        def inner(_instance):
            with lock:
                if not remaining[kind]:
                    return None
                remaining[kind] -= 1
            return {"task_id": kind}
        return inner

    def execute(kind):
        def inner(_task):
            with lock:
                started.append(kind)
                if len(started) == 2:
                    both_started.set()
            assert release.wait(timeout=2)
        return inner

    monkeypatch.setattr(worker_module.developer_platform, "_claim_next_web_task", claim("web"))
    monkeypatch.setattr(worker_module.developer_platform, "_claim_next_task", claim("developer"))
    monkeypatch.setattr(worker_module.developer_platform, "_run_web_detection_job", execute("web"))
    monkeypatch.setattr(worker_module.developer_platform, "_run_openapi_job", execute("developer"))
    worker = worker_module.DeveloperDetectionWorker()
    thread = threading.Thread(target=worker.run)
    thread.start()
    assert both_started.wait(timeout=2)
    with lock:
        assert set(started) == {"web", "developer"}
    worker.stop_event.set()
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_long_running_channel_reserves_next_slot_for_other_channel(monkeypatch):
    monkeypatch.setattr(worker_module, "WORKER_CONCURRENCY", 2)
    worker = worker_module.DeveloperDetectionWorker()
    web_future = object()
    worker.futures = {web_future: "web"}
    worker.prefer_web_task = True
    assert worker._prefer_web_for_next_claim() is False

    developer_future = object()
    worker.futures = {developer_future: "developer"}
    worker.prefer_web_task = False
    assert worker._prefer_web_for_next_claim() is True
    worker.executor.shutdown(wait=True)


def test_web_tasks_cannot_consume_developer_reserved_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(worker_module, "WORKER_CONCURRENCY", 2)
    monkeypatch.setattr(worker_module, "WEB_WORKER_CONCURRENCY", 1)
    monkeypatch.setattr(worker_module, "DEVELOPER_WORKER_CONCURRENCY", 1)
    monkeypatch.setattr(worker_module, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(worker_module, "HEARTBEAT_PATH", tmp_path / "worker.heartbeat")
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_spool_root", lambda: None)
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_web_spool_root", lambda: None)
    monkeypatch.setattr(worker_module.developer_platform, "_ensure_developer_platform_tables", lambda: True)
    monkeypatch.setattr(worker_module.developer_platform, "_run_worker_maintenance", lambda: {})
    monkeypatch.setattr(worker_module.developer_platform, "_detector_ready_for_worker", lambda: True)
    web_claims = []
    release = threading.Event()
    started = threading.Event()

    def claim_web(_instance):
        web_claims.append(len(web_claims))
        return {"task_id": f"web-{len(web_claims)}"}

    def run_web(_task):
        started.set()
        assert release.wait(timeout=2)

    monkeypatch.setattr(worker_module.developer_platform, "_claim_next_web_task", claim_web)
    monkeypatch.setattr(worker_module.developer_platform, "_claim_next_task", lambda _instance: None)
    monkeypatch.setattr(worker_module.developer_platform, "_run_web_detection_job", run_web)
    worker = worker_module.DeveloperDetectionWorker()
    thread = threading.Thread(target=worker.run)
    thread.start()
    assert started.wait(timeout=2)
    time.sleep(0.05)

    assert len(web_claims) == 1
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
