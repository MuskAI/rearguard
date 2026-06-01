export type Verdict = "real" | "suspected_fake" | "highly_suspected_fake";
export type FileType = "image" | "video" | "audio" | "document";
const ACCESS_TOKEN_KEY = "jianzhen_access_token";

function getStoredToken(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(ACCESS_TOKEN_KEY)?.trim() || "";
}

function withAuthHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  const token = getStoredToken();
  if (token) headers.set("X-Jianzhen-Token", token);
  return headers;
}

async function parseJson<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    try {
      const data = await res.json();
      throw new Error(data.detail || data.message || fallback);
    } catch (error) {
      if (error instanceof Error) throw error;
      throw new Error(fallback);
    }
  }
  return res.json();
}

function filenameFromDisposition(disposition: string | null, fallback: string): string {
  const value = disposition || "";
  const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1]);
  const plainMatch = value.match(/filename="?([^";]+)"?/i);
  if (plainMatch?.[1]) return plainMatch[1];
  return fallback;
}

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
  fileMeta: {
    name: string;
    type: FileType;
    size: string;
    resolution?: string | null;
    sha256?: string;
    thumbnail?: string | null;
    preview?: string | null;
  };
  verdict: Verdict;
  confidence: number;
  modelVersion: string;
  source: string;
  cacheHit?: boolean;
  elapsedMs: number;
  dimensions: Dimension[];
  regions: Region[];
  explanation: string;
  synthid?: SynthIDResult;
  visibleWatermark?: VisibleWatermarkResult;
  forensics?: ForensicReport | null;
  provenance?: ProvenanceReport | null;
  disclaimer: string;
}

export interface SynthIDResult {
  enabled: boolean;
  supported: boolean;
  detected: boolean | null;
  confidence: number;
  phaseMatch: number;
  profile: string | null;
  modelProfile: string;
  exactProfileMatch?: boolean;
  evidenceLevel: "strong" | "medium" | "weak" | "none" | "unavailable";
  note: string;
  error: string | null;
  elapsedMs?: number;
}

export interface VisibleWatermarkHit {
  provider: string;
  confidence: number;
  bbox: { x: number; y: number; w: number; h: number };
  method: string;
  frame: number | null;
  scores: Record<string, number>;
  crop?: string | null;
}

export interface VisibleWatermarkResult {
  enabled: boolean;
  supported: boolean;
  detected: boolean;
  provider: string | null;
  confidence: number;
  evidenceLevel: "strong" | "medium" | "weak" | "none" | "unavailable";
  hits: VisibleWatermarkHit[];
  temporal: { sampledFrames: number; positiveFrames: number; moving: boolean };
  note: string;
  elapsedMs?: number;
}

export interface HistoryItem {
  taskId: string;
  reportId: string;
  name: string;
  type: FileType;
  verdict: Verdict;
  confidence: number;
  createdAt: string;
  thumbnail?: string | null;
  source?: string;
  cacheHit?: boolean;
  hasForensics?: boolean;
  hasProvenance?: boolean;
  hasVisibleWatermark?: boolean;
  visibleWatermarkProvider?: string | null;
  hasSynthid?: boolean;
}

export type HistorySidebarFilter = "all" | "vlm" | "mock" | "maps-only" | "unknown" | "real" | "suspected" | "highly" | "forensics" | "provenance" | "synthid" | "watermark";

export interface HistoryFilterCounts {
  all: number;
  vlm: number;
  mock: number;
  "maps-only": number;
  unknown: number;
  real: number;
  suspected: number;
  highly: number;
  forensics: number;
  provenance: number;
  synthid: number;
  watermark: number;
}

export interface HealthStatus {
  status: string;
  model: string;
  vlmEnabled: boolean;
  accessProtectionEnabled: boolean;
  protectedEndpoints: string[];
}

export async function detect(file: File, fileType?: FileType): Promise<DetectResult> {
  const fd = new FormData();
  fd.append("file", file);
  if (fileType) fd.append("fileType", fileType);
  const res = await fetch("/v2-api/detect", { method: "POST", body: fd, headers: withAuthHeaders() });
  return parseJson(res, `检测失败 (${res.status})`);
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
  const res = await fetch("/v2-api/forensics", { method: "POST", body: fd, headers: withAuthHeaders() });
  return parseJson(res, `取证分析失败 (${res.status})`);
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
  const res = await fetch("/v2-api/provenance", { method: "POST", body: fd, headers: withAuthHeaders() });
  return parseJson(res, `内容凭证验证失败 (${res.status})`);
}

