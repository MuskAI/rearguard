import type { AgentOutcome } from "./agentTypes";
import type { CaptureEvidence, DetectResult, ProvenanceReport, VisibleWatermarkHit, VisibleWatermarkResult } from "./api";

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
  direction?: "fake" | "real" | "warning" | "neutral";
  importance: "critical" | "supporting" | "context";
}

const CRITICAL_EVIDENCE_LIMIT = 3;

function criticalEvidenceScore(point: ExplanationPoint): number {
  if (point.importance === "context") return 0;
  const importanceScore = point.importance === "critical" ? 800 : point.importance === "supporting" ? 400 : 0;
  const directionScore = point.direction === "fake" || point.direction === "real" ? 100 : point.direction === "warning" ? 50 : 0;
  return importanceScore + directionScore + (point.decisive ? 200 : 0);
}

export function selectCriticalEvidence(points: ExplanationPoint[], limit = CRITICAL_EVIDENCE_LIMIT): ExplanationPoint[] {
  return points
    .map((point, index) => ({ point, index, score: point.label === "综合结论" ? 0 : criticalEvidenceScore(point) }))
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score || left.index - right.index)
    .slice(0, Math.max(0, limit))
    .sort((left, right) => left.index - right.index)
    .map((item) => item.point);
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

function verdictDirection(verdictLabel: string, reviewOnly: boolean): ExplanationPoint["direction"] {
  if (reviewOnly) return "warning";
  return /AI生成|伪造|篡改|深伪|fake/i.test(verdictLabel) ? "fake" : "real";
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
  if (
    report?.explicitWatermark?.available !== true
    || report.explicitWatermark.detected !== true
    || report.explicitWatermark.aiWatermarkVerdict?.verdict !== "yes"
    || report.explicitWatermark.aiWatermarkVerdict?.isAiGeneratedWatermark !== true
  ) return [];
  return localizedWatermarkHits(report).filter((hit) => hit.decisive === true);
}

export function hasDecisiveAiWatermark(report?: VisibleWatermarkResult): boolean {
  return decisiveWatermarkHits(report).length > 0;
}

function watermarkPoint(report?: VisibleWatermarkResult): ExplanationPoint {
  const hits = localizedWatermarkHits(report);
  const decisive = decisiveWatermarkHits(report);
  if (decisive.length > 0) {
    const names = Array.from(new Set(
      decisive.map((hit) => hit.label || PROVIDER_LABELS[hit.provider] || "AI 平台水印"),
    ));
    return {
      label: "强 AI 水印证据",
      decisive: true,
      direction: "fake",
      importance: "critical",
      text: `定位到 ${decisive.length} 处强水印证据（${names.join("、")}），并通过平台图形、文字内容和位置三项核对，属于支持 AI 生成的强证据。`,
    };
  }
  if (hits.length > 0) {
    const names = Array.from(new Set(
      hits.map((hit) => hit.label || PROVIDER_LABELS[hit.provider] || "可见标记"),
    ));
    return {
      label: "可见标记线索",
      direction: "neutral",
      importance: "context",
      text: `定位到 ${hits.length} 处可见标记区域（${names.join("、")}），但平台归属或水印性质尚未完全确认，仅供核对来源，不单独决定真伪。`,
    };
  }
  if (!report) {
    return { label: "水印扫描", direction: "neutral", importance: "context", text: "本次结果没有可用的水印扫描数据，因此本项不参与真假判断。" };
  }
  if (!report.supported) {
    return { label: "水印扫描", direction: "warning", importance: "context", text: "可见水印检测本次未完成，系统没有据此生成替代性结论。" };
  }
  return { label: "水印扫描", direction: "neutral", importance: "context", text: "扫描已完成，未检出带有效位置的可见水印；没有水印不等同于真实，本项保持中性。" };
}

