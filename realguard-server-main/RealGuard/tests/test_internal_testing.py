from pathlib import Path
import io
import sys
import time

from PIL import Image
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection import creat_app  # noqa: E402
from imagedetection.views import admin, internal_testing  # noqa: E402


def _png_bytes(color=(34, 139, 94)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (96, 80), color).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture(autouse=True)
def isolated_testing_store(monkeypatch, tmp_path):
    root = tmp_path / "internal-testing"
    monkeypatch.setattr(internal_testing, "DATA_ROOT", root)
    monkeypatch.setattr(internal_testing, "DB_PATH", root / "testing.sqlite3")
    monkeypatch.setattr(internal_testing, "_SCHEMA_READY", False)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(admin, "_refresh_admin_session", lambda user: user)
    app = creat_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _login(client, role="operator"):
    with client.session_transaction() as session:
        session["user_info"] = {
            "Userid": 1,
            "username": "tester",
            "phone": "13800000000",
            "openid": "openid-1",
        }
        session[admin.ADMIN_SESSION_KEY] = {
            "Userid": "admin:1",
            "adminId": 1,
            "username": "root",
            "phone": "13800000000",
            "role": role,
            "authType": "admin_account",
            "sessionVersion": 1,
            "issuedAt": int(time.time()),
        }


def _csrf(client):
    token = "internal-testing-csrf"
    with client.session_transaction() as session:
        session[admin.ADMIN_CSRF_SESSION_KEY] = token
    return {"X-CSRF-Token": token}


def test_create_dataset_deduplicates_and_persists_labels():
    payload = _png_bytes()
    dataset = internal_testing.create_dataset(
        [("real-a.png", payload), ("duplicate.png", payload)],
        name="camera baseline",
        default_label="real",
        actor={"adminId": 3, "username": "operator"},
    )

    assert dataset["name"] == "camera baseline"
    assert dataset["sample_count"] == 1
    assert dataset["labeled_count"] == 1
    assert dataset["samples"][0]["ground_truth"] == "real"
    assert internal_testing.sample_path(dataset["samples"][0]["id"])[0].is_file()


def test_evaluation_metrics_use_only_labeled_valid_predictions():
    metrics = internal_testing._evaluation_metrics([
        {"ok": True, "groundTruth": "fake", "predictedLabel": "fake", "latencyMs": 100},
        {"ok": True, "groundTruth": "real", "predictedLabel": "fake", "latencyMs": 200},
        {"ok": True, "groundTruth": "real", "predictedLabel": "real", "latencyMs": 300},
        {"ok": False, "groundTruth": "fake", "predictedLabel": "unknown", "latencyMs": 50},
        {"ok": True, "groundTruth": "unlabeled", "predictedLabel": "fake", "latencyMs": 400},
    ])

    assert metrics["confusionMatrix"] == {"tp": 1, "tn": 1, "fp": 1, "fn": 0}
    assert metrics["labeledCount"] == 3
    assert metrics["accuracy"] == pytest.approx(2 / 3)
    assert metrics["precision"] == pytest.approx(0.5)
    assert metrics["recall"] == pytest.approx(1)
    assert metrics["f1"] == pytest.approx(2 / 3)
    assert metrics["latency"]["p95Ms"] == 400


