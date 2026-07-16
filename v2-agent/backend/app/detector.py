"""检测核心：只返回真实模型或真实取证工具产生的结果。

生产检测失败时必须明确返回不可用，不能用演示数据替代真实性结论。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time

from dotenv import load_dotenv
from openai import OpenAI

from . import document_utils, forensics, synthid_detector, visible_watermark_detector

load_dotenv()

API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
VLM_MODEL = os.getenv("VLM_MODEL", "qwen3-vl-flash")
FORENSICS_PIPELINE_VERSION = "huijian-forensics-v2-png-prompt1"
FORENSIC_MAPS_VERSION = "huijian-forensic-maps-v2"
IMAGE_SUSPECT_THRESHOLD = float(os.getenv("JIANZHEN_IMAGE_SUSPECT_THRESHOLD", "0.62"))
IMAGE_HIGH_THRESHOLD = float(os.getenv("JIANZHEN_IMAGE_HIGH_THRESHOLD", "0.82"))
IMAGE_REGION_THRESHOLD = float(os.getenv("JIANZHEN_IMAGE_REGION_THRESHOLD", "0.72"))
IMAGE_AUXILIARY_WEIGHT = float(os.getenv("JIANZHEN_IMAGE_AUXILIARY_WEIGHT", "0.20"))
IMAGE_AUXILIARY_DISPLAY_CAP = float(os.getenv("JIANZHEN_IMAGE_AUXILIARY_DISPLAY_CAP", "0.35"))
IMAGE_AUXILIARY_KEYS = {"ela"}
FORENSIC_AUXILIARY_KEYS = {"ela", "noise", "noise_consistency", "jpeg_curve"}

_client: OpenAI | None = None


class DetectionUnavailableError(RuntimeError):
    """Raised when no real detector can produce a defensible result."""


def _get_client() -> OpenAI | None:
    global _client
    if not API_KEY:
        return None
    if _client is None:
        _client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=60)
    return _client


def calibration_status() -> dict:
    return {
        "imageSuspectThreshold": IMAGE_SUSPECT_THRESHOLD,
        "imageHighThreshold": IMAGE_HIGH_THRESHOLD,
        "imageRegionThreshold": IMAGE_REGION_THRESHOLD,
        "imageAuxiliaryWeight": IMAGE_AUXILIARY_WEIGHT,
        "imageAuxiliaryDisplayCap": IMAGE_AUXILIARY_DISPLAY_CAP,
        "auxiliaryEvidenceKeys": sorted(IMAGE_AUXILIARY_KEYS),
        "forensicAuxiliaryKeys": sorted(FORENSIC_AUXILIARY_KEYS),
    }


DIMENSIONS_BY_TYPE: dict[str, list[dict[str, str]]] = {
    "image": [
        {"key": "aigc", "label": "AIGC生成检测"},
        {"key": "tamper", "label": "篡改/拼接检测"},
        {"key": "deepfake", "label": "人脸深伪检测"},
        {"key": "ela", "label": "ELA/噪声取证"},
    ],
    "video": [
        {"key": "deepfake", "label": "换脸检测"},
        {"key": "frame_forgery", "label": "帧级伪造检测"},
        {"key": "av_sync", "label": "音画一致性"},
    ],
    "audio": [
        {"key": "tts", "label": "AI合成语音检测"},
        {"key": "clone", "label": "语音克隆检测"},
    ],
    "document": [
        {"key": "aigc_text", "label": "AIGC文本检测"},
    ],
}

REGION_LABELS = ["面部边缘异常", "光照方向不一致", "频域生成指纹", "局部重绘痕迹", "拼接接缝", "纹理重复异常"]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def image_dimensions(data: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        return im.width, im.height


def image_size(data: bytes) -> str:
    try:
        width, height = image_dimensions(data)
        return f"{width}x{height}"
    except Exception:
        return "未知"


def _verdict_from_score(score: float) -> str:
    if score >= 0.75:
        return "highly_suspected_fake"
    if score >= 0.5:
        return "suspected_fake"
    return "real"


def _image_verdict_from_score(score: float) -> str:
    if score >= IMAGE_HIGH_THRESHOLD:
        return "highly_suspected_fake"
    if score >= IMAGE_SUSPECT_THRESHOLD:
        return "suspected_fake"
    return "real"


def _merge_synthid(result: dict, data: bytes) -> dict:
    """Attach optional SynthID evidence and fold strong hits into verdict."""
    synthid = synthid_detector.detect(data)
    result["synthid"] = synthid

    # Keep the dimension list stable for the frontend while making clear that
    # a disabled/unavailable module is not evidence for or against authenticity.
    if synthid.get("supported"):
        score = float(synthid.get("confidence") or 0.0)
        detected = bool(synthid.get("detected"))
        if not detected:
            score = min(score, 0.35)
        result["dimensions"].append({
            "key": "synthid",
            "label": "SynthID水印取证",
            "score": round(score, 2),
            "result": (
                f"检测到疑似 Gemini 水印，相位匹配 {synthid.get('phaseMatch', 0)}"
                if detected
                else "未检测到可靠 Gemini 水印"
            ),
        })

        if detected and float(synthid.get("confidence") or 0.0) >= 0.85:
            result["confidence"] = round(max(float(result.get("confidence", 0.0)), float(synthid["confidence"])), 2)
            result["verdict"] = "highly_suspected_fake"
            result["explanation"] = (
                f"{result.get('explanation', '')}\n"
                "SynthID 水印取证检测到高置信度 Gemini 隐形水印信号，"
                "该信号通常与 Google Gemini 生成图像相关，因此作为强 AI 生成辅助证据纳入最终判定。"
            ).strip()
        elif detected and float(synthid.get("confidence") or 0.0) >= 0.6 and result.get("verdict") == "real":
            result["confidence"] = round(max(float(result.get("confidence", 0.0)), float(synthid["confidence"])), 2)
            result["verdict"] = "suspected_fake"
            result["explanation"] = (
                f"{result.get('explanation', '')}\n"
                "SynthID 水印取证发现中等置信度 Gemini 水印信号，当前结论上调为疑似伪造。"
            ).strip()
    else:
        result.setdefault("dimensions", []).append({
            "key": "synthid",
            "label": "SynthID水印取证",
            "score": 0.0,
            "result": str(synthid.get("note") or "未启用"),
        })

    return result


def _merge_visible_watermark(result: dict, file_type: str, filename: str, data: bytes) -> dict:
    """Attach visible watermark evidence without modifying the uploaded media."""
    visible = visible_watermark_detector.detect(data, file_type, filename)
    result["visibleWatermark"] = visible
    score = float(visible.get("confidence") or 0.0)
    detected = bool(visible.get("detected"))
    provider = str(visible.get("provider") or "")
    is_known_platform = provider in {"gemini"}

    result.setdefault("dimensions", []).append({
        "key": "visible_watermark",
        "label": "可见AI水印检测",
        "score": round(score if detected else min(score, 0.25), 2),
        "result": visible.get("note") or ("检测到可见水印" if detected else "未检测到可见水印"),
    })

    if detected and file_type == "image":
        for hit in visible.get("hits", [])[:3]:
            bbox = hit.get("bbox") or {}
            try:
                result.setdefault("regions", []).append({
                    "x": round(float(bbox["x"]), 3),
                    "y": round(float(bbox["y"]), 3),
                    "w": round(float(bbox["w"]), 3),
                    "h": round(float(bbox["h"]), 3),
                    "label": "可见AI水印",
                    "score": round(float(hit.get("confidence") or score), 2),
                })
            except (KeyError, TypeError, ValueError):
                continue

    if detected and is_known_platform and score >= 0.82:
        result["confidence"] = round(max(float(result.get("confidence", 0.0)), score), 2)
        result["verdict"] = "highly_suspected_fake"
        result["explanation"] = (
            f"{result.get('explanation', '')}\n"
            "可见水印检测发现高置信度 AI 平台导出水印，这是直接的平台生成/导出痕迹，"
            "已作为强证据纳入最终判定。"
        ).strip()
    elif detected and is_known_platform and score >= 0.6 and result.get("verdict") == "real":
        result["confidence"] = round(max(float(result.get("confidence", 0.0)), score), 2)
        result["verdict"] = "suspected_fake"
        result["explanation"] = (
            f"{result.get('explanation', '')}\n"
            "可见水印检测发现中等置信度角标水印信号，当前结论上调为疑似伪造。"
        ).strip()

    return result


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _usage_value(usage, *names: str) -> int:
    for name in names:
        if isinstance(usage, dict):
            value = usage.get(name)
        else:
            value = getattr(usage, name, None)
        if value is None:
            continue
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            continue
    return 0


def _token_usage(resp) -> dict:
    usage = getattr(resp, "usage", None)
    prompt_tokens = _usage_value(usage, "prompt_tokens", "input_tokens")
    completion_tokens = _usage_value(usage, "completion_tokens", "output_tokens")
    total_tokens = _usage_value(usage, "total_tokens")
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "promptTokens": prompt_tokens,
        "completionTokens": completion_tokens,
        "totalTokens": total_tokens,
    }


# ---------------------------------------------------------------------------
# 真实 VLM 分析
# ---------------------------------------------------------------------------
IMAGE_SYSTEM = """你是「慧鉴 AI」图像鉴伪取证引擎，融合视觉语义分析与信号级取证。
你的判定必须建立在证据之上，绝不能因为画面内容看起来正常、好看或合理就默认判真——
AIGC 与深伪的危险恰恰在于「看起来很真」。同时也不能见风就是雨，要给出有理有据的概率判断。

