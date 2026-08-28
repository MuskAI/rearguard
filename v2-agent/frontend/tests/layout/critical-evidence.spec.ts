import { expect, test } from "@playwright/test";
import { selectCriticalEvidence, type ExplanationPoint } from "../../src/evidenceExplanation";

test("关键证据筛选会隐藏背景说明并保留有效辅助线索", () => {
  const points: ExplanationPoint[] = [
    { label: "水印扫描", text: "未检出水印。", direction: "neutral", importance: "context" },
    { label: "真实性分析", text: "模型判断完成。", direction: "fake", importance: "critical" },
    { label: "视觉复核", text: "发现局部纹理异常。", direction: "neutral", importance: "supporting" },
    { label: "来源凭证", text: "没有可用凭证。", direction: "neutral", importance: "context" },
    { label: "综合结论", text: "AI生成图像。", direction: "fake", importance: "critical", decisive: true },
  ];

  expect(selectCriticalEvidence(points).map((point) => point.label)).toEqual(["真实性分析", "视觉复核"]);
});

test("关键证据筛选最多保留三项并按原判断顺序展示", () => {
  const points: ExplanationPoint[] = [
    { label: "真实性分析", text: "模型判断完成。", direction: "real", importance: "critical" },
    { label: "辅助分析", text: "发现辅助线索。", direction: "neutral", importance: "supporting" },
    { label: "强 AI 水印证据", text: "确认平台水印。", direction: "fake", importance: "critical", decisive: true },
    { label: "可信实拍来源凭证", text: "来源可信。", direction: "real", importance: "critical" },
    { label: "原生实拍支持", text: "相机链完整。", direction: "real", importance: "critical" },
  ];

  expect(selectCriticalEvidence(points).map((point) => point.label)).toEqual([
    "真实性分析",
    "强 AI 水印证据",
    "可信实拍来源凭证",
  ]);
});
