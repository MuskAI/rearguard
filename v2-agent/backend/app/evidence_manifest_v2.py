"""Immutable Ed25519 evidence manifests and offline report verification.

The module deliberately keeps signing and verification separate: verification
never needs the private key, while production signing fails closed unless an
explicit, strictly validated Ed25519 key is configured.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


MANIFEST_SCHEMA = "cn.huijian.evidence-manifest-v2"
ARTIFACT_SCHEMA = "cn.huijian.report-artifact-v1"
BUNDLE_SCHEMA = "cn.huijian.evidence-verification-bundle-v1"
KEY_REGISTRY_SCHEMA = "cn.huijian.evidence-key-registry-v1"
CANONICALIZATION = "huijian-json-sort-keys-utf8-v1"
SIGNATURE_ALGORITHM = "Ed25519"
MANIFEST_SIGNATURE_DOMAIN = b"HUJIAN-V2-EVIDENCE-MANIFEST\x00"
ARTIFACT_SIGNATURE_DOMAIN = b"HUJIAN-V2-REPORT-ARTIFACT\x00"
_KEY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,63}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class EvidenceConfigurationError(RuntimeError):
    """Raised when production signing or trust configuration is unsafe."""


class EvidenceIntegrityError(RuntimeError):
    """Raised when persisted evidence fails cryptographic verification."""


class EvidenceConflictError(RuntimeError):
    """Raised when an immutable evidence object is written inconsistently."""


@dataclass(frozen=True)
class SigningKey:
    key_id: str
    private_key: Ed25519PrivateKey
    public_key_bytes: bytes


def canonical_json(value: Any) -> bytes:
    """Return the platform's versioned deterministic JSON representation."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _json_clone(value: Any) -> Any:
    return json.loads(canonical_json(value).decode("utf-8"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64decode(value: Any, *, expected_size: int, label: str) -> bytes:
    raw = str(value or "").strip()
    if raw.startswith("base64:"):
        raw = raw[7:]
    try:
        decoded = base64.b64decode(raw.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise EvidenceConfigurationError(f"{label} must be strict base64") from exc
    if len(decoded) != expected_size:
        raise EvidenceConfigurationError(f"{label} must decode to {expected_size} bytes")
    return decoded


def _validate_key_id(value: Any) -> str:
    key_id = str(value or "").strip()
    if not _KEY_ID_RE.fullmatch(key_id):
        raise EvidenceConfigurationError(
            "JIANZHEN_EVIDENCE_SIGNING_KEY_ID must be 3-64 safe ASCII characters"
        )
    return key_id


def _load_private_key_material(material: bytes, *, label: str) -> Ed25519PrivateKey:
    stripped = material.strip()
    if stripped.startswith(b"-----BEGIN"):
        try:
            key = serialization.load_pem_private_key(stripped, password=None)
        except (TypeError, ValueError) as exc:
            raise EvidenceConfigurationError(f"{label} is not a valid unencrypted PKCS8 key") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise EvidenceConfigurationError(f"{label} must contain an Ed25519 private key")
        return key
    try:
        text = stripped.decode("ascii")
    except UnicodeDecodeError as exc:
        raise EvidenceConfigurationError(f"{label} must be PEM or base64: raw seed") from exc
    if not text.startswith("base64:"):
        raise EvidenceConfigurationError(f"{label} raw keys must use the base64: prefix")
    seed = _b64decode(text, expected_size=32, label=label)
    return Ed25519PrivateKey.from_private_bytes(seed)


def _read_private_key_file(path_value: str) -> bytes:
    path = Path(path_value)
    if not path.is_absolute():
        raise EvidenceConfigurationError("private key file path must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceConfigurationError("private key file is not readable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise EvidenceConfigurationError("private key file must be a regular non-symlink file")
        if metadata.st_mode & 0o077:
            raise EvidenceConfigurationError(
                "private key file permissions must not grant group/world access"
            )
        if metadata.st_size <= 0 or metadata.st_size > 16 * 1024:
            raise EvidenceConfigurationError("private key file size is invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            material = handle.read(16 * 1024 + 1)
        if len(material) != metadata.st_size:
            raise EvidenceConfigurationError("private key file changed while being read")
        return material
    except OSError as exc:
        raise EvidenceConfigurationError("private key file is not readable") from exc
    finally:
        os.close(descriptor)


def load_signing_key() -> SigningKey:
    inline = os.getenv("JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY", "").strip()
    file_path = os.getenv("JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY_FILE", "").strip()
    if bool(inline) == bool(file_path):
        raise EvidenceConfigurationError(
            "configure exactly one of JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY or "
            "JIANZHEN_EVIDENCE_SIGNING_PRIVATE_KEY_FILE"
        )
    key_id = _validate_key_id(os.getenv("JIANZHEN_EVIDENCE_SIGNING_KEY_ID", ""))
    material = inline.encode("utf-8") if inline else _read_private_key_file(file_path)
    private_key = _load_private_key_material(material, label="evidence signing private key")
    public_key_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return SigningKey(key_id=key_id, private_key=private_key, public_key_bytes=public_key_bytes)


def configured_verification_keys(*, include_current_signer: bool = True) -> dict[str, bytes]:
    configured: dict[str, bytes] = {}
    raw = os.getenv("JIANZHEN_EVIDENCE_VERIFY_PUBLIC_KEYS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvidenceConfigurationError("JIANZHEN_EVIDENCE_VERIFY_PUBLIC_KEYS must be JSON") from exc
        if not isinstance(parsed, dict):
            raise EvidenceConfigurationError("JIANZHEN_EVIDENCE_VERIFY_PUBLIC_KEYS must be an object")
        for raw_key_id, raw_public_key in parsed.items():
            key_id = _validate_key_id(raw_key_id)
            configured[key_id] = _b64decode(
                raw_public_key,
                expected_size=32,
                label=f"verification public key {key_id}",
            )
    if include_current_signer:
        signer = load_signing_key()
        existing = configured.get(signer.key_id)
        if existing is not None and existing != signer.public_key_bytes:
            raise EvidenceConfigurationError("current signing key conflicts with verification key registry")
        configured[signer.key_id] = signer.public_key_bytes
    return configured


def signing_status() -> dict[str, Any]:
    try:
        signer = load_signing_key()
        configured_verification_keys(include_current_signer=True)
    except EvidenceConfigurationError:
        return {
            "configured": False,
            "algorithm": SIGNATURE_ALGORITHM,
            "error": "evidence_signing_configuration_invalid",
        }
    return {
        "configured": True,
        "algorithm": SIGNATURE_ALGORITHM,
        "keyId": signer.key_id,
        "publicKey": _b64encode(signer.public_key_bytes),
        "publicKeySha256": _sha256(signer.public_key_bytes),
    }


def verification_key_registry() -> dict[str, Any]:
    """Return exportable verification keys without claiming an external trust anchor."""
    signer = load_signing_key()
    keys = configured_verification_keys(include_current_signer=True)
    return {
        "schema": KEY_REGISTRY_SCHEMA,
        "version": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "algorithm": SIGNATURE_ALGORITHM,
        "externallyAnchored": False,
        "trustedTimestampAvailable": False,
        "trustNotice": (
            "包内公钥只能用于完整性计算；必须通过运营方独立发布渠道核对公钥指纹，"
            "才能建立签名者身份信任。"
        ),
        "keys": [
            {
                "keyId": key_id,
                "publicKey": _b64encode(public_key),
                "publicKeySha256": _sha256(public_key),
                "status": "active" if key_id == signer.key_id else "verification-only",
            }
            for key_id, public_key in sorted(keys.items())
        ],
    }


def _signature_profile(signer: SigningKey) -> dict[str, str]:
    return {
        "algorithm": SIGNATURE_ALGORITHM,
        "keyId": signer.key_id,
        "publicKeySha256": _sha256(signer.public_key_bytes),
    }


def _seal(payload: Mapping[str, Any], *, domain: bytes) -> dict[str, Any]:
    signer = load_signing_key()
    sealed = _json_clone(dict(payload))
    if any(field in sealed for field in ("signing", "sealedAt", "timeEvidence")):
        raise EvidenceIntegrityError("payload must not supply its own signing metadata")
    sealed_at = datetime.now(timezone.utc).isoformat()
    sealed["sealedAt"] = sealed_at
    sealed["timeEvidence"] = {
        "observedAt": sealed_at,
        "clockSource": "application_system_utc",
        "assurance": "untrusted_system_clock",
        "trustedTimestampAuthority": None,
        "externallyAnchored": False,
    }
    sealed["signing"] = _signature_profile(signer)
    encoded = canonical_json(sealed)
    signature = signer.private_key.sign(domain + encoded)
    return {
        "payload": sealed,
        "sha256": _sha256(encoded),
        "signature": {
            "algorithm": SIGNATURE_ALGORITHM,
            "keyId": signer.key_id,
            "publicKey": _b64encode(signer.public_key_bytes),
            "value": _b64encode(signature),
        },
    }


def seal_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return _seal(manifest, domain=MANIFEST_SIGNATURE_DOMAIN)


def seal_artifact_statement(statement: Mapping[str, Any]) -> dict[str, Any]:
    return _seal(statement, domain=ARTIFACT_SIGNATURE_DOMAIN)


def _release_identity() -> str:
    configured = (
        os.getenv("JIANZHEN_RELEASE_ID", "").strip()
        or os.getenv("JIANZHEN_RELEASE_COMMIT", "").strip()
    )
    if configured:
        return configured[:128]
    marker = Path(
        os.getenv("JIANZHEN_DEPLOYED_COMMIT_FILE", "/opt/jianzhen-v2/DEPLOYED_COMMIT")
    )
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return "development"
    return value[:128] or "development"


def _result_snapshot(result: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = {
        key: value
        for key, value in result.items()
        if key not in {"taskId", "reportId", "createdAt", "fileMeta"}
        and not str(key).startswith("_")
    }
    return _json_clone(snapshot)


def _tenant_binding(actor: Mapping[str, Any] | None) -> dict[str, str]:
    actor = actor or {}
    account_uuid = str(actor.get("accountUuid") or "").strip()
    if account_uuid:
        return {
            "scope": "account",
            "subjectSha256": _sha256(f"account:{account_uuid}".encode("utf-8")),
        }
    mode = str(actor.get("mode") or "unowned").strip().lower()
    if mode not in {"admin", "internal", "public", "unowned"}:
        mode = "unowned"
    return {"scope": mode}


def build_manifest(
    result: Mapping[str, Any],
    *,
    original_sha256: str,
    file_size: int,
    actor: Mapping[str, Any] | None = None,
    origin: str = "detection",
) -> dict[str, Any]:
    sha256 = str(original_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(sha256):
        raise EvidenceIntegrityError("original SHA-256 must be 64 lowercase hexadecimal characters")
    try:
        size_bytes = int(file_size)
    except (TypeError, ValueError) as exc:
        raise EvidenceIntegrityError("file size must be an integer") from exc
    if size_bytes < 0:
        raise EvidenceIntegrityError("file size cannot be negative")

    task_id = str(result.get("taskId") or "").strip()
    report_id = str(result.get("reportId") or "").strip()
    created_at = str(result.get("createdAt") or "").strip()
    if not task_id or not report_id or not created_at:
        raise EvidenceIntegrityError("taskId, reportId, and createdAt are required")
    file_meta = result.get("fileMeta") if isinstance(result.get("fileMeta"), Mapping) else {}
    supplied_sha256 = str(file_meta.get("sha256") or "").strip().lower()
    if supplied_sha256 and supplied_sha256 != sha256:
        raise EvidenceIntegrityError("result file SHA-256 does not match the uploaded original")

    snapshot = _result_snapshot(result)
    return {
        "schema": MANIFEST_SCHEMA,
        "version": 1,
        "canonicalization": CANONICALIZATION,
        "origin": str(origin or "detection")[:64],
        "frozenAt": created_at,
        "binding": {
            "taskId": task_id,
            "reportId": report_id,
            "subject": {
                "sha256": sha256,
                "sizeBytes": size_bytes,
                "fileName": str(file_meta.get("name") or "unknown"),
                "fileType": str(file_meta.get("type") or "unknown"),
                "resolution": file_meta.get("resolution"),
            },
            "tenant": _tenant_binding(actor),
            "result": snapshot,
        },
        "runtime": {
            "service": {
                "name": "huijian-v2-backend",
                "version": os.getenv("JIANZHEN_APP_VERSION", "0.3.0").strip() or "0.3.0",
                "releaseId": _release_identity(),
            },
            "python": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
            },
            "model": {
                "modelVersion": snapshot.get("modelVersion"),
                "source": snapshot.get("source"),
                "cacheVersion": snapshot.get("cacheVersion"),
                "probabilityModel": snapshot.get("probabilityModel"),
                "provenanceEngineVersion": (
                    (snapshot.get("provenancePrecheck") or {}).get("engineVersion")
                    if isinstance(snapshot.get("provenancePrecheck"), dict)
                    else None
                ),
            },
        },
    }


def manifest_matches_history(
    manifest: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    original_sha256: str,
    file_size: int,
    actor: Mapping[str, Any] | None = None,
) -> bool:
    try:
        expected = build_manifest(
            result,
            original_sha256=original_sha256,
            file_size=file_size,
            actor=actor,
            origin=str(manifest.get("origin") or "detection"),
        )
    except (EvidenceIntegrityError, TypeError, ValueError):
        return False
    return canonical_json(manifest.get("binding")) == canonical_json(expected.get("binding"))


def build_artifact_statement(
    manifest_record: Mapping[str, Any],
    *,
    report_payload: Mapping[str, Any],
    artifact_bytes: bytes,
    filename: str,
    media_type: str = "application/pdf",
) -> dict[str, Any]:
    manifest_payload = manifest_record.get("payload") or {}
    binding = manifest_payload.get("binding") or {}
    manifest_sha256 = str(manifest_record.get("sha256") or "")
    if not _SHA256_RE.fullmatch(manifest_sha256):
        raise EvidenceIntegrityError("manifest hash is missing or invalid")
    report_payload_bytes = canonical_json(report_payload)
    return {
        "schema": ARTIFACT_SCHEMA,
        "version": 1,
        "canonicalization": CANONICALIZATION,
        "frozenAt": manifest_payload.get("frozenAt"),
        "taskId": binding.get("taskId"),
        "reportId": binding.get("reportId"),
        "manifestSha256": manifest_sha256,
        "reportPayloadSha256": _sha256(report_payload_bytes),
        "artifact": {
            "sha256": _sha256(artifact_bytes),
            "sizeBytes": len(artifact_bytes),
            "mediaType": str(media_type),
            "filename": str(filename),
        },
    }


def _verification_result(status: str, *, errors: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {"status": status, "errors": errors or [], **extra}


def verify_signed_record(
    record: Mapping[str, Any] | None,
    *,
    domain: bytes,
    trusted_public_keys: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        return _verification_result("missing", errors=["signed_record_missing"])
    payload = record.get("payload")
    signature = record.get("signature")
    claimed_sha256 = str(record.get("sha256") or "").lower()
    if not isinstance(payload, Mapping) or not isinstance(signature, Mapping):
        return _verification_result("missing", errors=["signed_record_incomplete"])
    try:
        encoded = canonical_json(payload)
    except (TypeError, ValueError) as exc:
        return _verification_result("invalid", errors=[f"canonicalization_failed:{type(exc).__name__}"])
    actual_sha256 = _sha256(encoded)
    errors: list[str] = []
    if not _SHA256_RE.fullmatch(claimed_sha256) or claimed_sha256 != actual_sha256:
        errors.append("payload_sha256_mismatch")
    algorithm = str(signature.get("algorithm") or "")
    key_id = str(signature.get("keyId") or "")
    if algorithm != SIGNATURE_ALGORITHM:
        errors.append("signature_algorithm_invalid")
    try:
        _validate_key_id(key_id)
        public_key_bytes = _b64decode(
            signature.get("publicKey"), expected_size=32, label="embedded public key"
        )
        signature_bytes = _b64decode(
            signature.get("value"), expected_size=64, label="Ed25519 signature"
        )
    except EvidenceConfigurationError:
        return _verification_result("invalid", errors=errors + ["signature_encoding_invalid"])

    profile = payload.get("signing") if isinstance(payload.get("signing"), Mapping) else {}
    if profile.get("algorithm") != SIGNATURE_ALGORITHM:
        errors.append("signed_algorithm_profile_mismatch")
    if profile.get("keyId") != key_id:
        errors.append("signed_key_id_mismatch")
    if profile.get("publicKeySha256") != _sha256(public_key_bytes):
        errors.append("signed_public_key_fingerprint_mismatch")

    trust_source = "none"
    trusted = False
    if trusted_public_keys is None:
        errors.append("trusted_public_key_required")
    else:
        expected_public_key = trusted_public_keys.get(key_id)
        trust_source = "configured"
        if expected_public_key is None:
            errors.append("untrusted_key_id")
        elif expected_public_key != public_key_bytes:
            errors.append("trusted_public_key_mismatch")
        else:
            trusted = True
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature_bytes,
            domain + encoded,
        )
    except (InvalidSignature, ValueError):
        errors.append("signature_invalid")
    return _verification_result(
        "valid" if not errors else "invalid",
        errors=errors,
        sha256=actual_sha256,
        algorithm=algorithm,
        keyId=key_id,
        publicKey=_b64encode(public_key_bytes),
        trustSource=trust_source,
        trusted=trusted,
    )


def verify_bundle(
    bundle: Mapping[str, Any] | None,
    *,
    artifact_bytes: bytes | None = None,
    report_payload: Mapping[str, Any] | None = None,
    subject_bytes: bytes | None = None,
    trusted_public_keys: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    if not isinstance(bundle, Mapping):
        missing = _verification_result("missing", errors=["bundle_missing"])
        return {
            "status": "missing",
            "complete": False,
            "subjectVerified": False,
            "manifest": missing,
            "artifact": missing,
            "subject": missing,
        }
    manifest_record = bundle.get("manifest")
    manifest_result = verify_signed_record(
        manifest_record if isinstance(manifest_record, Mapping) else None,
        domain=MANIFEST_SIGNATURE_DOMAIN,
        trusted_public_keys=trusted_public_keys,
    )
    artifact_record = bundle.get("artifact")
    if not isinstance(artifact_record, Mapping):
        artifact_result = _verification_result("missing", errors=["artifact_attestation_missing"])
    else:
        artifact_result = verify_signed_record(
            artifact_record,
            domain=ARTIFACT_SIGNATURE_DOMAIN,
            trusted_public_keys=trusted_public_keys,
        )
        statement = artifact_record.get("payload") or {}
        errors = list(artifact_result.get("errors") or [])
        manifest_payload = (manifest_record or {}).get("payload") if isinstance(manifest_record, Mapping) else {}
        manifest_binding = (manifest_payload or {}).get("binding") or {}
        if statement.get("manifestSha256") != (manifest_record or {}).get("sha256"):
            errors.append("artifact_manifest_link_mismatch")
        if statement.get("taskId") != manifest_binding.get("taskId"):
            errors.append("artifact_task_id_mismatch")
        if statement.get("reportId") != manifest_binding.get("reportId"):
            errors.append("artifact_report_id_mismatch")
        artifact_meta = statement.get("artifact") if isinstance(statement.get("artifact"), Mapping) else {}
        if artifact_bytes is None:
            errors.append("artifact_bytes_missing")
            artifact_status = "missing" if artifact_result.get("status") == "valid" else "invalid"
        else:
            if artifact_meta.get("sha256") != _sha256(artifact_bytes):
                errors.append("artifact_sha256_mismatch")
            if artifact_meta.get("sizeBytes") != len(artifact_bytes):
                errors.append("artifact_size_mismatch")
            artifact_status = "valid" if not errors else "invalid"
        if report_payload is not None:
            try:
                report_payload_sha256 = _sha256(canonical_json(report_payload))
            except (TypeError, ValueError):
                errors.append("report_payload_canonicalization_failed")
            else:
                if statement.get("reportPayloadSha256") != report_payload_sha256:
                    errors.append("report_payload_sha256_mismatch")
            if errors:
                artifact_status = "invalid"
        artifact_result = {
            **artifact_result,
            "status": artifact_status,
            "errors": list(dict.fromkeys(errors)),
            "artifactSha256": artifact_meta.get("sha256"),
            "reportPayloadSha256": statement.get("reportPayloadSha256"),
        }

    manifest_payload = (
        manifest_record.get("payload")
        if isinstance(manifest_record, Mapping)
        and isinstance(manifest_record.get("payload"), Mapping)
        else {}
    )
    manifest_binding = (
        manifest_payload.get("binding")
        if isinstance(manifest_payload.get("binding"), Mapping)
        else {}
    )
    subject_meta = (
        manifest_binding.get("subject")
        if isinstance(manifest_binding.get("subject"), Mapping)
        else {}
    )
    subject_errors: list[str] = []
    expected_subject_sha256 = str(subject_meta.get("sha256") or "").lower()
    try:
        expected_subject_size = int(subject_meta.get("sizeBytes"))
    except (TypeError, ValueError):
        expected_subject_size = -1
    if not _SHA256_RE.fullmatch(expected_subject_sha256):
        subject_errors.append("subject_sha256_binding_invalid")
    if expected_subject_size < 0:
        subject_errors.append("subject_size_binding_invalid")
    actual_subject_sha256 = None
    if subject_bytes is None:
        subject_status = "missing" if not subject_errors else "invalid"
        subject_errors.append("subject_bytes_missing")
    else:
        actual_subject_sha256 = _sha256(subject_bytes)
        if expected_subject_sha256 != actual_subject_sha256:
            subject_errors.append("subject_sha256_mismatch")
        if expected_subject_size != len(subject_bytes):
            subject_errors.append("subject_size_mismatch")
        subject_status = "valid" if not subject_errors else "invalid"
    subject_result = _verification_result(
        subject_status,
        errors=list(dict.fromkeys(subject_errors)),
        sha256=actual_subject_sha256,
        expectedSha256=expected_subject_sha256 or None,
        sizeBytes=len(subject_bytes) if subject_bytes is not None else None,
        expectedSizeBytes=expected_subject_size if expected_subject_size >= 0 else None,
        fileName=subject_meta.get("fileName"),
    )

    if manifest_result["status"] == "missing":
        status = "missing"
    elif (
        manifest_result["status"] != "valid"
        or artifact_result["status"] == "invalid"
        or subject_result["status"] == "invalid"
    ):
        status = "invalid"
    elif artifact_result["status"] == "missing":
        status = "missing"
    else:
        status = "valid"
    return {
        "status": status,
        "packageIntegrityVerified": (
            manifest_result["status"] == "valid"
            and artifact_result["status"] == "valid"
        ),
        "complete": (
            manifest_result["status"] == "valid"
            and artifact_result["status"] == "valid"
            and subject_result["status"] == "valid"
        ),
        "subjectVerified": subject_result["status"] == "valid",
        "manifest": manifest_result,
        "artifact": artifact_result,
        "subject": subject_result,
    }


def _load_cli_bundle(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(parsed, dict) and isinstance(parsed.get("bundle"), dict):
        return parsed["bundle"]
    if not isinstance(parsed, dict):
        raise ValueError("verification bundle must be a JSON object")
    return parsed


def _cli_verify(args: argparse.Namespace) -> int:
    try:
        bundle = _load_cli_bundle(Path(args.bundle))
        artifact_bytes = Path(args.artifact).read_bytes() if args.artifact else None
        subject_bytes = Path(args.subject).read_bytes() if args.subject else None
        report_payload = None
        if args.report_payload:
            report_payload = json.loads(Path(args.report_payload).read_text(encoding="utf-8"))
            if not isinstance(report_payload, dict):
                raise ValueError("report payload must be a JSON object")
        key_id = args.key_id or str(
            (((bundle.get("manifest") or {}).get("signature") or {}).get("keyId") or "")
        )
        trusted_keys = {
            _validate_key_id(key_id): _b64decode(
                args.public_key,
                expected_size=32,
                label="trusted public key",
            )
        }
        result = verify_bundle(
            bundle,
            artifact_bytes=artifact_bytes,
            report_payload=report_payload,
            subject_bytes=subject_bytes,
            trusted_public_keys=trusted_keys,
        )
    except (OSError, ValueError, EvidenceConfigurationError) as exc:
        result = {
            "status": "invalid",
            "complete": False,
            "errors": [f"cli_input_invalid:{type(exc).__name__}"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") == "valid" and result.get("complete") is True else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a HuiJian V2 evidence bundle without a private key")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify_parser = subparsers.add_parser(
        "verify",
        help="verify a signed manifest, PDF artifact, report payload, and original subject",
    )
    verify_parser.add_argument("--bundle", required=True, help="JSON bundle returned by the protected verify endpoint")
    verify_parser.add_argument("--artifact", help="downloaded PDF to verify")
    verify_parser.add_argument("--report-payload", help="frozen report-payload.json to verify")
    verify_parser.add_argument("--subject", help="original submitted file to verify against the manifest")
    verify_parser.add_argument(
        "--public-key",
        required=True,
        help="trusted Ed25519 public key (base64 or base64:...)",
    )
    verify_parser.add_argument("--key-id", help="keyId for --public-key; defaults to the manifest keyId")
    verify_parser.set_defaults(handler=_cli_verify)
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
