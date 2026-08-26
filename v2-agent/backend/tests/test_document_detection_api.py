from __future__ import annotations

import hashlib
import importlib
import io
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def document_client(monkeypatch, tmp_path):
    monkeypatch.setenv("JIANZHEN_ENV", "test")
    monkeypatch.setenv("JIANZHEN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JIANZHEN_DOCUMENT_JOB_DIR", str(tmp_path / "document-jobs"))
    monkeypatch.setenv("JIANZHEN_ALLOW_ANONYMOUS_DETECT", "true")
    monkeypatch.setenv("JIANZHEN_DOCUMENT_ROUTER_MODEL_DIR", str(tmp_path / "missing-router-model"))
    monkeypatch.setenv("JIANZHEN_CONSENT_AUDIT_SALT", "document-consent-audit-secret-32")
    monkeypatch.setenv("JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY", "base64:AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
    for module_name in (
        "app.storage",
        "app.document_jobs",
        "app.document_router_semantic",
        "app.document_router",
        "app.main",
    ):
        sys.modules.pop(module_name, None)
    import app.storage as storage  # noqa: WPS433
    import app.document_jobs as document_jobs  # noqa: WPS433
    import app.main as main  # noqa: WPS433

    importlib.reload(storage)
    importlib.reload(document_jobs)
    importlib.reload(main)
    from app.document_router_semantic import TinyClipSemanticClassifier

    missing_router = TinyClipSemanticClassifier(tmp_path / "missing-router-model")
    monkeypatch.setattr(main.document_router, "default_semantic_classifier", lambda: missing_router)
    monkeypatch.setattr(main, "_session_auth_reachable", lambda: True)
    with TestClient(main.app, client=("127.0.0.1", 50100)) as client:
        yield main, client


def _consent() -> dict[str, str]:
    return {
        "upload_consent": "1",
        "consent_version": "2026-08-07+2026-08-08.1",
        "terms_sha256": "619aee74677629f4f5e2c4ccbaa99c458671086de45c0a586e76c8c8c062d2c5",
        "privacy_sha256": "e2dd0904fbbccef7df74168ede051da7a93029f00b072d0a5f1bd41b7ebf826c",
    }


def _png(array: np.ndarray) -> bytes:
    output = io.BytesIO()
    Image.fromarray(array.astype(np.uint8), mode="RGB").save(output, "PNG")
    return output.getvalue()


def test_document_router_preview_does_not_call_detection_model(document_client, monkeypatch):
    main, client = document_client
    from app.document_images import DocumentExtraction, DocumentImageAsset

    uniform = _png(np.full((180, 240, 3), 150, dtype=np.uint8))
    random = np.random.default_rng(7)
    photo = _png(random.integers(0, 256, size=(240, 360, 3), dtype=np.uint8))
    extraction = DocumentExtraction(
        filename="router.pdf",
        page_count=1,
        warnings=[],
        assets=[
            DocumentImageAsset(1, uniform, "image/png", 240, 180, hashlib.sha256(uniform).hexdigest(), "pdf_embedded", 1, None, 1, None),
            DocumentImageAsset(2, photo, "image/png", 360, 240, hashlib.sha256(photo).hexdigest(), "pdf_embedded", 1, None, 2, None),
        ],
    )
    monkeypatch.setattr(main.document_images, "extract_document_images", lambda _name, _data: extraction)

    async def forbidden_model_call(*_args, **_kwargs):
        raise AssertionError("Router preview must not call the detection model")

    monkeypatch.setattr(main, "_analyze_document_asset", forbidden_model_call)
    response = client.post(
        "/api/document-router/preview",
        files={"file": ("router.pdf", b"%PDF-router-fixture", "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {
        "extracted": 2,
        "detect": 1,
        "skip": 1,
        "uncertain": 0,
        "recommendedModelCalls": 1,
        "modelCallsAvoided": 1,
    }
    assert [item["router"]["route"] for item in payload["assets"]] == ["skip", "detect"]
    assert all(item["preview"].startswith("data:image/") for item in payload["assets"])


def test_document_task_skips_high_confidence_router_objects(document_client, monkeypatch):
    main, client = document_client
    from app.document_images import DocumentExtraction, DocumentImageAsset

    uniform = _png(np.full((180, 240, 3), 150, dtype=np.uint8))
    extraction = DocumentExtraction(
        filename="background.docx",
        page_count=None,
        warnings=[],
        assets=[
            DocumentImageAsset(1, uniform, "image/png", 240, 180, hashlib.sha256(uniform).hexdigest(), "docx_body", None, "word/document.xml", 1, None),
        ],
    )
    monkeypatch.setattr(main.document_images, "extract_document_images", lambda _name, _data: extraction)

    async def forbidden_model_call(*_args, **_kwargs):
        raise AssertionError("High-confidence skipped objects must not call the model")

    monkeypatch.setattr(main, "_analyze_document_asset", forbidden_model_call)
    response = client.post(
        "/api/document-detections",
        data=_consent(),
        files={"file": ("background.docx", b"PK\x03\x04fixture", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers={"Idempotency-Key": "document-router-skip-001"},
    )
    assert response.status_code == 202
    task = response.json()
    token = task["accessToken"]
    deadline = time.monotonic() + 3
    while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
        task = client.get(
            f"/api/document-detections/{task['id']}?wait=1&assetLimit=100",
            headers={"X-Document-Task-Token": token},
        ).json()

    assert task["status"] == "completed"
    assert task["completed"] == 1
    assert task["succeeded"] == 0
    assert task["failed"] == 0
    assert task["skipped"] == 1
    assert task["routerSummary"]["modelCallsAvoided"] == 1
    assert task["assets"][0]["status"] == "skipped"
    assert task["assets"][0]["router"]["category"] == "uniform_layer"


def test_document_detection_runs_as_owned_parent_task(document_client, monkeypatch):
    main, client = document_client
    from app.document_images import DocumentExtraction, DocumentImageAsset

    first = b"first-image"
    second = b"second-image"
    extraction = DocumentExtraction(
        filename="evidence.docx",
        page_count=3,
        warnings=["页眉图片已包含"],
        assets=[
            DocumentImageAsset(1, first, "image/png", 32, 24, hashlib.sha256(first).hexdigest(), "docx_body", None, "word/document.xml", 1, None),
            DocumentImageAsset(2, second, "image/jpeg", 40, 30, hashlib.sha256(second).hexdigest(), "docx_header", None, "word/header1.xml", 1, None),
        ],
    )
    monkeypatch.setattr(main.document_images, "extract_document_images", lambda _name, _data: extraction)

    async def fake_analyze(_task_id, filename, _data, _actor):
        fake = filename.endswith("0002.jpg")
        return {
            "verdict": "highly_suspected_fake" if fake else "real",
            "verdictLabel": "AI生成图像" if fake else "真实图像",
            "confidence": 0.91 if fake else 0.12,
            "aiProbability": 0.91 if fake else 0.12,
            "modelVersion": "pytest-document-model",
            "source": "vlm",
            "explanation": "pytest child result",
            "regions": [],
            "visibleWatermark": None,
            "synthid": None,
            "elapsedMs": 5,
        }

    monkeypatch.setattr(main, "_analyze_document_asset", fake_analyze)
    response = client.post(
        "/api/document-detections",
        data={**_consent(), "mode": "swarm"},
        files={"file": ("evidence.docx", b"PK\x03\x04fixture", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers={"Idempotency-Key": "doc-parent-task-001"},
    )

    assert response.status_code == 202
    created = response.json()
    token = created["accessToken"]
    task_id = created["id"]
    forbidden = client.get(f"/api/document-detections/{task_id}", headers={"X-Document-Task-Token": "wrong"})
    assert forbidden.status_code == 404

    deadline = time.monotonic() + 3
    task = created
    while task["status"] in {"queued", "running"} and time.monotonic() < deadline:
        polled = client.get(
            f"/api/document-detections/{task_id}?wait=1&assetLimit=100",
            headers={"X-Document-Task-Token": token},
        )
        assert polled.status_code == 200
        task = polled.json()

    assert task["status"] == "completed"
    assert task["mode"] == "fast"
    assert task["pageCount"] == 3
    assert task["discovered"] == 2
    assert task["succeeded"] == 2
    assert task["summary"]["fakeCount"] == 1
    assert task["summary"]["realCount"] == 1
    assert [asset["sourceKind"] for asset in task["assets"]] == ["docx_body", "docx_header"]
    stored = main.document_jobs.get(task_id)
    assert stored is not None
    assert not Path(stored["_sourcePath"]).exists()
    assert polled.headers["cache-control"] == "private, no-store, max-age=0"

    replay = client.post(
        "/api/document-detections",
        data={**_consent(), "mode": "fast"},
        files={"file": ("evidence.docx", b"PK\x03\x04fixture", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers={"Idempotency-Key": "doc-parent-task-001"},
    )
    assert replay.status_code == 202
    assert replay.json()["id"] == task_id
    assert replay.json()["idempotentReplay"] is True


def test_document_detection_rejects_legacy_word(document_client):
    _main, client = document_client
    response = client.post(
        "/api/document-detections",
        data=_consent(),
        files={"file": ("legacy.doc", b"legacy-word", "application/msword")},
        headers={"Idempotency-Key": "doc-parent-task-002"},
    )
    assert response.status_code == 415
    assert ".docx" in response.json()["detail"]


def test_owned_document_task_cannot_be_opened_by_another_account(document_client):
    main, _client = document_client
    owner = "11111111-1111-4111-8111-111111111111"
    other = "22222222-2222-4222-8222-222222222222"
    task, token, created = main.document_jobs.create(
        filename="owned.pdf",
        mime="application/pdf",
        size=4,
        sha256=hashlib.sha256(b"test").hexdigest(),
        mode="fast",
        actor={"mode": "session", "accountUuid": owner},
        source=b"test",
        owner_key=f"account:{owner}",
        idempotency_key="owned-parent-task-001",
        token_secret="x" * 40,
        max_active=4,
        max_owner_active=1,
    )

    assert created is True
    assert main.document_jobs.is_authorized(task, {"mode": "session", "accountUuid": owner})
    assert not main.document_jobs.is_authorized(
        task,
        {"mode": "session", "accountUuid": other},
        token,
    )
    assert main.document_jobs.is_authorized(task, {"mode": "admin"})


def test_document_idempotency_rejects_different_payload(document_client, monkeypatch):
    main, client = document_client
    from app.document_images import DocumentExtraction

    monkeypatch.setattr(
        main.document_images,
        "extract_document_images",
        lambda name, _data: DocumentExtraction(name, None, [], []),
    )
    first = client.post(
        "/api/document-detections",
        data=_consent(),
        files={"file": ("first.docx", b"PK\x03\x04first", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers={"Idempotency-Key": "doc-conflict-key-001"},
    )
    second = client.post(
        "/api/document-detections",
        data=_consent(),
        files={"file": ("second.docx", b"PK\x03\x04second", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers={"Idempotency-Key": "doc-conflict-key-001"},
    )

    assert first.status_code == 202
    assert second.status_code == 409


def test_document_job_store_enforces_owner_capacity(document_client):
    main, _client = document_client
    kwargs = {
        "mime": "application/pdf",
        "size": 4,
        "mode": "fast",
        "actor": {"mode": "session", "accountUuid": "33333333-3333-4333-8333-333333333333"},
        "owner_key": "account:33333333-3333-4333-8333-333333333333",
        "token_secret": "y" * 40,
        "max_active": 4,
        "max_owner_active": 1,
    }
    main.document_jobs.create(
        **kwargs,
        filename="one.pdf",
        sha256=hashlib.sha256(b"one!").hexdigest(),
        source=b"one!",
        idempotency_key="capacity-task-one",
    )

    with pytest.raises(main.document_jobs.DocumentJobCapacityError):
        main.document_jobs.create(
            **kwargs,
            filename="two.pdf",
            sha256=hashlib.sha256(b"two!").hexdigest(),
            source=b"two!",
            idempotency_key="capacity-task-two",
        )
