import type { AgentOutcome } from "./agentTypes";
import type { CaptureEvidence, DetectResult, VisibleWatermarkHit, VisibleWatermarkResult } from "./api";

const PROVIDER_LABELS: Record<string, string> = {
  gemini: "Google Gemini",
  doubao: "豆包",
  jimeng: "即梦",
  jimeng_pill: "即梦",
  samsung: "Samsung",
  yolo11x_watermark: "通用可见水印",
};

export interface ExplanationPoint {
  label: string;
  text: string;
  decisive?: boolean;
}

type RichDetectResult = DetectResult & {
  watermarkVerdictOverride?: { modelConfidence?: number };
  probabilityModel?: { pixelBaseline?: number; adjustedBaseline?: number; baseRate?: number };
};

function clamp01(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, Math.min(parsed, 1)) : 0;
}

function percent(value: unknown): string {
  return `${(clamp01(value) * 100).toFixed(1)}%`;
}

function isLocalizedHit(hit: VisibleWatermarkHit): boolean {
  return clamp01(hit.bbox?.w) > 0 && clamp01(hit.bbox?.h) > 0;
}

export function localizedWatermarkHits(report?: VisibleWatermarkResult): VisibleWatermarkHit[] {
  if (!report?.detected) return [];
  return (report.hits || []).filter(isLocalizedHit);
}

export function hasLocalizedWatermark(report?: VisibleWatermarkResult): boolean {
  return localizedWatermarkHits(report).length > 0;
}

export function decisiveWatermarkHits(report?: VisibleWatermarkResult): VisibleWatermarkHit[] {
  void report;
  return [];
}

export function hasDecisiveAiWatermark(report?: VisibleWatermarkResult): boolean {
  void report;
  return false;
}

function watermarkPoint(report?: VisibleWatermarkResult): ExplanationPoint {
  const hits = localizedWatermarkHits(report);
  if (hits.length > 0) {
    const names = Array.from(new Set(
      hits.map((hit) => hit.label || PROVIDER_LABELS[hit.provider] || "可见标记"),
    ));
    return {
      label: "可见标记线索",
      text: `定位到 ${hits.length} 处可见标记区域（${names.join("、")}）。标记可被复制、覆盖或后期添加，仅供人工核对来源，不单独决定真伪，也不抬高模型风险。`,
    };
  }
  if (!report) {
    return { label: "水印扫描", text: "本次结果未包含可用的可见水印扫描数据，因此不据此作真伪判断。" };
  }
  if (!report.supported) {
    return { label: "水印扫描", text: "可见水印检测本次不可用，没有生成替代性水印结论。" };
  }
  return { label: "水印扫描", text: "扫描已完成，未检出带有效定位框的可见水印；本项未参与抬高风险。" };
}

function captureEvidencePoint(report?: CaptureEvidence): ExplanationPoint {
  if (!report) {
    return { label: "实拍来源证据", text: "本次结果未包含结构化实拍来源分析；元数据缺失本身不作为伪造证据。" };
  }
  if (report.level === "conflict") {
    const conflicts = (report.conflicts || []).map((item) => item.label).slice(0, 2).join("、");
    return {
      label: "实拍证据冲突",
      text: `${report.summary}${conflicts ? ` 已标记：${conflicts}。` : ""}这些字段不会用于降低 AI 风险。`,
    };
  }
  if (report.supportsRealCapture) {
    const evidence = (report.evidence || []).map((item) => item.label).slice(0, 3).join("、");
    return {
      label: "拍摄流程线索",
      text: `${report.title}（${report.levelText || "辅助"}强度）：${report.summary}${evidence ? ` 可复核字段包括${evidence}。` : ""}这些字段供人工核对拍摄链，不自动证明真实，也不直接降低模型风险。`,
    };
  }
  return { label: "实拍来源证据", text: `${report.summary} 本项保持中性，不因缺少拍摄字段抬高 AI 风险。` };
}

