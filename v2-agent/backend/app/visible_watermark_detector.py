"""Visible AI watermark detector.

Detection-only module. It reuses the public Gemini/Nano Banana visible sparkle
localization idea from:
- Allen Kuo, GeminiWatermarkTool, MIT License
- wiltodelta/remove-ai-watermarks Python port, MIT License

Only the detector side is implemented here. No reverse alpha blending,
inpainting, or watermark-removal output is included.
"""
from __future__ import annotations

import io
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


ENABLED = os.getenv("VISIBLE_WATERMARK_ENABLED", "true").lower() not in {"0", "false", "no"}
ASSET_DIR = Path(__file__).parent / "assets"
GEMINI_CONFIDENCE_THRESHOLD = float(os.getenv("VISIBLE_WATERMARK_GEMINI_CONFIDENCE_THRESHOLD", "0.72"))
GEMINI_SPATIAL_THRESHOLD = float(os.getenv("VISIBLE_WATERMARK_GEMINI_SPATIAL_THRESHOLD", "0.62"))
GEMINI_GRADIENT_THRESHOLD = float(os.getenv("VISIBLE_WATERMARK_GEMINI_GRADIENT_THRESHOLD", "0.35"))
GENERIC_CORNER_THRESHOLD = float(os.getenv("VISIBLE_WATERMARK_GENERIC_CORNER_THRESHOLD", "0.84"))
GENERIC_IMAGE_ENABLED = os.getenv("VISIBLE_WATERMARK_GENERIC_IMAGE_ENABLED", "false").lower() not in {"0", "false", "no"}


@dataclass
class WatermarkHit:
    provider: str
    confidence: float
    bbox: tuple[float, float, float, float]
    method: str
    frame: int | None = None
    scores: dict[str, float] | None = None
    crop: str | None = None


def status() -> dict[str, Any]:
    return {
        "enabled": ENABLED,
        "available": (ASSET_DIR / "gemini_bg_48.png").exists() and (ASSET_DIR / "gemini_bg_96.png").exists(),
        "mode": "detect-only",
        "providers": ["gemini-visible-sparkle", "generic-corner-overlay"],
        "thresholds": {
            "geminiConfidence": GEMINI_CONFIDENCE_THRESHOLD,
            "geminiSpatial": GEMINI_SPATIAL_THRESHOLD,
            "geminiGradient": GEMINI_GRADIENT_THRESHOLD,
            "genericCorner": GENERIC_CORNER_THRESHOLD,
            "genericImageEnabled": GENERIC_IMAGE_ENABLED,
        },
    }


