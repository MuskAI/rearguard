"""Persistent, administrator-only model evaluation and load-test support."""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import math
import mimetypes
import os
import shutil
import socket
import sqlite3
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, UnidentifiedImageError

from imagedetection import legal_documents


DATA_ROOT = Path(
    os.environ.get("REALGUARD_INTERNAL_TEST_ROOT", "/opt/realguard-data/internal-testing")
)
DB_PATH = Path(
    os.environ.get("REALGUARD_INTERNAL_TEST_DB", str(DATA_ROOT / "internal-testing.sqlite3"))
)
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
MAX_DATASET_BYTES = 128 * 1024 * 1024
MAX_DATASET_SAMPLES = 200
MAX_WEB_IMAGES = 80
MAX_REDIRECTS = 4
MAX_EVALUATION_CONCURRENCY = 4
MAX_LOAD_CONCURRENCY = 16
MAX_LOAD_REQUESTS = 1000
MAX_LOAD_DURATION_SECONDS = 120
MAX_STORED_DATASETS = int(os.environ.get("REALGUARD_INTERNAL_TEST_MAX_DATASETS", "200"))
MAX_STORED_BYTES = int(
    os.environ.get("REALGUARD_INTERNAL_TEST_MAX_BYTES", str(10 * 1024 * 1024 * 1024))
)
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF"}
ALLOWED_LABELS = {"real", "fake", "unlabeled"}
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="internal-testing")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    ensure_schema()
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        os.chmod(DATA_ROOT, 0o700)
        connection = sqlite3.connect(DB_PATH, timeout=30)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_name TEXT,
                    default_label TEXT NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    labeled_count INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    actor_id TEXT,
                    actor_name TEXT
                );
                CREATE TABLE IF NOT EXISTS samples (
                    id TEXT PRIMARY KEY,
                    dataset_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source TEXT,
                    sha256 TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    byte_size INTEGER NOT NULL,
                    ground_truth TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(dataset_id) REFERENCES datasets(id) ON DELETE CASCADE,
                    UNIQUE(dataset_id, sha256)
                );
                CREATE INDEX IF NOT EXISTS idx_samples_dataset ON samples(dataset_id);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    dataset_id TEXT,
                    model_id TEXT NOT NULL,
                    model_name TEXT,
                    status TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    metrics_json TEXT,
                    error TEXT,
                    completed_count INTEGER NOT NULL DEFAULT 0,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL,
                    actor_id TEXT,
                    actor_name TEXT,
                    FOREIGN KEY(dataset_id) REFERENCES datasets(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    sample_id TEXT,
                    status TEXT NOT NULL,
                    predicted_label TEXT,
                    score REAL,
                    latency_ms INTEGER,
                    http_status INTEGER,
                    error TEXT,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
                    FOREIGN KEY(sample_id) REFERENCES samples(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_results_run ON results(run_id, id);
                """
            )
            connection.commit()
            os.chmod(DB_PATH, 0o600)
        finally:
            connection.close()
        _SCHEMA_READY = True


def _row_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    payload = dict(row)
    for key in ("configuration_json", "metrics_json", "response_json"):
        if key not in payload:
            continue
        target = key.removesuffix("_json")
        try:
            payload[target] = json.loads(payload.pop(key) or "{}")
        except json.JSONDecodeError:
            payload[target] = {}
            payload.pop(key, None)
    return payload


def _actor_fields(actor: dict | None) -> tuple[str, str]:
    actor = actor or {}
    return (
        str(actor.get("adminId") or actor.get("Userid") or "")[:80],
        str(actor.get("username") or "")[:80],
    )


def _safe_name(value: str, fallback: str = "sample") -> str:
    name = Path(str(value or "")).name.strip().replace("\x00", "")
    return (name or fallback)[:180]


def _normalize_label(value: str | None) -> str:
    label = str(value or "unlabeled").strip().lower()
    return label if label in ALLOWED_LABELS else "unlabeled"


def _image_payload(data: bytes) -> tuple[str, int, int, str]:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("图片为空或超过 24 MB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("不是可读取的图片") from exc
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise ValueError(f"不支持的图片格式：{image_format or 'unknown'}")
    if width < 32 or height < 32 or width > 8192 or height > 8192:
        raise ValueError(f"图片分辨率 {width}x{height} 超出 32-8192 像素范围")
    mime = Image.MIME.get(image_format) or mimetypes.guess_type(f"x.{image_format.lower()}")[0] or "image/octet-stream"
    suffix = {"JPEG": ".jpg", "TIFF": ".tif"}.get(image_format, f".{image_format.lower()}")
    return mime, width, height, suffix


def _pdf_images(data: bytes, source_name: str) -> list[tuple[str, bytes, str]]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise ValueError("服务器尚未安装 PDF 图片提取组件") from exc
    images: list[tuple[str, bytes, str]] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as document:
            for page_number, page in enumerate(document.pages, 1):
                for image_number, item in enumerate(page.images, 1):
                    try:
                        payload = item["stream"].get_data()
                        _image_payload(payload)
                    except (KeyError, ValueError, OSError):
                        continue
                    images.append((
                        f"{Path(source_name).stem}-p{page_number}-img{image_number}",
                        payload,
                        f"pdf:{page_number}",
                    ))
                    if len(images) >= MAX_DATASET_SAMPLES:
                        return images
    except Exception as exc:
        raise ValueError("PDF 无法解析或未包含可提取图片") from exc
    return images


def _docx_images(data: bytes, source_name: str) -> list[tuple[str, bytes, str]]:
    try:
        from docx import Document
    except ImportError as exc:
        raise ValueError("服务器尚未安装 Word 图片提取组件") from exc
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise ValueError("Word 文档无法解析") from exc
    images: list[tuple[str, bytes, str]] = []
    for index, relation in enumerate(document.part.rels.values(), 1):
        if "image" not in str(relation.target_ref):
            continue
        try:
            payload = relation.target_part.blob
            _image_payload(payload)
        except (AttributeError, ValueError, OSError):
            continue
        images.append((f"{Path(source_name).stem}-img{index}", payload, "docx"))
        if len(images) >= MAX_DATASET_SAMPLES:
            break
    return images


def _public_https_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(ord(char) <= 32 for char in url)
    ):
        raise ValueError("网页地址必须是不含账号信息的公网 HTTPS URL")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("网页地址端口无效") from exc
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("网页域名无法解析") from exc
    if not addresses:
        raise ValueError("网页域名没有可用地址")
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if not address.is_global:
            raise ValueError("网页地址不能指向内网、本机或保留地址")
    return url


def _bounded_get(session: requests.Session, url: str, *, accept_image: bool = False) -> requests.Response:
    current = _public_https_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        response = session.get(
            current,
            timeout=(4, 12),
            allow_redirects=False,
            stream=True,
            headers={"User-Agent": "HuiJian-Internal-Evaluation/1.0"},
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("网页重定向缺少目标地址")
            current = _public_https_url(urljoin(current, location))
            continue
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if accept_image and not content_type.startswith("image/"):
            response.close()
            raise ValueError("网页资源不是图片")
        length = int(response.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            response.close()
            raise ValueError("网页资源超过 24 MB")
        return response
    raise ValueError("网页重定向次数过多")


def _read_response(response: requests.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise ValueError("网页资源超过 24 MB")
            chunks.append(chunk)
    finally:
        response.close()
    return b"".join(chunks)


def _web_images(url: str) -> list[tuple[str, bytes, str]]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise ValueError("服务器尚未安装网页图片提取组件") from exc
    session = requests.Session()
    session.trust_env = False
    page_response = _bounded_get(session, url)
    page_type = str(page_response.headers.get("Content-Type") or "").lower()
    if "text/html" not in page_type:
        page_response.close()
        raise ValueError("目标地址不是 HTML 网页")
    html = _read_response(page_response)
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for tag in soup.find_all("img"):
        source = tag.get("src") or tag.get("data-src") or tag.get("data-original")
        if not source:
            continue
        absolute = urljoin(url, source)
        if absolute not in candidates:
            candidates.append(absolute)
        if len(candidates) >= MAX_WEB_IMAGES:
            break
    images: list[tuple[str, bytes, str]] = []
    for index, source in enumerate(candidates, 1):
        try:
            payload = _read_response(_bounded_get(session, source, accept_image=True))
            _image_payload(payload)
        except (ValueError, requests.RequestException):
            continue
        images.append((f"web-image-{index}", payload, source[:500]))
    return images


def _extract_source(name: str, data: bytes) -> list[tuple[str, bytes, str]]:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return _pdf_images(data, name)
    if suffix == ".docx":
        return _docx_images(data, name)
    _image_payload(data)
    return [(Path(name).stem or "image", data, "upload")]


def create_dataset(
    uploads: list[tuple[str, bytes]],
    *,
    source_url: str = "",
    name: str = "",
    default_label: str = "unlabeled",
    labels: dict[str, str] | None = None,
    actor: dict | None = None,
) -> dict:
    if not uploads and not source_url:
        raise ValueError("请上传图片、PDF、DOCX，或填写网页地址")
    if len(uploads) > 50:
        raise ValueError("单次最多上传 50 个文件")
    total_upload = sum(len(data) for _, data in uploads)
    if total_upload > MAX_DATASET_BYTES:
        raise ValueError("单个数据集上传总量不能超过 128 MB")
    for _, data in uploads:
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("单个文件不能超过 24 MB")
    ensure_schema()
    with _connect() as connection:
        usage = connection.execute(
            "SELECT COUNT(*) AS datasets, COALESCE(SUM(total_bytes),0) AS bytes FROM datasets"
        ).fetchone()
    if int(usage["datasets"] or 0) >= MAX_STORED_DATASETS:
        raise ValueError("内部测试数据集已达到数量上限，请先删除不再使用的数据集")
    if int(usage["bytes"] or 0) + total_upload > MAX_STORED_BYTES:
        raise ValueError("内部测试存储空间已达到上限，请先清理旧数据集")

    extracted: list[tuple[str, bytes, str]] = []
    source_types: set[str] = set()
    for filename, data in uploads:
        safe_name = _safe_name(filename)
        suffix = Path(safe_name).suffix.lower()
        source_types.add("document" if suffix in {".pdf", ".docx"} else "upload")
        extracted.extend(_extract_source(safe_name, data))
        if len(extracted) >= MAX_DATASET_SAMPLES:
            break
    if source_url and len(extracted) < MAX_DATASET_SAMPLES:
        source_types.add("web")
        extracted.extend(_web_images(source_url))
    extracted = extracted[:MAX_DATASET_SAMPLES]
    if not extracted:
        raise ValueError("没有提取到符合要求的图片")

    dataset_id = f"ds_{uuid.uuid4().hex[:20]}"
    dataset_dir = DATA_ROOT / "datasets" / dataset_id
    dataset_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(dataset_dir, 0o700)
    actor_id, actor_name = _actor_fields(actor)
    normalized_default = _normalize_label(default_label)
    label_map = {
        _safe_name(key): _normalize_label(value)
        for key, value in (labels or {}).items()
    }
    seen: set[str] = set()
    samples: list[dict] = []
    total_bytes = 0
    try:
        for index, (sample_name, payload, source) in enumerate(extracted, 1):
            digest = hashlib.sha256(payload).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            mime, width, height, suffix = _image_payload(payload)
            stored_name = f"{index:04d}-{digest[:16]}{suffix}"
            path = dataset_dir / stored_name
            path.write_bytes(payload)
            os.chmod(path, 0o600)
            safe_sample_name = _safe_name(sample_name, f"sample-{index}")
            label = label_map.get(safe_sample_name, normalized_default)
            samples.append({
                "id": f"sm_{uuid.uuid4().hex[:20]}",
                "name": safe_sample_name,
                "source": source,
                "sha256": digest,
                "mimeType": mime,
                "width": width,
                "height": height,
                "byteSize": len(payload),
                "groundTruth": label,
                "storagePath": str(path),
            })
            total_bytes += len(payload)
        if not samples:
            raise ValueError("图片均为重复内容或不符合要求")
        if int(usage["bytes"] or 0) + total_bytes > MAX_STORED_BYTES:
            raise ValueError("网页或文档提取结果超过内部测试存储上限")
        created_at = _now()
        source_type = "mixed" if len(source_types) > 1 else next(iter(source_types), "upload")
        dataset_name = _safe_name(name, f"测试集 {created_at[:10]}")
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO datasets
                    (id,name,source_type,source_name,default_label,sample_count,labeled_count,
                     total_bytes,created_at,actor_id,actor_name)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    dataset_id, dataset_name, source_type, source_url[:500],
                    normalized_default, len(samples),
                    sum(1 for item in samples if item["groundTruth"] != "unlabeled"),
                    total_bytes, created_at, actor_id, actor_name,
                ),
            )
            connection.executemany(
                """
                INSERT INTO samples
                    (id,dataset_id,name,source,sha256,mime_type,width,height,byte_size,
                     ground_truth,storage_path,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        item["id"], dataset_id, item["name"], item["source"], item["sha256"],
                        item["mimeType"], item["width"], item["height"], item["byteSize"],
                        item["groundTruth"], item["storagePath"], created_at,
                    )
                    for item in samples
                ],
            )
            connection.commit()
    except Exception:
        for path in dataset_dir.glob("*"):
            path.unlink(missing_ok=True)
        dataset_dir.rmdir()
        raise
    return get_dataset(dataset_id, include_samples=True) or {}


