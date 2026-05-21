"""Jianzhen V2 image forensics agent integrated into the Flask backend.

The agent sends the original image plus two forensic views, ELA and noise
residual, to a DashScope OpenAI-compatible VLM endpoint. It returns a compact
RealGuard-shaped result and falls back silently when the remote model is not
configured or unavailable.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from typing import Any

import requests
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps


API_KEY = os.environ.get("DASHSCOPE_API_KEY", "").strip()
BASE_URL = os.environ.get(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
).rstrip("/")
VLM_MODEL = os.environ.get("VLM_MODEL", "qwen3-vl-flash").strip() or "qwen3-vl-flash"
AGENT_ENABLED = os.environ.get("JIANZHEN_AGENT_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
AGENT_TIMEOUT = float(os.environ.get("JIANZHEN_AGENT_TIMEOUT", "75"))


IMAGE_SYSTEM = """你是「鉴真」图像鉴伪取证引擎，融合视觉语义分析与信号级取证。
你的判定必须建立在证据之上，不能因为画面内容看起来正常或合理就默认判真，也不能无证据地判伪。

你会一次收到同一张图的三个视图：
【图1·原图】用于语义与视觉层面的判读。
【图2·ELA 误差水平分析图】对原图按固定质量重新 JPEG 压缩后的像素级误差，已归一化增亮。
【图3·噪声残差图】原图减去高斯模糊得到的高频残差，已自动对比拉伸。

只输出 JSON，不要 markdown 代码块，不要任何额外解释文字。"""


IMAGE_PROMPT = """请对这张图片做四个维度的鉴定，综合「原图语义线索」与「ELA/噪声信号线索」给出结论。

[aigc] AIGC 生成检测：观察皮肤/材质过度平滑、毛发/手指/文字结构异常、背景物体语义崩坏、景深与透视不自洽、噪声残差是否缺少自然传感器噪声或呈现周期性人工纹理。
[tamper] 篡改/拼接检测：观察边缘生硬、光照和阴影不一致、复制纹理、比例错位；重点参考 ELA 中是否有局部块压缩历史与周围不同。
[deepfake] 人脸深伪检测：观察五官边界、发际线、瞳孔反光、牙齿耳朵细节、肤色与脖子衔接；若无人脸，score 给 0。
[ela] ELA/噪声取证：描述 ELA 或噪声图上实际观察到的信号异常，例如局部突变、全局异常均匀、规则网格或区块噪声不一致。

严格输出 JSON：
{
  "verdict": "real | suspected_fake | highly_suspected_fake",
  "confidence": 0.0-1.0,
  "dimensions": [
    {"key":"aigc","label":"AIGC生成检测","score":0.0-1.0,"result":"简短结论"},
    {"key":"tamper","label":"篡改/拼接检测","score":0.0-1.0,"result":"简短结论"},
    {"key":"deepfake","label":"人脸深伪检测","score":0.0-1.0,"result":"简短结论"},
    {"key":"ela","label":"ELA/噪声取证","score":0.0-1.0,"result":"描述 ELA/噪声图上的具体观察"}
  ],
  "regions": [{"x":0-1,"y":0-1,"w":0-1,"h":0-1,"label":"可疑区描述","score":0-1}],
  "explanation": "中文综合说明，3-5 句，需点明语义证据和 ELA/噪声证据"
}

