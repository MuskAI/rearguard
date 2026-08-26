from __future__ import annotations

from dataclasses import dataclass
import io
import math
from typing import Any, Literal, Protocol

import numpy as np
from PIL import Image, ImageOps


RouteName = Literal["detect", "skip", "uncertain"]


class RoutableDocumentAsset(Protocol):
    data: bytes
    width: int
    height: int
    source_kind: str
    duplicate_of: int | None
    pdf_object_id: int | None
    pdf_smask_object_id: int | None
    pdf_is_soft_mask: bool
    pdf_is_image_mask: bool
    pdf_color_space: str | None
    pdf_bits_per_component: int | None
    pdf_page_image_count: int
    pdf_figure_caption_count: int


@dataclass(frozen=True, slots=True)
class RouterDecision:
    route: RouteName
    should_detect: bool
    confidence: float
    category: str
    category_label: str
    reasons: tuple[str, ...]
    features: dict[str, Any]

    def public_payload(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "shouldDetect": self.should_detect,
            "confidence": round(self.confidence, 4),
            "category": self.category,
            "categoryLabel": self.category_label,
            "reasons": list(self.reasons),
            "features": self.features,
            "version": "document-router-rules-v1",
        }


def _safe_float(value: float, digits: int = 4) -> float:
    if not math.isfinite(value):
        return 0.0
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded


def _visual_features(data: bytes) -> dict[str, Any]:
    with Image.open(io.BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source)
        frame_count = int(getattr(image, "n_frames", 1) or 1)
        image.seek(0)
        rgba = image.convert("RGBA")
        rgba.thumbnail((192, 192), Image.Resampling.LANCZOS)
        values = np.asarray(rgba, dtype=np.uint8)

    alpha = values[..., 3].astype(np.float32) / 255.0
    transparent_ratio = float(np.mean(alpha <= 0.05))
    semitransparent_ratio = float(np.mean((alpha > 0.05) & (alpha < 0.95)))
    rgb = values[..., :3].astype(np.float32)
    composite = rgb * alpha[..., None] + 255.0 * (1.0 - alpha[..., None])
    gray = (
        composite[..., 0] * 0.2126
        + composite[..., 1] * 0.7152
        + composite[..., 2] * 0.0722
    )

    histogram, _edges = np.histogram(gray, bins=64, range=(0, 256))
    probabilities = histogram.astype(np.float64) / max(1, int(histogram.sum()))
    nonzero = probabilities[probabilities > 0]
    entropy = float(-np.sum(nonzero * np.log2(nonzero)))
    luminance_std = float(np.std(gray))
    near_white_ratio = float(np.mean(gray >= 247))
    near_black_ratio = float(np.mean(gray <= 8))

    horizontal = np.abs(np.diff(gray, axis=1)) if gray.shape[1] > 1 else np.zeros((1, 1))
    vertical = np.abs(np.diff(gray, axis=0)) if gray.shape[0] > 1 else np.zeros((1, 1))
    edge_density = float(
        (np.mean(horizontal >= 18.0) + np.mean(vertical >= 18.0)) / 2.0
    )

    red, green, blue = composite[..., 0], composite[..., 1], composite[..., 2]
    red_green = red - green
    yellow_blue = (red + green) / 2.0 - blue
    colorfulness = float(
        math.sqrt(float(np.var(red_green)) + float(np.var(yellow_blue))) / 128.0
    )
    grayscale_delta = float(np.mean(np.max(composite, axis=2) - np.min(composite, axis=2)) / 255.0)

    palette_source = Image.fromarray(np.clip(composite, 0, 255).astype(np.uint8), mode="RGB")
    quantized = palette_source.quantize(colors=16)
    palette_counts = quantized.getcolors(maxcolors=16) or []
    dominant_color_ratio = max((count for count, _index in palette_counts), default=0) / max(
        1, quantized.width * quantized.height
    )

    return {
        "entropy": _safe_float(entropy),
        "luminanceStd": _safe_float(luminance_std, 2),
        "edgeDensity": _safe_float(edge_density),
        "colorfulness": _safe_float(colorfulness),
        "grayscaleDelta": _safe_float(grayscale_delta),
        "dominantColorRatio": _safe_float(dominant_color_ratio),
        "transparentRatio": _safe_float(transparent_ratio),
        "semitransparentRatio": _safe_float(semitransparent_ratio),
        "nearWhiteRatio": _safe_float(near_white_ratio),
        "nearBlackRatio": _safe_float(near_black_ratio),
        "frameCount": frame_count,
    }


def _decision(
    route: RouteName,
    confidence: float,
    category: str,
    category_label: str,
    reasons: list[str],
    features: dict[str, Any],
) -> RouterDecision:
    return RouterDecision(
        route=route,
        should_detect=route != "skip",
        confidence=min(max(float(confidence), 0.0), 1.0),
        category=category,
        category_label=category_label,
        reasons=tuple(reasons[:4]),
        features=features,
    )


