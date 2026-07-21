"""Persistent CLIP + FAISS retrieval for known explicit AI watermarks."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter


CLIP_MEAN = np.asarray((0.48145466, 0.4578275, 0.40821073), dtype=np.float32)
CLIP_STD = np.asarray((0.26862954, 0.26130258, 0.27577711), dtype=np.float32)


def _unit_rows(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return values / np.maximum(norms, 1e-8)


def prepare_clip_pixels(image: Image.Image, size: int = 224) -> np.ndarray:
    """Fit the complete candidate into CLIP's square input without cropping text."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    scale = min(size / max(width, 1), size / max(height, 1))
    resized = rgb.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        Image.Resampling.BICUBIC,
    )
    pixels = np.asarray(rgb, dtype=np.uint8)
    border = np.concatenate((pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]), axis=0)
    fill = tuple(int(value) for value in np.median(border, axis=0))
    canvas = Image.new("RGB", (size, size), fill)
    canvas.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
    values = np.asarray(canvas, dtype=np.float32) / 255.0
    values = (values - CLIP_MEAN) / CLIP_STD
    return np.transpose(values, (2, 0, 1))


def extract_bright_overlay(image: Image.Image) -> Image.Image:
    """Suppress scene content and retain pale, locally brighter overlay strokes."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    gray = rgb.mean(axis=2)
    saturation = rgb.max(axis=2) - rgb.min(axis=2)
    radius = max(2.0, min(image.size) / 18.0)
    background = np.asarray(
        Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius)),
        dtype=np.float32,
    )
    signal = np.maximum(gray - background, 0.0)
    signal *= np.clip((70.0 - saturation) / 35.0, 0.0, 1.0)
    scale = float(np.percentile(signal, 99.5)) if signal.size else 0.0
    if scale > 1e-6:
        signal = np.clip(signal / scale, 0.0, 1.0)
    signal[signal < 0.08] = 0.0
    rendered = (signal * 255.0).astype(np.uint8)
    return Image.fromarray(np.repeat(rendered[:, :, None], 3, axis=2), mode="RGB")


class ClipImageEncoder:
    def __init__(self, model_name: str, device: str = "auto", representation: str = "bright_overlay_v1") -> None:
        import torch
        from transformers import CLIPVisionModelWithProjection

        selected = device
        if selected == "auto":
            selected = "cpu"
            if torch.cuda.is_available():
                major, minor = torch.cuda.get_device_capability(0)
                supported = set(torch.cuda.get_arch_list())
                if f"sm_{major}{minor}" in supported:
                    selected = "cuda"
        self.device = selected
        self.torch = torch
        self.model_name = model_name
        self.representation = representation
        self.model = CLIPVisionModelWithProjection.from_pretrained(
            model_name,
            local_files_only=True,
        ).to(selected)
        self.model.eval()

    def embed(self, images: list[Image.Image], batch_size: int = 16) -> np.ndarray:
        if not images:
            return np.empty((0, 512), dtype=np.float32)
        batches: list[np.ndarray] = []
        for offset in range(0, len(images), batch_size):
            selected = images[offset:offset + batch_size]
            if self.representation == "bright_overlay_v1":
                selected = [extract_bright_overlay(image) for image in selected]
            values = np.stack(
                [prepare_clip_pixels(image) for image in selected]
            )
            tensor = self.torch.from_numpy(values).to(self.device)
            with self.torch.inference_mode():
                output = self.model(pixel_values=tensor).image_embeds
            batches.append(output.float().cpu().numpy())
        return _unit_rows(np.concatenate(batches, axis=0))


class WatermarkRetrievalIndex:
    def __init__(
        self,
        index_dir: Path,
        model_name: str,
        device: str = "auto",
    ) -> None:
        import faiss

        self.index_dir = Path(index_dir)
        metadata = json.loads((self.index_dir / "metadata.json").read_text(encoding="utf-8"))
        calibration = json.loads((self.index_dir / "calibration.json").read_text(encoding="utf-8"))
        self.entries = metadata.get("entries") or []
        self.model_name = str(metadata.get("model") or model_name)
        self.representation = str(metadata.get("representation") or "bright_overlay_v1")
        if self.model_name != model_name:
            raise ValueError("retrieval_model_mismatch")
        self.platform_rules = calibration.get("platforms") or {}
        self.default_threshold = float(calibration.get("defaultThreshold", 0.82))
        self.default_margin = float(calibration.get("defaultMinMargin", 0.02))
        self.index = faiss.read_index(str(self.index_dir / "index.faiss"))
        if self.index.ntotal != len(self.entries):
            raise ValueError("retrieval_index_metadata_mismatch")
        self.encoder = ClipImageEncoder(self.model_name, device=device, representation=self.representation)

    def search(self, image: Image.Image, position: str | None = None, top_k: int = 8) -> dict[str, Any]:
        if not self.entries:
            return {"accepted": False, "platform": None, "similarity": 0.0, "reason": "index_empty"}
        query = self.encoder.embed([image])
        scores, indices = self.index.search(query, min(max(2, top_k), len(self.entries)))
        platform_hits: dict[str, dict[str, Any]] = {}
        top_matches: list[dict[str, Any]] = []
        for score, index in zip(scores[0], indices[0]):
            if index < 0:
                continue
            entry = self.entries[int(index)]
            match = {
                "platform": entry["platform"],
                "similarity": round(float(score), 4),
                "referenceId": entry.get("id"),
                "source": entry.get("source"),
                "position": entry.get("position"),
            }
            top_matches.append(match)
            current = platform_hits.get(entry["platform"])
            if current is None or float(score) > current["similarity"]:
                platform_hits[entry["platform"]] = {**match, "similarity": float(score)}
        ranked = sorted(platform_hits.values(), key=lambda item: item["similarity"], reverse=True)
        if not ranked:
            return {"accepted": False, "platform": None, "similarity": 0.0, "reason": "index_empty"}
        best = ranked[0]
        second_score = ranked[1]["similarity"] if len(ranked) > 1 else -1.0
        rule = self.platform_rules.get(best["platform"]) or {}
        threshold = float(rule.get("threshold", self.default_threshold))
        minimum_margin = float(rule.get("minMargin", self.default_margin))
        margin = float(best["similarity"] - second_score)
        allowed_positions = set(rule.get("positions") or [])
        position_ok = not position or not allowed_positions or position in allowed_positions
        accepted = best["similarity"] >= threshold and margin >= minimum_margin and position_ok
        if best["similarity"] < threshold:
            reason = "below_platform_threshold"
        elif margin < minimum_margin:
            reason = "ambiguous_platform_margin"
        elif not position_ok:
            reason = "position_mismatch"
        else:
            reason = "accepted"
        platform_scores: dict[str, dict[str, Any]] = {}
        for hit in ranked:
            hit_rule = self.platform_rules.get(hit["platform"]) or {}
            hit_positions = set(hit_rule.get("positions") or [])
            platform_scores[hit["platform"]] = {
                "similarity": round(float(hit["similarity"]), 4),
                "threshold": round(float(hit_rule.get("threshold", self.default_threshold)), 4),
                "positionMatch": not position or not hit_positions or position in hit_positions,
                "referenceId": hit.get("referenceId"),
                "referenceSource": hit.get("source"),
            }
        return {
            "accepted": accepted,
            "platform": best["platform"] if accepted else None,
            "candidatePlatform": best["platform"],
            "similarity": round(float(best["similarity"]), 4),
            "threshold": round(threshold, 4),
            "margin": round(margin, 4),
            "minimumMargin": round(minimum_margin, 4),
            "referenceId": best.get("referenceId"),
            "referenceSource": best.get("source"),
            "reason": reason,
            "topMatches": top_matches[:5],
            "platformScores": platform_scores,
        }


def load_from_environment(index_dir: Path, model_name: str) -> WatermarkRetrievalIndex:
    return WatermarkRetrievalIndex(
        index_dir=index_dir,
        model_name=model_name,
        device=os.getenv("WATERMARK_CLIP_DEVICE", "auto"),
    )