你会一次收到同一张图的三个视图：
【图1·原图】用于语义与视觉层面的判读。
【图2·ELA 误差水平分析图】对原图按固定质量重新 JPEG 压缩后的像素级误差，已归一化增亮。
【图3·噪声残差图】原图减去高斯模糊得到的高频残差，已自动对比拉伸。

只输出 JSON，不要 markdown 代码块，不要任何额外解释文字。"""

IMAGE_PROMPT = """请对这张图片做四个维度的鉴定，综合「原图语义线索」与「ELA/噪声信号线索」给出结论。

==== 各维度判读要点 ====

[aigc] AIGC 生成检测（是否由扩散/GAN 等生成模型整图生成）
- 语义线索：皮肤/材质过度平滑或塑料感、毛发与牙齿/手指数目结构异常、文字与纹理乱码、
  背景物体语义崩坏、景深与透视不自洽、对称性异常规整。
- 信号线索：ELA 全图异常均匀（缺少真实拍摄的纹理依赖误差）或出现规则网格/棋盘状高频；
  噪声残差缺乏自然传感器噪声、或呈现周期性人工花纹 → 强烈指向生成图。

[tamper] 篡改/拼接检测（局部 PS、换物、移花接木）
- 语义线索：边缘生硬/羽化痕迹、光照方向与阴影不一致、同一物体多次复制、比例错位。
- 信号线索（ELA 主战场）：真实未改动的照片 ELA 大体均匀、误差沿边缘自然分布；
  若**某一局部块**的 ELA 亮度明显高于/低于周围，或与全局压缩历史不一致，
  该区域极可能是后期粘贴/重绘的拼接区 → 用 regions 框出。

