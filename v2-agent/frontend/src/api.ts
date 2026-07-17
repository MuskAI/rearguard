export type Verdict = "real" | "suspected_fake" | "highly_suspected_fake" | "unknown";
export type FileType = "image" | "video" | "audio" | "document";

export class ApiRequestError extends Error {
  readonly status: number;
  readonly retryAfterMs: number;

  constructor(message: string, status: number, retryAfterMs = 0) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.retryAfterMs = retryAfterMs;
  }
}

export function isRateLimitedError(error: unknown): error is ApiRequestError {
  return error instanceof ApiRequestError && error.status === 429;
}

function retryAfterMs(response: Response): number {
  const value = response.headers.get("Retry-After")?.trim();
  if (!value) return 0;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : 0;
}

function withAuthHeaders(init?: HeadersInit): Headers {
  return new Headers(init);
}

function withSession(init: RequestInit = {}): RequestInit {
  return {
    ...init,
    credentials: "include",
    headers: withAuthHeaders(init.headers),
  };
}

async function parseJson<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    let message = res.status === 429 ? "当前请求较多，系统已启动短时保护，请稍候重试" : fallback;
    try {
      const data = await res.json();
      if (res.status !== 429) message = data.detail || data.message || message;
    } catch {
      // Keep the user-facing fallback when the server returns HTML, empty text, or a proxy error.
    }
    throw new ApiRequestError(message, res.status, retryAfterMs(res));
  }
  try {
    return await res.json();
  } catch {
    throw new Error(fallback);
  }
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

export interface UnifiedForensicsRegion {
  modality: "image" | "video" | string;
  source: string;
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  confidence: number;
  frame?: number;
}

export interface UnifiedForensicsTemporalSegment {
  source: string;
  start_frame: number;
  end_frame: number;
  label: string;
  confidence: number;
}

export interface UnifiedForensicsOutput {
  interface_version: string;
  verdict: Verdict;
  confidence: number;
  generator_attribution: {
    status: "known_signal" | "unknown" | string;
    family: string | null;
    model: string | null;
    confidence: number;
    evidence: string[];
  };
  open_set_score: number;
  evidence_regions: UnifiedForensicsRegion[];
  temporal_segments: UnifiedForensicsTemporalSegment[];
  provenance_signals: Record<string, unknown>;
  explanation: string;
  uncertainty: {
    score: number;
    factors: string[];
  };
  compute_cost: {
    elapsed_ms: number;
    cache_hit: boolean;
    source: string;
    model_version: string;
    cache_version: string;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
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
  cacheVersion: string;
  cacheHit?: boolean;
  elapsedMs: number;
  dimensions: Dimension[];
  regions: Region[];
  explanation: string;
  synthid?: SynthIDResult;
  visibleWatermark?: VisibleWatermarkResult;
  unifiedForensics?: UnifiedForensicsOutput;
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
  label?: string;
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
  modelVersion?: string;
  cacheVersion?: string;
  cacheHit?: boolean;
  hasForensics?: boolean;
  hasProvenance?: boolean;
  hasVisibleWatermark?: boolean;
  visibleWatermarkProvider?: string | null;
  hasSynthid?: boolean;
}

export type HistorySidebarFilter = "all" | "vlm" | "mock" | "maps-only" | "unknown" | "real" | "suspected" | "highly" | "unknownVerdict" | "cache" | "forensics" | "provenance" | "synthid" | "watermark";

export interface HistoryFilterCounts {
  all: number;
  vlm: number;
  mock: number;
  "maps-only": number;
  unknown: number;
  real: number;
  suspected: number;
  highly: number;
  unknownVerdict: number;
  cache: number;
  forensics: number;
  provenance: number;
  synthid: number;
  watermark: number;
}

export interface HealthStatus {
  status: string;
  model?: string;
  vlmEnabled: boolean;
  accessProtectionEnabled: boolean;
  unifiedLoginEnabled?: boolean;
  sessionAuthEnabled?: boolean;
  capabilities?: Partial<Record<FileType, string>>;
  limits?: {
    maxUploadBytes?: number;
  };
}

type ApiRecord = Record<string, unknown>;

const FILE_TYPES: FileType[] = ["image", "video", "audio", "document"];
const VERDICTS: Verdict[] = ["real", "suspected_fake", "highly_suspected_fake", "unknown"];

function asRecord(value: unknown): ApiRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as ApiRecord) : {};
}