def get_dataset(dataset_id: str, *, include_samples: bool = False) -> dict | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        if not row:
            return None
        payload = _row_dict(row) or {}
        if include_samples:
            samples = connection.execute(
                """
                SELECT id,name,source,sha256,mime_type,width,height,byte_size,ground_truth,created_at
                FROM samples WHERE dataset_id = ? ORDER BY created_at,id
                """,
                (dataset_id,),
            ).fetchall()
            payload["samples"] = [dict(item) for item in samples]
        return payload


def list_datasets(limit: int = 40) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM datasets ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_dict(row) or {} for row in rows]


def sample_path(sample_id: str) -> tuple[Path, str, str] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT storage_path,mime_type,name FROM samples WHERE id = ?", (sample_id,)
        ).fetchone()
    if not row:
        return None
    path = Path(row["storage_path"]).resolve()
    root = DATA_ROOT.resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path, str(row["mime_type"]), str(row["name"])


def update_sample_label(sample_id: str, ground_truth: str) -> dict | None:
    label = _normalize_label(ground_truth)
    with _connect() as connection:
        row = connection.execute(
            "SELECT dataset_id FROM samples WHERE id = ?", (sample_id,)
        ).fetchone()
        if not row:
            return None
        connection.execute(
            "UPDATE samples SET ground_truth = ? WHERE id = ?", (label, sample_id)
        )
        connection.execute(
            """
            UPDATE datasets
            SET labeled_count = (
                SELECT COUNT(*) FROM samples
                WHERE dataset_id = datasets.id AND ground_truth != 'unlabeled'
            )
            WHERE id = ?
            """,
            (row["dataset_id"],),
        )
        updated = connection.execute(
            """
            SELECT id,name,source,sha256,mime_type,width,height,byte_size,ground_truth,created_at
            FROM samples WHERE id = ?
            """,
            (sample_id,),
        ).fetchone()
        connection.commit()
    return dict(updated) if updated else None