def test_evaluation_run_persists_reproducible_metrics(monkeypatch):
    dataset = internal_testing.create_dataset(
        [
            ("fake.png", _png_bytes((220, 30, 40))),
            ("real.png", _png_bytes((30, 120, 220))),
        ],
        labels={"fake": "fake", "real": "real"},
    )

    def fake_model(_model, _image, filename, _mime_type):
        predicted = "fake" if "fake" in filename else "real"
        return {
            "ok": True,
            "httpStatus": 200,
            "latencyMs": 120,
            "predictedLabel": predicted,
            "score": 0.9 if predicted == "fake" else 0.1,
            "payload": {"label": predicted},
            "error": "",
        }

    monkeypatch.setattr(internal_testing, "run_model", fake_model)
    run = internal_testing.create_evaluation(
        dataset["id"],
        {
            "id": "model-a",
            "name": "Model A",
            "version": "2026.07",
            "runtime": "pytest",
            "endpoint": "http://127.0.0.1:9000/image",
        },
        concurrency=2,
    )
    deadline = time.time() + 3
    while time.time() < deadline:
        completed = internal_testing.get_run(run["id"])
        if completed["status"] == "completed":
            break
        time.sleep(0.02)

    assert completed["status"] == "completed"
    assert completed["metrics"]["accuracy"] == 1
    assert completed["metrics"]["f1"] == 1
    assert completed["configuration"]["modelSnapshot"]["version"] == "2026.07"
    assert len(completed["configuration"]["modelSnapshot"]["endpointSha256"]) == 64
    assert len(completed["results"]) == 2


def test_web_ingestion_rejects_private_network(monkeypatch):
    monkeypatch.setattr(
        internal_testing.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )

    with pytest.raises(ValueError, match="内网"):
        internal_testing._public_https_url("https://private.example/report")


def test_overview_marks_orphaned_background_run_failed():
    dataset = internal_testing.create_dataset(
        [("sample.png", _png_bytes())],
        name="stale task",
    )
    old_timestamp = "2020-01-01T00:00:00+00:00"
    with internal_testing._connect() as connection:
        connection.execute(
            """
            INSERT INTO runs
                (id,kind,dataset_id,model_id,status,configuration_json,
                 created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                "eval_stale",
                "evaluation",
                dataset["id"],
                "model-a",
                "running",
                "{}",
                old_timestamp,
                old_timestamp,
            ),
        )
        connection.commit()

    overview = internal_testing.overview()
    stale = next(run for run in overview["runs"] if run["id"] == "eval_stale")

    assert stale["status"] == "failed"
    assert "服务重启" in stale["error"]
    assert stale["finished_at"]


def test_operator_can_use_admin_testing_api(client, monkeypatch):
    _login(client, "operator")
    monkeypatch.setattr(admin, "_testing_system_snapshot", lambda: {
        "host": {"status": "ok"},
        "algorithmServer": {"serviceReady": True},
        "services": {"online": 1, "total": 1},
        "models": [],
    })

    created = client.post(
        "/api/admin/testing/datasets",
        data={
            "name": "operator samples",
            "defaultLabel": "fake",
            "files": (io.BytesIO(_png_bytes()), "generated.png"),
        },
        headers=_csrf(client),
        content_type="multipart/form-data",
    )
    overview = client.get("/api/admin/testing/overview")

    assert created.status_code == 201
    assert created.get_json()["dataset"]["sample_count"] == 1
    assert overview.status_code == 200
    assert overview.get_json()["summary"]["datasetCount"] == 1


def test_reviewer_can_view_but_cannot_mutate_testing_data(client, monkeypatch):
    _login(client, "reviewer")
    monkeypatch.setattr(internal_testing, "overview", lambda: {
        "datasets": [], "runs": [], "summary": {}, "limits": {},
    })
    monkeypatch.setattr(admin, "_testing_system_snapshot", lambda: {})

    overview = client.get("/api/admin/testing/overview")
    create = client.post(
        "/api/admin/testing/datasets",
        data={"files": (io.BytesIO(_png_bytes()), "sample.png")},
        headers=_csrf(client),
        content_type="multipart/form-data",
    )

    assert overview.status_code == 200
    assert create.status_code == 403
    assert "testing.run" in create.get_json()["message"]


def test_load_test_requires_explicit_confirmation(client):
    _login(client, "operator")
    response = client.post(
        "/api/admin/testing/load-tests",
        data={
            "modelId": "primary",
            "file": (io.BytesIO(_png_bytes()), "sample.png"),
        },
        headers=_csrf(client),
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "确认" in response.get_json()["message"]


def test_admin_page_contains_internal_testing_workspace(client):
    _login(client, "operator")
    response = client.get("/admin")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "内部测试平台" in html
    assert 'id="view-testing"' in html
    assert "受控压力测试" in html
