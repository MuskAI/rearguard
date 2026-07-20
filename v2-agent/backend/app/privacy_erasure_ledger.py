"""Durable, append-only privacy erasure tombstones shared across restores."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_LEDGER_PATH = Path(
    "/opt/realguard-data/privacy-erasure/privacy-erasure-tombstones.sqlite3"
)
MAX_IDENTIFIER_LENGTH = 256
FINGERPRINT_LENGTH = 64


class PrivacyErasureLedgerError(RuntimeError):
    pass


def ledger_path() -> Path:
    configured = os.environ.get("REALGUARD_PRIVACY_ERASURE_LEDGER_PATH", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_LEDGER_PATH


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _validate_identifier(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > MAX_IDENTIFIER_LENGTH or any(
        ord(char) < 32 for char in normalized
    ):
        raise PrivacyErasureLedgerError(f"invalid {label}")
    return normalized


def resource_fingerprint(fields: dict) -> str:
    """Hash stable resource-generation fields without retaining their raw values."""
    return hashlib.sha256(_canonical(fields).encode("ascii")).hexdigest()


def _validate_fingerprint(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != FINGERPRINT_LENGTH or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise PrivacyErasureLedgerError("invalid resource fingerprint")
    return normalized


def _connect() -> sqlite3.Connection:
    path = ledger_path()
    if not path.is_absolute():
        raise PrivacyErasureLedgerError("privacy erasure ledger path must be absolute")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    parent_stat = path.parent.lstat()
    if not stat.S_ISDIR(parent_stat.st_mode) or stat.S_ISLNK(parent_stat.st_mode):
        raise PrivacyErasureLedgerError("privacy erasure ledger directory is unsafe")
    if path.exists() and path.is_symlink():
        raise PrivacyErasureLedgerError("privacy erasure ledger cannot be a symlink")
    connection = None
    try:
        connection = sqlite3.connect(path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS privacy_erasure_tombstones (
                tombstone_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                source_system TEXT NOT NULL,
                resource_kind TEXT NOT NULL,
                primary_id TEXT NOT NULL,
                secondary_id TEXT,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_privacy_tombstones_created
                ON privacy_erasure_tombstones(created_at, tombstone_id);
            CREATE TRIGGER IF NOT EXISTS privacy_tombstones_immutable_update
            BEFORE UPDATE ON privacy_erasure_tombstones
            BEGIN
                SELECT RAISE(ABORT, 'privacy erasure tombstones are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS privacy_tombstones_immutable_delete
            BEFORE DELETE ON privacy_erasure_tombstones
            BEGIN
                SELECT RAISE(ABORT, 'privacy erasure tombstones are immutable');
            END;
            """
        )
        connection.commit()
        os.chmod(path, 0o600)
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise PrivacyErasureLedgerError("privacy erasure ledger file is unsafe")
        return connection
    except Exception as exc:
        if connection is not None:
            connection.close()
        if isinstance(exc, PrivacyErasureLedgerError):
            raise
        raise PrivacyErasureLedgerError("privacy erasure ledger is unavailable") from exc


