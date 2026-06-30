import { DetectionJob, PublicExpertReviewExpert } from "./api";

export type PublicExpertReviewStatus = "queued" | "running" | "success" | "failed" | "skipped";
export type PublicLang = "zh" | "en";

function tr(lang: PublicLang, zh: string, en: string) {
  return lang === "zh" ? zh : en;
}

export function swarmStatusIcon(status?: string) {
  if (status === "running") return "fa-circle-o-notch detect-spin";
  if (status === "success") return "fa-check";
  if (status === "failed") return "fa-exclamation-triangle";
  if (status === "skipped") return "fa-minus";
  return "fa-circle-o";
}

export function normalizeExpertReviewStatus(status?: string): PublicExpertReviewStatus {
  if (status === "running" || status === "success" || status === "failed" || status === "skipped") return status;
  return "queued";
}

export function publicExpertReviewExpertName(expert: PublicExpertReviewExpert, index: number, lang: PublicLang) {
  return expert.publicName || tr(lang, `复核专家 ${index + 1}`, `Review expert ${index + 1}`);
}

export function publicExpertReviewExpertStatusLabel(status: string | undefined, lang: PublicLang) {
  const normalized = normalizeExpertReviewStatus(status);
  if (normalized === "running") return tr(lang, "复核中", "Reviewing");
  if (normalized === "success") return tr(lang, "已完成", "Completed");
  if (normalized === "failed") return tr(lang, "暂不可用", "Unavailable");
  if (normalized === "skipped") return tr(lang, "已跳过", "Skipped");
  return tr(lang, "等待中", "Queued");
}

export function publicExpertReviewExpertMessage(expert: PublicExpertReviewExpert, lang: PublicLang, includeVerdict = false) {
  if (includeVerdict && expert.publicVerdict) return expert.publicVerdict;
  if (expert.publicMessage) return expert.publicMessage;
  const status = normalizeExpertReviewStatus(expert.status);
  if (status === "running") return tr(lang, "正在复核", "Reviewing");
  if (status === "failed") return tr(lang, "该专家暂不可用", "Temporarily unavailable");
  if (status === "skipped") return tr(lang, "已跳过", "Skipped");
  if (status === "success") return tr(lang, "复核完成", "Review complete");
  return tr(lang, "等待调度", "Queued");
}

export function publicExpertReviewJobSummary(job: DetectionJob | null, lang: PublicLang) {
  if (!job) return tr(lang, "等待专家队列启动", "Waiting for expert queue");
  if (job.status === "success") return tr(lang, "专家会诊复核完成", "Expert review complete");
  if (job.status === "failed") return tr(lang, "专家会诊复核失败", "Expert review failed");
  if (job.status === "running") return tr(lang, "多名鉴伪专家正在复核", "Forensic experts are reviewing");
  return tr(lang, "等待专家队列启动", "Waiting for expert queue");
}

export function publicExpertReviewText(value: string, lang: PublicLang) {
  const fallback = tr(lang, "证据项已脱敏", "Evidence redacted");
  const cleaned = String(value || "")
    .replace(/^[^:：]{0,32}[:：]\s*/, "")
    .replace(/阿里云|Aliyun|V2|Qwen|ONNX|RealGuard\s*V?\d*|AIGC\s*专业版|隐式标识专家|局部编辑专家|主路由鉴伪专家|视觉语言复核专家|专家复核完成|专家/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
  return cleaned || fallback;
}

export function publicExpertReviewEvidence(item: string | PublicExpertReviewExpert, lang: PublicLang) {
  if (typeof item === "string") return publicExpertReviewText(item, lang);
  return item.publicVerdict || item.publicMessage || tr(lang, "证据项已脱敏", "Evidence redacted");
}
