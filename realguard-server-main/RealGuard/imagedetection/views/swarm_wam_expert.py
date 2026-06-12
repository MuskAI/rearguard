"""Watermark-Anything (WAM) expert for the Swarm pipeline.

WAM (facebookresearch/watermark-anything, ICLR 2025, MIT license for the
``wam_mit.pth`` checkpoint) is a deep-learning generic watermark detector that
can locate watermarked regions and recover a 32-bit message even when only a
small portion of the image carries the watermark. It complements
``swarm_watermark_expert`` (which only matches well-known AI-generator
fingerprints via blind DWT/DCT decoding) by detecting watermarks whose
*content* we don't know in advance — anyone who used WAM as their embedding
scheme.

Because WAM requires PyTorch + a 378 MB checkpoint + the full
``watermark_anything`` Python source tree, we do **not** load it in-process.
Instead the expert acts as a thin HTTP client to a sidecar service. To enable
WAM:

  1. Clone https://github.com/facebookresearch/watermark-anything and
     install its deps in a separate venv on any machine (CPU works,
     GPU is faster).
  2. Run the provided sidecar (``tools/wam_sidecar.py`` in this repo, or roll
     your own) which exposes ``POST /detect`` accepting multipart form-data
     image and returning the contract documented below.
  3. Set ``REALGUARD_WAM_SIDECAR_URL`` to the sidecar's URL (e.g.
     ``http://10.0.0.5:8901/detect``).
  4. Optionally set ``REALGUARD_WAM_SIDECAR_TOKEN`` for HMAC bearer auth.

If the URL env var is empty, the expert returns ``status='skipped'`` and the
Swarm pipeline carries on without it — the rest of the experts still vote.

# Sidecar response contract

JSON body with all fields optional except ``ok``::

    {
      "ok": true,                       // required
      "watermark_present": true|false,  // boolean
      "watermark_score": 0..1,          // confidence the image carries a WAM-style watermark
      "watermark_area_ratio": 0..1,     // fraction of image masked as watermarked
      "predicted_message_hex": "1a2b…",  // 32-bit message extracted (8 hex chars)
      "model_attribution": "string",    // optional best-guess of which generator produced it
      "latency_ms": int                 // optional sidecar self-reported latency
    }

The expert maps that to the standard expert update dict.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None  # type: ignore


SIDECAR_URL_ENV = "REALGUARD_WAM_SIDECAR_URL"
SIDECAR_TOKEN_ENV = "REALGUARD_WAM_SIDECAR_TOKEN"
SIDECAR_TIMEOUT_ENV = "REALGUARD_WAM_SIDECAR_TIMEOUT"

DEFAULT_TIMEOUT_SECONDS = 25.0
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB hard cap so the sidecar isn't DoS'd
AREA_RATIO_HIGH_CONFIDENCE = 0.05   # ≥5% of image masked → high confidence
SCORE_THRESHOLD_PRESENT = 0.55      # below this, treat as "no watermark"


def _truncate(text: str, limit: int = 120) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def sidecar_url() -> str:
    return (os.environ.get(SIDECAR_URL_ENV) or "").strip()


def is_configured() -> bool:
    return bool(sidecar_url())


def _timeout() -> float:
    raw = (os.environ.get(SIDECAR_TIMEOUT_ENV) or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _classify(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Map sidecar JSON → standard expert update."""
    present_flag = bool(payload.get("watermark_present"))
    score_raw = payload.get("watermark_score")
    try:
        score = float(score_raw) if score_raw is not None else None
    except (TypeError, ValueError):
        score = None

    area = payload.get("watermark_area_ratio")
    try:
        area_val = float(area) if area is not None else 0.0
    except (TypeError, ValueError):
        area_val = 0.0

    message_hex = str(payload.get("predicted_message_hex") or "").strip()
    attribution = str(payload.get("model_attribution") or "").strip()

    if not present_flag and (score is None or score < SCORE_THRESHOLD_PRESENT):
        return {
            "status": "success",
            "score": 0.4,
            "verdict": "WAM 未检出水印",
            "confidence": "低",
            "evidence": ["WAM 模型在图像中未检测到通用水印信号。"],
            "message": "no_watermark",
        }

    confidence = "高" if (score is not None and score >= 0.8 and area_val >= AREA_RATIO_HIGH_CONFIDENCE) else "中"
    evidence = []
    if attribution:
        evidence.append(f"WAM 检出疑似 {attribution} 留下的水印。")
    else:
        evidence.append("WAM 检出通用水印信号，但未识别具体生成模型。")
    if area_val > 0:
        evidence.append(f"水印覆盖估计 {round(area_val * 100, 1)}% 的图像区域。")
    if message_hex:
        evidence.append(f"提取出的 32-bit 水印消息：{_truncate(message_hex, 16)}。")
    return {
        "status": "success",
        "score": min(0.96, max(0.6, float(score) if score is not None else 0.7)),
        "verdict": "WAM 检测到隐式水印",
        "confidence": confidence,
        "evidence": evidence[:4],
        "message": f"present|score={score}|area={round(area_val,3)}",
    }


