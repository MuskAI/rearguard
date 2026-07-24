export type BinaryVerdictLabel = "真实图像" | "AI生成图像";

function normalizedScore(value: unknown): number | null {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  const score = parsed > 1 ? parsed / 100 : parsed;
  return Math.max(0, Math.min(score, 1));
}

export function binaryVerdictLabel(label: unknown, score?: unknown): BinaryVerdictLabel {
  const text = String(label || "").trim().toLowerCase();
  if (
    text.includes("ai")
    || text.includes("生成")
    || text.includes("伪造")
    || text.includes("篡改")
    || text.includes("深伪")
    || text.includes("翻拍")
    || text.includes("风险")
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