def delete_dataset(dataset_id: str) -> bool:
    with _connect() as connection:
        row = connection.execute(
            "SELECT id FROM datasets WHERE id = ?", (dataset_id,)
        ).fetchone()
        if not row:
            return False
        active = connection.execute(
            """
            SELECT COUNT(*) AS count FROM runs
            WHERE dataset_id = ? AND status IN ('queued','running','cancel_requested')
            """,
            (dataset_id,),
        ).fetchone()
        if int(active["count"] or 0):
            raise ValueError("该数据集仍有运行中的任务")
        paths = [
            Path(item["storage_path"])
            for item in connection.execute(
                "SELECT storage_path FROM samples WHERE dataset_id = ?", (dataset_id,)
            ).fetchall()
        ]
        connection.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        connection.commit()
    dataset_dir = DATA_ROOT / "datasets" / dataset_id
    for path in paths:
        resolved = path.resolve()
        if DATA_ROOT.resolve() in resolved.parents:
            resolved.unlink(missing_ok=True)
    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    return True


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return int(ordered[index])


def _prediction(payload: dict) -> tuple[str, float | None]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    label = str(
        data.get("final_label")
        or data.get("verdict")
        or data.get("label")
        or ""
    ).strip().lower()
    score = data.get("fake_percentage")
    if score is not None:
        try:
            score = float(score) / 100.0
        except (TypeError, ValueError):
            score = None
    if score is None:
        try:
            score = float(data.get("aiProbability", data.get("confidence")))
        except (TypeError, ValueError):
            score = None
    if any(token in label for token in ("highly_suspected_fake", "suspected_fake", "ai生成", "fake")):
        return "fake", score
    if any(token in label for token in ("真实", "real")):
        return "real", score
    if score is not None:
        return ("fake" if score >= 0.5 else "real"), score
    return "unknown", None