function textValue(value: unknown, fallback = ""): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function numberValue(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function nullableText(value: unknown): string | null {
  const text = textValue(value);
  return text || null;
}

function inferFileType(name: string): FileType {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "heic", "heif"].includes(ext)) return "image";
  if (["mp4", "mov", "m4v", "webm", "avi", "mkv"].includes(ext)) return "video";
  if (["mp3", "wav", "m4a", "flac", "aac", "ogg"].includes(ext)) return "audio";
  return "document";
}

function fileTypeValue(value: unknown, name: string): FileType {
  const normalized = typeof value === "string" ? value.toLowerCase() : "";
  return FILE_TYPES.includes(normalized as FileType) ? (normalized as FileType) : inferFileType(name);
}

function verdictValue(value: unknown): Verdict {
  return VERDICTS.includes(value as Verdict) ? (value as Verdict) : "unknown";
}

function normalizeDetectResult(raw: unknown): DetectResult {
  const source = asRecord(raw);
  const originalFileMeta = asRecord(source.fileMeta);
  const fallbackName = textValue(source.name, textValue(source.fileName, textValue(source.filename, "未知文件")));
  const fileName = textValue(originalFileMeta.name, fallbackName);
  const fileType = fileTypeValue(originalFileMeta.type ?? source.type ?? source.fileType, fileName);
  const thumbnail = nullableText(originalFileMeta.thumbnail ?? source.thumbnail);
  const preview = nullableText(originalFileMeta.preview ?? source.preview) || thumbnail;

  return {
    ...(source as Partial<DetectResult>),
    taskId: textValue(source.taskId, textValue(source.id)),
    reportId: textValue(source.reportId, textValue(source.id, "未返回")),
    createdAt: textValue(source.createdAt),
    fileMeta: {
      name: fileName,
      type: fileType,
      size: textValue(originalFileMeta.size, textValue(source.size, textValue(source.fileSize, "未知"))),
      resolution: nullableText(originalFileMeta.resolution ?? source.resolution),
      sha256: textValue(originalFileMeta.sha256, textValue(source.sha256)),
      thumbnail,
      preview,
    },
    verdict: verdictValue(source.verdict),
    confidence: numberValue(source.confidence),
    modelVersion: textValue(source.modelVersion),
    source: textValue(source.source, "unknown"),
    cacheVersion: textValue(source.cacheVersion),
    elapsedMs: numberValue(source.elapsedMs),
    dimensions: Array.isArray(source.dimensions) ? (source.dimensions as Dimension[]) : [],
    regions: Array.isArray(source.regions) ? (source.regions as Region[]) : [],
    explanation: textValue(source.explanation, "历史记录缺少模型说明。"),
    disclaimer: textValue(source.disclaimer, "本结果仅供参考，不构成权威鉴定结论。"),
  } as DetectResult;
}

function normalizeHistoryItem(raw: unknown): HistoryItem {
  const source = asRecord(raw);
  const name = textValue(source.name, textValue(source.fileName, textValue(source.filename, "未知文件")));
  return {
    ...(source as Partial<HistoryItem>),
    taskId: textValue(source.taskId, textValue(source.id)),
    reportId: textValue(source.reportId, textValue(source.id, "未返回")),
    name,
    type: fileTypeValue(source.type ?? source.fileType, name),
    verdict: verdictValue(source.verdict),
    confidence: numberValue(source.confidence),
    createdAt: textValue(source.createdAt),
    thumbnail: nullableText(source.thumbnail),
  } as HistoryItem;
}

