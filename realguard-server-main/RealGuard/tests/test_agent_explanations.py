from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imagedetection.Agent.utils.public_explanation import clean_visual_issues, user_explanation  # noqa: E402


def test_explanation_does_not_invent_visual_or_metadata_risk():
    parsed = {
        "final_label": "AI生成图像",
        "probability": 0.5709,
        "confidence": "低",
    }

    explanation = user_explanation(
        parsed,
        llm_used=False,
        metadata_field_count=0,
        visual_issues=[],
    )

    assert "边界区间" in explanation
    assert "不提供视觉细节结论" in explanation
    assert "元数据缺失本身不代表" in explanation
    assert "视觉层面存在" not in explanation
    assert "增加了内容真实性风险" not in explanation


def test_visual_issues_are_empty_when_visual_review_did_not_run():
    issues = clean_visual_issues("局部纹理与边缘过渡存在不自然特征。", False)

    assert issues == []


def test_explanation_counts_only_returned_visual_evidence():
    issues = clean_visual_issues("- 边缘融合异常\n- 光照方向不一致", True)
    explanation = user_explanation(
        {"final_label": "AI生成图像", "probability": 0.88, "confidence": "高"},
        llm_used=True,
        metadata_field_count=5,
        visual_issues=issues,
    )

    assert issues == ["边缘融合异常", "光照方向不一致"]
    assert "提取到 2 项可复核线索" in explanation
    assert "元数据仅作为辅助线索" in explanation