def run_model(model: dict, image: bytes, filename: str, mime_type: str) -> dict:
    endpoint = str(model.get("endpoint") or "").strip()
    if not endpoint:
        raise ValueError("模型没有配置 Endpoint")
    started = time.monotonic()
    headers = {
        "X-RealGuard-Internal-Test": "1",
        "X-RealGuard-Test-Run": uuid.uuid4().hex,
    }
    files = {
        "image_file": (filename, io.BytesIO(image), mime_type),
    }
    data = {
        "openid": "__realguard_internal_test__",
        "phone": "__internal_test__",
        "source_task_id": "internal-test",
    }
    if "/api/detect" in endpoint:
        token = (
            os.environ.get("REALGUARD_V2_INTERNAL_TOKEN")
            or os.environ.get("JIANZHEN_ACCESS_TOKEN")
            or ""
        ).strip()
        if token:
            headers["X-Jianzhen-Token"] = token
        files = {"file": (filename, io.BytesIO(image), mime_type)}
        data = {
            "fileType": "image",
            "upload_consent": "1",
            "consent_version": legal_documents.CONSENT_VERSION,
            "terms_sha256": legal_documents.TERMS.sha256,
            "privacy_sha256": legal_documents.PRIVACY.sha256,
        }
    timeout = min(max(int(model.get("timeoutSeconds") or 45), 2), 120)
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(
            endpoint,
            headers=headers,
            files=files,
            data=data,
            timeout=timeout,
            allow_redirects=False,
        )
        latency = int((time.monotonic() - started) * 1000)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        predicted, score = _prediction(payload)
        return {
            "ok": 200 <= response.status_code < 300,
            "httpStatus": response.status_code,
            "latencyMs": latency,
            "predictedLabel": predicted,
            "score": score,
            "payload": payload,
            "error": "" if 200 <= response.status_code < 300 else response.text[:500],
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "httpStatus": None,
            "latencyMs": int((time.monotonic() - started) * 1000),
            "predictedLabel": "unknown",
            "score": None,
            "payload": {},
            "error": str(exc)[:500],
        }
    finally:
        session.close()


