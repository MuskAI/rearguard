from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


MANIFEST_SCHEMA = "cn.huijian.image-evidence-manifest"
MANIFEST_VERSION = "1.0"
SIGNATURE_ALGORITHM = "HMAC-SHA256"
DEFAULT_POLICY_VERSION = "huijian-v1-image-report-policy-v1"
DEFAULT_SNAPSHOT_ROOT = Path("/opt/realguard-data/evidence-manifests")
UNRECORDED_MODEL_VERSION = "unrecorded-legacy"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{64}$")
_BASE64URL_RE = re.compile(br"^[A-Za-z0-9_-]+$")
_PDF_BEGIN = b"% HUIJIAN-EVIDENCE-MANIFEST-V1-BEGIN\n"
_PDF_END = b"% HUIJIAN-EVIDENCE-MANIFEST-V1-END\n"
_MAX_EVIDENCE_SUMMARY_LENGTH = 4000
_MAX_EMBEDDED_ENVELOPE_BYTES = 1024 * 1024
_MAX_SNAPSHOT_BYTES = 256 * 1024
_ARTIFACT_SIGNATURE_DOMAIN = b"huijian-pdf-artifact-v1\0"
_KNOWN_PLACEHOLDER_KEYS = {
    "change-me",
    "replace-me",
    "replace-with-an-independent-random-64-hex-character-key",
}


class EvidenceManifestError(RuntimeError):
    """Raised when a report cannot be backed by verifiable server evidence."""


def canonical_json(value: Mapping[str, Any]) -> bytes:
    """Return the sole byte representation covered by the HMAC signature."""
    if not isinstance(value, Mapping):
        raise EvidenceManifestError("证据清单必须是 JSON 对象")
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise EvidenceManifestError("证据清单包含不可规范化的字段") from exc
    return rendered.encode("utf-8")


def _signing_key(key: str | bytes | None = None) -> bytes:
    raw = key if key is not None else os.environ.get("REALGUARD_EVIDENCE_HMAC_KEY", "")
    if isinstance(raw, str):
        rendered = raw.strip()
        if rendered.lower() in _KNOWN_PLACEHOLDER_KEYS or rendered.lower().startswith(("change-", "replace-")):
            raise EvidenceManifestError("证据签名密钥仍是公开占位符")
        raw = rendered.encode("utf-8")
    if not isinstance(raw, bytes) or len(raw) < 32:
        raise EvidenceManifestError(
            "未配置独立证据签名密钥，REALGUARD_EVIDENCE_HMAC_KEY 至少需要 32 字节"
        )
    return raw


def sign_manifest(manifest: Mapping[str, Any], *, key: str | bytes | None = None) -> str:
    return hmac.new(_signing_key(key), canonical_json(manifest), hashlib.sha256).hexdigest()