[deepfake] 人脸深伪检测（换脸/重演）
- 五官边界与发际线过渡、瞳孔反光不一致、牙齿/耳朵细节、肤色与脖子衔接、面部与背景噪声不一致。
- 若图中无人脸，score 给 0 并在 result 注明「无人脸」。

[ela] ELA/噪声取证（对图2、图3 的专项信号判读，作为独立证据维度）
- score 表示「信号级异常程度」：ELA/噪声越是出现局部突变、全局异常均匀、周期性人工纹理，分越高。
- 在 result 里**具体描述你在 ELA/噪声图上观察到的现象**（如「右上角存在矩形高亮块」「全图 ELA 异常均匀」「噪声呈规则网格」）。

==== 输出 JSON 结构 ====
{
  "verdict": "real | suspected_fake | highly_suspected_fake",
  "confidence": 0.0-1.0,
  "dimensions": [
    {"key":"aigc","label":"AIGC生成检测","score":0.0-1.0,"result":"简短结论"},
    {"key":"tamper","label":"篡改/拼接检测","score":0.0-1.0,"result":"简短结论"},
    {"key":"deepfake","label":"人脸深伪检测","score":0.0-1.0,"result":"简短结论"},
    {"key":"ela","label":"ELA/噪声取证","score":0.0-1.0,"result":"描述 ELA/噪声图上的具体观察"}
  ],
  "regions": [{"x":0-1,"y":0-1,"w":0-1,"h":0-1,"label":"基于 ELA 的可疑区描述","score":0-1}],
  "explanation": "用中文综合语义+信号两类证据给出判定依据，3-5 句，需点明 ELA/噪声证据"
}