def _run_row(run_id: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _row_dict(row)


def _set_run(run_id: str, **updates) -> None:
    allowed = {
        "status", "metrics_json", "error", "completed_count", "total_count",
        "started_at", "finished_at", "updated_at",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    values["updated_at"] = _now()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with _connect() as connection:
        connection.execute(
            f"UPDATE runs SET {assignments} WHERE id = ?",
            (*values.values(), run_id),
        )
        connection.commit()


def _is_cancelled(run_id: str) -> bool:
    row = _run_row(run_id)
    return bool(row and row.get("status") == "cancel_requested")


def _evaluation_metrics(results: list[dict]) -> dict:
    latencies = [int(item["latencyMs"]) for item in results if item.get("ok")]
    labeled = [
        item for item in results
        if item.get("groundTruth") in {"real", "fake"}
        and item.get("predictedLabel") in {"real", "fake"}
    ]
    tp = sum(item["groundTruth"] == "fake" and item["predictedLabel"] == "fake" for item in labeled)
    tn = sum(item["groundTruth"] == "real" and item["predictedLabel"] == "real" for item in labeled)
    fp = sum(item["groundTruth"] == "real" and item["predictedLabel"] == "fake" for item in labeled)
    fn = sum(item["groundTruth"] == "fake" and item["predictedLabel"] == "real" for item in labeled)
    accuracy = (tp + tn) / len(labeled) if labeled else None
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "sampleCount": len(results),
        "successCount": sum(bool(item.get("ok")) for item in results),
        "failureCount": sum(not bool(item.get("ok")) for item in results),
        "labeledCount": len(labeled),
        "confusionMatrix": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "latency": {
            "meanMs": round(statistics.fmean(latencies), 1) if latencies else None,
            "p50Ms": _percentile(latencies, 0.50),
            "p95Ms": _percentile(latencies, 0.95),
            "p99Ms": _percentile(latencies, 0.99),
            "maxMs": max(latencies) if latencies else None,
        },
    }


def _execute_evaluation(run_id: str, model: dict, concurrency: int) -> None:
    _set_run(run_id, status="running", started_at=_now())
    with _connect() as connection:
        run = connection.execute("SELECT dataset_id FROM runs WHERE id = ?", (run_id,)).fetchone()
        samples = connection.execute(
            "SELECT * FROM samples WHERE dataset_id = ? ORDER BY created_at,id",
            (run["dataset_id"],),
        ).fetchall()
    completed: list[dict] = []

    def evaluate(row: sqlite3.Row) -> dict:
        path = Path(row["storage_path"])
        result = run_model(model, path.read_bytes(), row["name"], row["mime_type"])
        return {
            **result,
            "sampleId": row["id"],
            "groundTruth": row["ground_truth"],
        }

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(evaluate, row): row for row in samples}
            for future in as_completed(futures):
                if _is_cancelled(run_id):
                    for pending in futures:
                        pending.cancel()
                    break
                row = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "ok": False,
                        "sampleId": row["id"],
                        "groundTruth": row["ground_truth"],
                        "predictedLabel": "unknown",
                        "score": None,
                        "latencyMs": None,
                        "httpStatus": None,
                        "payload": {},
                        "error": str(exc)[:500],
                    }
                completed.append(result)
                with _connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO results
                            (run_id,sample_id,status,predicted_label,score,latency_ms,
                             http_status,error,response_json,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            run_id, result["sampleId"],
                            "success" if result["ok"] else "failed",
                            result["predictedLabel"], result["score"], result["latencyMs"],
                            result["httpStatus"], result["error"],
                            json.dumps(result["payload"], ensure_ascii=False)[:200000],
                            _now(),
                        ),
                    )
                    connection.commit()
                _set_run(run_id, completed_count=len(completed))
        status = "cancelled" if _is_cancelled(run_id) else "completed"
        _set_run(
            run_id,
            status=status,
            metrics_json=json.dumps(_evaluation_metrics(completed), ensure_ascii=False),
            finished_at=_now(),
        )
    except Exception as exc:
        _set_run(run_id, status="failed", error=str(exc)[:1000], finished_at=_now())


