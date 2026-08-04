import base64
import importlib.util
import json
from pathlib import Path


SERVICE_PATH = Path(__file__).resolve().parents[3] / "services" / "internal-testing" / "service.py"


def _load_service(monkeypatch):
    monkeypatch.setenv("REALGUARD_INTERNAL_TESTING_TOKEN", "bootstrap-token")
    spec = importlib.util.spec_from_file_location("realguard_internal_testing_service", SERVICE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Core:
    MAX_UPLOAD_BYTES = 24 * 1024 * 1024
    ALLOWED_LABELS = {"real", "fake", "unlabeled"}

    def __init__(self, root):
        self.DATA_ROOT = root
        self.created = None

    def ensure_schema(self):
        self.DATA_ROOT.mkdir(parents=True, exist_ok=True)

    def create_import_session(self, **kwargs):
        self.created = kwargs
        return {"id": "imp_remote", "status": "uploading"}


def _model_header(payload):
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")


def test_remote_service_requires_shared_token(monkeypatch, tmp_path):
    service = _load_service(monkeypatch)
    core = _Core(tmp_path / "testing")
    client = service.create_app(core, service_token="expected-token").test_client()

    denied = client.get("/internal/testing/health")
    accepted = client.get(
        "/internal/testing/health",
        headers={"Authorization": "Bearer expected-token"},
    )

    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert accepted.get_json()["storage"]["host"] == "10.1.20.66"


def test_remote_service_maps_public_tunnel_model_to_local_gpu(monkeypatch, tmp_path):
    service = _load_service(monkeypatch)
    core = _Core(tmp_path / "testing")
    monkeypatch.setenv(
        "REALGUARD_INTERNAL_TEST_MODEL_URL_MAP",
        json.dumps({"http://127.0.0.1:15002": "http://127.0.0.1:5071"}),
    )
    client = service.create_app(core, service_token="expected-token").test_client()
    model = {
        "id": "dinov3",
        "endpoint": "http://127.0.0.1:15002/image",
        "healthUrl": "http://127.0.0.1:15002/health",
    }

    response = client.post(
        "/api/admin/testing/dataset-imports",
        json={"name": "folder", "streamEvaluation": True, "modelId": "dinov3"},
        headers={
            "Authorization": "Bearer expected-token",
            "X-RealGuard-Actor-Id": "9",
            "X-RealGuard-Actor-Name": "operator",
            "X-RealGuard-Testing-Model": _model_header(model),
        },
    )

    assert response.status_code == 201
    assert core.created["model"]["endpoint"] == "http://127.0.0.1:5071/image"
    assert core.created["model"]["healthUrl"] == "http://127.0.0.1:5071/health"
    assert core.created["actor"]["adminId"] == 9
