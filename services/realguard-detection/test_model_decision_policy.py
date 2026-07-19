import base64
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from model_decision_policy import MANIFEST_SCHEMA, model_decision_policy


MODEL_SHA256 = "f" * 64
RUNTIME_CONTRACT = {
    "schema": "cn.huijian.runtime-inference-contract-v1",
    "modelSha256": MODEL_SHA256,
    "inferenceImplementationSha256": "c" * 64,
    "decisionPolicyImplementationSha256": "e" * 64,
    "runtimeLockSha256": "d" * 64,
    "classMapping": {"0": "real", "1": "fake"},
    "preprocessing": {
        "schema": "cn.huijian.image-preprocessing-v1",
        "implementationSha256": "b" * 64,
        "colorMode": "RGB",
    },
    "inferenceParameters": {
        "requestedMaxTiles": 16,
        "requestedTopK": 3,
        "requestedChunkSize": 1024,
    },
}


def _write_manifest(monkeypatch, tmp_path, **overrides):
    private_key = Ed25519PrivateKey.generate()
    runtime_contract = overrides.pop("runtimeContract", RUNTIME_CONTRACT)
    contract_bytes = json.dumps(
        runtime_contract,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    preprocessing_bytes = json.dumps(
        runtime_contract["preprocessing"],
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "calibrationId": "heldout-2026-07",
        "modelSha256": MODEL_SHA256,
        "datasetSha256": "a" * 64,
        "evaluationCodeRevision": "eval-commit-abc123",
        "runtimeContract": runtime_contract,
        "runtimeContractSha256": hashlib.sha256(contract_bytes).hexdigest(),
        "preprocessingSha256": hashlib.sha256(preprocessing_bytes).hexdigest(),
        "expiresAt": "2099-12-31T23:59:59Z",
        "realSamples": 800,
        "fakeSamples": 700,
        "observedFpr": 0.025,
        "observedFnr": 0.08,
        "aiThreshold": 0.83,
        "probabilityCalibration": {
            "method": "temperature_scaling",
            "temperature": 1.7,
            "parametersSha256": hashlib.sha256(
                json.dumps(
                    {"method": "temperature_scaling", "temperature": 1.7},
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            "ece": 0.03,
            "brierScore": 0.12,
            "reliabilityBinCount": 15,
        },
    }
    manifest.update(overrides)
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    manifest["signature"] = base64.b64encode(private_key.sign(canonical)).decode("ascii")
    manifest_path = tmp_path / "calibration.json"
    key_path = tmp_path / "calibration-ed25519.pub"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    key_path.write_bytes(
        private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    manifest_path.chmod(0o600)
    key_path.chmod(0o644)
    monkeypatch.setenv("REALGUARD_V2_CALIBRATION_MANIFEST", str(manifest_path))
    monkeypatch.setenv("REALGUARD_V2_CALIBRATION_PUBLIC_KEY_FILE", str(key_path))
    monkeypatch.setenv("REALGUARD_V2_CALIBRATION_REQUIRE_ROOT_OWNERSHIP", "0")
    return manifest_path


def test_model_decision_defaults_to_review_only(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "REALGUARD_V2_CALIBRATION_MANIFEST", str(tmp_path / "missing.json")
    )
    monkeypatch.setenv(
        "REALGUARD_V2_CALIBRATION_PUBLIC_KEY_FILE", str(tmp_path / "missing.pub")
    )

    policy = model_decision_policy(
        model_sha256=MODEL_SHA256,
        runtime_contract=RUNTIME_CONTRACT,
    )

    assert policy["ready"] is False
    assert policy["mode"] == "review_only"
    assert "calibration_manifest_missing" in policy["gateReasons"]
    assert "calibration_public_key_missing" in policy["gateReasons"]


def test_model_decision_requires_signed_model_bound_calibration_record(monkeypatch, tmp_path):
    _write_manifest(monkeypatch, tmp_path)

    policy = model_decision_policy(
        model_sha256=MODEL_SHA256,
        runtime_contract=RUNTIME_CONTRACT,
    )

    assert policy["ready"] is True
    assert policy["mode"] == "calibrated_verdict"
    assert policy["aiThreshold"] == 0.83
    assert policy["modelSha256"] == MODEL_SHA256
    assert policy["inferenceImplementationSha256"] == "c" * 64
    assert policy["decisionPolicyImplementationSha256"] == "e" * 64
    assert policy["runtimeLockSha256"] == "d" * 64
    assert policy["probabilityCalibration"]["method"] == "temperature_scaling"
    assert policy["calibrationManifest"]["signature"]
    assert policy["manifestSha256"]
    assert policy["gateReasons"] == []


def test_model_decision_rejects_failed_false_positive_gate(monkeypatch, tmp_path):
    _write_manifest(monkeypatch, tmp_path, observedFpr=0.25)

    policy = model_decision_policy(
        model_sha256=MODEL_SHA256,
        runtime_contract=RUNTIME_CONTRACT,
    )

    assert policy["ready"] is False
    assert "false_positive_rate_above_gate" in policy["gateReasons"]


def test_model_decision_rejects_missing_probability_calibration(monkeypatch, tmp_path):
    _write_manifest(monkeypatch, tmp_path, probabilityCalibration=None)

    policy = model_decision_policy(
        model_sha256=MODEL_SHA256,
        runtime_contract=RUNTIME_CONTRACT,
    )

    assert policy["ready"] is False
    assert "probability_calibration_missing" in policy["gateReasons"]


def test_model_decision_rejects_tampered_or_wrong_model_manifest(monkeypatch, tmp_path):
    manifest_path = _write_manifest(monkeypatch, tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["fakeSamples"] = 9999
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    tampered = model_decision_policy(
        model_sha256=MODEL_SHA256,
        runtime_contract=RUNTIME_CONTRACT,
    )

    assert tampered["ready"] is False
    assert "calibration_signature_invalid" in tampered["gateReasons"]

    _write_manifest(monkeypatch, tmp_path, modelSha256="b" * 64)
    wrong_model = model_decision_policy(
        model_sha256=MODEL_SHA256,
        runtime_contract=RUNTIME_CONTRACT,
    )
    assert wrong_model["ready"] is False
    assert "calibration_model_mismatch" in wrong_model["gateReasons"]


def test_model_decision_rejects_runtime_contract_drift(monkeypatch, tmp_path):
    _write_manifest(monkeypatch, tmp_path)
    changed = json.loads(json.dumps(RUNTIME_CONTRACT))
    changed["inferenceParameters"]["requestedTopK"] = 5

    policy = model_decision_policy(
        model_sha256=MODEL_SHA256,
        runtime_contract=changed,
    )

    assert policy["ready"] is False
    assert "runtime_contract_mismatch" in policy["gateReasons"]


def test_model_decision_rejects_expired_calibration(monkeypatch, tmp_path):
    _write_manifest(monkeypatch, tmp_path, expiresAt="2020-01-01T00:00:00Z")

    policy = model_decision_policy(
        model_sha256=MODEL_SHA256,
        runtime_contract=RUNTIME_CONTRACT,
    )

    assert policy["ready"] is False
    assert "calibration_expired" in policy["gateReasons"]