def create_evaluation(
    dataset_id: str,
    model: dict,
    *,
    concurrency: int = 1,
    actor: dict | None = None,
) -> dict:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise ValueError("测试数据集不存在")
    concurrency = max(1, min(int(concurrency), MAX_EVALUATION_CONCURRENCY))
    run_id = f"eval_{uuid.uuid4().hex[:20]}"
    actor_id, actor_name = _actor_fields(actor)
    created_at = _now()
    model_snapshot = {
        "id": str(model.get("id") or ""),
        "name": str(model.get("name") or model.get("id") or ""),
        "version": str(model.get("version") or model.get("modelVersion") or ""),
        "runtime": str(model.get("runtime") or ""),
        "endpointSha256": hashlib.sha256(
            str(model.get("endpoint") or "").encode("utf-8")
        ).hexdigest(),
        "timeoutSeconds": min(max(int(model.get("timeoutSeconds") or 45), 2), 120),
    }
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO runs
                (id,kind,dataset_id,model_id,model_name,status,configuration_json,
                 completed_count,total_count,created_at,updated_at,actor_id,actor_name)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, "evaluation", dataset_id, str(model.get("id") or ""),
                str(model.get("name") or model.get("id") or ""), "queued",
                json.dumps(
                    {"concurrency": concurrency, "modelSnapshot": model_snapshot},
                    ensure_ascii=False,
                ),
                0, int(dataset.get("sample_count") or 0), created_at, created_at,
                actor_id, actor_name,
            ),
        )
        connection.commit()
    _EXECUTOR.submit(_execute_evaluation, run_id, dict(model), concurrency)
    return _run_row(run_id) or {}


