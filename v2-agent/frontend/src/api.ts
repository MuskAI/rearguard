export type Verdict = "real" | "suspected_fake" | "highly_suspected_fake";
export type FileType = "image" | "video" | "audio" | "document";

export interface Dimension {
  key: string;
  label: string;
  score: number;
  result: string;
}

export interface Region {
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  score: number;
}

export interface DetectResult {
  taskId: string;
  reportId: string;
  createdAt: string;
  fileMeta: { name: string; type: FileType; size: string };
  verdict: Verdict;
  confidence: number;
  modelVersion: string;
  elapsedMs: number;
  dimensions: Dimension[];
  regions: Region[];
  explanation: string;
  disclaimer: string;
}

export interface HistoryItem {
  taskId: string;
  reportId: string;
  name: string;
  type: FileType;
  verdict: Verdict;
  confidence: number;
  createdAt: string;
}

export async function detect(file: File, fileType?: FileType): Promise<DetectResult> {
  const fd = new FormData();
  fd.append("file", file);
  if (fileType) fd.append("fileType", fileType);
  const res = await fetch("/v2-api/detect", { method: "POST", body: fd });
  if (!res.ok) throw new Error(`检测失败 (${res.status})`);
  return res.json();
}

export type ForensicStatus = "ok" | "warn" | "danger";

export interface ForensicItem {
  key: string;
  title: string;
  explanation: string;
  status: ForensicStatus;
  finding: string;
  image: string; // data URI
}

export interface ForensicReport {
  verdict: Verdict;
  confidence: number;
  summary: string;
  items: ForensicItem[];
  jpegPoints: { quality: number; error: number }[];
  modelVersion: string;
  source: string;
  elapsedMs: number;
  fileMeta: { name: string; type: FileType; size: string };
}

export async function runForensics(file: File): Promise<ForensicReport> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/v2-api/forensics", { method: "POST", body: fd });
  if (!res.ok) throw new Error(`取证分析失败 (${res.status})`);
  return res.json();
}

export const STATUS_META: Record<ForensicStatus, { dot: string; color: string; label: string }> = {
  ok: { dot: "🟢", color: "#3fb6a8", label: "正常" },
  warn: { dot: "🟠", color: "#d99a2b", label: "可疑" },
  danger: { dot: "🔴", color: "#d8412f", label: "高危" },
};

export interface ProvenanceReport {
  hasCredentials: boolean;
  validationState: string | null;
  generator: string | null;
  issuer: string | null;
  signatureAlg: string | null;
  signedTime: string | null;
  isAiGenerated: boolean | null;
  actions: { action: string; softwareAgent?: string; digitalSourceType?: string | null }[];
  ingredients: { title?: string; relationship?: string }[];
  synthid: { supported: boolean; detected: boolean | null; note: string };
  error: string | null;
  elapsedMs: number;
  fileMeta: { name: string; size: string };
}

export async function runProvenance(file: File): Promise<ProvenanceReport> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/v2-api/provenance", { method: "POST", body: fd });
  if (!res.ok) throw new Error(`内容凭证验证失败 (${res.status})`);
  return res.json();
}

export async function fetchHistory(): Promise<HistoryItem[]> {
  const res = await fetch("/v2-api/history");
  if (!res.ok) throw new Error("加载历史失败");
  const data = await res.json();
  return data.items;
}

export async function deleteHistory(taskId: string): Promise<void> {
  await fetch(`/v2-api/history/${taskId}`, { method: "DELETE" });
}

export const VERDICT_META: Record<Verdict, { label: string; color: string; ring: string }> = {
  real: { label: "真实", color: "#3fb6a8", ring: "verdict-real" },
  suspected_fake: { label: "疑似伪造", color: "#d99a2b", ring: "verdict-warn" },
  highly_suspected_fake: { label: "高度疑似伪造", color: "#d8412f", ring: "verdict-fake" },
};

export const TYPE_LABEL: Record<FileType, string> = {
  image: "图像",
  video: "视频",
  audio: "音频",
  document: "文档",
};