function captureEvidencePoint(report?: CaptureEvidence): ExplanationPoint {
  if (!report) {
    return { label: "实拍来源证据", direction: "neutral", importance: "context", text: "本次没有形成可用的实拍来源分析；缺少拍摄信息本身不代表图片是伪造的。" };
  }
  if (report.level === "conflict") {
    const conflicts = (report.conflicts || []).map((item) => item.label).slice(0, 2).join("、");
    return {
      label: "实拍证据冲突",
      direction: "warning",
      importance: "critical",
      text: `${report.summary}${conflicts ? ` 冲突项包括：${conflicts}。` : ""}这些字段不会用于降低 AI 风险。`,
    };
  }
  if (report.supportsRealCapture) {
    const evidence = (report.evidence || []).map((item) => item.label).slice(0, 3).join("、");
    const impact = report.adjustmentEligible
      ? "在不存在强水印、生成声明或完整性冲突时，该证据可对边界区间的 AI 风险作保守下调，但不会覆盖高风险模型结果。"
      : "这些字段供人工核对拍摄链；普通 EXIF 可以被编辑，因此不会单独证明真实。";
    return {
      label: report.adjustmentEligible ? "原生实拍支持" : "拍摄流程线索",
      direction: "real",
      importance: "critical",
      text: `${report.title}：${report.summary}${evidence ? ` 可核对的信息包括${evidence}。` : ""}${impact}`,
    };
  }
  return { label: "实拍来源证据", direction: "neutral", importance: "context", text: `${report.summary} 系统不会因为缺少拍摄字段而提高 AI 风险。` };
}

function provenancePoint(report?: ProvenanceReport): ExplanationPoint {
  if (!report) {
    return {
      label: "内容来源凭证",
      direction: "neutral",
      importance: "context",
      text: "本次结果没有包含可验证的内容来源凭证；凭证缺失本身不代表图片经过生成或篡改。",
    };
  }
  const validation = String(report.validationState || "").trim().toLowerCase();
  const trusted = report.credentialTrusted === true || validation === "trusted";
  if (trusted && report.isAiGenerated === true) {
    return {
      label: "可信 AI 来源凭证",
      direction: "fake",
      decisive: true,
      importance: "critical",
      text: `文件携带可信的内容来源凭证，并明确声明包含 AI 生成或合成内容${report.generator ? `；生成工具为 ${report.generator}` : ""}。`,
    };
  }
  if (trusted && report.isAiGenerated === false) {
    return {
      label: "可信实拍来源凭证",
      direction: "real",
      importance: "critical",
      text: `文件携带可信的内容来源凭证，并声明由相机捕获${report.issuer ? `；签发方为 ${report.issuer}` : ""}。`,
    };
  }
  if (validation === "invalid") {
    return { label: "来源凭证异常", direction: "warning", importance: "critical", text: "文件包含内容来源凭证，但完整性校验未通过；其中的来源声明不会被直接采信。" };
  }
  if (report.metadataAiGenerated === true || report.aiMetadata?.isAiLikely === true) {
    const tools = (report.aiMetadata?.matchedTools || []).slice(0, 3).join("、");
    return {
      label: "AI 工具元数据",
      direction: "fake",
      importance: "critical",
      text: `文件元数据中命中${tools || "已知生成工具"}标记，支持其经过 AI 生成工具处理。元数据可以被修改，因此本项作为强来源线索，但不等同于可信签名凭证。`,
    };
  }
  if (validation === "valid") {
    return { label: "来源凭证待确认", direction: "neutral", importance: "context", text: "内容来源凭证的签名结构有效，但签发者信任关系尚未建立；其中的声明仅作为辅助信息。" };
  }
  if (report.error === "remote_manifest_blocked") {
    return { label: "远程来源凭证", direction: "neutral", importance: "context", text: "文件引用了外置内容凭证。出于安全考虑，系统没有自动访问该网络地址，本项保持中性。" };
  }
  return {
    label: "来源凭证",
    direction: "neutral",
    importance: "context",
    text: report.error === "no_manifest"
      ? "未发现内容来源凭证；凭证缺失本身不代表图片经过生成或篡改。"
      : "本次没有形成可验证的内容来源凭证，本项不参与提高风险。",
  };
}