def _execute_load_test(
    run_id: str,
    model: dict,
    image: bytes,
    filename: str,
    mime_type: str,
    concurrency: int,
    request_count: int,
    duration_seconds: int,
) -> None:
    started_monotonic = time.monotonic()
    _set_run(run_id, status="running", started_at=_now())
    results: list[dict] = []

    def invoke() -> dict:
        return run_model(model, image, filename, mime_type)

    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            pending = set()
            submitted = 0
            while submitted < request_count:
                if _is_cancelled(run_id) or time.monotonic() - started_monotonic >= duration_seconds:
                    break
                while len(pending) < concurrency and submitted < request_count:
                    pending.add(pool.submit(invoke))
                    submitted += 1
                done = {future for future in pending if future.done()}
                if not done:
                    time.sleep(0.02)
                    continue
                for future in done:
                    pending.remove(future)
                    results.append(future.result())
                _set_run(run_id, completed_count=len(results), total_count=submitted)
            for future in as_completed(pending):
                results.append(future.result())
                _set_run(run_id, completed_count=len(results), total_count=submitted)
        elapsed = max(time.monotonic() - started_monotonic, 0.001)
        latencies = [int(item["latencyMs"]) for item in results if item.get("ok")]
        status = "cancelled" if _is_cancelled(run_id) else "completed"
        metrics = {
            "requestCount": len(results),
            "successCount": sum(bool(item.get("ok")) for item in results),
            "failureCount": sum(not bool(item.get("ok")) for item in results),
            "elapsedSeconds": round(elapsed, 3),
            "throughputRps": round(len(results) / elapsed, 3),
            "errorRate": round(
                sum(not bool(item.get("ok")) for item in results) / len(results), 4
            ) if results else None,
            "latency": {
                "meanMs": round(statistics.fmean(latencies), 1) if latencies else None,
                "p50Ms": _percentile(latencies, 0.50),
                "p95Ms": _percentile(latencies, 0.95),
                "p99Ms": _percentile(latencies, 0.99),
                "maxMs": max(latencies) if latencies else None,
            },
            "statusCodes": {
                str(code): sum(item.get("httpStatus") == code for item in results)
                for code in sorted({item.get("httpStatus") for item in results}, key=str)
            },
        }
        _set_run(
            run_id,
            status=status,
            metrics_json=json.dumps(metrics, ensure_ascii=False),
            finished_at=_now(),
        )
    except Exception as exc:
        _set_run(run_id, status="failed", error=str(exc)[:1000], finished_at=_now())


def create_load_test(
    model: dict,
    image: bytes,
    filename: str,
    mime_type: str,
    *,
    concurrency: int,
    request_count: int,
    duration_seconds: int,
    actor: dict | None = None,
) -> dict:
    _image_payload(image)
    concurrency = max(1, min(int(concurrency), MAX_LOAD_CONCURRENCY))
    request_count = max(1, min(int(request_count), MAX_LOAD_REQUESTS))
    duration_seconds = max(1, min(int(duration_seconds), MAX_LOAD_DURATION_SECONDS))
    run_id = f"load_{uuid.uuid4().hex[:20]}"
    actor_id, actor_name = _actor_fields(actor)
    configuration = {
        "concurrency": concurrency,
        "requestCount": request_count,
        "durationSeconds": duration_seconds,
        "modelSnapshot": {
            "id": str(model.get("id") or ""),
            "name": str(model.get("name") or model.get("id") or ""),
            "version": str(model.get("version") or model.get("modelVersion") or ""),
            "runtime": str(model.get("runtime") or ""),
            "endpointSha256": hashlib.sha256(
                str(model.get("endpoint") or "").encode("utf-8")
            ).hexdigest(),
        },
        "hardLimits": {
            "maxConcurrency": MAX_LOAD_CONCURRENCY,
            "maxRequests": MAX_LOAD_REQUESTS,
            "maxDurationSeconds": MAX_LOAD_DURATION_SECONDS,
        },
    }
    created_at = _now()
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO runs
                (id,kind,model_id,model_name,status,configuration_json,completed_count,
                 total_count,created_at,updated_at,actor_id,actor_name)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, "load_test", str(model.get("id") or ""),
                str(model.get("name") or model.get("id") or ""), "queued",
                json.dumps(configuration, ensure_ascii=False), 0, request_count,
                created_at, created_at, actor_id, actor_name,
            ),
        )
        connection.commit()
    _EXECUTOR.submit(
        _execute_load_test,
        run_id,
        dict(model),
        bytes(image),
        _safe_name(filename, "load-test.png"),
        mime_type,
        concurrency,
        request_count,
        duration_seconds,
    )
    return _run_row(run_id) or {}