约束：
- score 越高=越可疑；confidence 取最可疑维度分数并与 verdict 自洽。
- 为降低误判，只有明确、可复核的异常才给高分：
  <0.62→real，0.62~0.82→suspected_fake，≥0.82→highly_suspected_fake。
- ELA/噪声取证属于辅助证据：只有 ELA/噪声异常、但原图语义和其他维度没有互相印证时，
  不要直接判 suspected_fake 或 highly_suspected_fake，最多在 ela.result 中写“需复核”。
- ELA/噪声的 score 表示辅助提示强度，不等同于伪造概率；不要给它超过 0.35 的主导分值。
- regions 坐标为归一化（0~1）。只有局部异常边界清晰、与周围 ELA/噪声模式明显不同，
  且区域置信度 ≥0.72 时才输出；普通高亮边缘、纹理、压缩块、整图均匀变化不要框。"""


def analyze_image_vlm(data: bytes) -> dict | None:
    client = _get_client()
    if client is None:
        return None
    try:
        im = forensics.prep(data, max_side=1024)
        orig_uri = "data:image/jpeg;base64," + base64.b64encode(forensics.to_jpeg(im)).decode()
        ela_uri = "data:image/png;base64," + base64.b64encode(forensics.ela_png(im)).decode()
        noise_uri = "data:image/png;base64," + base64.b64encode(forensics.noise_png(im)).decode()
    except Exception as e:
        print(f"[forensics] preprocessing failed: {e}")
        return None

    try:
        resp = client.chat.completions.create(
            model=VLM_MODEL,
            messages=[
                {"role": "system", "content": IMAGE_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "【图1·原图】"},
                        {"type": "image_url", "image_url": {"url": orig_uri}},
                        {"type": "text", "text": "【图2·ELA 误差水平分析图】亮区=压缩误差大=潜在异常"},
                        {"type": "image_url", "image_url": {"url": ela_uri}},
                        {"type": "text", "text": "【图3·噪声残差图】用于判断传感器噪声一致性"},
                        {"type": "image_url", "image_url": {"url": noise_uri}},
                        {"type": "text", "text": IMAGE_PROMPT},
                    ],
                },
            ],
            temperature=0.2,
            max_tokens=1200,
        )
        content = resp.choices[0].message.content or ""
    except Exception as e:
        print(f"[VLM] image call failed: {e}")
        return None

    parsed = _extract_json(content)
    if not parsed:
        print(f"[VLM] failed to parse JSON: {content[:200]}")
        return None
    return _normalize(parsed, "image", source="vlm", model=VLM_MODEL, token_usage=_token_usage(resp))


TEXT_SYSTEM = (
    "你是专业的 AIGC 文本检测引擎，判断一段文本是否由 AI（大语言模型）生成。"
    "从用词分布、句式规律、逻辑连贯性、信息密度等角度分析。只输出 JSON。"
)

TEXT_PROMPT = """请判断下面的文本是否为 AI 生成，严格输出 JSON：
{
  "verdict": "real | suspected_fake | highly_suspected_fake",
  "confidence": 0.0-1.0,
  "dimensions": [{"key":"aigc_text","label":"AIGC文本检测","score":0.0-1.0,"result":"简短结论"}],
  "regions": [],
  "explanation": "中文判定依据"
}
（verdict=real 表示人类撰写，suspected/highly 表示疑似/高度疑似 AI 生成）

待检测文本：
---
{TEXT}
---"""


def analyze_text_vlm(text: str) -> dict | None:
    client = _get_client()
    if client is None:
        return None
    snippet = text[:4000]
    try:
        resp = client.chat.completions.create(
            model=VLM_MODEL,
            messages=[
                {"role": "system", "content": TEXT_SYSTEM},
                {"role": "user", "content": TEXT_PROMPT.replace("{TEXT}", snippet)},
            ],
            temperature=0.2,
            max_tokens=600,
        )
        content = resp.choices[0].message.content or ""
    except Exception as e:
        print(f"[VLM] text call failed: {e}")
        return None
    parsed = _extract_json(content)
    if not parsed:
        return None
    return _normalize(parsed, "document", source="vlm", model=VLM_MODEL, token_usage=_token_usage(resp))


FORENSIC_SYSTEM = """你是「慧鉴 AI」可解释性取证分析引擎。你会收到一张原图，以及对它做的多张信号级取证可视化图
（ELA压缩对齐、噪声成分、噪声一致性、频域谱、光照梯度、光照一致性、多次JPEG压缩曲线）。