function imageExplanation(outcome: Extract<AgentOutcome, { kind: "image" }>, risk: number, verdictLabel: string): ExplanationPoint[] {
  const result = outcome.result;
  const report = result.visibleWatermark;
  const reviewOnly = result.decisionStatus !== "verdict" || result.reviewRequired === true;
  const watermarkDecisive = hasDecisiveAiWatermark(report);
  const points: ExplanationPoint[] = [
    watermarkPoint(report),
    {
      label: "真实性分析",
      direction: verdictDirection(verdictLabel, reviewOnly),
      importance: "critical",
      text: watermarkDecisive && result.modelDecisionReady !== true
        ? "真实性分析的原始分数尚未经过独立数据集校准；本次结论由已经确认的强 AI 水印直接支持。"
        : reviewOnly
        ? `模型分析给出“${verdictLabel}”，页面同时展示未校准的模型输出分；当前证据强度有限，因此按低置信结果解释。`
        : `真实性模型已完成分析，本次 AI 生成风险为 ${percent(risk)}。`,
    },
  ];

  const ignored = ["无明显", "暂未提取", "未提取到明确", "未发现明确"];
  const visualIssues = (result.visual_issues || []).filter((item) => item && !ignored.some((marker) => item.includes(marker)));
  if (visualIssues.length > 0) {
    points.push({
      label: "视觉复核",
      direction: "neutral",
      importance: "supporting",
      text: `发现 ${visualIssues.length} 项可核对的视觉线索，其中一项为“${visualIssues[0]}”。这些线索用于解释结果，不单独决定真假。`,
    });
  } else if (result.llm_used === false) {
    points.push({ label: "视觉复核", direction: "warning", importance: "context", text: "本次视觉复核未完成，系统没有生成替代性视觉结论。" });
  } else {
    points.push({ label: "视觉复核", direction: "neutral", importance: "context", text: "没有发现明确的局部异常线索，本项没有提高 AI 风险。" });
  }

  const provenance = provenancePoint(outcome.provenance);
  points.push(provenance);
  points.push(captureEvidencePoint(result.capture_evidence));
  points.push({
    label: "综合结论",
    decisive: true,
    direction: verdictDirection(verdictLabel, reviewOnly),
    importance: "critical",
    text: reviewOnly
      ? `本次最终结论为“${verdictLabel}”，但置信度较低；建议结合原始文件、来源记录和标记位置继续核对。`
      : watermarkDecisive
        ? `已经确认的强 AI 水印直接支持“${verdictLabel}”，综合 AI 风险为 ${percent(risk)}。`
        : `综合以上证据，本次结论为“${verdictLabel}”，AI 风险为 ${percent(risk)}；建议保留原始文件和来源记录。`,
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
      direction: verdictDirection(verdictLabel, reviewOnly),
      importance: "critical",
      text: reviewOnly
        ? "自动分析已经完成，页面展示模型原始输出分；该数值尚未经过独立数据集校准，因此按低置信结果解释。"
        : result.source === "provenance"
          ? "内容来源凭证已经通过验证，本次结论由可核对的来源记录直接支持。"
          : `模型和证据均已完成校验，本次 AI 风险为 ${percent(risk)}。`,
    },
  ];

  const dimensions = result.dimensions || [];
  const positive = dimensions.filter((item) => item.key !== "visible_watermark" && clamp01(item.score) >= 0.5);
  points.push(positive.length > 0
    ? { label: "辅助分析", direction: "neutral", importance: "supporting", text: `已完成 ${dimensions.length} 项辅助检查，其中 ${positive.slice(0, 2).map((item) => item.label).join("、")}提示风险；这些结果用于解释，不单独定案。` }
    : { label: "辅助分析", direction: "neutral", importance: "context", text: `已完成 ${dimensions.length} 项辅助检查，没有出现能够单独决定真假的强证据。` });

  const provenance = outcome.provenance || result.provenance || undefined;
  const sourcePoint = provenancePoint(provenance);
  points.push(sourcePoint);
  points.push(captureEvidencePoint(result.captureEvidence || provenance?.captureEvidence));
  points.push({
    label: "综合结论",
    decisive: true,
    direction: verdictDirection(verdictLabel, reviewOnly),
    importance: "critical",
    text: reviewOnly
      ? `本次最终结论为“${verdictLabel}”，但置信度较低。缺少元数据或水印都不能单独证明文件经过生成或篡改。`
      : `综合以上证据，本次结论为“${verdictLabel}”，AI 风险为 ${percent(risk)}；建议结合原始来源复核。`,
  });
  return points;
}

