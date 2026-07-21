"""Build and calibrate the explicit-watermark FAISS gallery from public captures."""
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import faiss
import numpy as np
from PIL import Image

from retrieval import ClipImageEncoder


@dataclass(frozen=True)
class CaptureSpec:
    pattern: str
    platform: str
    position: str
    bbox: tuple[float, float, float, float]
    include_tokens: tuple[str, ...] = ("black", "gray")


CAPTURE_SPECS = (
    CaptureSpec("gemini_capture/captures/*.png", "Google Gemini", "bottom-right", (0.90, 0.89, 0.10, 0.10)),
    CaptureSpec("doubao_capture/captures/*.png", "豆包", "bottom-right", (0.81, 0.93, 0.19, 0.07)),
    CaptureSpec("jimeng_capture/captures/*.png", "即梦AI", "bottom-right", (0.77, 0.91, 0.23, 0.09), ("cap_a", "cap_c")),
    CaptureSpec("samsung_capture/captures/*.png", "Samsung Galaxy AI", "bottom-left", (0.0, 0.94, 0.345, 0.06)),
    CaptureSpec("real/gemini/*.png", "Google Gemini", "bottom-right", (0.92, 0.875, 0.08, 0.105), ()),
    CaptureSpec("real/doubao/*.png", "豆包", "bottom-right", (0.81, 0.93, 0.19, 0.07), ()),
)

NEGATIVE_CROPS = (
    ("bottom-right", (0.70, 0.86, 0.30, 0.14)),
    ("bottom-left", (0.0, 0.88, 0.45, 0.12)),
    ("top-left", (0.0, 0.0, 0.30, 0.14)),
)


def _crop(image: Image.Image, bbox: tuple[float, float, float, float]) -> Image.Image:
    x, y, width, height = bbox
    iw, ih = image.size
    return image.crop((round(x * iw), round(y * ih), round((x + width) * iw), round((y + height) * ih))).convert("RGB")


def _iter_images(paths: Iterable[Path]) -> Iterable[Path]:
    for root in paths:
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                yield path


def collect_references(data_root: Path, crops_dir: Path) -> tuple[list[Image.Image], list[dict]]:
    images: list[Image.Image] = []
    entries: list[dict] = []
    crops_dir.mkdir(parents=True, exist_ok=True)
    for spec in CAPTURE_SPECS:
        for source in sorted(data_root.glob(spec.pattern)):
            lowered = source.stem.lower()
            if spec.include_tokens and not any(token in lowered for token in spec.include_tokens):
                continue
            with Image.open(source) as original:
                crop = _crop(original, spec.bbox)
            identifier = f"{spec.platform.replace(' ', '-').lower()}-{source.stem}"
            target = crops_dir / f"{identifier}.png"
            crop.save(target, optimize=True)
            images.append(crop)
            entries.append({
                "id": identifier,
                "platform": spec.platform,
                "position": spec.position,
                "source": f"wiltodelta/remove-ai-watermarks:{source.relative_to(data_root)}",
                "cropPath": f"crops/{target.name}",
                "bbox": {"x": spec.bbox[0], "y": spec.bbox[1], "w": spec.bbox[2], "h": spec.bbox[3]},
            })
    return images, entries


def collect_negative_crops(paths: list[Path]) -> tuple[list[Image.Image], list[str]]:
    crops: list[Image.Image] = []
    positions: list[str] = []
    for source in _iter_images(paths):
        try:
            with Image.open(source) as original:
                for position, bbox in NEGATIVE_CROPS:
                    crops.append(_crop(original, bbox))
                    positions.append(position)
        except OSError:
            continue
    return crops, positions


def calibrate(
    embeddings: np.ndarray,
    entries: list[dict],
    negative_embeddings: np.ndarray,
    minimum_threshold: float,
) -> dict:
    platforms = sorted({entry["platform"] for entry in entries})
    rules: dict[str, dict] = {}
    for platform in platforms:
        own = np.asarray([i for i, entry in enumerate(entries) if entry["platform"] == platform])
        other = np.asarray([i for i, entry in enumerate(entries) if entry["platform"] != platform])
        positive_scores: list[float] = []
        margins: list[float] = []
        for index in own:
            peers = own[own != index]
            if peers.size == 0:
                continue
            positive = float(np.max(embeddings[peers] @ embeddings[index]))
            rival = float(np.max(embeddings[other] @ embeddings[index])) if other.size else -1.0
            positive_scores.append(positive)
            margins.append(positive - rival)
        negative_scores = (
            np.max(negative_embeddings @ embeddings[own].T, axis=1).tolist()
            if negative_embeddings.size and own.size else []
        )
        negative_ceiling = float(np.percentile(negative_scores, 99.5)) if negative_scores else 0.0
        positive_floor = float(np.percentile(positive_scores, 10)) if positive_scores else 1.0
        if positive_floor > negative_ceiling + 0.02:
            threshold = min(positive_floor - 0.01, negative_ceiling + 0.025)
        else:
            threshold = negative_ceiling + 0.03
        threshold = float(np.clip(max(minimum_threshold, threshold), 0.0, 0.99))
        min_margin = float(np.clip(max(0.015, np.percentile(margins, 10) * 0.35 if margins else 0.02), 0.015, 0.10))
        rules[platform] = {
            "threshold": round(threshold, 4),
            "minMargin": round(min_margin, 4),
            "positions": sorted({entry["position"] for entry in entries if entry["platform"] == platform}),
            "positiveLeaveOneOutP10": round(positive_floor, 4),
            "negativeP995": round(negative_ceiling, 4),
            "referenceCount": int(own.size),
            "calibrationWarning": positive_floor <= negative_ceiling + 0.02,
        }
    return {
        "schemaVersion": 1,
        "defaultThreshold": minimum_threshold,
        "defaultMinMargin": 0.02,
        "policy": "platform_threshold + inter_platform_margin + position_match",
        "negativeCropCount": int(len(negative_embeddings)),
        "platforms": rules,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--negative-root", action="append", type=Path, default=[])
    parser.add_argument("--model", default="openai/clip-vit-base-patch32")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--minimum-threshold", type=float, default=0.82)
    args = parser.parse_args()

    output = args.output_dir
    if output.exists():
        shutil.rmtree(output)
    crops_dir = output / "crops"
    references, entries = collect_references(args.data_root, crops_dir)
    if len(references) < 2:
        raise SystemExit("not enough reference captures")
    negatives, _negative_positions = collect_negative_crops(args.negative_root)
    representation = "bright_overlay_v1"
    encoder = ClipImageEncoder(args.model, device=args.device, representation=representation)
    embeddings = encoder.embed(references)
    negative_embeddings = encoder.embed(negatives) if negatives else np.empty((0, embeddings.shape[1]), dtype=np.float32)
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    faiss.write_index(index, str(output / "index.faiss"))
    metadata = {
        "schemaVersion": 1,
        "model": args.model,
        "representation": representation,
        "embeddingDimension": int(embeddings.shape[1]),
        "entryCount": len(entries),
        "entries": entries,
    }
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    calibration = calibrate(embeddings, entries, negative_embeddings, args.minimum_threshold)
    (output / "calibration.json").write_text(json.dumps(calibration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "entries": len(entries), "calibration": calibration}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