export async function fetchHistory(params?: {
  query?: string;
  filter?: HistorySidebarFilter;
  limit?: number;
  offset?: number;
}): Promise<{ items: HistoryItem[]; total: number; filterCounts: HistoryFilterCounts }> {
  const search = new URLSearchParams();
  if (params?.limit) search.set("limit", String(params.limit));
  if (params?.offset) search.set("offset", String(params.offset));
  if (params?.query?.trim()) search.set("query", params.query.trim());
  if (params?.filter === "vlm" || params?.filter === "mock" || params?.filter === "maps-only" || params?.filter === "unknown") {
    search.set("source", params.filter);
  } else if (params?.filter === "real") {
    search.set("verdict", "real");
  } else if (params?.filter === "suspected") {
    search.set("verdict", "suspected_fake");
  } else if (params?.filter === "highly") {
    search.set("verdict", "highly_suspected_fake");
  } else if (params?.filter === "forensics") {
    search.set("hasForensics", "true");
  } else if (params?.filter === "provenance") {
    search.set("hasProvenance", "true");
  } else if (params?.filter === "synthid") {
    search.set("hasSynthid", "true");
  } else if (params?.filter === "watermark") {
    search.set("hasWatermark", "true");
  }
  const qs = search.toString();
  const res = await fetch(`/v2-api/history${qs ? `?${qs}` : ""}`, { headers: withAuthHeaders() });
  return parseJson(res, "加载历史失败");
}

export async function fetchHistoryItem(taskId: string): Promise<DetectResult> {
  const res = await fetch(`/v2-api/history/${taskId}`, { headers: withAuthHeaders() });
  return parseJson(res, "加载历史详情失败");
}

export async function deleteHistory(taskId: string): Promise<void> {
  const res = await fetch(`/v2-api/history/${taskId}`, { method: "DELETE", headers: withAuthHeaders() });
  await parseJson<Record<string, string>>(res, "删除历史失败");
}

export async function persistArtifacts(
  taskId: string,
  extras: { forensics?: ForensicReport | null; provenance?: ProvenanceReport | null },
): Promise<void> {
  const res = await fetch(`/v2-api/history/${encodeURIComponent(taskId)}/artifacts`, {
    method: "POST",
    headers: withAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      forensics: extras.forensics ?? null,
      provenance: extras.provenance ?? null,
    }),
  });
  await parseJson<Record<string, boolean>>(res, "保存附加分析结果失败");
}

export interface Metrics {
  summary: {
    totalDetections: number;
    recentDetections: number;
    todayDetections: number;
    uniqueClientsToday: number;
    requestsToday: number;
    avgLatencyMs: number;
    cacheEntries: number;
    cacheHitRate: number;
  };
  byDay: {
    date: string;
    detections: number;
    sources: { vlm: number; mock: number; "maps-only": number; unknown: number };
    verdicts: { real: number; suspected_fake: number; highly_suspected_fake: number; unknown: number };
    evidence: {
      visibleWatermarkHits: number;
      synthidHits: number;
      forensicsCompleted: number;
      provenanceCompleted: number;
    };
  }[];
  byType: Partial<Record<FileType, number>>;
  byVerdict: Partial<Record<Verdict | "unknown", number>>;
  bySource: Partial<Record<"vlm" | "mock" | "maps-only" | "unknown", number>>;
  sourceVerdict: Partial<Record<"vlm" | "mock" | "maps-only" | "unknown", Partial<Record<Verdict | "unknown", number>>>>;
  sourceEvidence: Partial<Record<"vlm" | "mock" | "maps-only" | "unknown", Partial<Record<"visibleWatermarkHits" | "synthidHits" | "forensicsCompleted" | "provenanceCompleted", number>>>>;
  evidence: {
    visibleWatermarkHits: number;
    synthidHits: number;
    forensicsCompleted: number;
    provenanceCompleted: number;
  };
  recentErrors: { createdAt: string; status: number; path: string }[];
}

export async function fetchMetrics(days = 14): Promise<Metrics> {
  const res = await fetch(`/v2-api/metrics?days=${encodeURIComponent(String(days))}`, { headers: withAuthHeaders() });
  return parseJson(res, "加载监控指标失败");
}

export async function downloadReport(
  reportId: string,
  extras?: { forensics?: ForensicReport | null; provenance?: ProvenanceReport | null },
): Promise<string> {
  const fallbackName = `jianzhen-report-${reportId}.html`;
  const hasExtras = Boolean(extras?.forensics || extras?.provenance);
  const res = await fetch(
    `/v2-api/report/${encodeURIComponent(reportId)}${hasExtras ? "/export" : "/download"}`,
    hasExtras
      ? {
          method: "POST",
          headers: withAuthHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            forensics: extras?.forensics ?? null,
            provenance: extras?.provenance ?? null,
          }),
        }
      : {
          headers: withAuthHeaders(),
        },
  );
  if (!res.ok) {
    await parseJson<Record<string, never>>(res, "下载报告失败");
  }
  const blob = await res.blob();
  const filename = filenameFromDisposition(res.headers.get("content-disposition"), fallbackName);
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(url), 0);
  return filename;
}

export async function fetchHealth(): Promise<HealthStatus> {
  const res = await fetch("/v2-api/health", { headers: withAuthHeaders() });
  return parseJson(res, "加载系统状态失败");
}

export function getAccessToken(): string {
  return getStoredToken();
}

export function setAccessToken(token: string): void {
  if (typeof window === "undefined") return;
  const normalized = token.trim();
  if (normalized) window.localStorage.setItem(ACCESS_TOKEN_KEY, normalized);
  else window.localStorage.removeItem(ACCESS_TOKEN_KEY);
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