def _load_alpha(name: str, size: int) -> np.ndarray:
    asset_path = ASSET_DIR / name
    data = np.frombuffer(asset_path.read_bytes(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"Failed to decode visible watermark asset: {asset_path}")
    if img.shape[:2] != (size, size):
        img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    return np.max(img[:, :, :3], axis=2).astype(np.float32) / 255.0


class GeminiVisibleDetector:
    def __init__(self) -> None:
        self.alpha_large = _load_alpha("gemini_bg_96.png", 96)

    def detect(self, image: np.ndarray) -> WatermarkHit | None:
        if image is None or image.size == 0:
            return None
        if len(image.shape) == 2:
            bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        elif image.shape[2] == 4:
            bgr = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
        else:
            bgr = image

        h, w = bgr.shape[:2]
        search_size = int(min(min(w, h), 256))
        if search_size < 24:
            return None

        sx1 = max(0, w - search_size)
        sy1 = max(0, h - search_size)
        search = bgr[sy1:h, sx1:w]
        gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

        best_scale = 0
        best_score = -1.0
        best_raw = -1.0
        best_loc = (0, 0)
        for scale in range(16, 120, 2):
            if scale > gray.shape[0] or scale > gray.shape[1]:
                continue
            tmpl = cv2.resize(self.alpha_large, (scale, scale), interpolation=cv2.INTER_AREA)
            match = cv2.matchTemplate(gray, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(match)
            adjusted = float(max_val) * min(1.0, (scale / 96.0) ** 0.5)
            if adjusted > best_score:
                best_scale = scale
                best_score = adjusted
                best_raw = float(max_val)
                best_loc = max_loc

        if best_scale < 16:
            return None

        x = sx1 + best_loc[0]
        y = sy1 + best_loc[1]
        x2 = min(w, x + best_scale)
        y2 = min(h, y + best_scale)
        region = bgr[y:y2, x:x2]
        if region.size == 0:
            return None

        gray_region = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        alpha = cv2.resize(self.alpha_large, (best_scale, best_scale), interpolation=cv2.INTER_AREA)
        alpha = alpha[: gray_region.shape[0], : gray_region.shape[1]]

        if best_raw < 0.25:
            confidence = max(0.0, best_raw * 0.5)
            detected = False
            grad_score = 0.0
            var_score = 0.0
        else:
            img_gx = cv2.Sobel(gray_region, cv2.CV_32F, 1, 0, ksize=3)
            img_gy = cv2.Sobel(gray_region, cv2.CV_32F, 0, 1, ksize=3)
            img_gmag = cv2.magnitude(img_gx, img_gy)

            alpha_gx = cv2.Sobel(alpha, cv2.CV_32F, 1, 0, ksize=3)
            alpha_gy = cv2.Sobel(alpha, cv2.CV_32F, 0, 1, ksize=3)
            alpha_gmag = cv2.magnitude(alpha_gx, alpha_gy)
            grad_match = cv2.matchTemplate(img_gmag, alpha_gmag, cv2.TM_CCOEFF_NORMED)
            _, grad_score, _, _ = cv2.minMaxLoc(grad_match)
            grad_score = float(grad_score)

            var_score = 0.0
            ref_h = min(y, best_scale)
            if ref_h > 8:
                ref_region = bgr[y - ref_h : y, x:x2]
                gray_ref = cv2.cvtColor(ref_region, cv2.COLOR_BGR2GRAY)
                _, s_wm = cv2.meanStdDev(gray_region)
                _, s_ref = cv2.meanStdDev(gray_ref)
                if s_ref[0][0] > 5.0:
                    var_score = max(0.0, min(1.0, 1.0 - (s_wm[0][0] / s_ref[0][0])))

            confidence = max(0.0, min(1.0, best_raw * 0.50 + grad_score * 0.30 + var_score * 0.20))
            # For forensic evidence, false positives are worse than skips.
            # Require both template shape and gradient structure, so bright
            # corner objects cannot pass just because their variance is low.
            detected = (
                confidence >= GEMINI_CONFIDENCE_THRESHOLD
                and best_raw >= GEMINI_SPATIAL_THRESHOLD
                and grad_score >= GEMINI_GRADIENT_THRESHOLD
            )

        if not detected:
            return None

        return WatermarkHit(
            provider="gemini",
            confidence=round(float(confidence), 3),
            bbox=(x / w, y / h, (x2 - x) / w, (y2 - y) / h),
            method="gemini_sparkle_ncc",
            scores={
                "spatial": round(float(best_raw), 3),
                "gradient": round(float(grad_score), 3),
                "variance": round(float(var_score), 3),
            },
        )


_GEMINI_DETECTOR: GeminiVisibleDetector | None = None


def _gemini_detector() -> GeminiVisibleDetector | None:
    global _GEMINI_DETECTOR
    if not status()["available"]:
        return None
    if _GEMINI_DETECTOR is None:
        _GEMINI_DETECTOR = GeminiVisibleDetector()
    return _GEMINI_DETECTOR


def _generic_corner_overlay(image: np.ndarray) -> WatermarkHit | None:
    """Heuristic for visible corner overlays when no known template matches."""
    if image is None or image.size == 0:
        return None
    bgr = image if len(image.shape) == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    h, w = bgr.shape[:2]
    if min(h, w) < 80:
        return None

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    corner_w = max(48, int(w * 0.24))
    corner_h = max(48, int(h * 0.24))
    corners = {
        "top_left": (0, 0, corner_w, corner_h),
        "top_right": (w - corner_w, 0, corner_w, corner_h),
        "bottom_left": (0, h - corner_h, corner_w, corner_h),
        "bottom_right": (w - corner_w, h - corner_h, corner_w, corner_h),
    }

    best: tuple[float, tuple[int, int, int, int], str] | None = None
    for name, (x, y, cw, ch) in corners.items():
        roi_gray = gray[y : y + ch, x : x + cw]
        roi_sat = hsv[y : y + ch, x : x + cw, 1]
        if roi_gray.size == 0:
            continue

        bright = cv2.inRange(roi_gray, 185, 255)
        low_sat = cv2.inRange(roi_sat, 0, 95)
        mask = cv2.bitwise_and(bright, low_sat)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        mask = cv2.dilate(mask, np.ones((2, 2), np.uint8), iterations=1)

        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            bx = int(stats[i, cv2.CC_STAT_LEFT])
            by = int(stats[i, cv2.CC_STAT_TOP])
            bw = int(stats[i, cv2.CC_STAT_WIDTH])
            bh = int(stats[i, cv2.CC_STAT_HEIGHT])
            if area < 14 or bw < 4 or bh < 4:
                continue
            if (bw / w) < 0.03 or (bh / h) < 0.025:
                continue
            if area > cw * ch * 0.18:
                continue
            aspect = bw / max(1, bh)
            if aspect > 8 or aspect < 0.12:
                continue
            cx = bx + bw / 2
            cy = by + bh / 2
            if name == "top_left" and (cx > cw * 0.62 or cy > ch * 0.62):
                continue
            if name == "top_right" and (cx < cw * 0.38 or cy > ch * 0.62):
                continue
            if name == "bottom_left" and (cx > cw * 0.62 or cy < ch * 0.38):
                continue
            if name == "bottom_right" and (cx < cw * 0.38 or cy < ch * 0.38):
                continue
            patch = roi_gray[by : by + bh, bx : bx + bw]
            edge_density = float(cv2.Canny(patch, 80, 180).mean() / 255.0)
            fill = area / max(1, bw * bh)
            size_score = min(1.0, area / (cw * ch * 0.035))
            score = 0.35 * size_score + 0.35 * min(1.0, edge_density * 4.0) + 0.30 * min(1.0, fill * 2.5)
            if best is None or score > best[0]:
                best = (score, (x + bx, y + by, bw, bh), name)

    if best is None or best[0] < GENERIC_CORNER_THRESHOLD:
        return None
    score, (x, y, bw, bh), _ = best
    return WatermarkHit(
        provider="unknown_visible",
        # Unknown corner overlays are intentionally capped below strong
        # evidence. They should support review, not decide authenticity.
        confidence=round(float(min(score, 0.58)), 3),
        bbox=(x / w, y / h, bw / w, bh / h),
        method="corner_overlay_heuristic",
        scores={"cornerOverlay": round(float(score), 3)},
    )


def _hit_to_dict(hit: WatermarkHit) -> dict[str, Any]:
    return {
        "provider": hit.provider,
        "confidence": hit.confidence,
        "bbox": {
            "x": round(hit.bbox[0], 4),
            "y": round(hit.bbox[1], 4),
            "w": round(hit.bbox[2], 4),
            "h": round(hit.bbox[3], 4),
        },
        "method": hit.method,
        "frame": hit.frame,
        "scores": hit.scores or {},
        "crop": hit.crop,
    }


def _detect_frame(image: np.ndarray, *, allow_generic: bool) -> WatermarkHit | None:
    detector = _gemini_detector()
    hit = detector.detect(image) if detector else None
    if hit:
        return hit
    if allow_generic:
        return _generic_corner_overlay(image)
    return None


def _attach_crop(image: np.ndarray, hit: WatermarkHit, *, padding: float = 0.45) -> WatermarkHit:
    """Attach a small evidence crop around the detected watermark."""
    h, w = image.shape[:2]
    x, y, bw, bh = hit.bbox
    x1 = int(max(0, (x - bw * padding) * w))
    y1 = int(max(0, (y - bh * padding) * h))
    x2 = int(min(w, (x + bw * (1 + padding)) * w))
    y2 = int(min(h, (y + bh * (1 + padding)) * h))
    if x2 <= x1 or y2 <= y1:
        return hit

    crop = image[y1:y2, x1:x2].copy()
    if crop.size == 0:
        return hit

    # Draw a thin local box so users can see the exact evidence position inside
    # the crop. This is an annotation on the evidence copy, not a media edit.
    bx1 = int(max(0, x * w - x1))
    by1 = int(max(0, y * h - y1))
    bx2 = int(min(x2 - x1 - 1, (x + bw) * w - x1))
    by2 = int(min(y2 - y1 - 1, (y + bh) * h - y1))
    cv2.rectangle(crop, (bx1, by1), (bx2, by2), (216, 65, 47), 1)

    max_side = max(crop.shape[:2])
    if max_side > 180:
        scale = 180 / max_side
        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), max(1, int(crop.shape[0] * scale))), interpolation=cv2.INTER_AREA)

    ok, buf = cv2.imencode(".webp", crop, [int(cv2.IMWRITE_WEBP_QUALITY), 58])
    if ok:
        import base64

        hit.crop = "data:image/webp;base64," + base64.b64encode(buf.tobytes()).decode()
    return hit