def _record_phase(
    source_system: str,
    resource_kind: str,
    primary_id: object,
    secondary_id: object | None,
    *,
    resource_fingerprint_value: object,
    phase: str,
) -> dict:
    source = _validate_identifier(source_system, "source system")
    kind = _validate_identifier(resource_kind, "resource kind")
    primary = _validate_identifier(primary_id, "primary id")
    secondary = (
        _validate_identifier(secondary_id, "secondary id")
        if secondary_id is not None and str(secondary_id).strip()
        else None
    )
    fingerprint = _validate_fingerprint(resource_fingerprint_value)
    identity = {
        "sourceSystem": source,
        "resourceKind": kind,
        "primaryId": primary,
        "secondaryId": secondary,
        "resourceFingerprint": fingerprint,
    }
    operation_id = "eraseop_" + hashlib.sha256(
        _canonical(identity).encode("ascii")
    ).hexdigest()
    normalized_phase = str(phase or "").strip().lower()
    if normalized_phase not in {"prepared", "committed"}:
        raise PrivacyErasureLedgerError("invalid erasure phase")
    payload = {
        "schema": "com.huijian.privacy-erasure-tombstone.v2",
        "operationId": operation_id,
        "phase": normalized_phase,
        "sourceSystem": _validate_identifier(source_system, "source system"),
        "resourceKind": _validate_identifier(resource_kind, "resource kind"),
        "primaryId": primary,
        "secondaryId": secondary,
        "resourceFingerprint": fingerprint,
    }
    payload_json = _canonical(payload)
    payload_sha256 = hashlib.sha256(payload_json.encode("ascii")).hexdigest()
    # The digest is the stable public identifier; the phase is part of the
    # signed payload, not the identifier. This keeps replay tooling portable.
    tombstone_id = f"erase_{payload_sha256}"
    created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT payload_json, payload_sha256 FROM privacy_erasure_tombstones "
            "WHERE tombstone_id = ?",
            (tombstone_id,),
        ).fetchone()
        if existing:
            if existing["payload_json"] != payload_json or existing["payload_sha256"] != payload_sha256:
                raise PrivacyErasureLedgerError("privacy erasure tombstone conflict")
        else:
            connection.execute(
                """
                INSERT INTO privacy_erasure_tombstones
                    (tombstone_id, created_at, source_system, resource_kind,
                     primary_id, secondary_id, payload_json, payload_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tombstone_id,
                    created_at,
                    payload["sourceSystem"],
                    payload["resourceKind"],
                    payload["primaryId"],
                    payload["secondaryId"],
                    payload_json,
                    payload_sha256,
                ),
            )
        connection.commit()
    return {"tombstoneId": tombstone_id, "payloadSha256": payload_sha256, **payload}


def prepare_tombstone(
    source_system: str,
    resource_kind: str,
    primary_id: object,
    secondary_id: object | None = None,
    *,
    resource_fingerprint_value: object,
) -> dict:
    return _record_phase(
        source_system,
        resource_kind,
        primary_id,
        secondary_id,
        resource_fingerprint_value=resource_fingerprint_value,
        phase="prepared",
    )


def commit_tombstone(prepared: dict) -> dict:
    if not isinstance(prepared, dict) or prepared.get("phase") != "prepared":
        raise PrivacyErasureLedgerError("invalid prepared erasure tombstone")
    committed = _record_phase(
        str(prepared.get("sourceSystem") or ""),
        str(prepared.get("resourceKind") or ""),
        prepared.get("primaryId"),
        prepared.get("secondaryId"),
        resource_fingerprint_value=prepared.get("resourceFingerprint"),
        phase="committed",
    )
    if committed["operationId"] != prepared.get("operationId"):
        raise PrivacyErasureLedgerError("erasure operation identity changed")
    return committed


def record_tombstone(
    source_system: str,
    resource_kind: str,
    primary_id: object,
    secondary_id: object | None = None,
    *,
    resource_fingerprint_value: object | None = None,
) -> dict:
    if resource_fingerprint_value is None:
        # Backward-compatible callers still get a deterministic, non-sensitive
        # fingerprint. New deletion paths should pass the richer resource
        # fingerprint built from the persisted record.
        resource_fingerprint_value = resource_fingerprint({
            "primaryId": str(primary_id or ""),
            "secondaryId": str(secondary_id or ""),
        })
    # The common deletion path records one committed, idempotent tombstone.
    # Callers that need a crash-recoverable two-phase operation can use
    # prepare_tombstone/commit_tombstone explicitly.
    return _record_phase(
        source_system,
        resource_kind,
        primary_id,
        secondary_id,
        resource_fingerprint_value=resource_fingerprint_value,
        phase="committed",
    )


def healthcheck() -> dict:
    try:
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "SELECT COUNT(*) FROM privacy_erasure_tombstones"
            ).fetchone()
            connection.rollback()
        return {"available": True, "writable": True}
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__}
