from __future__ import annotations

from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import detector, synthid_detector


def _reset_synthid_caches() -> None:
    synthid_detector._index_cache.clear()
    synthid_detector._codebook_cache.clear()
    synthid_detector._extractor_cache = None


def test_codebook_status_discovers_multiple_models_without_expanding_profiles(tmp_path, monkeypatch):
    repo = tmp_path / "reverse-SynthID"
    repo.mkdir()
    codebook_path = repo / "spectral_codebook_v4.npz"
    half = np.zeros((4, 3, 3), dtype=np.uint8)
    phase = np.zeros((4, 3, 3), dtype=np.int8)
    keys = np.array(["model-a|4|4", "model-b|4|4"], dtype=object)
    np.savez_compressed(
        codebook_path,
        format_version=np.array(5),
        keys=keys,
        **{
            "model-a|4x4/cons": half,
            "model-a|4x4/phase": phase,
            "model-a|4x4/phase__scale": np.array(0.02, dtype=np.float32),
            "model-b|4x4/cons": half,
            "model-b|4x4/phase": phase,
            "model-b|4x4/phase__scale": np.array(0.02, dtype=np.float32),
        },
    )
    monkeypatch.setattr(synthid_detector, "SYNTHID_ENABLED", True)
    monkeypatch.setattr(synthid_detector, "SYNTHID_REPO_PATH", repo)
    monkeypatch.setattr(synthid_detector, "SYNTHID_CODEBOOK_PATH", codebook_path)
    monkeypatch.setattr(synthid_detector, "SYNTHID_MODEL_PROFILES_RAW", "auto")
    monkeypatch.setattr(synthid_detector, "SYNTHID_LEGACY_MODEL_PROFILE", "")
    _reset_synthid_caches()

    report = synthid_detector.status()

    assert report["available"] is True
    assert report["modelProfiles"] == ["model-a", "model-b"]
    assert report["profileCount"] == 2
    codebook = synthid_detector._get_codebook()
    profile, key, exact = codebook.get_profile(4, 4, model="model-b")
    assert key == ("model-b", 4, 4)
    assert exact is True
    assert profile.consensus_phase.shape == (4, 4, 3)


def test_detect_returns_ranked_possible_multi_model_result(tmp_path, monkeypatch):
    repo = tmp_path / "reverse-SynthID"
    repo.mkdir()
    codebook_path = repo / "spectral_codebook_v4.npz"
    codebook_path.write_bytes(b"placeholder")
    monkeypatch.setattr(synthid_detector, "SYNTHID_ENABLED", True)
    monkeypatch.setattr(synthid_detector, "SYNTHID_REPO_PATH", repo)
    monkeypatch.setattr(synthid_detector, "SYNTHID_CODEBOOK_PATH", codebook_path)
    monkeypatch.setattr(
        synthid_detector,
        "status",
        lambda: {"modelProfiles": ["model-a", "model-b"], "available": True},
    )
    monkeypatch.setattr(synthid_detector, "_image_array", lambda data: object())
    monkeypatch.setattr(synthid_detector, "_get_codebook", lambda: object())
    monkeypatch.setattr(synthid_detector, "_get_extractor", lambda: object())

    def fake_model_result(extractor, codebook, image, model):
        confidence = 0.70 if model == "model-a" else 0.65
        return {
            "modelProfile": model,
            "modelLabel": model,
            "supported": True,
            "detected": False,
            "possiblyDetected": True,
            "detectionState": "possible",
            "confidence": confidence,
            "phaseMatch": confidence,
            "profile": f"{model}/4x4",
            "exactResolutionMatch": True,
            "evidenceLevel": "none",
            "error": None,
        }

    monkeypatch.setattr(synthid_detector, "_model_result", fake_model_result)

    result = synthid_detector.detect(b"image")

    assert result["supported"] is True
    assert result["detected"] is False
    assert result["possiblyDetected"] is True
    assert result["detectionState"] == "possible"
    assert result["candidateModelProfiles"] == ["model-a", "model-b"]
    assert result["modelAttribution"] == "ambiguous"
    assert result["attributedModelProfile"] is None
    assert result["officialVerification"] is False


def test_experimental_synthid_only_raises_exact_strong_hit_to_suspected(monkeypatch):
    base = {
        "verdict": "real",
        "confidence": 0.2,
        "dimensions": [],
        "explanation": "基础模型倾向真实。",
    }
    monkeypatch.setattr(
        synthid_detector,
        "detect",
        lambda data: {
            "supported": True,
            "detected": True,
            "possiblyDetected": True,
            "confidence": 0.94,
            "phaseMatch": 0.71,
            "evidenceLevel": "strong",
            "exactResolutionMatch": True,
        },
    )

    result = detector._merge_synthid(base, b"image")

    assert result["verdict"] == "suspected_fake"
    assert result["confidence"] == 0.6
    assert "官方验证" in result["explanation"]


def test_possible_synthid_signal_does_not_change_verdict(monkeypatch):
    base = {
        "verdict": "real",
        "confidence": 0.2,
        "dimensions": [],
        "explanation": "基础模型倾向真实。",
    }
    monkeypatch.setattr(
        synthid_detector,
        "detect",
        lambda data: {
            "supported": True,
            "detected": False,
            "possiblyDetected": True,
            "confidence": 0.66,
            "phaseMatch": 0.58,
            "evidenceLevel": "none",
            "exactResolutionMatch": False,
        },
    )

    result = detector._merge_synthid(base, b"image")

    assert result["verdict"] == "real"
    assert result["confidence"] == 0.2
    assert result["dimensions"][-1]["result"] == "发现低强度疑似信号，待交叉验证"