def _utc_iso(value: datetime | str | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise EvidenceManifestError("证据清单生成时间不是有效 ISO-8601 时间") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_component(value: object, field: str) -> str:
    rendered = str(value or "").strip()
    if not rendered or rendered in {".", ".."} or Path(rendered).name != rendered:
        raise EvidenceManifestError(f"检测记录缺少安全的{field}")
    return rendered


def _record_id(item: Mapping[str, Any]) -> str:
    try:
        record_number = int(item.get("itemid"))
    except (AttributeError, TypeError, ValueError):
        record_number = 0
    if record_number <= 0:
        raise EvidenceManifestError("检测记录缺少 itemid")
    return str(record_number)


def _configured_source_roots() -> tuple[Path, ...]:
    default_root = Path(__file__).resolve().parents[1] / "static"
    configured = [
        value.strip()
        for value in os.environ.get("REALGUARD_EVIDENCE_SOURCE_ROOTS", "").split(os.pathsep)
        if value.strip()
    ]
    roots = [Path(value).expanduser() for value in configured] or [default_root]
    return tuple(root.resolve() for root in roots)


def resolve_source_path(item: Mapping[str, Any], *, source_path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve an original only inside a server-configured evidence root.

    ``source_path`` is an explicit server-side override for migrations and tests. It
    is never read from ``item`` or the client result payload.
    """
    roots = _configured_source_roots()
    candidates: list[Path] = []
    if source_path is not None:
        candidates.append(Path(source_path).expanduser())
    else:
        folder = _safe_component(item.get("openid") or item.get("phone") or "guest", "存储目录")
        filename = _safe_component(item.get("filename"), "原文件名")
        candidates.extend(root / "uploads" / folder / "image" / filename for root in roots)

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if not resolved.is_file():
            continue
        if source_path is not None:
            return resolved
        if any(os.path.commonpath((str(root), str(resolved))) == str(root) for root in roots):
            return resolved
    raise EvidenceManifestError("无法从服务端受控存储读取原始图像，拒绝生成无原件哈希的报告")


def sha256_file(path: str | os.PathLike[str]) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with Path(path).open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise EvidenceManifestError("读取原始图像失败，无法计算 SHA-256") from exc
    return digest.hexdigest(), size


def load_recorded_model_run(record_id: object) -> dict[str, Any]:
    """Load model provenance from the server audit store, never from the client."""
    try:
        from . import admin_state

        run = admin_state.model_runs_by_itemids([record_id]).get(str(record_id)) or {}
    except Exception as exc:
        raise EvidenceManifestError("无法读取服务端模型运行审计记录") from exc
    return dict(run) if isinstance(run, dict) else {}


def _model_snapshot(model_run: Mapping[str, Any] | None) -> dict[str, str]:
    run = model_run if isinstance(model_run, Mapping) else {}
    model = run.get("model") if isinstance(run.get("model"), Mapping) else {}
    model_id = str(model.get("id") or "").strip()
    model_version = str(model.get("version") or "").strip()
    return {
        "id": model_id or UNRECORDED_MODEL_VERSION,
        "version": model_version or model_id or UNRECORDED_MODEL_VERSION,
        "run_id": str(run.get("id") or "").strip(),
        "route": str(run.get("route") or "").strip(),
        "provenance": "recorded_server_run" if model_id or model_version else "legacy_record_unavailable",
    }


def _evidence_signals(model_run: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    run = model_run if isinstance(model_run, Mapping) else {}
    meta = run.get("meta") if isinstance(run.get("meta"), Mapping) else {}
    visible = meta.get("visibleWatermark") if isinstance(meta.get("visibleWatermark"), Mapping) else {}
    if not visible:
        return []
    try:
        confidence = round(max(0.0, min(1.0, float(visible.get("confidence") or 0.0))), 4)
    except (TypeError, ValueError):
        confidence = 0.0
    return [{
        "type": "visible_watermark",
        "detected": bool(visible.get("detected")),
        "confidence": confidence,
        "provider": str(visible.get("provider") or "server_watermark_detector")[:120],
        "hit_count": min(1000, len(visible.get("hits") or [])) if isinstance(visible.get("hits"), list) else 0,
    }]


def _percent(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise EvidenceManifestError("检测记录缺少有效风险分数")
    if not math.isfinite(number):
        raise EvidenceManifestError("检测记录风险分数不是有限数值")
    return round(max(0.0, min(100.0, number)), 2)


def _stored_text(item: Mapping[str, Any], key: str, default: str) -> str:
    value = str(item.get(key) or "").strip()
    return value or default


def build_image_manifest(
    item: Mapping[str, Any],
    *,
    source_path: str | os.PathLike[str] | None = None,
    model_run: Mapping[str, Any] | None = None,
    generated_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Build a manifest exclusively from the persisted record and server state."""
    if not isinstance(item, Mapping):
        raise EvidenceManifestError("检测记录格式无效")
    record_id = _record_id(item)

    original = resolve_source_path(item, source_path=source_path)
    source_sha256, source_size = sha256_file(original)
    selected_run = load_recorded_model_run(record_id) if model_run is None else model_run
    probability = _percent(item.get("fake"))
    conclusion = _stored_text(
        item,
        "aigc",
        "AI生成图像" if probability >= 50 else "真实图像",
    )
    evidence_summary = _stored_text(item, "explantation", "服务端未留存详细证据摘要")
    evidence_summary = evidence_summary[:_MAX_EVIDENCE_SUMMARY_LENGTH]

    return {
        "schema": MANIFEST_SCHEMA,
        "schema_version": MANIFEST_VERSION,
        "signature_key_id": os.environ.get(
            "REALGUARD_EVIDENCE_HMAC_KEY_ID", "v1"
        ).strip() or "v1",
        "task_id": f"IMG-{record_id}",
        "record_id": record_id,
        "source": {
            "hash_algorithm": "SHA-256",
            "sha256": source_sha256,
            "size_bytes": source_size,
        },
        "model": _model_snapshot(selected_run),
        "policy_version": os.environ.get(
            "REALGUARD_EVIDENCE_POLICY_VERSION", DEFAULT_POLICY_VERSION
        ).strip() or DEFAULT_POLICY_VERSION,
        "conclusion": {
            "label": conclusion,
            "risk_score_percent": probability,
            "confidence": _stored_text(item, "clarity", "未记录"),
        },
        "evidence_summary": {
            "text": evidence_summary,
            "source": "persisted_server_record",
            "signals": _evidence_signals(selected_run),
        },
        "generated_at": _utc_iso(generated_at),
    }


def create_signed_image_manifest(
    item: Mapping[str, Any],
    *,
    source_path: str | os.PathLike[str] | None = None,
    model_run: Mapping[str, Any] | None = None,
    generated_at: datetime | str | None = None,
    key: str | bytes | None = None,
) -> dict[str, Any]:
    """Create an in-memory signed snapshot without storing it."""
    manifest = build_image_manifest(
        item,
        source_path=source_path,
        model_run=model_run,
        generated_at=generated_at,
    )
    key_id = manifest["signature_key_id"]
    return {
        "manifest": manifest,
        "signature": {
            "algorithm": SIGNATURE_ALGORITHM,
            "key_id": key_id,
            "value": sign_manifest(manifest, key=key),
        },
    }


def _snapshot_root(snapshot_root: str | os.PathLike[str] | None = None) -> Path:
    configured = snapshot_root
    if configured is None:
        configured = os.environ.get("REALGUARD_EVIDENCE_SNAPSHOT_ROOT", "").strip()
    root = Path(configured).expanduser() if configured else DEFAULT_SNAPSHOT_ROOT
    if not root.is_absolute():
        raise EvidenceManifestError("证据快照目录必须使用绝对路径")
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_stat = root.lstat()
        if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
            raise EvidenceManifestError("证据快照目录不是安全的实体目录")
        if stat.S_IMODE(root_stat.st_mode) & 0o077:
            os.chmod(root, 0o700)
        return root.resolve(strict=True)
    except EvidenceManifestError:
        raise
    except OSError as exc:
        raise EvidenceManifestError("无法创建或访问服务端证据快照目录") from exc


def _snapshot_path(root: Path, record_id: str) -> Path:
    return root / f"image-{record_id}.manifest.json"


@contextmanager
def _snapshot_lock(root: Path, record_id: str):
    lock_path = root / f".image-{record_id}.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise EvidenceManifestError("无法锁定证据快照") from exc
    try:
        lock_stat = os.fstat(descriptor)
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
            raise EvidenceManifestError("证据快照锁文件类型或链接数异常")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _snapshot_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise EvidenceManifestError("无法检查证据快照状态") from exc
    return True


def _read_snapshot(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceManifestError("无法读取已持久化的证据快照") from exc
    try:
        snapshot_stat = os.fstat(descriptor)
        if not stat.S_ISREG(snapshot_stat.st_mode) or snapshot_stat.st_nlink != 1:
            raise EvidenceManifestError("证据快照文件类型或链接数异常")
        if snapshot_stat.st_size <= 0 or snapshot_stat.st_size > _MAX_SNAPSHOT_BYTES:
            raise EvidenceManifestError("证据快照大小异常")
        if stat.S_IMODE(snapshot_stat.st_mode) & 0o022:
            raise EvidenceManifestError("证据快照具有不安全的写权限")
        chunks: list[bytes] = []
        remaining = snapshot_stat.st_size
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) != snapshot_stat.st_size:
        raise EvidenceManifestError("证据快照读取不完整")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceManifestError("证据快照 JSON 已损坏") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"manifest", "signature"}:
        raise EvidenceManifestError("证据快照结构已损坏")
    canonical = canonical_json(envelope)
    if not hmac.compare_digest(raw, canonical):
        raise EvidenceManifestError("证据快照不是首次写入的规范化字节序列")
    return envelope


def _write_snapshot_once(path: Path, envelope: Mapping[str, Any]) -> None:
    payload = canonical_json(envelope)
    if not payload or len(payload) > _MAX_SNAPSHOT_BYTES:
        raise EvidenceManifestError("证据快照超过安全大小限制")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    created = False
    try:
        descriptor = os.open(path, flags, 0o400)
        created = True
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("short write")
            written += count
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.close(descriptor)
        descriptor = None
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_descriptor = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except FileExistsError as exc:
        raise EvidenceManifestError("证据快照已存在，拒绝覆盖首次快照") from exc
    except OSError as exc:
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise EvidenceManifestError("首次证据快照持久化失败") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_persisted_snapshot(
    envelope: Mapping[str, Any],
    *,
    record_id: str,
    source_sha256: str,
    source_size: int,
    key: str | bytes | None = None,
) -> None:
    if not verify_manifest(envelope, key=key):
        raise EvidenceManifestError("已持久化证据快照签名校验失败")
    manifest = envelope.get("manifest")
    source = manifest.get("source") if isinstance(manifest, Mapping) else None
    if not isinstance(manifest, Mapping) or manifest.get("record_id") != record_id:
        raise EvidenceManifestError("证据快照与检测记录 ID 不匹配")
    if not isinstance(source, Mapping):
        raise EvidenceManifestError("证据快照缺少原件指纹")
    if (
        source.get("sha256") != source_sha256
        or source.get("size_bytes") != source_size
    ):
        raise EvidenceManifestError("原始图像已变化，与首次证据快照不一致")


def get_or_create_signed_image_manifest(
    item: Mapping[str, Any],
    *,
    source_path: str | os.PathLike[str] | None = None,
    model_run: Mapping[str, Any] | None = None,
    generated_at: datetime | str | None = None,
    key: str | bytes | None = None,
    snapshot_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Atomically persist the first signed snapshot and reuse it thereafter."""
    if not isinstance(item, Mapping):
        raise EvidenceManifestError("检测记录格式无效")
    record_id = _record_id(item)
    original = resolve_source_path(item, source_path=source_path)
    root = _snapshot_root(snapshot_root)
    path = _snapshot_path(root, record_id)

    with _snapshot_lock(root, record_id):
        source_sha256, source_size = sha256_file(original)
        if _snapshot_exists(path):
            persisted = _read_snapshot(path)
            _validate_persisted_snapshot(
                persisted,
                record_id=record_id,
                source_sha256=source_sha256,
                source_size=source_size,
                key=key,
            )
            return persisted

        candidate = create_signed_image_manifest(
            item,
            source_path=original,
            model_run=model_run,
            generated_at=generated_at,
            key=key,
        )
        _validate_persisted_snapshot(
            candidate,
            record_id=record_id,
            source_sha256=source_sha256,
            source_size=source_size,
            key=key,
        )
        _write_snapshot_once(path, candidate)
        persisted = _read_snapshot(path)
        _validate_persisted_snapshot(
            persisted,
            record_id=record_id,
            source_sha256=source_sha256,
            source_size=source_size,
            key=key,
        )
        return persisted


def _valid_manifest_shape(manifest: object) -> bool:
    if not isinstance(manifest, Mapping):
        return False
    source = manifest.get("source")
    model = manifest.get("model")
    conclusion = manifest.get("conclusion")
    summary = manifest.get("evidence_summary")
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("schema_version") != MANIFEST_VERSION:
        return False
    if not str(manifest.get("task_id") or "") or not str(manifest.get("record_id") or ""):
        return False
    if manifest.get("task_id") != f"IMG-{manifest.get('record_id')}":
        return False
    if not str(manifest.get("policy_version") or "") or not str(manifest.get("signature_key_id") or ""):
        return False
    if not isinstance(source, Mapping) or source.get("hash_algorithm") != "SHA-256":
        return False
    if not _SHA256_RE.fullmatch(str(source.get("sha256") or "")):
        return False
    if not isinstance(source.get("size_bytes"), int) or source.get("size_bytes") < 0:
        return False
    if not isinstance(model, Mapping) or not str(model.get("version") or ""):
        return False
    if not isinstance(conclusion, Mapping) or not str(conclusion.get("label") or ""):
        return False
    risk_score = conclusion.get("risk_score_percent")
    if isinstance(risk_score, bool) or not isinstance(risk_score, (int, float)):
        return False
    if not 0 <= risk_score <= 100:
        return False
    if not isinstance(summary, Mapping) or summary.get("source") != "persisted_server_record":
        return False
    if not isinstance(summary.get("text"), str) or not isinstance(summary.get("signals"), list):
        return False
    try:
        _utc_iso(str(manifest.get("generated_at") or ""))
    except EvidenceManifestError:
        return False
    return True


def verify_manifest(
    manifest_or_envelope: Mapping[str, Any],
    signature: str | None = None,
    *,
    key: str | bytes | None = None,
) -> bool:
    """Verify an envelope, or a manifest plus a hexadecimal HMAC signature."""
    if not isinstance(manifest_or_envelope, Mapping):
        return False
    if signature is None and "manifest" in manifest_or_envelope:
        manifest = manifest_or_envelope.get("manifest")
        signature_meta = manifest_or_envelope.get("signature")
        if not isinstance(signature_meta, Mapping):
            return False
        if signature_meta.get("algorithm") != SIGNATURE_ALGORITHM:
            return False
        if signature_meta.get("key_id") != manifest.get("signature_key_id"):
            return False
        signature = str(signature_meta.get("value") or "")
    else:
        manifest = manifest_or_envelope
    if not _valid_manifest_shape(manifest) or not _SIGNATURE_RE.fullmatch(str(signature or "")):
        return False
    try:
        expected = sign_manifest(manifest, key=key)
    except EvidenceManifestError:
        return False
    return hmac.compare_digest(expected, str(signature))


def embed_envelope_in_pdf(pdf: bytes, envelope: Mapping[str, Any]) -> bytes:
    """Embed a signed canonical envelope as PDF-safe comments before EOF."""
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
        raise EvidenceManifestError("报告渲染器未返回有效 PDF")
    if _PDF_BEGIN in pdf or _PDF_END in pdf:
        raise EvidenceManifestError("PDF 已包含证据清单，拒绝重复嵌入")
    encoded = base64.urlsafe_b64encode(canonical_json(envelope)).rstrip(b"=")
    lines = [b"% " + encoded[index:index + 72] for index in range(0, len(encoded), 72)]
    eof = pdf.rfind(b"%%EOF")
    if eof < 0:
        raise EvidenceManifestError("PDF 缺少结束标记")
    embedded = _PDF_BEGIN + b"\n".join(lines) + b"\n" + _PDF_END
    return pdf[:eof] + embedded + pdf[eof:]


def _artifact_signature(
    manifest: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    key: str | bytes | None = None,
) -> str:
    payload = _ARTIFACT_SIGNATURE_DOMAIN + canonical_json(manifest) + b"\0" + canonical_json(artifact)
    return hmac.new(_signing_key(key), payload, hashlib.sha256).hexdigest()


def bind_pdf_artifact(
    pdf: bytes,
    envelope: Mapping[str, Any],
    *,
    key: str | bytes | None = None,
) -> dict[str, Any]:
    """Bind the visible PDF bytes to the signed manifest without a circular hash."""
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
        raise EvidenceManifestError("报告渲染器未返回有效 PDF")
    if not verify_manifest(envelope, key=key):
        raise EvidenceManifestError("拒绝将无效证据清单绑定到 PDF")
    manifest = envelope["manifest"]
    artifact = {
        "hash_algorithm": "SHA-256",
        "sha256": hashlib.sha256(pdf).hexdigest(),
        "size_bytes": len(pdf),
    }
    return {
        "manifest": dict(manifest),
        "signature": dict(envelope["signature"]),
        "artifact": {
            **artifact,
            "signature": {
                "algorithm": SIGNATURE_ALGORITHM,
                "key_id": manifest["signature_key_id"],
                "value": _artifact_signature(manifest, artifact, key=key),
            },
        },
    }


def extract_envelope_from_pdf(pdf: bytes) -> dict[str, Any]:
    if not isinstance(pdf, bytes):
        raise EvidenceManifestError("PDF 数据格式无效")
    start = pdf.rfind(_PDF_BEGIN)
    if start < 0:
        raise EvidenceManifestError("PDF 中未找到慧鉴证据清单")
    payload_start = start + len(_PDF_BEGIN)
    end = pdf.find(_PDF_END, payload_start)
    if end < 0 or pdf.find(_PDF_BEGIN, payload_start, end) >= 0:
        raise EvidenceManifestError("PDF 证据清单边界损坏")
    lines = pdf[payload_start:end].splitlines()
    if not lines or any(not line.startswith(b"% ") for line in lines):
        raise EvidenceManifestError("PDF 证据清单包含非法行")
    encoded = b"".join(line[2:].strip() for line in lines)
    if not encoded or len(encoded) > _MAX_EMBEDDED_ENVELOPE_BYTES or not _BASE64URL_RE.fullmatch(encoded):
        raise EvidenceManifestError("PDF 证据清单编码无效或过大")
    try:
        padding = b"=" * (-len(encoded) % 4)
        decoded = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        envelope = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceManifestError("PDF 证据清单编码损坏") from exc
    if not isinstance(envelope, dict):
        raise EvidenceManifestError("PDF 证据清单格式无效")
    return envelope


def _pdf_without_envelope(pdf: bytes) -> bytes:
    start = pdf.rfind(_PDF_BEGIN)
    if start < 0:
        raise EvidenceManifestError("PDF 中未找到慧鉴证据清单")
    end = pdf.find(_PDF_END, start + len(_PDF_BEGIN))
    if end < 0:
        raise EvidenceManifestError("PDF 证据清单边界损坏")
    return pdf[:start] + pdf[end + len(_PDF_END):]


def _verify_pdf_artifact(
    pdf: bytes,
    envelope: Mapping[str, Any],
    *,
    key: str | bytes | None = None,
) -> bool:
    artifact = envelope.get("artifact")
    if not isinstance(artifact, Mapping):
        return False
    artifact_signature = artifact.get("signature")
    if not isinstance(artifact_signature, Mapping):
        return False
    if artifact_signature.get("algorithm") != SIGNATURE_ALGORITHM:
        return False
    manifest = envelope.get("manifest")
    if not isinstance(manifest, Mapping):
        return False
    if artifact_signature.get("key_id") != manifest.get("signature_key_id"):
        return False
    unsigned_artifact = {
        "hash_algorithm": artifact.get("hash_algorithm"),
        "sha256": artifact.get("sha256"),
        "size_bytes": artifact.get("size_bytes"),
    }
    if unsigned_artifact["hash_algorithm"] != "SHA-256":
        return False
    if not _SHA256_RE.fullmatch(str(unsigned_artifact["sha256"] or "")):
        return False
    if not isinstance(unsigned_artifact["size_bytes"], int) or unsigned_artifact["size_bytes"] < 0:
        return False
    signature_value = str(artifact_signature.get("value") or "")
    if not _SIGNATURE_RE.fullmatch(signature_value):
        return False
    try:
        visible_pdf = _pdf_without_envelope(pdf)
        if len(visible_pdf) != unsigned_artifact["size_bytes"]:
            return False
        if not hmac.compare_digest(hashlib.sha256(visible_pdf).hexdigest(), unsigned_artifact["sha256"]):
            return False
        expected = _artifact_signature(manifest, unsigned_artifact, key=key)
    except EvidenceManifestError:
        return False
    return hmac.compare_digest(expected, signature_value)


def verify_pdf_report(pdf: bytes, *, key: str | bytes | None = None) -> bool:
    try:
        envelope = extract_envelope_from_pdf(pdf)
    except EvidenceManifestError:
        return False
    return verify_manifest(envelope, key=key) and _verify_pdf_artifact(pdf, envelope, key=key)
