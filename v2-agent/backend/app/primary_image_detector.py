"""Small internal client for the same primary image model used by Fast mode."""
from __future__ import annotations

import io
import json
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from PIL import Image, ImageOps, UnidentifiedImageError


ENDPOINT = (
    os.getenv("JIANZHEN_PRIMARY_IMAGE_DETECT_URL")
    or os.getenv("REALGUARD_DETECTION_BACKEND_URL")
    or "http://127.0.0.1:15001/image"
).strip()
TOKEN = (
    os.getenv("JIANZHEN_PRIMARY_IMAGE_DETECT_TOKEN")
    or os.getenv("REALGUARD_DETECTOR_INTERNAL_TOKEN")
    or ""
).strip()
TIMEOUT_SECONDS = max(5.0, float(os.getenv("JIANZHEN_PRIMARY_IMAGE_DETECT_TIMEOUT", "180")))
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TRANSPORT_SIDE = max(512, int(os.getenv("JIANZHEN_PRIMARY_IMAGE_MAX_SIDE", "2048")))
RECOMPRESS_BYTES = max(1024 * 1024, int(os.getenv("JIANZHEN_PRIMARY_IMAGE_RECOMPRESS_BYTES", "8388608")))
JPEG_QUALITY = min(96, max(80, int(os.getenv("JIANZHEN_PRIMARY_IMAGE_JPEG_QUALITY", "92"))))


class PrimaryDetectorError(RuntimeError):
    """A user-safe primary detector failure."""

    def __init__(self, message: str, *, status: int = 503, code: str = "detector_unavailable"):
        super().__init__(message)
        self.status = status
        self.code = code


def status() -> dict[str, Any]:
    return {
        "configured": bool(ENDPOINT and TOKEN),
        "timeoutSeconds": TIMEOUT_SECONDS,
        "maxTransportSide": MAX_TRANSPORT_SIDE,
    }


def _clean_filename(filename: str) -> str:
    return (Path(filename).name.replace('"', "").replace("\r", "").replace("\n", "") or "image.bin")[:160]


def _prepare_transport(filename: str, data: bytes) -> tuple[str, str, bytes]:
    """Downsample oversized inputs without changing the source evidence bytes."""
    safe_name = _clean_filename(filename)
    mime = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    try:
        with Image.open(io.BytesIO(data)) as source:
            if getattr(source, "is_animated", False):
                source.seek(0)
            image = ImageOps.exif_transpose(source)
            should_resize = max(image.size) > MAX_TRANSPORT_SIDE
            should_recompress = len(data) > RECOMPRESS_BYTES
            if not should_resize and not should_recompress:
                return safe_name, mime, data

            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                image = flattened
            else:
                image = image.convert("RGB")
            if should_resize:
                image.thumbnail((MAX_TRANSPORT_SIDE, MAX_TRANSPORT_SIDE), Image.Resampling.LANCZOS)

            output = io.BytesIO()
            options: dict[str, Any] = {
                "format": "JPEG",
                "quality": JPEG_QUALITY,
                "optimize": True,
                "progressive": True,
            }
            exif = source.getexif()
            if exif:
                options["exif"] = exif.tobytes()
            xmp = source.info.get("xmp") or source.info.get("XML:com.adobe.xmp")
            if xmp:
                options["xmp"] = xmp.encode("utf-8") if isinstance(xmp, str) else xmp
            image.save(output, **options)
            optimized = output.getvalue()
            if not should_resize and len(optimized) >= len(data):
                return safe_name, mime, data
            return f"{Path(safe_name).stem or 'image'}.jpg", "image/jpeg", optimized
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise PrimaryDetectorError("提取图片无法被主模型读取", status=415, code="invalid_image") from exc


def _multipart(
    filename: str,
    mime: str,
    data: bytes,
    fields: dict[str, str],
) -> tuple[bytes, str]:
    boundary = f"huijian-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        safe_name = str(name).replace('"', "")
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{safe_name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image_file"; filename="{_clean_filename(filename)}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.extend((data, f"\r\n--{boundary}--\r\n".encode("ascii")))
    return b"".join(chunks), boundary


def _error_payload(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _score(data: dict[str, Any]) -> float:
    value = data.get("fake_percentage")
    if value is not None:
        try:
            return min(max(float(str(value).replace("%", "")) / 100.0, 0.0), 1.0)
        except (TypeError, ValueError):
            pass
    try:
        return min(max(float(data.get("detector_probability")), 0.0), 1.0)
    except (TypeError, ValueError):
        raise PrimaryDetectorError("主模型响应缺少有效分数", status=502, code="invalid_detector_response")


def analyze(
    filename: str,
    data: bytes,
    *,
    account_uuid: str = "",
    phone: str = "",
    openid: str = "",
) -> dict[str, Any]:
    if not ENDPOINT or not TOKEN:
        raise PrimaryDetectorError("主模型内部连接尚未配置")
    transport_name, mime, transport_data = _prepare_transport(filename, data)
    body, boundary = _multipart(
        transport_name,
        mime,
        transport_data,
        {
            "account_uuid": account_uuid,
            "phone": phone,
            "openid": openid,
            "defer_visual_llm": "1",
            "persist_result": "0",
        },
    )
    request = urlrequest.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-RealGuard-Detector-Token": TOKEN,
        },
    )
    try:
        with urlrequest.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urlerror.HTTPError as exc:
        raw = exc.read(MAX_RESPONSE_BYTES + 1)
        payload = _error_payload(raw)
        message = str(payload.get("msg") or payload.get("message") or "主模型暂不可用")
        raise PrimaryDetectorError(
            message,
            status=int(exc.code or 503),
            code=str(payload.get("errorCode") or "detector_http_error"),
        ) from exc
    except (urlerror.URLError, TimeoutError, OSError) as exc:
        raise PrimaryDetectorError("主模型连接失败，请稍后重试") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise PrimaryDetectorError("主模型响应超过安全上限", status=502, code="detector_response_too_large")
    payload = _error_payload(raw)
    if payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
        raise PrimaryDetectorError(
            str(payload.get("msg") or "主模型返回了无效响应"),
            status=502,
            code="invalid_detector_response",
        )

    result = payload["data"]
    probability = _score(result)
    label = str(result.get("final_label") or "")
    verdict = "real" if label == "真实图像" or (not label and probability < 0.5) else "highly_suspected_fake"
    remote_evidence = result.get("remote_evidence") if isinstance(result.get("remote_evidence"), dict) else {}
    model_decision = remote_evidence.get("modelDecision") if isinstance(remote_evidence.get("modelDecision"), dict) else {}
    embedded_precheck = remote_evidence.get("visibleWatermarkPrecheck") if isinstance(remote_evidence.get("visibleWatermarkPrecheck"), dict) else None
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    return {
        "verdict": verdict,
        "confidence": probability,
        "aiProbability": probability,
        "riskScore": probability,
        "modelVersion": str(meta.get("model") or model_decision.get("modelRevision") or "realguard-primary"),
        "source": "primary_model",
        "decisionStatus": "verdict" if model_decision.get("ready") is True else "review_only",
        "reviewRequired": model_decision.get("ready") is not True,
        "explanation": str(result.get("explanation") or "主鉴伪模型已完成分析。"),
        "dimensions": [],
        "regions": [],
        "remoteEvidence": remote_evidence,
        "visibleWatermarkPrecheck": embedded_precheck,
        "transport": {
            "downsampled": len(transport_data) != len(data) or transport_name != _clean_filename(filename),
            "sourceBytes": len(data),
            "modelBytes": len(transport_data),
            "maxSide": MAX_TRANSPORT_SIDE,
        },
    }
