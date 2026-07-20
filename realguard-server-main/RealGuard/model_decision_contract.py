"""Verify signed calibration decisions and per-inference audit evidence."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key


MANIFEST_SCHEMA = "cn.huijian.model-calibration-v2"
PERSISTED_AUDIT_SCHEMA = "cn.huijian.persisted-inference-audit-v1"
MAX_MANIFEST_BYTES = 64 * 1024
HASH_FIELDS = (
    "datasetSha256",
    "manifestSha256",
    "modelSha256",
    "preprocessingSha256",
    "runtimeContractSha256",
    "inferenceImplementationSha256",
    "decisionPolicyImplementationSha256",
    "runtimeLockSha256",
)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _valid_key_id(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and value[0].isalnum()
        and all(char.isalnum() or char in "._:-" for char in value)
    )


def _response_hmac_keyring() -> dict[str, bytes]:
    raw = os.environ.get("REALGUARD_MODEL_RESPONSE_HMAC_KEYS_JSON", "{}").strip()
    try:
        decoded = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    candidates = dict(decoded)
    active_key = os.environ.get("REALGUARD_MODEL_RESPONSE_HMAC_KEY", "").strip().lower()
    active_key_id = os.environ.get("REALGUARD_MODEL_RESPONSE_HMAC_KEY_ID", "v1").strip()
    if active_key:
        candidates[active_key_id] = active_key
    keyring = {}
    for raw_key_id, raw_key in candidates.items():
        key_id = str(raw_key_id or "").strip()
        key = str(raw_key or "").strip().lower()
        if (
            _valid_key_id(key_id)
            and len(key) == 64
            and all(char in "0123456789abcdef" for char in key)
        ):
            keyring[key_id] = bytes.fromhex(key)
    return keyring


def seal_inference_audit(
    audit: Mapping[str, Any],
    *,
    key_id: str,
    key_hex: str | None = None,
) -> dict[str, Any]:
    """Seal the exact audit object persisted by the Web tier."""
    if not isinstance(audit, Mapping) or not _valid_key_id(key_id):
        raise ValueError("inference audit cannot be sealed")
    supplied_key = str(key_hex or "").strip().lower()
    key = (
        bytes.fromhex(supplied_key)
        if len(supplied_key) == 64 and all(char in "0123456789abcdef" for char in supplied_key)
        else _response_hmac_keyring().get(key_id)
    )
    if key is None:
        raise ValueError("inference audit sealing key is unavailable")
    sealed = dict(audit)
    sealed.pop("persistedAuditIntegrity", None)
    body_sha256 = hashlib.sha256(_canonical_json(sealed)).hexdigest()
    envelope = {
        "schema": PERSISTED_AUDIT_SCHEMA,
        "keyId": key_id,
        "bodySha256": body_sha256,
    }
    envelope["hmacSha256"] = hmac.new(
        key,
        _canonical_json(envelope),
        hashlib.sha256,
    ).hexdigest()
    sealed["persistedAuditIntegrity"] = envelope
    return sealed


def _verify_persisted_inference_audit(audit: Mapping[str, Any]) -> bool:
    integrity = audit.get("persistedAuditIntegrity")
    if not isinstance(integrity, Mapping):
        return False
    key_id = str(integrity.get("keyId") or "").strip()
    key = _response_hmac_keyring().get(key_id) if _valid_key_id(key_id) else None
    if key is None or integrity.get("schema") != PERSISTED_AUDIT_SCHEMA:
        return False
    unsigned_audit = dict(audit)
    unsigned_audit.pop("persistedAuditIntegrity", None)
    expected_body = hashlib.sha256(_canonical_json(unsigned_audit)).hexdigest()
    supplied_body = str(integrity.get("bodySha256") or "").strip().lower()
    supplied_hmac = str(integrity.get("hmacSha256") or "").strip().lower()
    signed_envelope = {
        "schema": integrity.get("schema"),
        "keyId": key_id,
        "bodySha256": supplied_body,
    }
    expected_hmac = hmac.new(
        key,
        _canonical_json(signed_envelope),
        hashlib.sha256,
    ).hexdigest()
    return bool(
        _valid_sha256(supplied_body)
        and _valid_sha256(supplied_hmac)
        and hmac.compare_digest(supplied_body, expected_body)
        and hmac.compare_digest(supplied_hmac, expected_hmac)
    )


def _valid_sha256(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _calibrated_probability(raw_score: float, calibration: Mapping[str, Any]) -> float | None:
    try:
        if calibration.get("method") != "temperature_scaling":
            return None
        temperature = float(calibration.get("temperature"))
        ece = float(calibration.get("ece"))
        brier = float(calibration.get("brierScore"))
        bin_count = int(calibration.get("reliabilityBinCount"))
        parameters = {"method": "temperature_scaling", "temperature": temperature}
        parameters_sha256 = hashlib.sha256(_canonical_json(parameters)).hexdigest()
        if (
            not math.isfinite(temperature)
            or temperature <= 0.0
            or not 0.0 <= ece <= 0.05
            or not 0.0 <= brier <= 0.20
            or bin_count < 10
            or calibration.get("parametersSha256") != parameters_sha256
        ):
            return None
        clipped = min(max(raw_score, 1e-7), 1.0 - 1e-7)
        calibrated_logit = math.log(clipped / (1.0 - clipped)) / temperature
        return 1.0 / (1.0 + math.exp(-calibrated_logit))
    except (TypeError, ValueError, OverflowError):
        return None


def _public_key() -> Ed25519PublicKey | None:
    path = Path(
        os.environ.get(
            "REALGUARD_V2_CALIBRATION_PUBLIC_KEY_FILE",
            "/etc/realguard/model-calibration-ed25519.pub",
        )
    )
    try:
        stat_result = path.stat()
        if not path.is_file() or stat_result.st_size < 64 or stat_result.st_size > 4096:
            return None
        if stat_result.st_mode & 0o022:
            return None
        require_root = os.environ.get(
            "REALGUARD_V2_CALIBRATION_REQUIRE_ROOT_OWNERSHIP", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if require_root and stat_result.st_uid != 0:
            return None
        key = load_pem_public_key(path.read_bytes())
    except (OSError, TypeError, ValueError):
        return None
    return key if isinstance(key, Ed25519PublicKey) else None


def validate_model_decision(decision: Mapping[str, Any] | None) -> bool:
    if not isinstance(decision, Mapping):
        return False
    envelope = decision.get("calibrationManifest")
    if not isinstance(envelope, Mapping):
        return False
    try:
        encoded = _canonical_json(envelope)
    except (TypeError, ValueError):
        return False
    if not encoded or len(encoded) > MAX_MANIFEST_BYTES:
        return False
    signature = str(envelope.get("signature") or "").strip()
    signed = {key: value for key, value in envelope.items() if key != "signature"}
    key = _public_key()
    if key is None:
        return False
    try:
        key.verify(base64.b64decode(signature.encode("ascii"), validate=True), _canonical_json(signed))
    except (InvalidSignature, ValueError, TypeError, UnicodeEncodeError):
        return False
    if envelope.get("schema") != MANIFEST_SCHEMA:
        return False
    if hashlib.sha256(encoded).hexdigest() != str(decision.get("manifestSha256") or "").lower():
        return False

    contract = envelope.get("runtimeContract")
    if not isinstance(contract, Mapping):
        return False
    contract_sha256 = hashlib.sha256(_canonical_json(contract)).hexdigest()
    preprocessing = contract.get("preprocessing")
    if not isinstance(preprocessing, Mapping):
        return False
    preprocessing_sha256 = hashlib.sha256(_canonical_json(preprocessing)).hexdigest()
    if (
        envelope.get("runtimeContractSha256") != contract_sha256
        or envelope.get("preprocessingSha256") != preprocessing_sha256
        or envelope.get("modelSha256") != contract.get("modelSha256")
    ):
        return False
    comparisons = {
        "calibrationId": envelope.get("calibrationId"),
        "datasetSha256": envelope.get("datasetSha256"),
        "modelSha256": envelope.get("modelSha256"),
        "preprocessingSha256": preprocessing_sha256,
        "runtimeContractSha256": contract_sha256,
        "inferenceImplementationSha256": contract.get("inferenceImplementationSha256"),
        "decisionPolicyImplementationSha256": contract.get("decisionPolicyImplementationSha256"),
        "runtimeLockSha256": contract.get("runtimeLockSha256"),
        "evaluationCodeRevision": envelope.get("evaluationCodeRevision"),
        "expiresAt": envelope.get("expiresAt"),
        "realSamples": envelope.get("realSamples"),
        "fakeSamples": envelope.get("fakeSamples"),
        "observedFpr": envelope.get("observedFpr"),
        "observedFnr": envelope.get("observedFnr"),
        "aiThreshold": envelope.get("aiThreshold"),
        "probabilityCalibration": envelope.get("probabilityCalibration"),
    }
    if any(decision.get(field) != expected for field, expected in comparisons.items()):
        return False
    if any(not _valid_sha256(decision.get(field)) for field in HASH_FIELDS):
        return False

    try:
        expiry = datetime.fromisoformat(str(decision.get("expiresAt") or "").replace("Z", "+00:00"))
        expiry = expiry.astimezone(timezone.utc) if expiry.tzinfo else None
        real_samples = int(decision.get("realSamples") or 0)
        fake_samples = int(decision.get("fakeSamples") or 0)
        observed_fpr = float(decision.get("observedFpr"))
        observed_fnr = float(decision.get("observedFnr"))
        threshold = float(decision.get("aiThreshold"))
        raw_score = float(decision.get("rawModelScore"))
        published = float(decision.get("publishedProbability"))
    except (TypeError, ValueError):
        return False
    probability_calibration = decision.get("probabilityCalibration")
    expected_published = (
        _calibrated_probability(raw_score, probability_calibration)
        if isinstance(probability_calibration, Mapping) else None
    )
    return bool(
        decision.get("ready") is True
        and decision.get("mode") == "calibrated_verdict"
        and expiry is not None
        and expiry > datetime.now(timezone.utc)
        and real_samples >= 500
        and fake_samples >= 500
        and 0.0 <= observed_fpr <= 0.05
        and 0.0 <= observed_fnr <= 0.10
        and all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in (raw_score, published))
        and 0.0 < threshold < 1.0
        and expected_published is not None
        and abs(expected_published - published) <= 1e-6
        and decision.get("finalLabel") == (
            "AI生成图像" if published >= threshold else "真实图像"
        )
        and isinstance(decision.get("gateReasons"), list)
        and not decision.get("gateReasons")
        and contract.get("classMapping") == {"0": "real", "1": "fake"}
    )


def validate_inference_audit(
    audit: Mapping[str, Any] | None,
    decision: Mapping[str, Any] | None,
) -> bool:
    if (
        not isinstance(audit, Mapping)
        or not validate_model_decision(decision)
        or not _verify_persisted_inference_audit(audit)
    ):
        return False
    try:
        raw_score = float(audit.get("rawModelScore"))
        published = float(audit.get("fakeProbability"))
        chunk_count = int(audit.get("chunkCount"))
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in (raw_score, published)):
        return False
    if abs(raw_score - float(decision.get("rawModelScore"))) > 1e-6:
        return False
    if abs(published - float(decision.get("publishedProbability"))) > 1e-6 or chunk_count <= 0:
        return False

    def valid_size(value: Any) -> bool:
        if not isinstance(value, Mapping):
            return False
        try:
            return int(value.get("width")) > 0 and int(value.get("height")) > 0
        except (TypeError, ValueError):
            return False

    parameters = audit.get("parameters")
    runtime = audit.get("runtime")
    downsample = audit.get("downsample")
    if not valid_size(audit.get("originalSize")) or not valid_size(audit.get("processedSize")):
        return False
    if not isinstance(downsample, Mapping) or not isinstance(downsample.get("downsampled"), bool):
        return False
    if not isinstance(parameters, Mapping) or not isinstance(runtime, Mapping):
        return False
    for field in (
        "chunkSize",
        "requestedChunkSize",
        "maxTiles",
        "effectiveTiles",
        "tileSize",
        "topK",
        "usedTopK",
        "maxAnalysisSide",
    ):
        try:
            if int(parameters.get(field)) <= 0:
                return False
        except (TypeError, ValueError):
            return False
    contract = decision["calibrationManifest"]["runtimeContract"]
    contract_parameters = contract.get("inferenceParameters")
    if not isinstance(contract_parameters, Mapping):
        return False
    expected_parameters = {
        "requestedChunkSize": contract_parameters.get("requestedChunkSize"),
        "maxTiles": contract_parameters.get("requestedMaxTiles"),
        "tileSize": contract_parameters.get("tileSize"),
        "topK": contract_parameters.get("requestedTopK"),
        "maxAnalysisSide": contract_parameters.get("maxAnalysisSide"),
    }
    try:
        if any(
            int(parameters.get(field)) != int(expected)
            for field, expected in expected_parameters.items()
        ):
            return False
    except (TypeError, ValueError):
        return False
    deployment_commit = str(runtime.get("deploymentCommit") or "").strip().lower()
    input_sha256 = str(audit.get("inputImageSha256") or "").strip().lower()
    response_integrity = audit.get("responseIntegrity")
    return bool(
        str(audit.get("model") or "").strip()
        and audit.get("finalLabel") == decision.get("finalLabel")
        and runtime.get("activeProvider") == "CUDAExecutionProvider"
        and runtime.get("modelSha256") == decision.get("modelSha256")
        and runtime.get("runtimeContractSha256") == decision.get("runtimeContractSha256")
        and str(runtime.get("modelRevision") or "").strip()
        and 7 <= len(deployment_commit) <= 40
        and all(char in "0123456789abcdef" for char in deployment_commit)
        and _valid_sha256(input_sha256)
        and isinstance(response_integrity, Mapping)
        and response_integrity.get("schema") == "cn.huijian.remote-inference-response-v1"
        and response_integrity.get("imageSha256") == input_sha256
        and _valid_sha256(response_integrity.get("bodySha256"))
        and _valid_sha256(response_integrity.get("hmacSha256"))
        and len(str(response_integrity.get("requestNonce") or "")) == 32
    )