def route_document_asset(asset: RoutableDocumentAsset) -> RouterDecision:
    width = max(0, int(asset.width))
    height = max(0, int(asset.height))
    min_side = min(width, height)
    max_side = max(width, height)
    pixel_count = width * height
    aspect_ratio = max_side / max(1, min_side)
    structural = {
        "width": width,
        "height": height,
        "pixelCount": pixel_count,
        "aspectRatio": _safe_float(aspect_ratio, 2),
        "sourceKind": str(asset.source_kind or "unknown"),
        "pdfObjectId": asset.pdf_object_id,
        "pdfSoftMaskObjectId": asset.pdf_smask_object_id,
        "pdfColorSpace": asset.pdf_color_space,
        "pdfBitsPerComponent": asset.pdf_bits_per_component,
        "pdfPageImageCount": asset.pdf_page_image_count,
        "pdfFigureCaptionCount": asset.pdf_figure_caption_count,
    }

    if asset.pdf_is_soft_mask or asset.pdf_is_image_mask:
        kind = "PDF 透明度蒙版" if asset.pdf_is_soft_mask else "PDF 图像遮罩"
        return _decision(
            "skip",
            0.999,
            "pdf_mask",
            kind,
            [f"该对象被 PDF 标记为{kind}", "它只控制其他图片的透明度或显示范围，不是独立视觉内容"],
            structural,
        )

    if asset.duplicate_of:
        return _decision(
            "skip",
            1.0,
            "duplicate",
            "重复图片",
            [f"与图片 {asset.duplicate_of} 的文件哈希完全一致", "可复用首次出现图片的路由或检测结果"],
            structural,
        )

    if (
        str(asset.source_kind) == "pdf_embedded"
        and int(asset.pdf_page_image_count or 0) >= 8
        and int(asset.pdf_figure_caption_count or 0) >= 1
    ):
        image_count = int(asset.pdf_page_image_count)
        caption_count = int(asset.pdf_figure_caption_count)
        return _decision(
            "skip",
            0.985,
            "compound_figure_component",
            "复合论文插图的子图",
            [
                f"本页包含 {image_count} 个图片对象，并识别到 {caption_count} 处 Figure 图注",
                "这些对象共同组成论文插图，不应被当作彼此独立的待鉴伪照片",
            ],
            structural,
        )

    try:
        visual = _visual_features(asset.data)
    except (OSError, SyntaxError, ValueError):
        return _decision(
            "uncertain",
            0.51,
            "unreadable",
            "特征读取失败",
            ["无法稳定读取用于路由的视觉特征", "为避免漏检，默认继续进入快速检测"],
            structural,
        )

    features = {**structural, **visual}
    entropy = float(visual["entropy"])
    luminance_std = float(visual["luminanceStd"])
    edge_density = float(visual["edgeDensity"])
    colorfulness = float(visual["colorfulness"])
    dominant = float(visual["dominantColorRatio"])
    transparent = float(visual["transparentRatio"])

    if transparent >= 0.97:
        return _decision(
            "skip",
            0.998,
            "transparent_layer",
            "近乎透明的图层",
            [f"透明像素占比为 {transparent:.1%}", "有效视觉内容不足，通常是排版或遮罩对象"],
            features,
        )

    if min_side < 48 or pixel_count < 2_304:
        return _decision(
            "skip",
            0.995,
            "tiny_asset",
            "微小装饰元素",
            [f"原始尺寸仅 {width}×{height}", "尺寸不足以形成可靠的图像鉴伪输入"],
            features,
        )

    if dominant >= 0.985 and (luminance_std <= 4.0 or entropy <= 0.8):
        return _decision(
            "skip",
            0.997,
            "uniform_layer",
            "纯色或低信息图层",
            [f"主色占比为 {dominant:.1%}", f"图像信息熵仅 {entropy:.2f}", "更接近背景、蒙版或占位块"],
            features,
        )

    if entropy <= 1.15 and dominant >= 0.90 and edge_density <= 0.015:
        return _decision(
            "skip",
            0.989,
            "low_information",
            "低信息图层",
            [f"图像信息熵为 {entropy:.2f}", "颜色变化和有效边缘都很少", "不具备值得鉴伪的视觉内容"],
            features,
        )

    if aspect_ratio >= 12.0 and min_side < 160 and entropy < 3.2:
        return _decision(
            "skip",
            0.974,
            "decorative_strip",
            "装饰性条带",
            [f"长宽比达到 {aspect_ratio:.1f}:1", "较窄且视觉信息有限，通常是分隔线或页面装饰"],
            features,
        )

    if (
        str(asset.source_kind) in {"docx_header", "docx_footer"}
        and min_side < 160
        and pixel_count < 80_000
        and entropy < 3.0
    ):
        return _decision(
            "skip",
            0.965,
            "header_footer_decoration",
            "页眉页脚装饰",
            ["图片来自文档页眉或页脚", "尺寸和视觉信息都较低，更可能是标识或装饰"],
            features,
        )

    photo_score = 0
    if min_side >= 160:
        photo_score += 2
    elif min_side >= 96:
        photo_score += 1
    if pixel_count >= 80_000:
        photo_score += 2
    elif pixel_count >= 25_000:
        photo_score += 1
    if entropy >= 4.2:
        photo_score += 2
    elif entropy >= 3.4:
        photo_score += 1
    if luminance_std >= 28:
        photo_score += 1
    if 0.012 <= edge_density <= 0.55:
        photo_score += 1
    if dominant <= 0.72:
        photo_score += 1
    if colorfulness >= 0.08 or float(visual["grayscaleDelta"]) <= 0.035:
        photo_score += 1

    if photo_score >= 8:
        return _decision(
            "detect",
            min(0.97, 0.82 + (photo_score - 8) * 0.035),
            "photo_or_artwork",
            "照片或完整视觉作品",
            [
                f"尺寸为 {width}×{height}，具备可分析细节",
                f"信息熵 {entropy:.2f}，不是单调背景或遮罩",
                "纹理、边缘和色彩分布符合完整视觉内容",
            ],
            features,
        )

    return _decision(
        "uncertain",
        min(0.79, 0.52 + photo_score * 0.035),
        "ambiguous_visual",
        "边界视觉内容",
        [
            "当前规则不足以确认它是照片还是图表、截图或装饰",
            "为避免错误跳过，正式流程默认继续进入快速检测",
        ],
        features,
    )