请像数字图像取证专家那样，逐张判读每幅证据图上**具体可见的异常现象**（位置、形态、颜色），
并据此综合判断原图是否为 AI 生成 / 篡改。务必基于证据图实际内容描述，不要凭空臆造；
真实照片也要如实说明"未见异常"。
ELA、噪声成分、噪声一致性、多次 JPEG 曲线只作为辅助线索，容易受压缩、缩放、纹理影响；
这些项目不能单独决定真伪，必须有语义/频域/光照等证据互相印证才上调整体结论。
只输出 JSON，不要 markdown。"""


def explainable(data: bytes) -> dict:
    """生成 7 项取证可视化 + 视觉模型逐项判读，返回完整可解释报告。"""
    started = time.perf_counter()
    suite, jpeg_points = forensics.build_suite(data)
    maps_ms = int((time.perf_counter() - started) * 1000)

    keys_desc = "\n".join(f"- {it['key']}（{it['title']}）：{it['explanation']}" for it in suite)
    prompt = f"""下面依次给出【原图】和 7 张取证图。各分析项含义：
{keys_desc}

多次JPEG压缩曲线数据点（quality→error）：{jpeg_points}

请逐项判读并严格输出 JSON：
{{
  "verdict": "real | suspected_fake | highly_suspected_fake",
  "confidence": 0.0-1.0,
  "items": [
    {{"key":"ela","status":"ok|warn|danger","finding":"在该证据图上看到的具体异常（位置/形态），无异常则写未见明显异常"}},
    ... 对全部 7 个 key 各一条，key 必须与上面一致 ...
  ],
  "summary": "综合判定，3-6 句：指出哪几项构成核心伪造指纹，给出结论与建议（中文）"
}}
status 含义：ok=正常 / warn=可疑 / danger=高危。confidence 与 verdict 自洽。"""

    client = _get_client()
    findings: dict[str, dict] = {}
    verdict, confidence, summary = None, None, ""
    source = "maps-only"
    token_usage = {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0}
    model_ms = 0

    if client is not None:
        content_parts: list[dict] = [{"type": "text", "text": "【原图】"}]
        # 原图
        orig = forensics.prep(data, max_side=768)
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(forensics.to_jpeg(orig)).decode()},
        })
        for it in suite:
            content_parts.append({"type": "text", "text": f"【{it['title']}】"})
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + base64.b64encode(it["png"]).decode()},
            })
        content_parts.append({"type": "text", "text": prompt})

        model_started = time.perf_counter()
        try:
            resp = client.chat.completions.create(
                model=VLM_MODEL,
                messages=[
                    {"role": "system", "content": FORENSIC_SYSTEM},
                    {"role": "user", "content": content_parts},
                ],
                temperature=0.2,
                max_tokens=1600,
            )
            parsed = _extract_json(resp.choices[0].message.content or "")
            if parsed:
                source = "vlm"
                token_usage = _token_usage(resp)
                verdict = parsed.get("verdict")
                confidence = parsed.get("confidence")
                summary = str(parsed.get("summary", ""))
                for item in parsed.get("items", []):
                    if isinstance(item, dict) and item.get("key"):
                        findings[item["key"]] = item
        except Exception as e:
            print(f"[VLM] forensic call failed: {e}")
        finally:
            model_ms = int((time.perf_counter() - model_started) * 1000)

    items = []
    for it in suite:
        f = findings.get(it["key"], {})
        status = f.get("status") if f.get("status") in ("ok", "warn", "danger") else "ok"
        finding = str(f.get("finding", "")) or "（仅提供可视化证据图，未生成判读）"
        if it["key"] in FORENSIC_AUXILIARY_KEYS and status == "danger":
            status = "warn"
            finding = f"{finding}（辅助线索，已降权）"
        items.append({
            "key": it["key"],
            "title": it["title"],
            "explanation": it["explanation"],
            "status": status,
            "finding": finding,
            "image": "data:image/png;base64," + base64.b64encode(it["png"]).decode(),
        })

    if confidence is None:
        confidence = 0.5
    confidence = round(min(max(float(confidence), 0.0), 1.0), 3)
    core_alerts = [it for it in items if it["key"] not in FORENSIC_AUXILIARY_KEYS and it["status"] in ("warn", "danger")]
    core_dangers = [it for it in core_alerts if it["status"] == "danger"]
    if not core_alerts:
        confidence = min(confidence, 0.49)
        verdict = "real"
        if summary:
            summary = f"{summary}\nELA/噪声/压缩曲线仅作为辅助提示，未见非辅助证据互相印证，整体结论已降权处理。"
    elif not core_dangers:
        confidence = min(confidence, 0.68)
        if verdict == "highly_suspected_fake":
            verdict = "suspected_fake"
    if verdict not in ("real", "suspected_fake", "highly_suspected_fake"):
        verdict = _verdict_from_score(confidence)
    if not summary:
        summary = "已生成 7 项取证可视化证据图，请结合各图异常综合判断。"

    return {
        "verdict": verdict,
        "confidence": confidence,
        "summary": summary,
        "items": items,
        "jpegPoints": jpeg_points,
        "modelVersion": VLM_MODEL if source == "vlm" else FORENSIC_MAPS_VERSION,
        "source": source,
        "tokenUsage": token_usage,
        "timings": {
            "mapsMs": maps_ms,
            "modelMs": model_ms,
            "totalMs": int((time.perf_counter() - started) * 1000),
        },
    }


def compact_explainable_for_cache(report: dict) -> dict:
    """Keep model findings in SQLite while leaving regenerated PNG maps out of the cache."""
    compact = {key: value for key, value in report.items() if key not in {"fileMeta", "elapsedMs"}}
    compact["items"] = [
        {key: value for key, value in item.items() if key != "image"}
        for item in report.get("items", [])
        if isinstance(item, dict)
    ]
    return compact


def attach_forensic_images(data: bytes, cached_report: dict) -> dict:
    """Regenerate deterministic maps for a cached model judgment."""
    started = time.perf_counter()
    suite, jpeg_points = forensics.build_suite(data)
    cached_items = {
        item.get("key"): item
        for item in cached_report.get("items", [])
        if isinstance(item, dict) and item.get("key")
    }
    items = []
    for item in suite:
        cached = cached_items.get(item["key"], {})
        items.append({
            "key": item["key"],
            "title": item["title"],
            "explanation": item["explanation"],
            "status": cached.get("status", "ok"),
            "finding": cached.get("finding", "未生成模型判读"),
            "image": "data:image/png;base64," + base64.b64encode(item["png"]).decode(),
        })

    report = dict(cached_report)
    report["items"] = items
    report["jpegPoints"] = jpeg_points
    timings = dict(report.get("timings") or {})
    timings["cacheMapsMs"] = int((time.perf_counter() - started) * 1000)
    report["timings"] = timings
    return report


def _normalize(parsed: dict, file_type: str, source: str, model: str, token_usage: dict | None = None) -> dict:
    """把模型返回的 JSON 规范化为标准结构，缺字段则补默认值。"""
    dims_def = DIMENSIONS_BY_TYPE[file_type]
    raw_dims = {d.get("key"): d for d in parsed.get("dimensions", []) if isinstance(d, dict)}
    dimensions = []
    raw_scores: dict[str, float] = {}
    for d in dims_def:
        rd = raw_dims.get(d["key"], {})
        raw_score = min(max(float(rd.get("score", 0.0)), 0.0), 1.0)
        raw_scores[d["key"]] = raw_score
        score = raw_score
        if file_type == "image" and d["key"] in IMAGE_AUXILIARY_KEYS:
            score = min(raw_score * IMAGE_AUXILIARY_WEIGHT, IMAGE_AUXILIARY_DISPLAY_CAP)
        result = str(rd.get("result") or ("疑似伪造" if raw_score >= 0.6 else "存疑" if raw_score >= 0.4 else "未见明显异常"))
        if file_type == "image" and d["key"] in IMAGE_AUXILIARY_KEYS and raw_score >= 0.6:
            result = f"{result}（辅助线索，已降权）"
        dimensions.append({
            **d,
            "score": round(score, 2),
            "result": result,
        })

    top = max(dimensions, key=lambda x: x["score"]) if dimensions else {"score": 0.0}
    regions = []
    for r in parsed.get("regions", []) or []:
        if not isinstance(r, dict):
            continue
        try:
            region_score = round(min(max(float(r.get("score", top["score"])), 0.0), 1.0), 2)
            w = round(min(max(float(r["w"]), 0.0), 1.0), 3)
            h = round(min(max(float(r["h"]), 0.0), 1.0), 3)
            if file_type == "image":
                area = w * h
                if region_score < IMAGE_REGION_THRESHOLD or area < 0.002 or area > 0.55:
                    continue
            regions.append({
                "x": round(min(max(float(r["x"]), 0.0), 1.0), 3),
                "y": round(min(max(float(r["y"]), 0.0), 1.0), 3),
                "w": w,
                "h": h,
                "label": str(r.get("label", "可疑区域")),
                "score": region_score,
            })
        except (KeyError, ValueError, TypeError):
            continue

    confidence = float(parsed.get("confidence", top["score"]))
    confidence = round(min(max(confidence, 0.0), 1.0), 2)
    verdict = parsed.get("verdict")

    if file_type == "image":
        non_aux_scores = [d["score"] for d in dimensions if d["key"] not in IMAGE_AUXILIARY_KEYS]
        aux_scores = [raw_scores.get(d["key"], 0.0) for d in dimensions if d["key"] in IMAGE_AUXILIARY_KEYS]
        strongest_non_aux = max(non_aux_scores) if non_aux_scores else 0.0
        strongest_aux = max(aux_scores) if aux_scores else 0.0
        strongest_region = max((r["score"] for r in regions), default=0.0)

        calibrated = max(strongest_non_aux, strongest_region)
        # ELA/noise maps are useful explainability layers, but compression,
        # resizing and texture can make them look abnormal. Let them lift a
        # borderline result slightly, never dominate the final verdict alone.
        if strongest_aux >= IMAGE_SUSPECT_THRESHOLD and strongest_non_aux >= IMAGE_SUSPECT_THRESHOLD:
            calibrated = max(calibrated, min(strongest_non_aux + 0.04, IMAGE_SUSPECT_THRESHOLD + 0.06))

        if calibrated < IMAGE_SUSPECT_THRESHOLD:
            confidence = min(confidence, max(calibrated, 0.49))
            verdict = "real"
        else:
            confidence = round(min(max(confidence, calibrated), calibrated + 0.08, 1.0), 2)
            verdict = _image_verdict_from_score(confidence)
    elif verdict not in ("real", "suspected_fake", "highly_suspected_fake"):
        verdict = _verdict_from_score(confidence)

    return {
        "verdict": verdict,
        "confidence": confidence,
        "dimensions": dimensions,
        "regions": regions,
        "explanation": str(parsed.get("explanation", "")) or "模型未提供详细依据。",
        "modelVersion": model,
        "source": source,
        "tokenUsage": token_usage or {"promptTokens": 0, "completionTokens": 0, "totalTokens": 0},
    }


def analyze(file_type: str, filename: str, data: bytes) -> dict:
    """统一入口：只接受真实检测结果，能力缺失或调用失败时明确中止。"""
    if file_type == "image":
        result = analyze_image_vlm(data)
        if result:
            result = _merge_synthid(result, data)
            return _merge_visible_watermark(result, file_type, filename, data)
        raise DetectionUnavailableError("图像检测模型暂不可用，本次未生成真实性结论，请稍后重试。")

    if file_type == "document":
        extracted = document_utils.extract_text(filename, data)
        if extracted.text.strip():
            result = analyze_text_vlm(extracted.text)
            if result:
                if extracted.note:
                    result["explanation"] = f"{result['explanation']}\n文档处理：{extracted.note}。"
                return result
            raise DetectionUnavailableError("文档检测模型暂不可用，本次未生成真实性结论，请稍后重试。")
        note = extracted.note or "未提取到可分析正文"
        raise DetectionUnavailableError(f"无法分析该文档：{note}。本次未生成真实性结论。")

    capability = {"video": "视频", "audio": "音频"}.get(file_type, "该类型")
    raise DetectionUnavailableError(f"{capability}检测能力尚未部署，本次未生成真实性结论。")
