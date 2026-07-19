import threading

import pytest

from imagedetection.views import admin_state, detection


@pytest.fixture(autouse=True)
def isolate_evidence_persistence(monkeypatch):
    monkeypatch.setattr(
        detection,
        "_persist_and_freeze_completed_image_result",
        lambda itemid, result, **kwargs: True,
    )


def _primary_result(filename):
    return {
        "itemid": 123,
        "filename": filename,
        "probability": 0.8,
        "detector_probability": 0.8,
        "final_label": "AI生成图像",
        "confidence": "高",
        "all_metadata": {},
    }


def test_swarm_v2_stagger_scales_with_upload_size(monkeypatch):
    monkeypatch.setattr(detection, "SWARM_V2_STAGGER_BYTES_PER_SECOND", 800_000)
    monkeypatch.setattr(detection, "SWARM_V2_MAX_STAGGER_SECONDS", 8.0)

    assert detection._swarm_v2_stagger_seconds(b"x" * 80_000) == pytest.approx(0.1)
    assert detection._swarm_v2_stagger_seconds(b"x" * 4_800_000) == pytest.approx(6.0)
    assert detection._swarm_v2_stagger_seconds(b"x" * 20_000_000) == pytest.approx(8.0)


def test_swarm_defers_network_experts_until_primary_finishes(monkeypatch):
    primary_finished = threading.Event()
    v2_started = threading.Event()
    ordering = []
    specs = [
        {"id": "primary", "name": "主检测", "role": "主检测", "provider": "internal", "weight": 0.7},
        {"id": "metadata", "name": "元数据", "role": "元数据", "provider": "local", "weight": 0.1},
        {"id": "v2", "name": "语义复核", "role": "语义复核", "provider": "internal", "weight": 0.2},
    ]
    monkeypatch.setattr(detection, "_swarm_specs", lambda include_disabled=False: specs)
    monkeypatch.setattr(detection, "_swarm_config", lambda: {"enabled": True, "minExperts": 2})
    monkeypatch.setattr(detection, "_swarm_v2_stagger_seconds", lambda image_bytes: 60.0)
    monkeypatch.setattr(detection, "_persist_swarm_history_result", lambda *args, **kwargs: 123)

    def fake_primary(*args, **kwargs):
        ordering.append(("primary_saw_v2", v2_started.is_set()))
        primary_finished.set()
        return _primary_result("parallel.png"), {
            "status": "success",
            "score": 0.8,
            "verdict": "AI生成图像",
            "confidence": "高",
            "evidence": [],
            "message": "完成",
            "latencyMs": 1,
        }

    def fake_v2(*args, **kwargs):
        v2_started.set()
        ordering.append(("v2_saw_primary_finished", primary_finished.is_set()))
        return {
            "status": "success",
            "score": 0.75,
            "verdict": "疑似伪造",
            "confidence": "高",
            "evidence": [],
            "message": "完成",
            "latencyMs": 1,
        }

    monkeypatch.setattr(detection, "_swarm_primary_expert", fake_primary)
    monkeypatch.setattr(detection, "_swarm_v2_expert", fake_v2)

    payload, status = detection._run_swarm_detection_payload(
        b"image-bytes",
        "parallel.png",
        "image/png",
        {"Userid": 1, "openid": "parallel-test"},
    )

    assert status == 200
    assert payload["status"] == "success"
    assert ordering == [
        ("primary_saw_v2", False),
        ("v2_saw_primary_finished", True),
    ]


def test_swarm_reuses_primary_visible_precheck(monkeypatch):
    specs = [
        {"id": "primary", "name": "主检测", "role": "主检测", "provider": "internal", "weight": 0.8},
        {"id": "metadata", "name": "元数据", "role": "元数据", "provider": "local", "weight": 0.2},
        {
            "id": "visible_watermark",
            "name": "平台水印",
            "role": "平台水印复核",
            "provider": "hybrid",
            "weight": 0.0,
        },
    ]
    monkeypatch.setattr(detection, "_swarm_specs", lambda include_disabled=False: specs)
    monkeypatch.setattr(detection, "_swarm_config", lambda: {"enabled": True, "minExperts": 2})
    monkeypatch.setattr(detection, "_persist_swarm_history_result", lambda *args, **kwargs: 123)
    monkeypatch.setattr(
        detection.swarm_visible_watermark_expert,
        "run_visible_watermark_expert",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("must not upload the image twice")
        ),
    )
    monkeypatch.setattr(
        detection,
        "_swarm_primary_expert",
        lambda *args, **kwargs: (
            _primary_result("shared.png"),
            {
                "status": "success",
                "score": 0.8,
                "verdict": "AI生成图像",
                "confidence": "高",
                "evidence": [],
                "message": "完成",
                "latencyMs": 1,
                "remoteEvidence": {
                    "visibleWatermarkPrecheck": {
                        "status": "ok",
                        "elapsedMs": 12,
                        "visibleHits": [],
                        "genericVisibleWatermark": {"available": True, "elapsedMs": 12},
                    }
                },
            },
        ),
    )

    payload, status = detection._run_swarm_detection_payload(
        b"image-bytes",
        "shared.png",
        "image/png",
        {"Userid": 1, "openid": "shared-test"},
    )

    assert status == 200
    visible = next(
        expert
        for expert in payload["result"]["swarm"]["experts"]
        if expert.get("id") == "visible_watermark"
    )
    assert visible["status"] == "success"
    assert visible["message"].endswith("source=shared-upload")


def test_interrupted_web_jobs_are_failed_without_touching_completed_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(admin_state, "STATE_PATH", tmp_path / "admin-state.json")
    queued = admin_state.create_detection_job({}, "queued.png", mode="fast")
    completed = admin_state.create_detection_job({}, "done.png", mode="swarm")
    admin_state.update_detection_job(completed["id"], {"status": "success", "progress": 100})

    assert admin_state.reconcile_interrupted_detection_jobs() == 1
    interrupted = admin_state.get_detection_job(queued["id"])
    assert interrupted["status"] == "failed"
    assert "重启中断" in interrupted["error"]
    assert admin_state.get_detection_job(completed["id"])["status"] == "success"