def detect_image(data: bytes) -> dict[str, Any]:
    started = time.perf_counter()
    if not ENABLED:
        return _empty("可见水印检测未启用", started)
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return _empty("无法解码图像", started)
    hit = _detect_frame(img, allow_generic=GENERIC_IMAGE_ENABLED)
    if not hit:
        return _empty("未检测到已知可见 AI 水印或稳定角标水印", started, supported=True)
    _attach_crop(img, hit)
    return _result([hit], "image", started)


def detect_video(data: bytes, suffix: str = ".mp4", max_frames: int = 18) -> dict[str, Any]:
    started = time.perf_counter()
    if not ENABLED:
        return _empty("可见水印检测未启用", started)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        cap = cv2.VideoCapture(tmp.name)
        if not cap.isOpened():
            return _empty("无法解码视频", started)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            sample_ids = list(range(max_frames))
        else:
            sample_ids = sorted({int(i) for i in np.linspace(0, max(total - 1, 0), num=min(max_frames, total))})

        hits: list[WatermarkHit] = []
        for frame_id in sample_ids:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_id)
            ok, frame = cap.read()
            if not ok:
                continue
            hit = _detect_frame(frame, allow_generic=True)
            if hit:
                hit.frame = frame_id
                _attach_crop(frame, hit)
                hits.append(hit)
        cap.release()

    if not hits:
        return _empty("采样帧中未检测到已知可见 AI 水印或稳定角标水印", started, supported=True, sampled=len(sample_ids))
    return _result(hits, "video", started, sampled=len(sample_ids))


