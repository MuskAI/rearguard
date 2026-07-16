import concurrent.futures
import json

from imagedetection.views import admin_state


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
