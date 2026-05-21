import json


def build_reasoning_prompt(summary: dict) -> str:
    """
    构建推理 Agent 提示词。

    权重分配：
        预训练检测器 : 0.6
        视觉推理     : 0.4
        元数据分析   : 0.4

    综合概率计算公式：
        P = (0.6 × P_detector + 0.4 × P_visual + 0.4 × P_metadata) / (0.6 + 0.4 + 0.4)
          = (0.6 × P_detector + 0.4 × P_visual + 0.4 × P_metadata) / 1.4

    强元数据信号（可显著提升元数据权重的贡献）：
        - 强AI信号  : UserComment 含生成提示词、JUMBF 块
        - 强真实信号: 相机品牌 + 型号 + GPS/镜头三件套同时存在

    输出要求：简洁、有据、指出视觉可能出错的点，并分别给出各维度的AI概率估计。
    """
    prob            = summary["detector_probability"]
    label           = summary["detector_label"]
    confidence      = summary["detector_confidence_level"]
    ai_details      = summary["ai_signal_details"]
    real_details    = summary["real_signal_details"]
    consistency     = summary["evidence_consistency"]
    strong_ai       = summary.get("strong_ai_metadata", False)
    strong_real     = summary.get("strong_real_metadata", False)
    strong_ai_rsn   = summary.get("strong_ai_reason", "")
    strong_real_rsn = summary.get("strong_real_reason", "")

    ai_details_str   = "\n".join(f"  · {s}" for s in ai_details)   or "  · 无"
    real_details_str = "\n".join(f"  · {s}" for s in real_details) or "  · 无"
    mcnt = summary.get("metadata_field_count", 0)
    capn = summary.get("metadata_capture_fields", 0)

    # 根据强信号状态动态生成权重说明
    if strong_ai:
        weight_note = (
            f"⚠️  存在强AI元数据信号（{strong_ai_rsn}），"
            "元数据证据可信度高，P_metadata 应偏向 1.0。"
        )
    elif strong_real:
        weight_note = (
            f"⚠️  存在强真实元数据信号（{strong_real_rsn}），"
            "元数据证据可信度高，P_metadata 应偏向 0.0。"
        )
    else:
        weight_note = (
            "未触发代码侧的「强真实/强AI」标记；若元数据为空或缺失关键相机字段，"
            "应将 P_metadata 设为不低于 0.5（可取 0.5~0.7）以体现风险，而不是偏低。"
        )

    prompt = f"""你是数字图像取证专家。请综合以下证据判断图像真实性。

══════════════════════════════════════════
加权融合公式（必须严格遵守）
══════════════════════════════════════════
最终概率 = (0.6 × P_detector + 0.4 × P_visual + 0.4 × P_metadata) / 1.4

其中：
  · P_detector = 预训练检测器的AI生成概率（已给出，不可修改）
  · P_visual   = 你的视觉分析得出的AI生成概率（0~1，你需要自行评估）
  · P_metadata = 元数据分析得出的AI生成概率（0~1，你需要自行评估）

══════════════════════════════════════════
证据 A｜预训练 AIGC 检测器  【权重 0.6】
══════════════════════════════════════════
· AI生成概率 P_detector: {prob:.4f}  ({prob*100:.1f}%)
· 判断: {label}　置信度: {confidence}
· ⚠️ 该值为预训练模型输出，不可修改，直接代入公式。

══════════════════════════════════════════
证据 B｜你的视觉分析        【权重 0.4】
══════════════════════════════════════════
请仔细观察图像，重点排查以下AI常见缺陷，并给出 P_visual（0~1）：
  1. 纹理异常：皮肤/毛发/布料是否过于均匀、重复、塑料感
  2. 光影矛盾：阴影方向与光源是否一致，高光是否自然
  3. 解剖错误：手指数量/关节、眼睛瞳孔、耳朵结构
  4. 文字失真：图中文字是否可读、字体是否扭曲
  5. 边缘融合：物体边界、背景过渡是否自然
  6. 背景逻辑：背景元素是否符合物理/现实逻辑
  7. 整体感：景深、噪点、镜头畸变等真实相机特征是否存在

评估标准：
  · 发现明显AI缺陷 → P_visual 偏向 1.0
  · 图像自然无异常 → P_visual 偏向 0.0
  · 存在轻微可疑点 → P_visual 在 0.3~0.7 之间

══════════════════════════════════════════
证据 C｜元数据分析          【权重 0.4】
══════════════════════════════════════════
{weight_note}

AI信号:
{ai_details_str}

真实图像信号:
{real_details_str}

一致性评估: {consistency}

内部统计（供你参考，勿照抄数值）: 共 {mcnt} 个元数据字段，其中约 {capn} 项与曝光/镜头/快门等相关。
若 EXIF:Software 等为手机厂商相机应用，不代表 AI 生成工具，不得据此把 P_metadata 抬高。

评估标准：
  · 存在强AI元数据信号 → P_metadata 偏向 1.0
  · 存在强真实元数据信号 → P_metadata 偏向 0.0
  · 无强信号但元数据为空/缺失明显 → P_metadata ≥ 0.5
  · 无强信号且元数据正常存在 → P_metadata ≈ 0.5（中性）

══════════════════════════════════════════
输出要求（严格按格式，不要多余内容）
══════════════════════════════════════════

**P_visual**: [你的视觉分析AI概率，0.0000格式]

**P_metadata**: [你的元数据分析AI概率，0.0000格式]

**最终判断**: [真实图像 / AI生成图像]

**AI生成概率**: [按公式计算: (0.6×{prob:.4f} + 0.4×P_visual + 0.4×P_metadata) / 1.4，结果保留4位小数]

**置信度**: [高 / 中 / 低]

**判断依据**（3~5句，简洁直接）:
· 检测器(权重0.6): P_detector={prob:.4f}，[一句话说明]
· 视觉(权重0.4): P_visual=[值]，[指出最关键的1~3个视觉证据]
· 元数据(权重0.4): P_metadata=[值]，[一句话总结]
· 加权计算: (0.6×[P_d] + 0.4×[P_v] + 0.4×[P_m]) / 1.4 = [最终概率]
· 结论: [为什么判断为真实/AI，核心依据是什么]

**视觉可疑点**（若判断为AI生成，列出具体缺陷；若判断为真实，列出哪些地方曾让你怀疑但最终排除）:
· [具体描述，如"右手第4根手指关节弯折方向异常"]，若无则写"无明显可疑点"
"""
    return prompt.strip()