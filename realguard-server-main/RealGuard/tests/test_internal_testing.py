from pathlib import Path
import io
import sys
import time
import zipfile

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


def test_dataset_ingestion_has_no_sample_count_cap():
    uploads = [
        (f"dataset/real/sample-{index}.png", _png_bytes((index % 255, index // 255, 90)))
        for index in range(205)
    ]

    dataset = internal_testing.create_dataset(uploads, default_label="unlabeled")

    assert dataset["sample_count"] == 205
    assert dataset["labeled_count"] == 205
    assert dataset["classification"]["automaticCount"] == 205


def test_directory_structure_infers_labels_at_different_depths():
    dataset = internal_testing.create_dataset(
        [
            ("set-a/train/authentic/camera.png", _png_bytes((20, 80, 140))),
            ("set-a/generated/sdxl.png", _png_bytes((180, 40, 90))),
            ("set-a/validation/实拍/phone.png", _png_bytes((40, 140, 60))),
            ("set-a/misc/unknown.png", _png_bytes((100, 100, 100))),
        ],
        default_label="unlabeled",
    )
    by_path = {item["relative_path"]: item for item in dataset["samples"]}

    assert by_path["set-a/train/authentic/camera.png"]["ground_truth"] == "real"
    assert by_path["set-a/generated/sdxl.png"]["ground_truth"] == "fake"
    assert by_path["set-a/validation/实拍/phone.png"]["ground_truth"] == "real"
    assert by_path["set-a/misc/unknown.png"]["ground_truth"] == "unlabeled"
    assert dataset["classification"]["automaticCount"] == 3
    assert dataset["classification"]["unresolvedCount"] == 1


def test_fraudbench_profile_preserves_semantic_subclasses():
    dataset = internal_testing.create_dataset(
        [
            ("FraudBench/Electronics/Positive/Review_004/Image_004_01.jpg", _png_bytes((20, 80, 140))),
            ("FraudBench/Electronics/Negative/Review_007/Image_007_02.jpg", _png_bytes((80, 140, 20))),
            ("FraudBench/Electronics/DeepFake/gpt-image-2/Review_004/Image_004_01.jpg", _png_bytes((180, 40, 90))),
        ],
        default_label="unlabeled",
    )
    by_path = {item["relative_path"]: item for item in dataset["samples"]}

    assert dataset["profile_name"] == "fraudbench"
    assert dataset["taxonomy"]["dimensions"]["domain"] == {"Electronics": 3}
    positive = by_path["FraudBench/Electronics/Positive/Review_004/Image_004_01.jpg"]
    negative = by_path["FraudBench/Electronics/Negative/Review_007/Image_007_02.jpg"]
    generated = by_path["FraudBench/Electronics/DeepFake/gpt-image-2/Review_004/Image_004_01.jpg"]
    assert positive["ground_truth"] == negative["ground_truth"] == "real"
    assert generated["ground_truth"] == "fake"
    assert generated["subclasses"]["generator"] == "gpt-image-2"
    assert generated["group_id"] == positive["group_id"]
    assert negative["group_id"] != positive["group_id"]


def test_rrdataset_profile_tracks_transform_and_groups_variants():
    dataset = internal_testing.create_dataset(
        [
            ("RRDataset_final/original/real/real_000021.jpg", _png_bytes((20, 80, 140))),
            ("RRDataset_final/redigital/real/redigital_real_000021.jpg", _png_bytes((40, 100, 180))),
            ("RRDataset_final/transfer/ai/transfer_ai_000021.jpg", _png_bytes((180, 50, 20))),
        ]
    )
    samples = dataset["samples"]
    by_path = {item["relative_path"]: item for item in samples}

    assert dataset["profile_name"] == "rrdataset"
    assert dataset["taxonomy"]["dimensions"]["transform"] == {
        "original": 1, "redigital": 1, "transfer": 1,
    }
    original = by_path["RRDataset_final/original/real/real_000021.jpg"]
    redigital = by_path["RRDataset_final/redigital/real/redigital_real_000021.jpg"]
    generated = by_path["RRDataset_final/transfer/ai/transfer_ai_000021.jpg"]
    assert original["ground_truth"] == redigital["ground_truth"] == "real"
    assert generated["ground_truth"] == "fake"
    assert original["group_id"] == redigital["group_id"]
    assert generated["group_id"] != original["group_id"]


def test_positive_and_negative_are_not_generic_truth_keywords():
    assert internal_testing._path_label("benchmark/Positive/a.png")[0] == "unlabeled"
    assert internal_testing._path_label("benchmark/Negative/b.png")[0] == "unlabeled"


def test_manual_label_overrides_directory_inference():
    dataset = internal_testing.create_dataset(
        [("dataset/real/photo.png", _png_bytes())],
        default_label="unlabeled",
    )

    updated = internal_testing.update_sample_label(dataset["samples"][0]["id"], "fake")

    assert updated["ground_truth"] == "fake"
    assert updated["label_source"] == "manual"


def test_zip_dataset_preserves_paths_and_infers_labels():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("benchmark/real/photo.png", _png_bytes((20, 100, 180)))
        output.writestr("benchmark/fake/render.png", _png_bytes((180, 50, 20)))

    dataset = internal_testing.create_dataset([("benchmark.zip", archive.getvalue())])
    labels = {item["relative_path"]: item["ground_truth"] for item in dataset["samples"]}

    assert labels == {
        "benchmark/fake/render.png": "fake",
        "benchmark/real/photo.png": "real",
    }


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


def test_evaluation_metrics_include_subclass_breakdown():
    metrics = internal_testing._evaluation_metrics([
        {"ok": True, "groundTruth": "real", "predictedLabel": "real", "latencyMs": 100, "subclasses": {"transform": "original"}},
        {"ok": True, "groundTruth": "fake", "predictedLabel": "real", "latencyMs": 120, "subclasses": {"transform": "transfer"}},
    ])
    grouped = {(item["dimension"], item["value"]): item for item in metrics["groupMetrics"]}

    assert grouped[("transform", "original")]["accuracy"] == 1
    assert grouped[("transform", "transfer")]["accuracy"] == 0


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
    assert completed["completed_count"] == completed["total_count"] == 2
    assert completed["resultSummary"] == {
        "count": 2,
        "successCount": 2,
        "failureCount": 0,
        "returnedCount": 2,
        "hasMore": False,
    }


def test_model_non_json_response_is_a_diagnostic_failure(monkeypatch):
    class Response:
        status_code = 413
        headers = {"Content-Type": "text/html"}
        text = "<html><h1>Request Entity Too Large</h1></html>"

        def json(self):
            raise ValueError("not json")

    class Session:
        trust_env = True

        def post(self, *_args, **_kwargs):
            return Response()

        def close(self):
            return None

    monkeypatch.setattr(internal_testing.requests, "Session", Session)

    result = internal_testing.run_model(
        {"endpoint": "http://127.0.0.1:9000/image"},
        _png_bytes(),
        "sample.png",
        "image/png",
    )

    assert result["ok"] is False
    assert result["httpStatus"] == 413
    assert "请求体过大" in result["error"]
    assert result["payload"]["responseFormat"] == "non_json"


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


def test_admin_dataset_upload_preserves_relative_filename(client):
    _login(client, "operator")
    created = client.post(
        "/api/admin/testing/datasets",
        data={
            "defaultLabel": "unlabeled",
            "files": (io.BytesIO(_png_bytes()), "benchmark/train/fake/render.png"),
        },
        headers=_csrf(client),
        content_type="multipart/form-data",
    )

    assert created.status_code == 201
    sample = created.get_json()["dataset"]["samples"][0]
    assert sample["relative_path"] == "benchmark/train/fake/render.png"
    assert sample["ground_truth"] == "fake"


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
    assert 'id="testingDirectory"' in html
    assert "非 JSON 响应" not in html


def test_internal_testing_has_a_cache_busting_direct_entry(client):
    _login(client, "operator")
    response = client.get("/admin/testing")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'const SERVER_INITIAL_ROUTE = "testing";' in html
    assert 'href="/admin/testing"' in html


def test_internal_testing_direct_entry_requires_admin_login(client):
    response = client.get("/admin/testing")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")
