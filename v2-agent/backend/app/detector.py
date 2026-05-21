"""检测核心：图像/文本走真实 VLM（qwen3-vl-flash），视频/音频暂用 Mock。

任何 VLM 调用失败都会回退到确定性 Mock，保证接口永不中断。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import re

from dotenv import load_dotenv
from openai import OpenAI

from . import forensics

load_dotenv()

API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
VLM_MODEL = os.getenv("VLM_MODEL", "qwen3-vl-flash")
MOCK_MODEL_VERSION = "ruijian-turing-mock-v0.1"

_client: OpenAI | None = None


def _get_client() -> OpenAI | None:
    global _client
    if not API_KEY:
        return None
    if _client is None:
        _client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=60)
    return _client


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
def image_size(data: bytes) -> str:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return f"{im.width}x{im.height}"
    except Exception:
        return "未知"


def _verdict_from_score(score: float) -> str:
    if score >= 0.75:
        return "highly_suspected_fake"
    if score >= 0.5:
        return "suspected_fake"
    return "real"


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# 真实 VLM 分析
# ---------------------------------------------------------------------------
IMAGE_SYSTEM = """你是「鉴真」图像鉴伪取证引擎，融合视觉语义分析与信号级取证。
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
- score 越高=越可疑；confidence 取最可疑维度分数并与 verdict 自洽
  （<0.4→real，0.4~0.75→suspected_fake，≥0.75→highly_suspected_fake）。
- regions 坐标为归一化（0~1），优先框出 ELA 上的异常区；判定真实或无明显局部异常时给空数组。"""


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
    return _normalize(parsed, "image", source="vlm", model=VLM_MODEL)


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
    return _normalize(parsed, "document", source="vlm", model=VLM_MODEL)


FORENSIC_SYSTEM = """你是「鉴真」可解释性取证分析引擎。你会收到一张原图，以及对它做的多张信号级取证可视化图
（ELA压缩对齐、噪声成分、噪声一致性、频域谱、光照梯度、光照一致性、多次JPEG压缩曲线）。

