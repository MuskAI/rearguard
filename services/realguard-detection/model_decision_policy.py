import base64
import hashlib
import json
import math
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key


MANIFEST_SCHEMA = "cn.huijian.model-calibration-v2"
MAX_MANIFEST_BYTES = 64 * 1024


def _int_env(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _float_env(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _load_signed_manifest() -> Tuple[Dict[str, Any], Dict[str, Any], str, list[str]]:
    manifest_path = Path(
        os.environ.get(
            "REALGUARD_V2_CALIBRATION_MANIFEST",
            "/etc/realguard/model-calibration.json",
        )
    )
    public_key_path = Path(
        os.environ.get(
            "REALGUARD_V2_CALIBRATION_PUBLIC_KEY_FILE",
            "/etc/realguard/model-calibration-ed25519.pub",
        )
    )
    reasons: list[str] = []
    if not manifest_path.is_file():
        reasons.append("calibration_manifest_missing")
    if not public_key_path.is_file():
        reasons.append("calibration_public_key_missing")
    if reasons:
        return {}, {}, "", reasons

    try:
        manifest_stat = manifest_path.lstat()
        key_stat = public_key_path.lstat()
        require_root_owner = os.environ.get(
            "REALGUARD_V2_CALIBRATION_REQUIRE_ROOT_OWNERSHIP", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if (
            not stat.S_ISREG(manifest_stat.st_mode)
            or stat.S_ISLNK(manifest_stat.st_mode)
            or manifest_stat.st_size <= 0
            or manifest_stat.st_size > MAX_MANIFEST_BYTES
            or manifest_stat.st_mode & 0o022
            or (require_root_owner and manifest_stat.st_uid != 0)
        ):
            raise ValueError("unsafe manifest file")
        if (
            not stat.S_ISREG(key_stat.st_mode)
            or stat.S_ISLNK(key_stat.st_mode)
            or key_stat.st_size < 64
            or key_stat.st_size > 4096
            or key_stat.st_mode & 0o022
            or (require_root_owner and key_stat.st_uid != 0)
        ):
            raise ValueError("unsafe public key file")
        raw = manifest_path.read_bytes()
        public_key_bytes = public_key_path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return {}, {}, "", ["calibration_manifest_unreadable"]
    if not isinstance(manifest, dict):
        return {}, {}, "", ["calibration_manifest_invalid"]
    signature = str(manifest.get("signature") or "").strip()
    signed = {key: value for key, value in manifest.items() if key != "signature"}
    try:
        public_key = load_pem_public_key(public_key_bytes)
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("calibration key is not Ed25519")
        signature_bytes = base64.b64decode(signature.encode("ascii"), validate=True)
        public_key.verify(signature_bytes, _canonical_json(signed))
    except (InvalidSignature, ValueError, TypeError, UnicodeEncodeError):
        return {}, {}, "", ["calibration_signature_invalid"]
    return signed, manifest, hashlib.sha256(_canonical_json(manifest)).hexdigest(), []


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _parse_expiry(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def model_decision_policy(
    model_sha256: str | None = None,
    runtime_contract: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    manifest, calibration_manifest, manifest_sha256, reasons = _load_signed_manifest()
    calibration_id = str(manifest.get("calibrationId") or "").strip()
    dataset_sha256 = str(manifest.get("datasetSha256") or "").strip().lower()
    bound_model_sha256 = str(manifest.get("modelSha256") or "").strip().lower()
    expected_model_sha256 = str(
        model_sha256 or os.environ.get("REALGUARD_V2_MODEL_SHA256", "")
    ).strip().lower()
    evaluation_revision = str(manifest.get("evaluationCodeRevision") or "").strip()
    expires_at = str(manifest.get("expiresAt") or "").strip()
    preprocessing_sha256 = str(manifest.get("preprocessingSha256") or "").strip().lower()
    bound_contract_sha256 = str(manifest.get("runtimeContractSha256") or "").strip().lower()
    manifest_runtime_contract = manifest.get("runtimeContract")
    probability_calibration = manifest.get("probabilityCalibration")
    runtime_contract_payload = dict(runtime_contract or {})
    runtime_contract_sha256 = (
        hashlib.sha256(_canonical_json(runtime_contract_payload)).hexdigest()
        if runtime_contract_payload
        else ""
    )
    manifest_contract_sha256 = (
        hashlib.sha256(_canonical_json(manifest_runtime_contract)).hexdigest()
        if isinstance(manifest_runtime_contract, Mapping)
        else ""
    )
    runtime_preprocessing = (
        runtime_contract_payload.get("preprocessing")
        if isinstance(runtime_contract_payload.get("preprocessing"), Mapping)
        else {}
    )
    expected_preprocessing_sha256 = (
        hashlib.sha256(_canonical_json(runtime_preprocessing)).hexdigest()
        if runtime_preprocessing
        else ""
    )
    runtime_class_mapping = runtime_contract_payload.get("classMapping")
    inference_implementation_sha256 = str(
        runtime_contract_payload.get("inferenceImplementationSha256") or ""
    ).strip().lower()
    decision_policy_implementation_sha256 = str(
        runtime_contract_payload.get("decisionPolicyImplementationSha256") or ""
    ).strip().lower()
    runtime_lock_sha256 = str(
        runtime_contract_payload.get("runtimeLockSha256") or ""
    ).strip().lower()
    try:
        real_samples = max(0, int(manifest.get("realSamples") or 0))
        fake_samples = max(0, int(manifest.get("fakeSamples") or 0))
        observed_fpr = float(manifest.get("observedFpr", 1.0))
        observed_fnr = float(manifest.get("observedFnr", 1.0))
        ai_threshold = float(manifest.get("aiThreshold", 0.5))
        calibration_temperature = float((probability_calibration or {}).get("temperature"))
        calibration_ece = float((probability_calibration or {}).get("ece"))
        calibration_brier = float((probability_calibration or {}).get("brierScore"))
        calibration_bins = int((probability_calibration or {}).get("reliabilityBinCount"))
    except (TypeError, ValueError):
        real_samples = fake_samples = 0
        observed_fpr = observed_fnr = 1.0
        ai_threshold = 0.5
        calibration_temperature = float("nan")
        calibration_ece = calibration_brier = float("inf")
        calibration_bins = 0
        reasons.append("calibration_metrics_invalid")

    min_class_samples = max(
        100, _int_env("REALGUARD_V2_CALIBRATION_MIN_CLASS_SAMPLES", 500)
    )
    max_fpr = min(1.0, max(0.0, _float_env("REALGUARD_V2_CALIBRATION_MAX_FPR", 0.05)))
    max_fnr = min(1.0, max(0.0, _float_env("REALGUARD_V2_CALIBRATION_MAX_FNR", 0.10)))

    if manifest and manifest.get("schema") != MANIFEST_SCHEMA:
        reasons.append("calibration_schema_invalid")
    if not calibration_id:
        reasons.append("calibration_id_missing")
    if not _valid_sha256(dataset_sha256):
        reasons.append("dataset_fingerprint_missing")
    if not _valid_sha256(expected_model_sha256):
        reasons.append("runtime_model_fingerprint_missing")
    if (
        len(bound_model_sha256) != 64
        or bound_model_sha256 != expected_model_sha256
    ):
        reasons.append("calibration_model_mismatch")
    if not evaluation_revision:
        reasons.append("evaluation_code_revision_missing")
    if not runtime_contract_payload:
        reasons.append("runtime_contract_missing")
    if runtime_class_mapping != {"0": "real", "1": "fake"}:
        reasons.append("class_mapping_mismatch")
    if not _valid_sha256(inference_implementation_sha256):
        reasons.append("inference_implementation_fingerprint_missing")
    if not _valid_sha256(decision_policy_implementation_sha256):
        reasons.append("decision_policy_implementation_fingerprint_missing")
    if not _valid_sha256(runtime_lock_sha256):
        reasons.append("runtime_lock_fingerprint_missing")
    if (
        not _valid_sha256(bound_contract_sha256)
        or bound_contract_sha256 != runtime_contract_sha256
        or manifest_contract_sha256 != runtime_contract_sha256
    ):
        reasons.append("runtime_contract_mismatch")
    if (
        not _valid_sha256(preprocessing_sha256)
        or preprocessing_sha256 != expected_preprocessing_sha256
    ):
        reasons.append("preprocessing_contract_mismatch")
    expiry = _parse_expiry(expires_at)
    if expiry is None:
        reasons.append("calibration_expiry_invalid")
    elif expiry <= datetime.now(timezone.utc):
        reasons.append("calibration_expired")
    if real_samples < min_class_samples:
        reasons.append("real_sample_count_below_gate")
    if fake_samples < min_class_samples:
        reasons.append("fake_sample_count_below_gate")
    if not 0.0 <= observed_fpr <= max_fpr:
        reasons.append("false_positive_rate_above_gate")
    if not 0.0 <= observed_fnr <= max_fnr:
        reasons.append("false_negative_rate_above_gate")
    if not 0.0 < ai_threshold < 1.0:
        reasons.append("decision_threshold_invalid")
    calibration_parameters = {
        "method": str((probability_calibration or {}).get("method") or ""),
        "temperature": calibration_temperature,
    }
    try:
        calibration_parameters_sha256 = hashlib.sha256(
            _canonical_json(calibration_parameters)
        ).hexdigest()
    except (TypeError, ValueError):
        calibration_parameters_sha256 = ""
    if not isinstance(probability_calibration, Mapping):
        reasons.append("probability_calibration_missing")
    elif (
        calibration_parameters["method"] != "temperature_scaling"
        or not math.isfinite(calibration_temperature)
        or calibration_temperature <= 0.0
        or probability_calibration.get("parametersSha256") != calibration_parameters_sha256
    ):
        reasons.append("probability_calibration_invalid")
    if not math.isfinite(calibration_ece) or not 0.0 <= calibration_ece <= 0.05:
        reasons.append("calibration_ece_above_gate")
    if not math.isfinite(calibration_brier) or not 0.0 <= calibration_brier <= 0.20:
        reasons.append("calibration_brier_above_gate")
    if calibration_bins < 10:
        reasons.append("calibration_reliability_bins_below_gate")
    reasons = list(dict.fromkeys(reasons))

    return {
        "ready": not reasons,
        "mode": "calibrated_verdict" if not reasons else "review_only",
        "calibrationId": calibration_id or None,
        "manifestSha256": manifest_sha256 or None,
        "datasetSha256": dataset_sha256 or None,
        "modelSha256": expected_model_sha256 or None,
        "boundModelSha256": bound_model_sha256 or None,
        "preprocessingSha256": expected_preprocessing_sha256 or None,
        "boundPreprocessingSha256": preprocessing_sha256 or None,
        "runtimeContractSha256": runtime_contract_sha256 or None,
        "boundRuntimeContractSha256": bound_contract_sha256 or None,
        "inferenceImplementationSha256": inference_implementation_sha256 or None,
        "decisionPolicyImplementationSha256": decision_policy_implementation_sha256 or None,
        "runtimeLockSha256": runtime_lock_sha256 or None,
        "calibrationManifest": calibration_manifest or None,
        "evaluationCodeRevision": evaluation_revision or None,
        "expiresAt": expires_at or None,
        "realSamples": real_samples,
        "fakeSamples": fake_samples,
        "observedFpr": observed_fpr,
        "observedFnr": observed_fnr,
        "aiThreshold": ai_threshold,
        "probabilityCalibration": dict(probability_calibration) if isinstance(probability_calibration, Mapping) else None,
        "gateReasons": reasons,
    }