def run_wam_expert(image_bytes: bytes, filename: Optional[str], mimetype: Optional[str]) -> Dict[str, Any]:
    started = time.time()

    def _finish(update: Dict[str, Any]) -> Dict[str, Any]:
        latency = int((time.time() - started) * 1000)
        update.setdefault("latencyMs", latency)
        update.setdefault("provenance_kind", "wam")
        return update

    url = sidecar_url()
    if not url:
        return _finish({
            "status": "skipped",
            "score": None,
            "verdict": "WAM 未启用",
            "confidence": "",
            "evidence": [],
            "message": f"环境变量 {SIDECAR_URL_ENV} 未设置",
        })

    if requests is None:
        return _finish({
            "status": "skipped",
            "score": None,
            "verdict": "缺少 requests",
            "confidence": "",
            "evidence": [],
            "message": "requests 库未安装",
        })

    if not image_bytes:
        return _finish({
            "status": "failed",
            "score": None,
            "verdict": "无图像",
            "confidence": "",
            "evidence": [],
            "message": "未收到图像字节",
        })

    if len(image_bytes) > MAX_UPLOAD_BYTES:
        return _finish({
            "status": "failed",
            "score": None,
            "verdict": "图像超限",
            "confidence": "",
            "evidence": [],
            "message": f"图像大小超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        })

    headers: Dict[str, str] = {}
    token = (os.environ.get(SIDECAR_TOKEN_ENV) or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    files = {
        "image": (filename or "upload.bin", image_bytes, mimetype or "application/octet-stream"),
    }

    try:
        resp = requests.post(url, files=files, headers=headers, timeout=_timeout())
    except requests.exceptions.Timeout:
        return _finish({
            "status": "failed",
            "score": None,
            "verdict": "WAM 超时",
            "confidence": "",
            "evidence": [],
            "message": f"sidecar 在 {int(_timeout())}s 内未响应",
        })
    except requests.exceptions.RequestException as exc:
        return _finish({
            "status": "failed",
            "score": None,
            "verdict": "WAM 不可达",
            "confidence": "",
            "evidence": [],
            "message": _truncate(str(exc), 120),
        })

    if resp.status_code != 200:
        return _finish({
            "status": "failed",
            "score": None,
            "verdict": "WAM 服务异常",
            "confidence": "",
            "evidence": [],
            "message": f"HTTP {resp.status_code}: {_truncate(resp.text, 80)}",
        })

    try:
        payload = resp.json()
    except ValueError:
        return _finish({
            "status": "failed",
            "score": None,
            "verdict": "WAM 响应非 JSON",
            "confidence": "",
            "evidence": [],
            "message": _truncate(resp.text or "", 100),
        })

    if not isinstance(payload, dict) or not payload.get("ok"):
        return _finish({
            "status": "failed",
            "score": None,
            "verdict": "WAM 上游报错",
            "confidence": "",
            "evidence": [],
            "message": _truncate(str(payload), 100) if payload else "sidecar 返回 ok=False",
        })

    return _finish(_classify(payload))