def cancel_run(run_id: str) -> dict | None:
    run = _run_row(run_id)
    if not run:
        return None
    if run.get("status") in {"queued", "running"}:
        _set_run(run_id, status="cancel_requested")
    return _run_row(run_id)


def list_runs(limit: int = 60) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_dict(row) or {} for row in rows]


def reconcile_stale_runs(max_idle_seconds: int = 600) -> int:
    """Fail orphaned background work after a worker restart or hard timeout."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_idle_seconds)).isoformat()
    finished_at = _now()
    with _connect() as connection:
        cursor = connection.execute(
            """
            UPDATE runs
            SET status = 'failed',
                error = '后台服务重启或任务长时间无响应，运行已中止',
                finished_at = ?,
                updated_at = ?
            WHERE status IN ('queued','running','cancel_requested')
              AND updated_at < ?
            """,
            (finished_at, finished_at, cutoff),
        )
        connection.commit()
        return int(cursor.rowcount or 0)


def get_run(run_id: str, *, include_results: bool = True) -> dict | None:
    run = _run_row(run_id)
    if not run:
        return None
    if include_results:
        with _connect() as connection:
            rows = connection.execute(
                """
                SELECT r.id,r.sample_id,r.status,r.predicted_label,r.score,r.latency_ms,
                       r.http_status,r.error,r.created_at,s.name AS sample_name,
                       s.ground_truth
                FROM results r
                LEFT JOIN samples s ON s.id = r.sample_id
                WHERE r.run_id = ? ORDER BY r.id
                """,
                (run_id,),
            ).fetchall()
        run["results"] = [dict(row) for row in rows]
    return run


def overview() -> dict:
    reconcile_stale_runs()
    datasets = list_datasets(40)
    runs = list_runs(80)
    active = [run for run in runs if run.get("status") in {"queued", "running", "cancel_requested"}]
    with _connect() as connection:
        totals = connection.execute(
            """
            SELECT COUNT(*) AS dataset_count, COALESCE(SUM(sample_count),0) AS sample_count,
                   COALESCE(SUM(labeled_count),0) AS labeled_count,
                   COALESCE(SUM(total_bytes),0) AS total_bytes
            FROM datasets
            """
        ).fetchone()
        run_count = connection.execute("SELECT COUNT(*) AS count FROM runs").fetchone()
    return {
        "datasets": datasets,
        "runs": runs,
        "summary": {
            "datasetCount": int(totals["dataset_count"] or 0),
            "sampleCount": int(totals["sample_count"] or 0),
            "labeledCount": int(totals["labeled_count"] or 0),
            "storedBytes": int(totals["total_bytes"] or 0),
            "runCount": int(run_count["count"] or 0),
            "activeRunCount": len(active),
        },
        "limits": {
            "maxSamplesPerDataset": MAX_DATASET_SAMPLES,
            "maxDatasetBytes": MAX_DATASET_BYTES,
            "maxEvaluationConcurrency": MAX_EVALUATION_CONCURRENCY,
            "maxLoadConcurrency": MAX_LOAD_CONCURRENCY,
            "maxLoadRequests": MAX_LOAD_REQUESTS,
            "maxLoadDurationSeconds": MAX_LOAD_DURATION_SECONDS,
            "maxStoredDatasets": MAX_STORED_DATASETS,
            "maxStoredBytes": MAX_STORED_BYTES,
        },
    }
