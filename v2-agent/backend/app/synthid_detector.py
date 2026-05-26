"""Optional SynthID watermark detection adapter for V2.

This module integrates the detection path from ``reverse-SynthID`` only.  It
does not expose or call any watermark removal / bypass functions.
"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

SYNTHID_ENABLED = os.getenv("SYNTHID_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
SYNTHID_REPO_PATH = Path(os.getenv("SYNTHID_REPO_PATH", "/opt/reverse-SynthID"))
SYNTHID_CODEBOOK_PATH = Path(
    os.getenv("SYNTHID_CODEBOOK_PATH", str(SYNTHID_REPO_PATH / "artifacts" / "spectral_codebook_v4.npz"))
)
SYNTHID_MODEL_PROFILE = os.getenv("SYNTHID_MODEL_PROFILE", "gemini-3.1-flash-image-preview")
SYNTHID_TOP_K = int(os.getenv("SYNTHID_TOP_K", "128"))
SYNTHID_CONSENSUS_FLOOR = float(os.getenv("SYNTHID_CONSENSUS_FLOOR", "0.75"))

_codebook_cache: dict[str, Any] = {}
_extractor_cache: Any | None = None


def status() -> dict:
    """Return configuration and availability without importing heavy modules."""
    repo_ok = SYNTHID_REPO_PATH.exists()
    codebook_ok = SYNTHID_CODEBOOK_PATH.exists()
    return {
        "enabled": SYNTHID_ENABLED,
        "repoPath": str(SYNTHID_REPO_PATH),
        "codebookPath": str(SYNTHID_CODEBOOK_PATH),
        "modelProfile": SYNTHID_MODEL_PROFILE,
        "configured": repo_ok and codebook_ok,
        "available": SYNTHID_ENABLED and repo_ok and codebook_ok,
    }


def unavailable(reason: str) -> dict:
    return {
        "enabled": SYNTHID_ENABLED,
        "supported": False,
        "detected": None,
        "confidence": 0.0,
        "phaseMatch": 0.0,
        "profile": None,
        "modelProfile": SYNTHID_MODEL_PROFILE,
        "evidenceLevel": "unavailable",
        "note": reason,
        "error": reason,
    }


def _load_reverse_synthid() -> tuple[Any, Any]:
    extraction_path = SYNTHID_REPO_PATH / "src" / "extraction"
    if not extraction_path.exists():
        raise RuntimeError(f"reverse-SynthID extraction path not found: {extraction_path}")
    path_str = str(extraction_path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

    from robust_extractor import RobustSynthIDExtractor  # type: ignore
    from synthid_bypass_v4 import SpectralCodebookV4  # type: ignore

    return RobustSynthIDExtractor, SpectralCodebookV4


def _get_codebook() -> Any:
    key = str(SYNTHID_CODEBOOK_PATH)
    stat = SYNTHID_CODEBOOK_PATH.stat()
    cache_key = f"{key}:{stat.st_mtime_ns}:{stat.st_size}"
    if _codebook_cache.get("key") == cache_key:
        return _codebook_cache["codebook"]

    _, SpectralCodebookV4 = _load_reverse_synthid()
    codebook = SpectralCodebookV4()
    codebook.load(str(SYNTHID_CODEBOOK_PATH))
    _codebook_cache.clear()
    _codebook_cache.update({"key": cache_key, "codebook": codebook})
    return codebook


def _get_extractor() -> Any:
    global _extractor_cache
    if _extractor_cache is None:
        RobustSynthIDExtractor, _ = _load_reverse_synthid()
        _extractor_cache = RobustSynthIDExtractor()
    return _extractor_cache


def _image_array(data: bytes) -> Any:
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        return np.asarray(im.convert("RGB"))


def _evidence_level(detected: bool, confidence: float) -> str:
    if not detected:
        return "none"
    if confidence >= 0.85:
        return "strong"
    if confidence >= 0.6:
        return "medium"
    return "weak"


def detect(data: bytes) -> dict:
    """Run SynthID detection on an image.

    The return shape is JSON-friendly and stable for frontend rendering.  Any
    setup/import/runtime failure is returned as an unavailable result so the V2
    detection pipeline never fails because of this optional module.
    """
    started = time.perf_counter()
    if not SYNTHID_ENABLED:
        return unavailable("SynthID 水印检测未启用")
    if not SYNTHID_REPO_PATH.exists():
        return unavailable(f"reverse-SynthID 仓库不存在：{SYNTHID_REPO_PATH}")
    if not SYNTHID_CODEBOOK_PATH.exists():
        return unavailable(f"SynthID codebook 不存在：{SYNTHID_CODEBOOK_PATH}")

    try:
        image = _image_array(data)
        codebook = _get_codebook()
        extractor = _get_extractor()
        raw = extractor.detect_from_v4_codebook(
            image,
            codebook,
            model=SYNTHID_MODEL_PROFILE,
            top_k=SYNTHID_TOP_K,
            consensus_floor=SYNTHID_CONSENSUS_FLOOR,
        )
        details = getattr(raw, "details", {}) or {}
        detected = bool(getattr(raw, "is_watermarked", False))
        confidence = round(float(getattr(raw, "confidence", 0.0)), 4)
        phase_match = round(float(getattr(raw, "phase_match", 0.0)), 4)
        profile = details.get("profile_key")
        level = _evidence_level(detected, confidence)
        note = (
            "检测到疑似 Google Gemini SynthID 隐形水印，可作为 AI 生成的强辅助证据。"
            if detected
            else "未检测到可靠 SynthID 水印；这不能证明图片真实，只说明未发现稳定的 Gemini 水印特征。"
        )
        return {
            "enabled": True,
            "supported": True,
            "detected": detected,
            "confidence": confidence,
            "phaseMatch": phase_match,
            "profile": profile,
            "modelProfile": SYNTHID_MODEL_PROFILE,
            "exactProfileMatch": bool(details.get("exact_match", False)),
            "evidenceLevel": level,
            "note": note,
            "error": None,
            "elapsedMs": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        result = unavailable(f"SynthID 检测失败：{exc}")
        result["elapsedMs"] = int((time.perf_counter() - started) * 1000)
        return result