function normalizeHistoryPayload(raw: unknown): { items: HistoryItem[]; total: number; filterCounts: HistoryFilterCounts } {
  const source = asRecord(raw);
  const items = Array.isArray(source.items) ? source.items.map(normalizeHistoryItem) : [];
  return {
    items,
    total: numberValue(source.total, items.length),
    filterCounts: asRecord(source.filterCounts) as unknown as HistoryFilterCounts,
  };
}

export async function detect(file: File, fileType?: FileType): Promise<DetectResult> {
  const fd = new FormData();
  fd.append("file", file);
  if (fileType) fd.append("fileType", fileType);
  const res = await fetch("/v2-api/detect", withSession({ method: "POST", body: fd }));
  return normalizeDetectResult(await parseJson(res, `检测失败 (${res.status})`));
}

export interface AccountUser {
  Userid: number;
  username: string;
  phone: string;
  openid?: string;
}

export interface AccountCounters {
  image_detect: number;
  video_detect: number;
}

export interface ImageAgentExpert {
  id: string;
  publicId?: string;
  status?: "queued" | "running" | "success" | "failed" | "skipped" | string;
  publicName?: string;
  publicMessage?: string;
  publicVerdict?: string;
}

export interface ImageAgentReview {
  enabled?: boolean;
  score?: number;
  finalLabel?: string;
  confidence?: string;
  consensusLevel?: string;
  consensusScore?: number;
  disagreement?: boolean;
  effectiveExperts?: number;
  totalExperts?: number;
  experts?: ImageAgentExpert[];
  evidence?: string[];
}

export interface ImageAgentResult {
  itemid: number;
  final_label: string;
  probability: number;
  detector_probability?: number;
  p_visual?: number | null;
  p_metadata?: number | null;
  confidence: string;
  explanation: string;
  image_url: string;
  filename: string;
  file_size?: string;
  resolution?: string;
  img_format?: string;
  visual_issues?: string[];
  all_metadata?: Record<string, unknown>;
  llm_used?: boolean;
  feedback?: 1 | -1 | null;
  swarm?: ImageAgentReview;
  visibleWatermark?: VisibleWatermarkResult;
}

export interface ImageAgentJob {
  id: string;
  filename?: string;
  status: "queued" | "running" | "success" | "failed" | string;
  createdAt?: string;
  updatedAt?: string;
  progress?: number;
  experts?: ImageAgentExpert[];
  summary?: string;
  error?: string;
  result?: {
    status?: string;
    result?: ImageAgentResult;
    message?: string;
  } | null;
}

export interface VideoAgentResult {
  itemid: number;
  filename: string;
  video_url: string;
  fake_percentage: number;
  real_percentage: number;
  final_label: string;
  confidence: string;
  confidence_score?: number;
  explanation: string;
  frame_count?: number;
  d3_std?: number;
  encoder?: string;
  meta?: Record<string, string>;
}

export interface ImageHistoryRecord {
  itemid: number;
  filename: string;
  image_url: string;
  thumbnail_url?: string;
  real_prob: number;
  fake_prob: number;
  final_label: string;
  confidence: string;
  createtime: string;
  report_url?: string;
}

export interface VideoHistoryRecord {
  itemid: number;
  filename: string;
  video_url: string;
  real_percentage: number;
  fake_percentage: number;
  final_label: string;
  confidence: string;
  createtime: string;
  report_url?: string;
}

async function accountJson<T>(path: string, init: RequestInit = {}, fallback = "请求失败"): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, credentials: "include", cache: "no-store", headers });
  return parseJson<T>(res, fallback);
}

export function fetchCurrentUser(): Promise<{ status: string; user: AccountUser; counters: AccountCounters }> {
  return accountJson("/api/me", {}, "用户状态暂不可用");
}

export function loginByPassword(phone: string, secret: string, acceptedTerms: boolean) {
  return accountJson<{ status: string; user: AccountUser }>(
    "/api/login/password",
    { method: "POST", body: JSON.stringify({ phone, secret, accepted_terms: acceptedTerms }) },
    "登录失败",
  );
}

