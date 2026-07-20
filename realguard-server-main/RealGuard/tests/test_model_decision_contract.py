import base64
import hashlib
import json
import math
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model_decision_contract import (  # noqa: E402
    seal_inference_audit,
    validate_inference_audit,
    validate_model_decision,
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()


def _signed_decision(monkeypatch, tmp_path):
    monkeypatch.setenv("REALGUARD_MODEL_RESPONSE_HMAC_KEY", "a" * 64)
    monkeypatch.setenv("REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID", "v1")
    private_key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "calibration.pub"
    key_path.write_bytes(private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    key_path.chmod(0o600)
    monkeypatch.setenv("REALGUARD_V2_CALIBRATION_PUBLIC_KEY_FILE", str(key_path))
    monkeypatch.setenv("REALGUARD_V2_CALIBRATION_REQUIRE_ROOT_OWNERSHIP", "0")
    preprocessing = {"schema": "preprocess-v1", "implementationSha256": "d" * 64}
    contract = {
        "schema": "runtime-v1",
        "modelSha256": "c" * 64,
        "inferenceImplementationSha256": "e" * 64,
        "decisionPolicyImplementationSha256": "f" * 64,
        "runtimeLockSha256": "1" * 64,
        "classMapping": {"0": "real", "1": "fake"},
        "preprocessing": preprocessing,
        "inferenceParameters": {
            "tileSize": 256,
            "requestedMaxTiles": 16,
            "requestedTopK": 3,
            "requestedChunkSize": 1024,
            "maxAnalysisSide": 2048,
        },
    }
    manifest = {
        "schema": "cn.huijian.model-calibration-v2",
        "calibrationId": "heldout-2026-07",
        "datasetSha256": "a" * 64,
        "modelSha256": "c" * 64,
        "runtimeContract": contract,
        "runtimeContractSha256": hashlib.sha256(_canonical(contract)).hexdigest(),
        "preprocessingSha256": hashlib.sha256(_canonical(preprocessing)).hexdigest(),
        "evaluationCodeRevision": "eval-commit",
        "expiresAt": "2099-12-31T23:59:59Z",
        "realSamples": 800,
        "fakeSamples": 800,
        "observedFpr": 0.02,
        "observedFnr": 0.05,
        "aiThreshold": 0.65,
        "probabilityCalibration": {
            "method": "temperature_scaling",
            "temperature": 2.0,
            "parametersSha256": hashlib.sha256(
                _canonical({"method": "temperature_scaling", "temperature": 2.0})
            ).hexdigest(),
            "ece": 0.03,
            "brierScore": 0.12,
            "reliabilityBinCount": 15,
        },
    }
    manifest["signature"] = base64.b64encode(private_key.sign(_canonical(manifest))).decode()
    decision = {
        "ready": True,
        "mode": "calibrated_verdict",
        "calibrationManifest": manifest,
        "calibrationId": manifest["calibrationId"],
        "manifestSha256": hashlib.sha256(_canonical(manifest)).hexdigest(),
        "datasetSha256": manifest["datasetSha256"],
        "modelSha256": manifest["modelSha256"],
        "preprocessingSha256": manifest["preprocessingSha256"],
        "runtimeContractSha256": manifest["runtimeContractSha256"],
        "inferenceImplementationSha256": contract["inferenceImplementationSha256"],
        "decisionPolicyImplementationSha256": contract["decisionPolicyImplementationSha256"],
        "runtimeLockSha256": contract["runtimeLockSha256"],
        "evaluationCodeRevision": manifest["evaluationCodeRevision"],
        "expiresAt": manifest["expiresAt"],
        "realSamples": 800,
        "fakeSamples": 800,
        "observedFpr": 0.02,
        "observedFnr": 0.05,
        "aiThreshold": 0.65,
        "probabilityCalibration": manifest["probabilityCalibration"],
        "rawModelScore": 0.82,
        "publishedProbability": 1.0 / (1.0 + math.exp(-(math.log(0.82 / 0.18) / 2.0))),
        "finalLabel": "AI生成图像",
        "gateReasons": [],
    }
    audit = {
        "model": "RealGuard v2 INT8 ONNX",
        "rawModelScore": 0.82,
        "fakeProbability": decision["publishedProbability"],
        "finalLabel": "AI生成图像",
        "originalSize": {"width": 3000, "height": 2000},
        "processedSize": {"width": 2048, "height": 1365},
        "downsample": {"downsampled": True},
        "chunkCount": 4,
        "parameters": {
            "chunkSize": 1024,
            "requestedChunkSize": 1024,
            "maxTiles": 16,
            "effectiveTiles": 16,
            "tileSize": 256,
            "topK": 3,
            "usedTopK": 3,
            "maxAnalysisSide": 2048,
        },
        "runtime": {
            "activeProvider": "CUDAExecutionProvider",
            "modelRevision": "realguard-v2-test",
            "modelSha256": decision["modelSha256"],
            "runtimeContractSha256": decision["runtimeContractSha256"],
            "deploymentCommit": "abcdef123456",
        },
        "inputImageSha256": "9" * 64,
        "responseIntegrity": {
            "schema": "cn.huijian.remote-inference-response-v1",
            "requestNonce": "8" * 32,
            "imageSha256": "9" * 64,
            "issuedAt": 1784460000,
            "bodySha256": "7" * 64,
            "hmacSha256": "6" * 64,
        },
    }
    return decision, seal_inference_audit(audit, key_id="v1")


def test_signed_calibration_and_complete_inference_audit_are_required(monkeypatch, tmp_path):
    decision, audit = _signed_decision(monkeypatch, tmp_path)

    assert validate_model_decision(decision) is True
    assert validate_inference_audit(audit, decision) is True
    assert validate_inference_audit({}, decision) is False
    assert validate_inference_audit({**audit, "chunkCount": 0}, decision) is False


def test_persisted_inference_audit_rejects_tampering_and_unknown_key(monkeypatch, tmp_path):
    decision, audit = _signed_decision(monkeypatch, tmp_path)

    assert validate_inference_audit({**audit, "finalLabel": "真实图像"}, decision) is False
    tampered_integrity = dict(audit["persistedAuditIntegrity"])
    tampered_integrity["keyId"] = "retired"
    assert validate_inference_audit(
        {**audit, "persistedAuditIntegrity": tampered_integrity},
        decision,
    ) is False


def test_tampered_calibration_envelope_fails_closed(monkeypatch, tmp_path):
    decision, _audit = _signed_decision(monkeypatch, tmp_path)
    decision["calibrationManifest"]["fakeSamples"] = 9999

    assert validate_model_decision(decision) is False


def test_unsigned_per_run_fields_must_remain_self_consistent(monkeypatch, tmp_path):
    decision, audit = _signed_decision(monkeypatch, tmp_path)

    decision["finalLabel"] = "真实图像"
    audit["finalLabel"] = "真实图像"

    assert validate_model_decision(decision) is False
    assert validate_inference_audit(audit, decision) is False


def test_audit_parameters_must_match_signed_runtime_contract(monkeypatch, tmp_path):
    decision, audit = _signed_decision(monkeypatch, tmp_path)
    audit["parameters"]["requestedChunkSize"] = 512

    assert validate_inference_audit(audit, decision) is False
