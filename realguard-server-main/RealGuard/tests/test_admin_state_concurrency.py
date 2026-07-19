import concurrent.futures
import json
import threading

from imagedetection.views import admin_state, model_registry


def test_concurrent_detection_job_creates_are_atomic(monkeypatch, tmp_path):
    state_path = tmp_path / "admin_state.json"
    monkeypatch.setattr(admin_state, "STATE_PATH", state_path)

    def create(index):
        return admin_state.create_detection_job(
            {"openid": f"guest-{index}"},
            f"concurrent-{index}.png",
            kind="swarm",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        jobs = list(executor.map(create, range(40)))

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    persisted_jobs = persisted["detectionJobs"]
    assert len(persisted_jobs) == 40
    assert {job["id"] for job in jobs} == set(persisted_jobs)
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_job_updates_do_not_drop_other_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    jobs = [
        admin_state.create_detection_job({"openid": "guest"}, f"job-{index}.png")
        for index in range(20)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(
            executor.map(
                lambda job: admin_state.update_detection_job(
                    job["id"], {"status": "success", "progress": 100}
                ),
                jobs,
            )
        )

    persisted_jobs = admin_state.load_state()["detectionJobs"]
    assert all(result and result["status"] == "success" for result in results)
    assert len(persisted_jobs) == 20
    assert all(job["status"] == "success" for job in persisted_jobs.values())


def test_concurrent_model_creates_do_not_overwrite_each_other(monkeypatch, tmp_path):
    registry_path = tmp_path / "model_registry.json"
    monkeypatch.setattr(model_registry, "REGISTRY_PATH", registry_path)
    monkeypatch.setenv("REALGUARD_MODEL_ALLOWED_ORIGINS", "https://models.example")

    def create(index):
        return model_registry.create_model({
            "id": f"candidate-{index}",
            "name": f"Candidate {index}",
            "endpoint": f"https://models.example/{index}/detect",
            "healthUrl": f"https://models.example/{index}/health",
        })

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(create, range(30)))

    ids = {item["id"] for item in model_registry.load_registry()["models"]}
    assert all(model is not None and not error for _, model, error in results)
    assert {f"candidate-{index}" for index in range(30)} <= ids
    assert not list(tmp_path.glob("*.tmp"))


def test_admin_state_files_are_owner_only(monkeypatch, tmp_path):
    state_path = tmp_path / "admin_state.json"
    monkeypatch.setattr(admin_state, "STATE_PATH", state_path)

    admin_state.update_alerts({"enabled": False})

    lock_path = tmp_path / ".admin_state.json.lock"
    assert state_path.stat().st_mode & 0o777 == 0o600
    assert lock_path.stat().st_mode & 0o777 == 0o600


def test_database_audit_listing_keeps_json_fallback_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    monkeypatch.setattr(admin_state, "_db_state_enabled", lambda: False)
    admin_state.append_audit({"Userid": "admin:1", "username": "root"}, "fallback.action", "fallback")
    monkeypatch.setattr(
        admin_state,
        "_system_sql",
        lambda *_args, **_kwargs: [{
            "id": 9,
            "created_at": "2026-07-18 12:00:00",
            "actor_id": "admin:2",
            "actor_username": "ops",
            "actor_phone": "",
            "action": "db.action",
            "target": "database",
            "before_json": None,
            "after_json": None,
            "meta_json": "{}",
        }],
    )

    actions = {entry["action"] for entry in admin_state.list_audit(20)}
    assert actions == {"db.action", "fallback.action"}


def test_concurrent_health_requests_share_one_probe(monkeypatch):
    model_registry.clear_health_cache()
    started = threading.Event()
    release = threading.Event()
    call_count = 0
    count_lock = threading.Lock()

    def fake_health(model):
        nonlocal call_count
        with count_lock:
            call_count += 1
        started.set()
        assert release.wait(timeout=2)
        return {"ok": True, "serviceOk": True, "modelId": model["id"]}

    monkeypatch.setattr(model_registry, "check_model_health", fake_health)
    model = {"id": "shared", "healthUrl": "https://model.example/health", "timeoutSeconds": 5}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(model_registry.check_models_health, [model])
        assert started.wait(timeout=2)
        second = executor.submit(model_registry.check_models_health, [model])
        release.set()
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    assert call_count == 1
    assert first_result["shared"]["ok"] is True
    assert second_result["shared"]["ok"] is True


def test_disabling_alert_rule_suppresses_recovery_notification(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    alert = admin_state.claim_alert_event("probeFailed", True, "探测失败", "模型离线")
    assert alert and alert["kind"] == "alert"

    admin_state.update_alerts({"rules": {"probeFailed": False}})
    recovery = admin_state.claim_alert_event("probeFailed", False, "探测失败", "模型离线")

    assert recovery is None
    event = admin_state.alerts()["runtime"]["events"]["probeFailed"]
    assert event["active"] is False
    assert event["suppressedAtEpoch"] > 0


def test_failed_recovery_delivery_remains_retryable(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_state, "STATE_PATH", tmp_path / "admin_state.json")
    admin_state.claim_alert_event("v1Offline", True, "主链路离线", "服务异常")
    recovery = admin_state.claim_alert_event("v1Offline", False, "主链路离线", "服务异常")
    assert recovery and recovery["kind"] == "recovery"

    admin_state.record_alert_delivery(recovery, False, error="timeout")

    event = admin_state.alerts()["runtime"]["events"]["v1Offline"]
    assert event["active"] is True
    assert event["lastDeliveryOk"] is False