export function loginBySms(phone: string, smsCode: string, acceptedTerms: boolean) {
  return accountJson<{ status: string; user: AccountUser }>(
    "/api/login/sms",
    { method: "POST", body: JSON.stringify({ phone, sms_code: smsCode, accepted_terms: acceptedTerms }) },
    "登录失败",
  );
}

export function sendSmsCode(phone: string, scene: "login" | "register" | "reset") {
  return accountJson<{ success: boolean; message?: string; debug_code?: string; expires_in?: number }>(
    "/sms/send_code",
    { method: "POST", body: JSON.stringify({ phone, scene }) },
    "验证码发送失败",
  );
}

export function registerAccount(payload: {
  phone: string;
  secret: string;
  username: string;
  smsCode: string;
  acceptedTerms: boolean;
}) {
  return accountJson<{ status: string; message: string }>(
    "/api/register",
    {
      method: "POST",
      body: JSON.stringify({
        phone: payload.phone,
        secret: payload.secret,
        username: payload.username,
        sms_code: payload.smsCode,
        accepted_terms: payload.acceptedTerms,
        terms_version: "2026-06-03",
      }),
    },
    "注册失败",
  );
}

export function logoutAccount() {
  return accountJson<{ status: string }>("/api/logout", { method: "POST" }, "退出失败");
}

export function startImageAgent(file: File, signal?: AbortSignal) {
  const body = new FormData();
  body.append("image", file);
  return accountJson<{ status: string; job: ImageAgentJob }>(
    "/image_upload/detect_swarm",
    { method: "POST", body, signal },
    "多源鉴伪任务启动失败",
  );
}

export function fetchImageAgentJob(jobId: string, signal?: AbortSignal) {
  return accountJson<{ status: string; job: ImageAgentJob }>(
    `/image_upload/jobs/${encodeURIComponent(jobId)}`,
    { signal },
    "鉴伪任务状态暂不可用",
  );
}

export function detectVideoWithAgent(file: File) {
  const body = new FormData();
  body.append("video_file", file);
  body.append("fast_mode", "1");
  return accountJson<{ status: string; result: VideoAgentResult }>(
    "/video_upload/detect",
    { method: "POST", body },
    "视频鉴伪失败",
  );
}

export function fetchImageHistory(limit = 100) {
  return accountJson<{ status: string; records: ImageHistoryRecord[]; total: number }>(
    `/api/history/image-detections?limit=${encodeURIComponent(String(limit))}`,
    {},
    "图像历史暂不可用",
  );
}

export function fetchVideoHistory(limit = 100) {
  return accountJson<{ status: string; records: VideoHistoryRecord[]; total: number }>(
    `/api/history/video-detections?limit=${encodeURIComponent(String(limit))}`,
    {},
    "视频历史暂不可用",
  );
}

export function fetchImageAgentResult(itemId: number) {
  return accountJson<{ status: string; result: ImageAgentResult }>(
    `/image_upload/result?itemid=${encodeURIComponent(String(itemId))}`,
    {},
    "图像记录暂不可用",
  );
}

export function fetchVideoAgentResult(itemId: number) {
  return accountJson<{ status: string; result: VideoAgentResult }>(
    `/video_upload/result?itemid=${encodeURIComponent(String(itemId))}`,
    {},
    "视频记录暂不可用",
  );
}

export function imageReportUrl(itemId: number): string {
  return `/image_upload/report?itemid=${encodeURIComponent(String(itemId))}`;
}

export function videoReportUrl(itemId: number): string {
  return `/video_upload/report?itemid=${encodeURIComponent(String(itemId))}`;
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

export async function runForensics(file: File, signal?: AbortSignal): Promise<ForensicReport> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/v2-api/forensics", withSession({ method: "POST", body: fd, signal }));
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
  metadataAiGenerated?: boolean;
  aiMetadata?: {
    score: number;
    confidence: "high" | "medium" | "low" | "none" | string;
    confidenceText: string;
    isAiLikely: boolean;
    signalCount: number;
    matchedTools: string[];
    signals: {
      id: string;
      label: string;
      weight: number;
      path: string;
      reason: string;
      value: string;
    }[];
  };
  metadataSummary?: {
    sectionCount: number;
    embeddedSectionCount: number;
    fieldCount: number;
    sections: { name: string; fieldCount: number }[];
    preview: { path: string; value: string }[];
    errors: { section: string; message: string }[];
  };
  metadata?: Record<string, unknown>;
  synthid: { supported: boolean; detected: boolean | null; note: string };
  error: string | null;
  elapsedMs: number;
  fileMeta: { name: string; size: string };
}

