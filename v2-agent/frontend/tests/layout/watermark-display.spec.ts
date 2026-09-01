import { expect, test } from "@playwright/test";
import { displayableWatermarkHits, WATERMARK_BOX_MIN_CONFIDENCE } from "../../src/evidenceExplanation";
import type { VisibleWatermarkResult } from "../../src/api";

function reportWithConfidences(confidences: number[]): VisibleWatermarkResult {
  return {
    supported: true,
    detected: true,
    provider: "yolo11x_watermark",
    confidence: Math.max(...confidences),
    evidenceLevel: "supporting",
    hits: confidences.map((confidence, index) => ({
      provider: "yolo11x_watermark",
      label: `候选区域 ${index + 1}`,
      confidence,
      bbox: { x: index * 0.1, y: index * 0.1, w: 0.08, h: 0.06 },
      method: "explicit_watermark_model_direct",
    })),
    temporal: { sampledFrames: 1, positiveFrames: 1, moving: false },
    note: "水印检测展示门槛测试",
  };
}

test("水印框只展示置信度不低于 50% 的定位结果", () => {
  const displayed = displayableWatermarkHits(reportWithConfidences([0.12, 0.499, 0.5, 0.83]));

  expect(WATERMARK_BOX_MIN_CONFIDENCE).toBe(0.5);
  expect(displayed.map((hit) => hit.confidence)).toEqual([0.5, 0.83]);
});

test("没有有效坐标的高分水印也不会生成定位框", () => {
  const report = reportWithConfidences([0.9]);
  report.hits[0].bbox = { x: 0.2, y: 0.2, w: 0, h: 0.1 };

  expect(displayableWatermarkHits(report)).toEqual([]);
});
