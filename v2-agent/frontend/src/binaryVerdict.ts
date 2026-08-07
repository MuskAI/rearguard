export type BinaryVerdictLabel = "真实图像" | "AI生成图像";

const AI_LABELS = new Set([
  "ai", "ai生成", "ai生成图像", "ai生成视频", "fake", "highly_suspected_fake",
  "suspected_fake", "高风险", "生成图像", "疑似ai生成", "疑似伪造", "疑似篡改图像", "疑似深伪图像",
]);
const REAL_LABELS = new Set(["real", "低风险", "原生拍摄", "实拍", "真实", "真实图像", "真实视频"]);

function normalizedScore(value: unknown): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  const score = parsed > 1 ? parsed / 100 : parsed;
  return Math.max(0, Math.min(score, 1));
}

export function binaryVerdictLabel(label: unknown, score?: unknown): BinaryVerdictLabel {
  const text = String(label || "").trim().toLowerCase();
  if (AI_LABELS.has(text) || text.includes("高风险")) return "AI生成图像";
  if (REAL_LABELS.has(text) || text.includes("低风险")) return "真实图像";
  if (
    text.includes("ai生成")
    || text.includes("疑似ai")
    || text.includes("伪造")
    || text.includes("篡改")
    || text.includes("深伪")
    || text.includes("翻拍")
    || text.includes("fake")
  ) {
    return "AI生成图像";
  }
  if (text.includes("真实") || text.includes("实拍") || text === "real") {
    return "真实图像";
  }
  const normalized = normalizedScore(score);
  return normalized !== null && normalized >= 0.5 ? "AI生成图像" : "真实图像";
}

export function isFakeVerdict(label: BinaryVerdictLabel): boolean {
  return label === "AI生成图像";
}
