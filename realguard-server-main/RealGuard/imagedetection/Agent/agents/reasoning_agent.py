import base64
import io
import json
import os
import re
from openai import OpenAI
from PIL import Image
from prompts.reasoning_prompt import build_reasoning_prompt


# ===== 权重配置 =====
W_DETECTOR = 0.6   # 预训练检测器权重
W_VISUAL   = 0.4   # 视觉推理权重
W_METADATA = 0.4   # 元数据分析权重
W_TOTAL    = W_DETECTOR + W_VISUAL + W_METADATA  # 1.4


class ReasoningAgent:
    """
    推理 Agent（Reasoning Agent）

    对应论文 AIFo 框架中的 Reasoning Agent。
    负责综合所有取证证据，结合图像视觉信息，输出最终判断结果。

    权重分配：
        预训练检测器 : 0.6
        视觉推理     : 0.4
        元数据分析   : 0.4

    综合概率 = (0.6 × P_detector + 0.4 × P_visual + 0.4 × P_metadata) / 1.4
    """

    def __init__(
        self,
        api_key: str = "your-api-key-here",
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-vl-plus",
        use_vision: bool = True
    ):
        timeout = max(1.0, min(45.0, float(os.environ.get("REALGUARD_LLM_TIMEOUT", "20"))))
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
        )
        self.model = model
        self.use_vision = use_vision

    def _encode_image(
        self,
        image_path: str,
        max_size: int = 1024,
        max_bytes: int = 7 * 1024 * 1024,
        quality: int = 85
    ) -> str:
        img = Image.open(image_path).convert("RGB")

        w, h = img.size
        if max(w, h) > max_size:
            scale = max_size / max(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            print(f"  [图像压缩] 原始尺寸 {w}x{h} → 缩放至 {new_w}x{new_h}")

        for q in range(quality, 9, -10):
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=q, optimize=True)
            data = buffer.getvalue()
            encoded = base64.b64encode(data).decode("utf-8")
            size_mb = len(data) / (1024 * 1024)
            if len(data) <= max_bytes:
                print(f"  [图像压缩] 最终大小 {size_mb:.2f} MB，quality={q}")
                return encoded
            print(f"  [图像压缩] quality={q} 时大小 {size_mb:.2f} MB，继续压缩...")

        img = img.resize((512, 512), Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=50)
        print("  [图像压缩] 已强制缩至 512x512，quality=50")
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _get_image_media_type(self, image_path: str) -> str:
        ext = image_path.lower().split(".")[-1]
        type_map = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
            "gif": "image/gif",
        }
        return type_map.get(ext, "image/jpeg")

    @staticmethod
    def weighted_probability(p_detector: float, p_visual: float, p_metadata: float) -> float:
        """
        按权重计算综合AI生成概率。

        公式: P = (W_d × P_d + W_v × P_v + W_m × P_m) / (W_d + W_v + W_m)

        Args:
            p_detector : 预训练检测器AI概率 (0~1)
            p_visual   : 视觉分析AI概率 (0~1)
            p_metadata : 元数据分析AI概率 (0~1)

        Returns:
            float: 综合AI生成概率 (0~1)
        """
        weighted = (
            W_DETECTOR * p_detector
            + W_VISUAL * p_visual
            + W_METADATA * p_metadata
        )
        return max(0.0, min(1.0, weighted / W_TOTAL))

    @staticmethod
    def confidence_from_probability(prob: float) -> str:
        """
        按与 0.5 的距离分档：
        - 靠近 0.5 -> 低
        - 稍远      -> 中
        - 接近 0/1  -> 高
        """
        p = max(0.0, min(1.0, float(prob)))
        d = abs(p - 0.5)
        if d >= 0.35:
            return "高"
        if d >= 0.18:
            return "中"
        return "低"

    def reason_offline(self, summary: dict) -> dict:
        """
        不调用大模型：使用规则兜底（与 LLM 失败时相同的结构化解析路径），
        仍按权重公式融合检测器、中性视觉与元数据启发式概率。
        """
        raw = self._fallback_reasoning(summary)
        return self._parse_response(raw, summary)

    def reason(self, summary: dict, image_path: str = None) -> dict:
        """
        对图像进行综合推理，输出结构化检测结果。

        Returns:
            dict:
                - "final_label"         : str
                - "probability"         : float，加权综合概率
                - "p_visual"            : float，视觉分析AI概率
                - "p_metadata"          : float，元数据分析AI概率
                - "confidence"          : str
                - "explanation"         : str
                - "visual_issues"       : str
                - "raw_response"        : str
                - "detector_probability": float
                - "all_metadata"        : dict
        """
        prompt = build_reasoning_prompt(summary)

        # ===== 构建消息内容 =====
        messages_content = []

        if self.use_vision and image_path:
            try:
                image_data = self._encode_image(image_path)
                media_type = self._get_image_media_type(image_path)
                messages_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{image_data}"
                    }
                })
                print("[推理Agent] 已加载图像用于视觉分析。")
            except Exception as e:
                print(f"[推理Agent] 图像加载失败: {e}")
                raise RuntimeError(f"图像加载失败，无法调用视觉分析 API: {e}") from e

        messages_content.append({
            "type": "text",
            "text": prompt
        })

        # ===== 调用 LLM 推理 =====
        # API 调用是严格检测链路的一环。这里不吞掉异常，由上层决定是否允许兜底。
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是专业数字图像取证专家。"
                            "判断图像真实性时，严格遵循加权融合公式：\n"
                            "  最终概率 = (0.6 × P_detector + 0.4 × P_visual + 0.4 × P_metadata) / 1.4\n\n"
                            "权重规则：\n"
                            "1. 预训练检测器 P_detector 权重 0.6（已给出，不可修改）；\n"
                            "2. 你的视觉分析 P_visual 权重 0.4（你需要自行评估 0~1）；\n"
                            "3. 元数据分析 P_metadata 权重 0.4（你需要自行评估 0~1）；\n"
                            "4. 输出中必须明确给出 P_visual 和 P_metadata 的值；\n"
                            "5. 最终 AI生成概率 必须按公式计算，不可凭感觉。"
                        )
                    },
                    {
                        "role": "user",
                        "content": messages_content
                    }
                ],
                temperature=0,
            )
        except Exception as e:
            print(f"[推理Agent] LLM 调用失败: {e}")
            raise RuntimeError(f"LLM API 调用失败: {e}") from e

        raw_response = response.choices[0].message.content

        # ===== 解析结构化输出 =====
        parsed = self._parse_response(raw_response, summary)

        return parsed

    def _parse_response(self, raw_response: str, summary: dict) -> dict:
        """
        解析 LLM 的原始输出，提取结构化字段。
        使用加权公式重新计算综合概率，确保一致性。
        """
        p_detector = summary["detector_probability"]

        result = {
            "final_label": None,
            "probability": p_detector,
            "p_visual": 0.5,        # 默认中性
            "p_metadata": 0.5,      # 默认中性
            "confidence": summary["detector_confidence_level"],
            "explanation": raw_response,
            "visual_issues": "",
            "raw_response": raw_response,
            "detector_probability": p_detector,
            "all_metadata": summary.get("all_metadata", {})
        }

        # ---------- 提取 P_visual ----------
        p_visual_pattern = r"\*\*P_visual\*\*[：:]\s*([\d.]+)"
        p_visual_match = re.search(p_visual_pattern, raw_response)
        if p_visual_match:
            try:
                val = float(p_visual_match.group(1))
                if 0.0 <= val <= 1.0:
                    result["p_visual"] = val
            except ValueError:
                pass

        # ---------- 提取 P_metadata ----------
        p_metadata_pattern = r"\*\*P_metadata\*\*[：:]\s*([\d.]+)"
        p_metadata_match = re.search(p_metadata_pattern, raw_response)
        if p_metadata_match:
            try:
                val = float(p_metadata_match.group(1))
                if 0.0 <= val <= 1.0:
                    result["p_metadata"] = val
            except ValueError:
                pass

        # ---------- 无元数据时，P_metadata 不应低于 0.5 ----------
        meta_count = summary.get("metadata_field_count")
        if meta_count is None:
            meta_count = len(summary.get("all_metadata", {}) or {})
        if meta_count == 0 and result["p_metadata"] < 0.5:
            print(f"  [元数据校正] 无元数据字段，P_metadata {result['p_metadata']:.4f} → 0.5000")
            result["p_metadata"] = 0.5

        # ---------- 使用加权公式计算综合概率（以代码为准，不依赖LLM算术） ----------
        result["probability"] = self.weighted_probability(
            p_detector, result["p_visual"], result["p_metadata"]
        )

        print(f"  [加权计算] P_detector={p_detector:.4f}(w=0.6), "
              f"P_visual={result['p_visual']:.4f}(w=0.4), "
              f"P_metadata={result['p_metadata']:.4f}(w=0.4) "
              f"→ 综合概率={result['probability']:.4f}")

        # ---------- 元数据校正：强真实拍摄链且无强 AI 元数据时，禁止 P_metadata 虚高为 0.5 ----------
        sr = summary.get("strong_real_metadata", False)
        sa = summary.get("strong_ai_metadata", False)
        if sr and not sa:
            cap_n = summary.get("metadata_capture_fields", 0) or 0
            # 强真实证据越充分，元数据AI概率上限越低
            if cap_n >= 6:
                meta_cap = 0.12
            elif cap_n >= 4:
                meta_cap = 0.18
            else:
                meta_cap = 0.25
            old_pm = result["p_metadata"]
            if old_pm > meta_cap:
                print(f"  [元数据校正] 强真实拍摄元数据，P_metadata "
                      f"{old_pm:.4f} → {meta_cap}")
                result["p_metadata"] = meta_cap
            result["probability"] = self.weighted_probability(
                p_detector, result["p_visual"], result["p_metadata"]
            )
            if old_pm > meta_cap:
                note = (
                    f"\n\n[系统说明] 已根据强真实拍摄元数据（{summary.get('strong_real_reason', '')}）"
                    f"将元数据维度的 AI 概率上限校正为 {meta_cap:.2f}，"
                    f"并据此重算综合概率为 {result['probability']:.4f}。"
                    "若上文模型输出中的 P_metadata 或加权算式与此不一致，以本说明与页顶概率为准。"
                )
                result["explanation"] = (result.get("explanation") or "") + note
                print(f"  [加权计算·校正后] 综合概率={result['probability']:.4f}")

        # ---------- 最终标签：仅由加权后的综合概率决定（避免与 LLM 文本「最终判断」不一致） ----------
        result["final_label"] = (
            "AI生成图像" if result["probability"] >= 0.5 else "真实图像"
        )

        # ---------- 置信度按最终综合概率与 0.5 的距离计算 ----------
        result["confidence"] = self.confidence_from_probability(result["probability"])

        # ---------- 提取判断依据 ----------
        explanation_pattern = r"\*\*判断依据\*\*[^：:]*[：:]?\s*([\s\S]+?)(?:\*\*视觉可疑点\*\*|$)"
        explanation_match = re.search(explanation_pattern, raw_response)
        if explanation_match:
            result["explanation"] = explanation_match.group(1).strip()

        # ---------- 提取视觉可疑点 ----------
        visual_issues_pattern = r"\*\*视觉可疑点\*\*[^：:]*[：:]?\s*([\s\S]+?)$"
        visual_issues_match = re.search(visual_issues_pattern, raw_response)
        if visual_issues_match:
            result["visual_issues"] = visual_issues_match.group(1).strip()

        # 判定为真实时，前端默认展示“无可疑点”
        if result["final_label"] == "真实图像":
            result["visual_issues"] = "无可疑点"

        return result

    def _fallback_reasoning(self, summary: dict) -> str:
        """
        当 LLM 调用失败时，使用规则推理生成兜底响应。
        视觉无法评估时 P_visual=0.5，元数据根据强信号判断。
        """
        p_detector = summary["detector_probability"]
        label      = summary["detector_label"]
        confidence = summary["detector_confidence_level"]
        strong_ai  = summary.get("strong_ai_metadata", False)
        strong_real = summary.get("strong_real_metadata", False)

        # 视觉无法分析，设为中性
        p_visual = 0.5

        # 元数据根据强信号设定
        if strong_ai:
            p_metadata = 0.9
            meta_note = f"强AI元数据信号: {summary.get('strong_ai_reason', '')}，P_metadata=0.9。"
        elif strong_real:
            p_metadata = 0.1
            meta_note = f"强真实元数据信号: {summary.get('strong_real_reason', '')}，P_metadata=0.1。"
        else:
            meta_count = summary.get("metadata_field_count")
            if meta_count is None:
                meta_count = len(summary.get("all_metadata", {}) or {})
            if meta_count == 0:
                p_metadata = 0.6
                meta_note = "无元数据字段，元数据维度存在风险信号，P_metadata=0.6。"
            else:
                p_metadata = 0.5
                meta_note = "无强信号，P_metadata=0.5，不影响判断。"

        # 加权计算
        final_prob = self.weighted_probability(p_detector, p_visual, p_metadata)
        final_label = "AI生成图像" if final_prob >= 0.5 else "真实图像"
        confidence = self.confidence_from_probability(final_prob)

        return (
            f"**P_visual**: {p_visual:.4f}\n\n"
            f"**P_metadata**: {p_metadata:.4f}\n\n"
            f"**最终判断**: {final_label}\n\n"
            f"**AI生成概率**: {final_prob:.4f}\n\n"
            f"**置信度**: {confidence}\n\n"
            f"**判断依据**:\n"
            f"· 检测器(权重0.6): P_detector={p_detector:.4f}（{p_detector*100:.1f}%），判断为{label}，置信度{confidence}\n"
            f"· 视觉(权重0.4): P_visual={p_visual:.4f}，LLM调用失败，无法进行视觉分析，设为中性值\n"
            f"· 元数据(权重0.4): {meta_note}\n"
            f"· 加权计算: (0.6×{p_detector:.4f} + 0.4×{p_visual:.4f} + 0.4×{p_metadata:.4f}) / 1.4 = {final_prob:.4f}\n"
            f"· 结论: 基于加权综合概率 {final_prob:.4f} 判断为{final_label}\n\n"
            f"**视觉可疑点**: LLM调用失败，无法提供视觉分析。"
        )
