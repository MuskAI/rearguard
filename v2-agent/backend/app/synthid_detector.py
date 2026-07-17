"""Optional multi-profile SynthID image watermark evidence adapter.

The Google image verifier is not exposed as a public API. This module therefore
labels reverse-SynthID spectral matches as experimental community evidence and
never presents them as an official Google verification result. Only detection
code is imported; watermark removal and bypass functions are not called.
"""
from __future__ import annotations

import io
import os
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

load_dotenv()

SYNTHID_ENABLED = os.getenv("SYNTHID_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
SYNTHID_REPO_PATH = Path(os.getenv("SYNTHID_REPO_PATH", "/opt/reverse-SynthID"))
SYNTHID_CODEBOOK_PATH = Path(
    os.getenv("SYNTHID_CODEBOOK_PATH", str(SYNTHID_REPO_PATH / "artifacts" / "spectral_codebook_v4.npz"))
)
SYNTHID_LEGACY_MODEL_PROFILE = os.getenv("SYNTHID_MODEL_PROFILE", "").strip()
SYNTHID_MODEL_PROFILES_RAW = os.getenv("SYNTHID_MODEL_PROFILES", "").strip()
SYNTHID_TOP_K = int(os.getenv("SYNTHID_TOP_K", "128"))
SYNTHID_CONSENSUS_FLOOR = float(os.getenv("SYNTHID_CONSENSUS_FLOOR", "0.75"))
SYNTHID_DETECTION_THRESHOLD = float(os.getenv("SYNTHID_DETECTION_THRESHOLD", "0.75"))
SYNTHID_POSSIBLE_THRESHOLD = float(os.getenv("SYNTHID_POSSIBLE_THRESHOLD", "0.53"))
SYNTHID_ATTRIBUTION_MARGIN = float(os.getenv("SYNTHID_ATTRIBUTION_MARGIN", "0.08"))
SYNTHID_PROFILE_CACHE_SIZE = max(1, int(os.getenv("SYNTHID_PROFILE_CACHE_SIZE", "2")))

DETECTION_METHOD = "reverse-synthid-spectral-codebook-v4"
MODEL_LABELS = {
    "gemini-3.1-flash-image-preview": "Gemini 3.1 Flash Image",
    "nano-banana-pro-preview": "Nano Banana Pro",
    "union": "Google SynthID 通用档案",
}

_index_cache: dict[str, Any] = {}
_codebook_cache: dict[str, Any] = {}
_extractor_cache: Any | None = None
_cache_lock = threading.RLock()


def _requested_profiles() -> tuple[str, ...]:
    raw = SYNTHID_MODEL_PROFILES_RAW or SYNTHID_LEGACY_MODEL_PROFILE or "auto"
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(dict.fromkeys(values)) or ("auto",)


def _codebook_index() -> tuple[tuple[tuple[str, int, int], ...], str | None]:
    """Read only the tiny profile index, without expanding spectral arrays."""
    try:
        stat = SYNTHID_CODEBOOK_PATH.stat()
        cache_key = f"{SYNTHID_CODEBOOK_PATH}:{stat.st_mtime_ns}:{stat.st_size}"
        with _cache_lock:
            if _index_cache.get("key") == cache_key:
                return _index_cache["profiles"], None

        import numpy as np

        with np.load(SYNTHID_CODEBOOK_PATH, allow_pickle=True) as archive:
            entries = archive["keys"]
            profiles = tuple(
                (model, int(height), int(width))
                for model, height, width in (str(entry).split("|") for entry in entries)
            )
        with _cache_lock:
            _index_cache.clear()
            _index_cache.update({"key": cache_key, "profiles": profiles})
        return profiles, None
    except Exception as exc:
        return (), str(exc)


def _selected_models(available_models: tuple[str, ...]) -> tuple[str, ...]:
    requested = _requested_profiles()
    if "auto" in requested:
        return tuple(model for model in available_models if model != "union") or available_models
    return tuple(model for model in requested if model in available_models)


def status() -> dict:
    """Return configuration and model-profile availability."""
    repo_ok = SYNTHID_REPO_PATH.exists()
    codebook_ok = SYNTHID_CODEBOOK_PATH.exists()
    profile_keys: tuple[tuple[str, int, int], ...] = ()
    index_error = None
    if codebook_ok:
        profile_keys, index_error = _codebook_index()
    available_models = tuple(sorted({key[0] for key in profile_keys}))
    selected_models = _selected_models(available_models)
    configured = repo_ok and codebook_ok and bool(selected_models) and index_error is None
    return {
        "enabled": SYNTHID_ENABLED,
        "repoPath": str(SYNTHID_REPO_PATH),
        "codebookPath": str(SYNTHID_CODEBOOK_PATH),
        "requestedModelProfiles": list(_requested_profiles()),
        "modelProfiles": list(selected_models),
        "availableModelProfiles": list(available_models),
        "profileCount": len(profile_keys),
        "configured": configured,
        "available": SYNTHID_ENABLED and configured,
        "method": DETECTION_METHOD,
        "officialVerification": False,
        "error": index_error,
    }


def unavailable(reason: str) -> dict:
    current = status()
    return {
        "enabled": SYNTHID_ENABLED,
        "supported": False,
        "detected": None,
        "possiblyDetected": None,
        "detectionState": "unavailable",
        "confidence": 0.0,
        "phaseMatch": 0.0,
        "profile": None,
        "modelProfile": None,
        "modelProfiles": current.get("modelProfiles") or [],
        "candidateModelProfiles": [],
        "attributedModelProfile": None,
        "modelAttribution": "unavailable",
        "modelResults": [],
        "exactProfileMatch": False,
        "exactResolutionMatch": False,
        "evidenceLevel": "unavailable",
        "method": DETECTION_METHOD,
        "verificationAuthority": "community_experimental",
        "officialVerification": False,
        "note": reason,
        "error": reason,
    }


def _load_reverse_synthid() -> Any:
    extraction_path = SYNTHID_REPO_PATH / "src" / "extraction"
    if not extraction_path.exists():
        raise RuntimeError(f"reverse-SynthID extraction path not found: {extraction_path}")
    path_str = str(extraction_path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

    from robust_extractor import RobustSynthIDExtractor  # type: ignore

    return RobustSynthIDExtractor


def _full_symmetric(half: Any, height: int, width: int) -> Any:
    import numpy as np

    half_width = width // 2 + 1
    full = np.zeros((height, width) + half.shape[2:], dtype=half.dtype)
    full[:, :half_width] = half
    if width > 2:
        symmetric_y = (height - np.arange(height)) % height
        symmetric_x = width - np.arange(half_width, width)
        full[:, half_width:] = full[symmetric_y[:, None], symmetric_x[None, :]]
    return full


def _full_antisymmetric(half: Any, height: int, width: int) -> Any:
    import numpy as np

    half_width = width // 2 + 1
    full = np.zeros((height, width) + half.shape[2:], dtype=half.dtype)
    full[:, :half_width] = half
    if width > 2:
        symmetric_y = (height - np.arange(height)) % height
        symmetric_x = width - np.arange(half_width, width)
        full[:, half_width:] = -full[symmetric_y[:, None], symmetric_x[None, :]]
    return full


class _DetectionCodebook:
    """Detection-only, bounded-memory reader for the V4 NPZ codebook."""

    def __init__(self, path: Path, profile_keys: tuple[tuple[str, int, int], ...]):
        self.path = path
        self.keys = list(profile_keys)
        self.models = sorted({key[0] for key in profile_keys})
        self._profiles: OrderedDict[tuple[str, int, int], Any] = OrderedDict()
        self._lock = threading.RLock()

    def _load_profile(self, key: tuple[str, int, int]) -> Any:
        with self._lock:
            cached = self._profiles.get(key)
            if cached is not None:
                self._profiles.move_to_end(key)
                return cached

            import numpy as np

            model, height, width = key
            prefix = f"{model}|{height}x{width}/"
            with np.load(self.path, allow_pickle=True) as archive:
                format_version = int(archive["format_version"]) if "format_version" in archive else 0
                consensus_half = archive[prefix + "cons"].astype(np.float32) / 255.0
                phase_half = archive[prefix + "phase"].astype(np.float32)
                if format_version >= 5:
                    phase_half *= float(archive[prefix + "phase__scale"])
            profile = SimpleNamespace(
                shape=(height, width),
                consensus_coherence=_full_symmetric(consensus_half, height, width),
                consensus_phase=_full_antisymmetric(phase_half, height, width),
            )
            self._profiles[key] = profile
            self._profiles.move_to_end(key)
            while len(self._profiles) > SYNTHID_PROFILE_CACHE_SIZE:
                self._profiles.popitem(last=False)
            return profile

    def get_profile(self, height: int, width: int, model: str | None = None) -> tuple[Any, tuple[str, int, int], bool]:
        candidates = [key for key in self.keys if model is None or key[0] == model]
        if not candidates:
            raise ValueError(f"Codebook has no profile for model: {model}")
        exact_key = (model, height, width) if model is not None else None
        if exact_key in candidates:
            return self._load_profile(exact_key), exact_key, True
        if model is None:
            for key in candidates:
                if key[1:] == (height, width):
                    return self._load_profile(key), key, True

        target_aspect = height / (width + 1e-9)

        def distance(key: tuple[str, int, int]) -> float:
            _, profile_height, profile_width = key
            aspect_delta = abs(profile_height / (profile_width + 1e-9) - target_aspect) / (target_aspect + 1e-9)
            pixel_delta = abs(profile_height * profile_width - height * width) / (height * width + 1e-9)
            return aspect_delta * 2.0 + pixel_delta

        best_key = min(candidates, key=distance)
        return self._load_profile(best_key), best_key, False


def _get_codebook() -> _DetectionCodebook:
    profile_keys, index_error = _codebook_index()
    if index_error:
        raise RuntimeError(index_error)
    stat = SYNTHID_CODEBOOK_PATH.stat()
    cache_key = f"{SYNTHID_CODEBOOK_PATH}:{stat.st_mtime_ns}:{stat.st_size}"
    with _cache_lock:
        if _codebook_cache.get("key") == cache_key:
            return _codebook_cache["codebook"]
        codebook = _DetectionCodebook(SYNTHID_CODEBOOK_PATH, profile_keys)
        _codebook_cache.clear()
        _codebook_cache.update({"key": cache_key, "codebook": codebook})
        return codebook


def _get_extractor() -> Any:
    global _extractor_cache
    with _cache_lock:
        if _extractor_cache is None:
            RobustSynthIDExtractor = _load_reverse_synthid()
            _extractor_cache = RobustSynthIDExtractor()
        return _extractor_cache


def _image_array(data: bytes) -> Any:
    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return np.asarray(image.convert("RGB"))


def _evidence_level(detected: bool, confidence: float, exact_resolution: bool) -> str:
    if not detected:
        return "none"
    if exact_resolution and confidence >= 0.9:
        return "strong"
    if confidence >= 0.8:
        return "medium"
    return "weak"


def _model_result(extractor: Any, codebook: _DetectionCodebook, image: Any, model: str) -> dict:
    try:
        raw = extractor.detect_from_v4_codebook(
            image,
            codebook,
            model=model,
            top_k=SYNTHID_TOP_K,
            consensus_floor=SYNTHID_CONSENSUS_FLOOR,
        )
        details = getattr(raw, "details", {}) or {}
        confidence = round(float(getattr(raw, "confidence", 0.0)), 4)
        phase_match = round(float(getattr(raw, "phase_match", 0.0)), 4)
        exact_resolution = bool(details.get("exact_match", False))
        detected = bool(getattr(raw, "is_watermarked", False)) and confidence >= SYNTHID_DETECTION_THRESHOLD
        possibly_detected = bool(getattr(raw, "is_watermarked", False)) and confidence >= SYNTHID_POSSIBLE_THRESHOLD
        return {
            "modelProfile": model,
            "modelLabel": MODEL_LABELS.get(model, model),
            "supported": True,
            "detected": detected,
            "possiblyDetected": possibly_detected,
            "detectionState": "detected" if detected else "possible" if possibly_detected else "not_detected",
            "confidence": confidence,
            "phaseMatch": phase_match,
            "profile": details.get("profile_key"),
            "exactResolutionMatch": exact_resolution,
            "evidenceLevel": _evidence_level(detected, confidence, exact_resolution),
            "error": None,
        }
    except Exception as exc:
        return {
            "modelProfile": model,
            "modelLabel": MODEL_LABELS.get(model, model),
            "supported": False,
            "detected": None,
            "possiblyDetected": None,
            "detectionState": "unavailable",
            "confidence": 0.0,
            "phaseMatch": 0.0,
            "profile": None,
            "exactResolutionMatch": False,
            "evidenceLevel": "unavailable",
            "error": str(exc),
        }


def detect(data: bytes) -> dict:
    """Run every configured image profile and return ranked model evidence."""
    started = time.perf_counter()
    current = status()
    if not SYNTHID_ENABLED:
        return unavailable("SynthID 实验性水印检测未启用")
    if not SYNTHID_REPO_PATH.exists():
        return unavailable(f"reverse-SynthID 仓库不存在：{SYNTHID_REPO_PATH}")
    if not SYNTHID_CODEBOOK_PATH.exists():
        return unavailable(f"SynthID codebook 不存在：{SYNTHID_CODEBOOK_PATH}")
    models = tuple(current.get("modelProfiles") or [])
    if not models:
        return unavailable("SynthID codebook 中没有与配置匹配的模型档案")

    try:
        image = _image_array(data)
        codebook = _get_codebook()
        extractor = _get_extractor()
        model_results = [_model_result(extractor, codebook, image, model) for model in models]
        successful = sorted(
            (item for item in model_results if item.get("supported")),
            key=lambda item: float(item.get("confidence") or 0.0),
            reverse=True,
        )
        if not successful:
            errors = "; ".join(str(item.get("error") or "unknown error") for item in model_results)
            result = unavailable(f"SynthID 多模型检测失败：{errors}")
            result["modelResults"] = model_results
            result["elapsedMs"] = int((time.perf_counter() - started) * 1000)
            return result

        winner = successful[0]
        matches = [item for item in successful if item.get("detected")]
        possible_matches = [item for item in successful if item.get("possiblyDetected")]
        candidate_models = [str(item["modelProfile"]) for item in possible_matches]
        attributed_model = None
        attribution = "none"
        if possible_matches:
            runner_up = float(possible_matches[1].get("confidence") or 0.0) if len(possible_matches) > 1 else 0.0
            margin = float(possible_matches[0].get("confidence") or 0.0) - runner_up
            if possible_matches[0].get("exactResolutionMatch") and (
                len(possible_matches) == 1 or margin >= SYNTHID_ATTRIBUTION_MARGIN
            ):
                attributed_model = str(possible_matches[0]["modelProfile"])
                attribution = "profile_candidate"
            else:
                attribution = "ambiguous"

        detected = bool(matches)
        possibly_detected = bool(possible_matches)
        if detected and attributed_model:
            note = (
                f"实验性频谱检测发现疑似 SynthID 信号，最接近 {MODEL_LABELS.get(attributed_model, attributed_model)} 档案。"
                "该结果来自社区 codebook，不是 Google 官方验证。"
            )
        elif detected:
            note = (
                "实验性频谱检测发现疑似 SynthID 信号，但多个 Google 模型档案同时匹配，"
                "无法可靠归属具体生成模型；该结果不是 Google 官方验证。"
            )
        elif possibly_detected:
            note = (
                "频谱检测发现低强度疑似 SynthID 信号，当前仅列为待交叉验证线索，不参与自动定案。"
                "可使用 Gemini 官方验证入口进一步核验。"
            )
        else:
            note = (
                f"已检查 {len(successful)} 个 Google 图像模型档案，未发现超过阈值的稳定 SynthID 频谱信号。"
                "未检出不能证明图片真实。"
            )

        return {
            "enabled": True,
            "supported": True,
            "detected": detected,
            "possiblyDetected": possibly_detected,
            "detectionState": "detected" if detected else "possible" if possibly_detected else "not_detected",
            "confidence": float(winner.get("confidence") or 0.0),
            "phaseMatch": float(winner.get("phaseMatch") or 0.0),
            "profile": winner.get("profile"),
            "modelProfile": winner.get("modelProfile"),
            "modelProfiles": list(models),
            "candidateModelProfiles": candidate_models,
            "attributedModelProfile": attributed_model,
            "modelAttribution": attribution,
            "modelResults": model_results,
            "exactProfileMatch": bool(winner.get("exactResolutionMatch")),
            "exactResolutionMatch": bool(winner.get("exactResolutionMatch")),
            "detectionThreshold": SYNTHID_DETECTION_THRESHOLD,
            "possibleThreshold": SYNTHID_POSSIBLE_THRESHOLD,
            "attributionMargin": SYNTHID_ATTRIBUTION_MARGIN,
            "evidenceLevel": _evidence_level(
                detected,
                float(winner.get("confidence") or 0.0),
                bool(winner.get("exactResolutionMatch")),
            ),
            "method": DETECTION_METHOD,
            "verificationAuthority": "community_experimental",
            "officialVerification": False,
            "note": note,
            "error": None,
            "elapsedMs": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:
        result = unavailable(f"SynthID 检测失败：{exc}")
        result["elapsedMs"] = int((time.perf_counter() - started) * 1000)
        return result