请像数字图像取证专家那样，逐张判读每幅证据图上**具体可见的异常现象**（位置、形态、颜色），
并据此综合判断原图是否为 AI 生成 / 篡改。务必基于证据图实际内容描述，不要凭空臆造；
真实照片也要如实说明"未见异常"。只输出 JSON，不要 markdown。"""


def explainable(data: bytes) -> dict:
    """生成 7 项取证可视化 + 视觉模型逐项判读，返回完整可解释报告。"""
    suite, jpeg_points = forensics.build_suite(data)

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
                verdict = parsed.get("verdict")
                confidence = parsed.get("confidence")
                summary = str(parsed.get("summary", ""))
                for item in parsed.get("items", []):
                    if isinstance(item, dict) and item.get("key"):
                        findings[item["key"]] = item
        except Exception as e:
            print(f"[VLM] forensic call failed: {e}")

    items = []
    for it in suite:
        f = findings.get(it["key"], {})
        status = f.get("status") if f.get("status") in ("ok", "warn", "danger") else "ok"
        items.append({
            "key": it["key"],
            "title": it["title"],
            "explanation": it["explanation"],
            "status": status,
            "finding": str(f.get("finding", "")) or "（仅提供可视化证据图，未生成判读）",
            "image": "data:image/png;base64," + base64.b64encode(it["png"]).decode(),
        })

    if confidence is None:
        confidence = 0.5
    confidence = round(min(max(float(confidence), 0.0), 1.0), 3)
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
        "modelVersion": VLM_MODEL if source == "vlm" else MOCK_MODEL_VERSION,
        "source": source,
    }


def _normalize(parsed: dict, file_type: str, source: str, model: str) -> dict:
    """把模型返回的 JSON 规范化为标准结构，缺字段则补默认值。"""
    dims_def = DIMENSIONS_BY_TYPE[file_type]
    raw_dims = {d.get("key"): d for d in parsed.get("dimensions", []) if isinstance(d, dict)}
    dimensions = []
    for d in dims_def:
        rd = raw_dims.get(d["key"], {})
        score = float(rd.get("score", 0.0))
        score = min(max(score, 0.0), 1.0)
        dimensions.append({
            **d,
            "score": round(score, 2),
            "result": str(rd.get("result") or ("疑似伪造" if score >= 0.6 else "存疑" if score >= 0.4 else "未见明显异常")),
        })

    top = max(dimensions, key=lambda x: x["score"]) if dimensions else {"score": 0.0}
    confidence = float(parsed.get("confidence", top["score"]))
    confidence = round(min(max(confidence, 0.0), 1.0), 2)
    verdict = parsed.get("verdict")
    if verdict not in ("real", "suspected_fake", "highly_suspected_fake"):
        verdict = _verdict_from_score(confidence)

    regions = []
    for r in parsed.get("regions", []) or []:
        if not isinstance(r, dict):
            continue
        try:
            regions.append({
                "x": round(min(max(float(r["x"]), 0.0), 1.0), 3),
                "y": round(min(max(float(r["y"]), 0.0), 1.0), 3),
                "w": round(min(max(float(r["w"]), 0.0), 1.0), 3),
                "h": round(min(max(float(r["h"]), 0.0), 1.0), 3),
                "label": str(r.get("label", "可疑区域")),
                "score": round(min(max(float(r.get("score", confidence)), 0.0), 1.0), 2),
            })
        except (KeyError, ValueError, TypeError):
            continue

    return {
        "verdict": verdict,
        "confidence": confidence,
        "dimensions": dimensions,
        "regions": regions,
        "explanation": str(parsed.get("explanation", "")) or "模型未提供详细依据。",
        "modelVersion": model,
        "source": source,
    }


# ---------------------------------------------------------------------------
# Mock 回退
# ---------------------------------------------------------------------------
def mock_analysis(file_type: str, data: bytes) -> dict:
    seed = int(hashlib.sha256(data).hexdigest(), 16) % (2**32)
    rng = random.Random(seed)
    dims_def = DIMENSIONS_BY_TYPE[file_type]
    dimensions = []
    for d in dims_def:
        score = round(rng.uniform(0.05, 0.97), 2)
        result = "疑似伪造" if score >= 0.6 else "存疑" if score >= 0.4 else "未见明显异常"
        dimensions.append({**d, "score": score, "result": result})

    top = max(dimensions, key=lambda x: x["score"])
    confidence = top["score"]
    verdict = _verdict_from_score(confidence)

    regions = []
    if file_type in ("image", "video") and verdict != "real":
        for _ in range(rng.randint(1, 3)):
            regions.append({
                "x": round(rng.uniform(0.05, 0.6), 3),
                "y": round(rng.uniform(0.05, 0.6), 3),
                "w": round(rng.uniform(0.15, 0.35), 3),
                "h": round(rng.uniform(0.15, 0.35), 3),
                "label": rng.choice(REGION_LABELS),
                "score": round(rng.uniform(0.6, 0.95), 2),
            })

    explanations = {
        "real": "各检测维度得分均处于正常区间，未发现生成模型指纹或篡改痕迹，判定为真实内容。",
        "suspected_fake": f"在「{top['label']}」维度检测到可疑信号（得分 {confidence}），存在一定伪造嫌疑。",
        "highly_suspected_fake": f"「{top['label']}」维度得分高达 {confidence}，并检测到区域级异常，判定为高度疑似伪造。",
    }
    return {
        "verdict": verdict,
        "confidence": confidence,
        "dimensions": dimensions,
        "regions": regions,
        "explanation": explanations[verdict],
        "modelVersion": MOCK_MODEL_VERSION,
        "source": "mock",
    }


def analyze(file_type: str, filename: str, data: bytes) -> dict:
    """统一入口：能用真实模型就用，否则回退 Mock。"""
    if file_type == "image":
        result = analyze_image_vlm(data)
        if result:
            return result
    elif file_type == "document":
        try:
            text = data.decode("utf-8", errors="ignore")
        except Exception:
            text = ""
        if text.strip():
            result = analyze_text_vlm(text)
            if result:
                return result
    # video / audio / 失败回退
    return mock_analysis(file_type, data)
