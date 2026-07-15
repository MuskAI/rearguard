def user_explanation(
    parsed: dict,
    llm_used: bool,
    metadata_field_count: int,
    visual_issues: list[str],
) -> str:
    """Build a public explanation from evidence that actually exists."""
    final = parsed.get("final_label", "")
    confidence = parsed.get("confidence", "")
    probability = float(parsed.get("probability", parsed.get("detector_probability", 0.5)) or 0.5)

    if 0.35 < probability < 0.75 or confidence == "低":
        lines = ["模型分数处于边界区间，当前证据不足以支持确定结论，建议人工复核。"]
    elif final == "AI生成图像":
        lines = ["检测模型分数更偏向 AI 生成风险，仍需结合下列可用证据复核。"]
    else:
        lines = ["检测模型分数更偏向真实内容，仍需结合原始文件与可用证据复核。"]

    if not llm_used:
        lines.append("多模态视觉复核未完成，本次不提供视觉细节结论。")
    elif visual_issues:
        lines.append(f"多模态视觉复核提取到 {len(visual_issues)} 项可复核线索，详见视觉证据。")
    else:
        lines.append("多模态视觉复核未提取到明确的视觉可疑点。")

    if metadata_field_count > 0:
        lines.append(f"已读取 {metadata_field_count} 项文件元数据；元数据仅作为辅助线索，不单独决定真伪。")
    else:
        lines.append("未读取到可用元数据；元数据缺失本身不代表内容经过生成或篡改。")

    lines.append(f"当前置信度：{confidence}。")
    return "\n".join(lines)


def clean_visual_issues(raw: str, llm_used: bool) -> list[str]:
    if not llm_used:
        return []

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
    if not issues and text and "无可疑" not in text and "未发现" not in text:
        issues = [text]
    return issues[:6]