function imageExplanation(outcome: Extract<AgentOutcome, { kind: "image" }>, risk: number, verdictLabel: string): ExplanationPoint[] {
  const result = outcome.result;
  const report = result.visibleWatermark;
  const reviewOnly = result.decisionStatus !== "verdict" || result.reviewRequired === true;
  const points: ExplanationPoint[] = [
    watermarkPoint(report),
    {
      label: "主模型",
      text: reviewOnly
        ? "模型分析已完成，但签名校准门禁未通过；原始审计分不作为真假概率展示，也不形成自动结论。"
        : `签名校准门禁已通过，本次发布的 AI 生成风险为 ${percent(risk)}。`,
    },
  ];

  const ignored = ["无明显", "暂未提取", "未提取到明确", "未发现明确"];
  const visualIssues = (result.visual_issues || []).filter((item) => item && !ignored.some((marker) => item.includes(marker)));
  if (visualIssues.length > 0) {
    points.push({
      label: "视觉复核",
      text: `提取到 ${visualIssues.length} 项可复核线索（${visualIssues[0]}）；这些线索供人工核对，不独立授权真假结论。`,
    });
  } else if (result.llm_used === false) {
    points.push({ label: "视觉复核", text: "本次未完成多模态视觉复核，不生成替代性视觉结论。" });
  } else {
    points.push({ label: "视觉复核", text: "未提取到明确异常线索，本项未参与抬高风险。" });
  }

  points.push(captureEvidencePoint(result.capture_evidence));
  points.push({
    label: "综合结论",
    decisive: !reviewOnly,
    text: reviewOnly
      ? "当前没有通过决策授权门禁的证据，结论保持“需人工复核”；请结合原始文件、来源链与可见标记位置核对。"
      : `综合现有已授权证据，结论为“${verdictLabel}”，发布风险 ${percent(risk)}；仍建议保留原始文件与来源记录。`,
  });
  return points;
}

function evidenceExplanation(outcome: Extract<AgentOutcome, { kind: "evidence" }>, risk: number, verdictLabel: string): ExplanationPoint[] {
  const result = outcome.result as RichDetectResult;
  const report = result.visibleWatermark;
  const reviewOnly = result.decisionStatus !== "verdict" || result.reviewRequired === true;
  const points: ExplanationPoint[] = [
    watermarkPoint(report),
    {
      label: "决策授权",
      text: reviewOnly
        ? "自动分析已完成，但当前模型或证据没有通过决策授权门禁；内部审计分不作为真假概率展示。"
        : result.source === "provenance"
          ? "内容凭证已通过服务器端来源验证，本次结论由可校验的来源链授权。"
          : `决策门禁已通过，本次发布风险为 ${percent(risk)}。`,
    },
  ];

  const dimensions = result.dimensions || [];
  const positive = dimensions.filter((item) => item.key !== "visible_watermark" && clamp01(item.score) >= 0.5);
  points.push(positive.length > 0
    ? { label: "辅助分析", text: `已完成 ${dimensions.length} 个证据维度，其中 ${positive.slice(0, 2).map((item) => item.label).join("、")}提示风险；作为辅助证据参与解释。` }
    : { label: "辅助分析", text: `已完成 ${dimensions.length} 个证据维度，未出现可替代水印证据的独立强结论。` });

  const provenance = outcome.provenance || result.provenance || undefined;
  points.push(provenance?.hasCredentials
    ? { label: "来源凭证", text: `检测到内容凭证，签名状态为${provenance.validationState || "待验证"}；作为来源链辅助证据。` }
    : { label: "来源凭证", text: "未发现可验证的来源凭证；凭证缺失本身不作为伪造证据。" });
  points.push(captureEvidencePoint(result.captureEvidence || provenance?.captureEvidence));
  points.push({
    label: "综合结论",
    decisive: !reviewOnly,
    text: reviewOnly
      ? "当前没有通过决策授权门禁的证据，结论保持“需人工复核”；元数据或水印缺失均不代表文件经过生成或篡改。"
      : `综合现有已授权证据，结论为“${verdictLabel}”，发布风险 ${percent(risk)}；仍建议结合原始来源复核。`,
  });
  return points;
}

export function buildEvidenceExplanation(outcome: AgentOutcome, risk: number, verdictLabel: string): ExplanationPoint[] {
  if (outcome.kind === "image") return imageExplanation(outcome, risk, verdictLabel);
  if (outcome.kind === "evidence") return evidenceExplanation(outcome, risk, verdictLabel);
  return [{ label: "模型分析", text: outcome.result.explanation || "本次未返回可展示的解释。" }];
}