function videoExplanation(outcome: Extract<AgentOutcome, { kind: "video" }>, verdictLabel: string): ExplanationPoint[] {
  const result = outcome.result;
  const evidence = result.evidence;
  const frames = evidence?.sampledFrames || [];
  const timestamps = frames.map((frame) => `${Number(frame.timestamp).toFixed(1)} 秒`).join("、");
  const technical = evidence?.technical;
  const profile = [
    result.meta?.resolution,
    result.meta?.codec || result.meta?.video_format,
    result.meta?.fps ? `${result.meta.fps} FPS` : "",
  ].filter(Boolean).join(" / ");
  const reviewOnly = result.decisionStatus !== "verdict" || result.reviewRequired === true;
  const points: ExplanationPoint[] = [
    {
      label: "时序模型判断",
      direction: verdictDirection(verdictLabel, reviewOnly),
      importance: "critical",
      text: `模型对采样画面进行联合分析，输出方向为“${verdictLabel}”；置信等级为${result.confidence || "未标注"}。这是一条视频级结论，不表示每一帧都被单独判为真假。`,
    },
    {
      label: "实际采样画面",
      direction: "neutral",
      importance: frames.length > 0 ? "supporting" : "context",
      text: frames.length > 0
        ? `本次实际读取 ${frames.length} 个模型输入帧，时间点为 ${timestamps}。可点击下方时间轴回到原视频逐一核对。`
        : `服务返回分析帧数 ${result.frame_count || 0}，但未返回可核对的采样时间点。`,
    },
    {
      label: "文件读取状态",
      direction: "neutral",
      importance: "context",
      text: profile
        ? `视频已成功解码（${profile}${technical?.totalFrames ? `，共 ${technical.totalFrames} 帧` : ""}），说明本次任务读取到了可分析的视频流。`
        : "视频已完成模型分析，但服务未返回完整的编码、帧率或分辨率信息。",
    },
  ];
  if (evidence?.limitations?.length) {
    points.push({
      label: "检测边界",
      direction: "warning",
      importance: "supporting",
      text: evidence.limitations.join(" "),
    });
  }
  points.push({
    label: "综合结论",
    decisive: !reviewOnly,
    direction: verdictDirection(verdictLabel, reviewOnly),
    importance: "critical",
    text: reviewOnly
      ? `最终仍给出二元结果“${verdictLabel}”，但当前证据只支持低置信等级；应结合采样时间点、未采样片段和原始来源复核。`
      : `综合已授权的视频证据，本次结论为“${verdictLabel}”。`,
  });
  return points;
}

export function buildEvidenceExplanation(outcome: AgentOutcome, risk: number, verdictLabel: string): ExplanationPoint[] {
  if (outcome.kind === "image") return imageExplanation(outcome, risk, verdictLabel);
  if (outcome.kind === "evidence") return evidenceExplanation(outcome, risk, verdictLabel);
  return videoExplanation(outcome, verdictLabel);
}