def detect(data: bytes, file_type: str, filename: str = "") -> dict[str, Any]:
    if file_type == "image":
        return detect_image(data)
    if file_type == "video":
        ext = Path(filename).suffix.lower() or ".mp4"
        return detect_video(data, suffix=ext)
    return _empty("当前文件类型不支持可见水印检测", time.perf_counter(), supported=False)


def _empty(note: str, started: float, *, supported: bool = True, sampled: int = 0) -> dict[str, Any]:
    return {
        "enabled": ENABLED,
        "supported": supported,
        "detected": False,
        "provider": None,
        "confidence": 0.0,
        "evidenceLevel": "none" if supported else "unavailable",
        "hits": [],
        "temporal": {"sampledFrames": sampled, "positiveFrames": 0, "moving": False},
        "note": note,
        "elapsedMs": int((time.perf_counter() - started) * 1000),
    }


def _result(hits: list[WatermarkHit], media_type: str, started: float, sampled: int = 1) -> dict[str, Any]:
    top = max(hits, key=lambda h: h.confidence)
    provider_counts: dict[str, int] = {}
    for hit in hits:
        provider_counts[hit.provider] = provider_counts.get(hit.provider, 0) + 1
    provider = max(provider_counts.items(), key=lambda kv: kv[1])[0]
    moving = False
    if media_type == "video" and len(hits) >= 3:
        centers = np.array([(h.bbox[0] + h.bbox[2] / 2, h.bbox[1] + h.bbox[3] / 2) for h in hits], dtype=np.float32)
        moving = bool(float(np.std(centers[:, 0]) + np.std(centers[:, 1])) > 0.035)

    if media_type == "video":
        confidence = max(float(top.confidence), min(0.98, len(hits) / max(1, sampled)))
    else:
        confidence = float(top.confidence)
    level = "strong" if confidence >= 0.82 else "medium" if confidence >= 0.6 else "weak"
    label = "Gemini/Nano Banana/Veo 可见水印" if provider == "gemini" else "可见角标水印"
    note = (
        f"检测到疑似 {label}，可作为 AI 生成或平台导出痕迹的辅助证据。"
        if media_type == "image"
        else f"在 {len(hits)}/{sampled} 个采样帧中检测到疑似 {label}"
        + ("，水印位置存在跳动/位移。" if moving else "。")
    )

    return {
        "enabled": ENABLED,
        "supported": True,
        "detected": True,
        "provider": provider,
        "confidence": round(confidence, 3),
        "evidenceLevel": level,
        "hits": [_hit_to_dict(hit) for hit in hits[:24]],
        "temporal": {"sampledFrames": sampled, "positiveFrames": len(hits), "moving": moving},
        "note": note,
        "elapsedMs": int((time.perf_counter() - started) * 1000),
    }
