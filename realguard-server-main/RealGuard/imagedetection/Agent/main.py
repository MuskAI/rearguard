import sys
import os
import json

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.evidence_collector import EvidenceCollector
from agents.reasoning_agent import ReasoningAgent
from utils.evidence_summary import summarize_evidence

_system = None


def _llm_settings():
    key = (
        os.environ.get("REALGUARD_LLM_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or ""
    ).strip()
    if not key or key in ("your-api-key-here", "sk-your-key"):
        return None
    base = (
        os.environ.get("REALGUARD_LLM_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).strip()
    model = (os.environ.get("REALGUARD_LLM_MODEL") or "qwen-vl-plus").strip()
    use_vision = os.environ.get("REALGUARD_LLM_VISION", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    return {"api_key": key, "base_url": base, "model": model, "use_vision": use_vision}


def _strict_pipeline_enabled() -> bool:
    return os.environ.get("REALGUARD_STRICT_PIPELINE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _user_explanation(
    parsed: dict,
    llm_used: bool,
    metadata_field_count: int,
) -> str:
    """面向页面的推理说明：保留证据结论，不暴露公式和内部概率。"""
    final = parsed.get("final_label", "")
    conf = parsed.get("confidence", "")
    p_visual = float(parsed.get("p_visual", 0.5) or 0.5)

    if final == "AI生成图像":
        lines = ["综合检测器、视觉细节与元数据线索，图像整体更偏向 AI 生成。"]
    else:
        lines = ["综合检测器、视觉细节与元数据线索，图像整体更偏向真实拍摄。"]

    if p_visual >= 0.65:
        lines.append("视觉层面存在一定不自然特征，支持当前判定。")
    elif p_visual <= 0.35:
        lines.append("视觉层面整体较自然，未发现明显 AI 生成痕迹。")
    else:
        lines.append("视觉层面存在少量不确定特征，需结合其他证据综合判断。")

    if metadata_field_count > 0:
        lines.append(f"已提取 {metadata_field_count} 项元数据，为真实性判断提供辅助线索。")
    else:
        lines.append("图像缺少可验证的相机元数据，增加了内容真实性风险。")

    if not llm_used:
        lines.append("多模态视觉 API 未完成调用，当前结果未包含完整视觉分析。")
    lines.append(f"当前置信度：{conf}。")
    return "\n".join(lines)


def _clean_visual_issues(raw: str, final_label: str) -> list:
    if final_label == "真实图像":
        return ["无明显视觉可疑点。"]

    text = (raw or "").strip()
    issues = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = line.lstrip("-·•*0123456789.、) ").strip()
        if not line or "无明显" in line or line == "无可疑点":
            continue
        issues.append(line)
    if not issues and text:
        issues = [text]
    if not issues:
        issues = [
            "局部纹理与边缘过渡存在不自然特征。",
            "画面缺少可验证的真实相机成像线索。",
        ]
    return issues[:6]


def detect(image_path: str) -> dict:
    """对外接口：证据采集 → 摘要 → 推理 Agent（LLM 可选）→ 与页面兼容的字段。"""
    global _system
    if _system is None:
        _system = _AIGCAgentSystem()
    return _system.run(image_path)


class _AIGCAgentSystem:

    def __init__(self):
        self.strict_pipeline = _strict_pipeline_enabled()
        self.collector = EvidenceCollector(strict=self.strict_pipeline)

    def run(self, image_path: str) -> dict:
        print(f"\n{'='*60}")
        print("  AIGC 检测系统（证据融合 + 可选视觉大模型）")
        print(f"  图像路径: {image_path}")
        print(f"{'='*60}")

        print("\n========== Step 1: 取证工具（元数据 + 检测器）==========")
        evidence = self.collector.collect(image_path)
        metadata_raw = evidence.get("metadata_raw") or {}
        metadata_signals = evidence.get("metadata_signals") or {}

        print("\n========== Step 2: 证据摘要 ==========")
        summary = summarize_evidence(evidence)

        cfg = _llm_settings()
        if self.strict_pipeline and not cfg:
            raise RuntimeError(
                "未配置大模型 API Key。请设置 REALGUARD_LLM_API_KEY（或 DASHSCOPE_API_KEY）后重试。"
            )
        llm_used = False
        agent = ReasoningAgent(
            api_key=(cfg or {}).get("api_key") or "offline-placeholder",
            base_url=(cfg or {}).get("base_url") or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=(cfg or {}).get("model") or "qwen-vl-plus",
            use_vision=bool((cfg or {}).get("use_vision", True)),
        )

        print("\n========== Step 3: 推理 Agent ==========")
        if cfg:
            print("  模式: 多模态大模型 + 加权融合")
            try:
                parsed = agent.reason(summary, image_path)
                llm_used = True
            except Exception as e:
                if self.strict_pipeline:
                    raise RuntimeError(f"大模型推理失败: {e}")
                print(f"  [警告] 大模型推理异常，改用规则融合: {e}")
                parsed = agent.reason_offline(summary)
                llm_used = False
        else:
            print("  模式: 规则融合（未配置 LLM API Key）")
            parsed = agent.reason_offline(summary)
            llm_used = False

        detector_probability = float(parsed.get("detector_probability", summary["detector_probability"]))
        probability = float(parsed.get("probability", detector_probability))
        p_visual = float(parsed.get("p_visual", 0.5))
        p_metadata = float(parsed.get("p_metadata", 0.5))
        final_label = parsed.get("final_label") or (
            "AI生成图像" if probability >= 0.5 else "真实图像"
        )
        confidence = parsed.get("confidence") or "低"
        raw_response = parsed.get("raw_response") or ""
        visual_issues = _clean_visual_issues(parsed.get("visual_issues", ""), final_label)
        agent_reasoning = raw_response

        explanation = _user_explanation(
            parsed,
            llm_used=llm_used,
            metadata_field_count=len(metadata_raw),
        )

        final_result = {
            "final_label": final_label,
            "probability": probability,
            "detector_probability": detector_probability,
            "p_visual": p_visual,
            "p_metadata": p_metadata,
            "confidence": confidence,
            "explanation": explanation,
            "agent_reasoning": agent_reasoning,
            "raw_response": raw_response,
            "visual_issues": visual_issues,
            "all_metadata": metadata_raw,
            "metadata_signals": metadata_signals,
            "metadata_count": len(metadata_raw),
            "llm_used": llm_used,
        }

        self._print_result(final_result)
        return final_result

    def _print_result(self, r: dict):
        print(f"\n{'='*60}")
        print("  最终检测结果")
        print(f"{'='*60}")
        print(f"\n【1】结论       : {r['final_label']}")
        print(f"【2】综合 AI 概率: {r['probability']:.4f}  ({r['probability']*100:.2f}%)")
        print(f"     检测器     : {r['detector_probability']:.4f}")
        print(f"     P_visual   : {r['p_visual']:.4f}  P_metadata: {r['p_metadata']:.4f}")
        print(f"     置信度      : {r['confidence']}")
        print(f"     LLM 视觉    : {'是' if r.get('llm_used') else '否'}")
        print(f"\n【3】说明:")
        print("-" * 50)
        print(r["explanation"])
        print("-" * 50)
        print(f"\n【4】完整元数据: {r['metadata_count']} 个字段")
        if r["all_metadata"]:
            print(json.dumps(r["all_metadata"], ensure_ascii=False, indent=2)[:4000])
            if len(json.dumps(r["all_metadata"])) > 4000:
                print("  ... (truncated)")
        else:
            print("  （未提取到元数据）")
        print(f"\n{'='*60}\n")


if __name__ == "__main__":
    image_path = "/home/ymk/RealGuard/AIGC_image_detection_system/imagedetection/static/system/92af4756-5250-42e8-8733-11639065f1e6.webp"

    result = detect(image_path)

    output_path = "forensic_result.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"结果已保存至: {output_path}")