export async function runProvenance(file: File): Promise<ProvenanceReport> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/v2-api/provenance", withSession({ method: "POST", body: fd }));
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
  } else if (params?.filter === "unknownVerdict") {
    search.set("verdict", "unknown");
  } else if (params?.filter === "cache") {
    search.set("hasCache", "true");
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
  const res = await fetch(`/v2-api/history${qs ? `?${qs}` : ""}`, withSession({ cache: "no-store" }));
  return normalizeHistoryPayload(await parseJson(res, "历史记录暂不可用"));
}

export async function fetchHistoryItem(taskId: string): Promise<DetectResult> {
  const res = await fetch(`/v2-api/history/${taskId}`, withSession({ cache: "no-store" }));
  return normalizeDetectResult(await parseJson(res, "历史详情暂不可用"));
}

export async function deleteHistory(taskId: string): Promise<void> {
  const res = await fetch(`/v2-api/history/${taskId}`, withSession({ method: "DELETE" }));
  await parseJson<Record<string, string>>(res, "删除历史失败");
}

export async function persistArtifacts(
  taskId: string,
  extras: { forensics?: ForensicReport | null; provenance?: ProvenanceReport | null },
): Promise<void> {
  const res = await fetch(`/v2-api/history/${encodeURIComponent(taskId)}/artifacts`, {
    credentials: "include",
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
  const res = await fetch(`/v2-api/metrics?days=${encodeURIComponent(String(days))}`, withSession());
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
          credentials: "include",
          method: "POST",
          headers: withAuthHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            forensics: extras?.forensics ?? null,
            provenance: extras?.provenance ?? null,
          }),
        }
      : {
          credentials: "include",
          headers: withAuthHeaders(),
        },
  );
  if (!res.ok) {
    await parseJson<Record<string, never>>(res, "下载报告失败");
  }
  const html = await res.text();
  if (!html.trim()) {
    throw new Error("下载报告失败：服务端返回了空报告");
  }
  const filename = filenameFromDisposition(res.headers.get("content-disposition"), fallbackName);
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000);
  return filename;
}

export interface ReportShareLink {
  url: string;
  publicPath?: string;
  apiPath?: string;
  expiresAt: string;
  expiresInSeconds: number;
}

export async function createReportShareLink(reportId: string, expiresInSeconds = 7 * 24 * 60 * 60): Promise<ReportShareLink> {
  const res = await fetch(`/v2-api/report/${encodeURIComponent(reportId)}/share`, {
    credentials: "include",
    method: "POST",
    headers: withAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ expiresInSeconds }),
  });
  const link = await parseJson<ReportShareLink>(res, "生成分享链接失败");
  if (link.publicPath && typeof window !== "undefined") {
    return {
      ...link,
      url: new URL(link.publicPath, window.location.origin).toString(),
    };
  }
  return link;
}

export async function fetchHealth(): Promise<HealthStatus> {
  const res = await fetch("/v2-api/health", withSession());
  return parseJson(res, "加载系统状态失败");
}

export const VERDICT_META: Record<Verdict, { label: string; color: string; ring: string }> = {
  real: { label: "真实", color: "#3fb6a8", ring: "verdict-real" },
  suspected_fake: { label: "疑似伪造", color: "#d99a2b", ring: "verdict-warn" },
  highly_suspected_fake: { label: "高度疑似伪造", color: "#d8412f", ring: "verdict-fake" },
  unknown: { label: "未知判定", color: "#7c8aa5", ring: "verdict-unknown" },
};

export const TYPE_LABEL: Record<FileType, string> = {
  image: "图像",
  video: "视频",
  audio: "音频",
  document: "文档",
};