约束：score 越高表示越可疑；confidence 取最可疑维度分数并与 verdict 自洽（<0.4→real，0.4~0.75→suspected_fake，≥0.75→highly_suspected_fake）。"""


DIMENSIONS = [
    {"key": "aigc", "label": "AIGC生成检测"},
    {"key": "tamper", "label": "篡改/拼接检测"},
    {"key": "deepfake", "label": "人脸深伪检测"},
    {"key": "ela", "label": "ELA/噪声取证"},
]


def is_configured() -> bool:
    return bool(AGENT_ENABLED and API_KEY)


def _prep(data: bytes, max_side: int = 1024) -> Image.Image:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), resampling)
    return im


def _to_jpeg(im: Image.Image, quality: int = 88) -> bytes:
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality)
    return out.getvalue()


def _to_png(im: Image.Image) -> bytes:
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def _ela_png(im: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    resaved = Image.open(buf).convert("RGB")
    ela = ImageChops.difference(im, resaved)
    max_diff = max((channel[1] for channel in ela.getextrema()), default=1) or 1
    ela = ImageEnhance.Brightness(ela).enhance(255.0 / max_diff)
    return _to_png(ela)


def _noise_png(im: Image.Image, radius: float = 2.0) -> bytes:
    gray = im.convert("L")
    residual = ImageChops.difference(gray, gray.filter(ImageFilter.GaussianBlur(radius)))
    return _to_png(ImageOps.autocontrast(residual).convert("RGB"))


def _data_uri(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _extract_json(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _post_chat(messages: list[dict[str, Any]]) -> str:
    payload = {
        "model": VLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1200,
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    with requests.Session() as sess:
        sess.trust_env = False
        response = sess.post(
            f"{BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=AGENT_TIMEOUT,
        )
    response.raise_for_status()
    data = response.json()
    return (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def analyze_image(data: bytes) -> dict[str, Any] | None:
    if not is_configured():
        return None
    try:
        image = _prep(data)
        content = [
            {"type": "text", "text": "【图1·原图】"},
            {"type": "image_url", "image_url": {"url": _data_uri("image/jpeg", _to_jpeg(image))}},
            {"type": "text", "text": "【图2·ELA 误差水平分析图】亮区表示压缩误差大，可提示潜在异常"},
            {"type": "image_url", "image_url": {"url": _data_uri("image/png", _ela_png(image))}},
            {"type": "text", "text": "【图3·噪声残差图】用于判断传感器噪声一致性"},
            {"type": "image_url", "image_url": {"url": _data_uri("image/png", _noise_png(image))}},
            {"type": "text", "text": IMAGE_PROMPT},
        ]
        raw = _post_chat([
            {"role": "system", "content": IMAGE_SYSTEM},
            {"role": "user", "content": content},
        ])
        parsed = _extract_json(raw)
        if not parsed:
            return None
        return _normalize(parsed)
    except Exception as exc:
        print(f"[Jianzhen V2] image agent failed: {exc}")
        return None


def _score(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _verdict_from_score(score: float) -> str:
    if score >= 0.75:
        return "highly_suspected_fake"
    if score >= 0.4:
        return "suspected_fake"
    return "real"


def _normalize(parsed: dict[str, Any]) -> dict[str, Any]:
    raw_dims = {
        item.get("key"): item
        for item in parsed.get("dimensions", [])
        if isinstance(item, dict) and item.get("key")
    }
    dimensions = []
    for definition in DIMENSIONS:
        item = raw_dims.get(definition["key"], {})
        score = _score(item.get("score"))
        result = str(item.get("result") or ("疑似异常" if score >= 0.4 else "未见明显异常"))
        dimensions.append({**definition, "score": round(score, 2), "result": result})

    top_score = max([d["score"] for d in dimensions] or [0.0])
    confidence = round(_score(parsed.get("confidence"), top_score), 2)
    verdict = parsed.get("verdict")
    if verdict not in {"real", "suspected_fake", "highly_suspected_fake"}:
        verdict = _verdict_from_score(confidence)

    regions = []
    for item in parsed.get("regions", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            regions.append({
                "x": round(_score(item.get("x")), 3),
                "y": round(_score(item.get("y")), 3),
                "w": round(_score(item.get("w")), 3),
                "h": round(_score(item.get("h")), 3),
                "label": str(item.get("label") or "可疑区域"),
                "score": round(_score(item.get("score"), confidence), 2),
            })
        except Exception:
            continue

    return {
        "verdict": verdict,
        "confidence": confidence,
        "dimensions": dimensions,
        "regions": regions[:5],
        "explanation": str(parsed.get("explanation") or "模型未提供详细依据。"),
        "modelVersion": VLM_MODEL,
        "source": "jianzhen-v2",
    }


def confidence_label(probability: float) -> str:
    distance = abs(max(0.0, min(1.0, probability)) - 0.5)
    if distance >= 0.35:
        return "高"
    if distance >= 0.18:
        return "中"
    return "低"


def to_realguard_result(analysis: dict[str, Any]) -> dict[str, Any]:
    probability = _score(analysis.get("confidence"), 0.5)
    verdict = analysis.get("verdict")
    final_label = "真实图像" if verdict == "real" and probability < 0.5 else "AI生成图像"

    issues: list[str] = []
    for item in analysis.get("dimensions", []) or []:
        if not isinstance(item, dict):
            continue
        score = _score(item.get("score"))
        text = str(item.get("result") or "").strip()
        if text and (score >= 0.35 or item.get("key") == "ela"):
            issues.append(f"{item.get('label', '取证维度')}：{text}")
    for region in analysis.get("regions", []) or []:
        label = str((region or {}).get("label") or "").strip()
        if label:
            issues.append(f"可疑区域：{label}")

    if final_label == "真实图像" and not issues:
        issues = ["未见明显视觉可疑点。"]
    elif not issues:
        issues = ["模型提示存在可疑生成或篡改信号，但未定位到明确局部区域。"]

    reasoning_lines = [
        f"鉴真 V2 Agent（{analysis.get('modelVersion') or VLM_MODEL}）已启用。",
        f"综合结论：{final_label}，AI 可疑概率约 {round(probability * 100, 1)}%。",
    ]
    for item in analysis.get("dimensions", []) or []:
        if isinstance(item, dict):
            reasoning_lines.append(
                f"- {item.get('label')}: {round(_score(item.get('score')) * 100, 1)}%｜{item.get('result')}"
            )

    return {
        "final_label": final_label,
        "probability": probability,
        "confidence": confidence_label(probability),
        "explanation": analysis.get("explanation") or "",
        "visual_issues": issues[:8],
        "agent_reasoning": "\n".join(reasoning_lines),
        "llm_used": True,
        "agent_model": analysis.get("modelVersion") or VLM_MODEL,
        "agent_source": analysis.get("source") or "jianzhen-v2",
        "agent_dimensions": analysis.get("dimensions") or [],
        "agent_regions": analysis.get("regions") or [],
    }
