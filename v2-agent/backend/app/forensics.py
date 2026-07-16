"""信号级取证预处理与可视化证据套件。

build_suite() 生成 7 张取证可视化图（ELA / 噪声 / 噪声一致性 / 频域 / 光照梯度 /
光照一致性 / 多次JPEG压缩曲线），连同原图喂给视觉模型做可解释性判读。
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
def prep(data: bytes, max_side: int = 1024) -> Image.Image:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return im


def _png(im: Image.Image) -> bytes:
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def to_jpeg(im: Image.Image, quality: int = 88) -> bytes:
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality)
    return out.getvalue()


def _norm01(a: np.ndarray) -> np.ndarray:
    lo, hi = float(a.min()), float(a.max())
    return (a - lo) / (hi - lo + 1e-6)


def _colorize(gray01: np.ndarray, cmap: str = "jet") -> Image.Image:
    if cmap != "jet":
        raise ValueError(f"unsupported color map: {cmap}")
    values = np.clip(gray01, 0.0, 1.0)
    red = np.clip(1.5 - np.abs(4 * values - 3), 0.0, 1.0)
    green = np.clip(1.5 - np.abs(4 * values - 2), 0.0, 1.0)
    blue = np.clip(1.5 - np.abs(4 * values - 1), 0.0, 1.0)
    rgb = (np.dstack([red, green, blue]) * 255).astype(np.uint8)
    return Image.fromarray(rgb)


# ---------------------------------------------------------------------------
# 各取证图
# ---------------------------------------------------------------------------
def ela_png(im: Image.Image, quality: int = 90) -> bytes:
    """ELA / 压缩对齐分析：亮区=压缩误差大=潜在篡改。"""
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    resaved = Image.open(buf).convert("RGB")
    ela = ImageChops.difference(im, resaved)
    max_diff = max((e[1] for e in ela.getextrema()), default=1) or 1
    ela = ImageEnhance.Brightness(ela).enhance(255.0 / max_diff)
    return _png(ela)


def noise_png(im: Image.Image, radius: float = 2.0) -> bytes:
    """噪声成分分析：高频残差（灰度）。"""
    gray = im.convert("L")
    residual = ImageChops.difference(gray, gray.filter(ImageFilter.GaussianBlur(radius)))
    return _png(ImageOps.autocontrast(residual).convert("RGB"))


def noise_consistency_png(im: Image.Image, radius: float = 2.0) -> bytes:
    """噪声一致性分析：噪声强度伪彩色映射，便于看区块一致性。"""
    gray = np.asarray(im.convert("L"), dtype=np.float32)
    blur = np.asarray(im.convert("L").filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
    residual = np.abs(gray - blur)
    return _png(_colorize(_norm01(residual), "jet"))


def fft_png(im: Image.Image) -> bytes:
    """频域分析：log 幅度谱，规则网格高频提示合成指纹。"""
    gray = np.asarray(im.convert("L"), dtype=np.float32)
    mag = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(gray))))
    arr = (_norm01(mag) * 255).astype(np.uint8)
    return _png(Image.fromarray(arr).convert("RGB"))


def light_gradient_png(im: Image.Image) -> bytes:
    """光照梯度分析：梯度法线可视化（绿/品红浮雕）。"""
    gray = np.asarray(im.convert("L"), dtype=np.float32)
    gy, gx = np.gradient(gray)
    r = (_norm01(gx) * 255).astype(np.uint8)
    g = (_norm01(gy) * 255).astype(np.uint8)
    b = np.full_like(r, 128)
    return _png(Image.fromarray(np.dstack([r, g, b])))


def light_consistency_png(im: Image.Image) -> bytes:
    """光照一致性分析：估计主光方向并在原图上叠加箭头。"""
    gray = np.asarray(im.convert("L"), dtype=np.float32)
    gy, gx = np.gradient(gray)
    # 光源方向 ≈ 指向亮处，取负梯度均值
    dx, dy = -float(gx.mean()), -float(gy.mean())
    norm = (dx**2 + dy**2) ** 0.5 + 1e-6
    dx, dy = dx / norm, dy / norm

    canvas = im.copy()
    d = ImageDraw.Draw(canvas)
    w, h = canvas.size
    cx, cy = w * 0.5, h * 0.5
    length = min(w, h) * 0.28
    ex, ey = cx + dx * length, cy + dy * length
    d.line([(cx, cy), (ex, ey)], fill=(57, 255, 20), width=max(3, w // 200))
    # 箭头
    import math

    ang = math.atan2(ey - cy, ex - cx)
    for da in (math.radians(150), math.radians(-150)):
        d.line([(ex, ey), (ex + length * 0.25 * math.cos(ang + da),
                            ey + length * 0.25 * math.sin(ang + da))],
               fill=(57, 255, 20), width=max(3, w // 200))
    return _png(canvas)


def jpeg_curve_png(im: Image.Image) -> tuple[bytes, list[dict]]:
    """多次JPEG压缩检测：不同质量下重压缩误差曲线。"""
    base = np.asarray(im, dtype=np.float32)
    qs = list(range(50, 100, 5))
    errs = []
    for q in qs:
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q)
        buf.seek(0)
        re = np.asarray(Image.open(buf).convert("RGB"), dtype=np.float32)
        errs.append(float(np.abs(base - re).mean()))

    canvas = Image.new("RGB", (440, 330), "#f8fbfc")
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = 58, 42, 418, 284
    chart_width, chart_height = right - left, bottom - top
    min_error, max_error = min(errs), max(errs)
    error_range = max(max_error - min_error, 0.1)

    for step in range(5):
        y = round(top + chart_height * step / 4)
        draw.line((left, y, right, y), fill="#d6e0e3", width=1)
    draw.line((left, top, left, bottom, right, bottom), fill="#73848a", width=1)
    draw.text((left, 16), "multiple JPEG compression", fill="#263b42")
    draw.text((left + chart_width // 2 - 18, 304), "quality", fill="#687b82")
    draw.text((left - 8, bottom + 7), "50", fill="#687b82")
    draw.text((right - 8, bottom + 7), "95", fill="#687b82")

    line_points = []
    for index, error in enumerate(errs):
        x = round(left + chart_width * index / (len(errs) - 1))
        y = round(bottom - ((error - min_error) / error_range) * chart_height)
        line_points.append((x, y))
    draw.line(line_points, fill="#196b8f", width=3, joint="curve")
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    points = [{"quality": q, "error": round(e, 2)} for q, e in zip(qs, errs)]
    return out.getvalue(), points


# ---------------------------------------------------------------------------
# 套件
# ---------------------------------------------------------------------------
SUITE_META = [
    ("ela", "压缩对齐分析",
     "按固定质量重新压缩并计算误差。真实照片误差随纹理均匀分布；局部'像素孤岛'或色块边框提示该区域压缩历史不同，疑似拼接/重绘。"),
    ("noise", "噪声成分分析",
     "提取高频噪声成分。真实相机噪声全图连续一致；AI 生成或粘贴区域常缺失自然噪声或出现伪影。"),
    ("noise_consistency", "噪声一致性分析",
     "将噪声强度伪彩色映射，便于观察区块一致性。非物理规律的色带/区块提示生成模型伪影。"),
    ("fft", "频域分析",
     "傅里叶 log 幅度谱。整图出现规则网格状高频条纹（人眼不可见）是合成内容的典型指纹。"),
    ("light_gradient", "光照梯度分析",
     "将图像梯度可视化为法线浮雕。光照衰减应平缓，断崖式亮度跳变提示数字合成。"),
    ("light_consistency", "光照一致性分析",
     "估计主光方向并叠加箭头。若局部高光方向与主光源逻辑冲突，可能是 AI 同时模拟多光源导致物理失真。"),
    ("jpeg_curve", "多次JPEG压缩检测",
     "在不同质量下反复压缩并统计误差曲线，用于交叉验证。曲线平稳不单独代表可信，需结合其他特征。"),
]


def build_suite(data: bytes, max_side: int = 768) -> tuple[list[dict], list[dict]]:
    """返回 (suite, jpeg_points)。suite 每项含 key/title/explanation/png(bytes)。"""
    im = prep(data, max_side=max_side)
    curve_png, points = jpeg_curve_png(im)
    images = {
        "ela": ela_png(im),
        "noise": noise_png(im),
        "noise_consistency": noise_consistency_png(im),
        "fft": fft_png(im),
        "light_gradient": light_gradient_png(im),
        "light_consistency": light_consistency_png(im),
        "jpeg_curve": curve_png,
    }
    suite = [
        {"key": k, "title": title, "explanation": expl, "png": images[k]}
        for (k, title, expl) in SUITE_META
    ]
    return suite, points
