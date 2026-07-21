import { appendUploadConsent, LEGAL_CONSENT } from "./legalConsent";

export type Verdict = "real" | "suspected_fake" | "highly_suspected_fake" | "unknown";
export type FileType = "image" | "video" | "audio" | "document";

interface ApiRequestErrorOptions {
  retryAfterMs?: number;
  code?: string;
  details?: unknown;
  requestId?: string;
}

export class ApiRequestError extends Error {
  readonly status: number;
  readonly retryAfterMs: number;
  readonly code: string;
  readonly details: unknown;
  readonly requestId: string;

  constructor(message: string, status: number, options: ApiRequestErrorOptions = {}) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.retryAfterMs = options.retryAfterMs || 0;
    this.code = options.code || "";
    this.details = options.details;
    this.requestId = options.requestId || "";
  }
}

export const SESSION_EXPIRED_EVENT = "huijian:session-expired";

export function notifySessionExpired(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
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

function errorRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function errorText(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

export async function apiRequestErrorFromResponse(response: Response, fallback: string): Promise<ApiRequestError> {
  const defaultMessage = response.status === 429
    ? "当前请求较多，系统已启动短时保护，请稍候重试"
    : fallback;
  let message = defaultMessage;
  let code = "";
  let details: unknown;
  let requestId = response.headers.get("X-Request-Id")?.trim() || "";

  try {
    const payload: unknown = await response.json();
    const root = errorRecord(payload);
    const envelope = errorRecord(root.error);
    message = errorText(envelope.message)
      || errorText(root.message)
      || errorText(root.detail)
      || defaultMessage;
    code = errorText(envelope.code) || errorText(root.code);
    details = envelope.details
      ?? root.details
      ?? (root.detail && typeof root.detail !== "string" ? root.detail : undefined);
    requestId = errorText(envelope.requestId)
      || errorText(envelope.request_id)
      || errorText(root.requestId)
      || errorText(root.request_id)
      || requestId;
  } catch {
    // Keep the operation-specific fallback when a proxy returns HTML or an empty body.
  }

  return new ApiRequestError(message, response.status, {
    code,
    details,
    requestId,
    retryAfterMs: retryAfterMs(response),
  });
}

function withAuthHeaders(init?: HeadersInit): Headers {
  return new Headers(init);
}

let sessionCsrfToken = "";
let sessionCsrfPromise: Promise<void> | null = null;

export async function ensureSessionCsrf(): Promise<void> {
  if (sessionCsrfToken || typeof window === "undefined") return;
  if (sessionCsrfPromise) return sessionCsrfPromise;
  sessionCsrfPromise = fetch("/v2-api/csrf", {
    credentials: "include",
    cache: "no-store",
    headers: { Accept: "application/json" },
  })
    .then(async (response) => {
      if (!response.ok) return;
      const payload = await response.json() as { csrfToken?: unknown };
      if (typeof payload.csrfToken === "string" && payload.csrfToken.length >= 32) {
        sessionCsrfToken = payload.csrfToken;
      }
    })
    .catch(() => undefined)
    .finally(() => {
      sessionCsrfPromise = null;
    });
  return sessionCsrfPromise;
}

export function sessionCsrfHeaders(init?: HeadersInit): Headers {
  const headers = withAuthHeaders(init);
  if (sessionCsrfToken) headers.set("X-Huijian-CSRF", sessionCsrfToken);
  return headers;
}

function withSession(init: RequestInit = {}): RequestInit {
  return {
    ...init,
    credentials: "include",
    headers: sessionCsrfHeaders(init.headers),
  };
}

async function parseJson<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    if (res.status === 401) notifySessionExpired();
    throw await apiRequestErrorFromResponse(res, fallback);
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

async function downloadResponse(res: Response, fallbackName: string, fallbackMessage: string): Promise<string> {
  if (!res.ok) await parseJson<Record<string, never>>(res, fallbackMessage);
  const contentType = (res.headers.get("content-type") || "").toLowerCase();
  const allowedType = contentType.includes("application/pdf")
    || contentType.includes("image/png")
    || contentType.includes("image/jpeg");
  if (!allowedType) {
    let message = fallbackMessage;
    try {
      const payload = await res.json();
      message = payload.detail || payload.message || message;
    } catch {
      // A successful report response must still be a downloadable image or PDF.
    }
    throw new Error(message);
  }
  const blob = await res.blob();
  if (blob.size === 0) throw new Error(`${fallbackMessage}：服务端返回了空文件`);
  if (blob.size > 50 * 1024 * 1024) throw new Error(`${fallbackMessage}：报告文件超过 50 MB 安全限制`);
  const header = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
  const isPdf = header.length >= 5 && String.fromCharCode(...header.slice(0, 5)) === "%PDF-";
  const isPng = header.length >= 8 && [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => header[index] === value);
  const isJpeg = header.length >= 3 && header[0] === 0xff && header[1] === 0xd8 && header[2] === 0xff;
  if (
    (contentType.includes("application/pdf") && !isPdf)
    || (contentType.includes("image/png") && !isPng)
    || (contentType.includes("image/jpeg") && !isJpeg)
  ) {
    throw new Error(`${fallbackMessage}：报告内容与文件类型不一致`);
  }
  const extension = contentType.includes("image/png")
    ? ".png"
    : contentType.includes("image/jpeg")
      ? ".jpg"
      : contentType.includes("application/pdf")
        ? ".pdf"
        : "";
  const typedFallback = extension ? fallbackName.replace(/\.[^.]+$/, extension) : fallbackName;
  const filename = filenameFromDisposition(res.headers.get("content-disposition"), typedFallback);
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

async function fetchReport(url: string, init: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 60_000);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("下载报告超时，请稍后重试");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
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

export interface ProbabilityFactor {
  kind: string;
  label: string;
  source?: string;
  group?: string;
  direction?: "fake" | "real" | string;
  likelihoodRatio?: number;
  effectiveLikelihoodRatio?: number;
  correlationExponent?: number;
}

export interface ProbabilityModel {
  version: string;
  method: string;
  pixelBaseline?: number;
  adjustedBaseline?: number;
  baseRate?: number;
  posterior: number;
  effectiveLikelihoodRatio?: number;
  crossModalExponent?: number;
  factors: ProbabilityFactor[];
  decisive?: boolean;
  corroborated?: boolean;
  conflicting?: boolean;
  calibrationStatus?: string;
  note?: string;
}

export interface CaptureEvidenceItem {
  key: string;
  label: string;
  value: string;
  strength: "strong" | "medium" | "weak" | string;
}

export interface CaptureEvidence {
  version: string;
  level: "strong" | "medium" | "weak" | "none" | "conflict" | string;
  levelText: string;
  supportsRealCapture: boolean;
  score: number;
  likelihoodRatio: number;
  title: string;
  summary: string;
  evidence: CaptureEvidenceItem[];
  conflicts: CaptureEvidenceItem[];
  limitations: string[];
  groups: string[];
  fieldCount: number;
  privacy?: {
    gpsRedacted?: boolean;
    serialRedacted?: boolean;
    captureTimeRedacted?: boolean;
  };
}

export interface UnifiedForensicsRegion {
  modality: "image" | "video" | string;
  source: string;
  x: number;
  y: number;
  w: number;
  h: number;
  label: string;
  confidence: number | null;
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
  riskScore?: number;
  aiProbability?: number | null;
  riskVector?: {
    aiGenerated: number | null;
    tampered: number | null;
    deepfake: number | null;
  };
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
  riskScore?: number;
  aiProbability?: number | null;
  riskVector?: {
    aiGenerated: number | null;
    tampered: number | null;
    deepfake: number | null;
  };
  modelVersion: string;
  source: string;
  decisionStatus?: "verdict" | "review_only";
  decisionAuthority?: "decisive_provenance" | "none" | string;
  reviewRequired?: boolean;
  cacheVersion: string;
  cacheHit?: boolean;
  elapsedMs: number;
  dimensions: Dimension[];
  regions: Region[];
  explanation: string;
  synthid?: SynthIDResult;
  visibleWatermark?: VisibleWatermarkResult;
  evidenceCompleteness?: boolean;
  evidenceWarnings?: string[];
  captureEvidence?: CaptureEvidence;
  probabilityModel?: ProbabilityModel;
  provenancePrecheck?: ProvenancePrecheckResult;
  unifiedForensics?: UnifiedForensicsOutput;
  forensics?: ForensicReport | null;
  provenance?: ProvenanceReport | null;
  disclaimer: string;
}

export interface ProvenancePrecheckResult {
  status: string;
  available: boolean;
  engine?: string;
  engineVersion?: string;
  elapsedMs?: number;
  roundTripMs?: number;
  genericVisibleWatermark?: {
    available: boolean;
    detected?: boolean;
    count?: number;
    elapsedMs?: number;
    roundTripMs?: number;
    model?: string;
    modelRevision?: string;
    confidenceThreshold?: number;
    mode?: string;
    error?: string;
  };
  visibleHits?: Array<{
    provider: string;
    label?: string;
    confidence: number;
    bbox: { x: number; y: number; w: number; h: number };
    model?: string;
    modelRevision?: string;
    decisive?: boolean;
    yoloCorroborated?: boolean;
    yoloConfidence?: number;
  }>;
  decision?: {
    shortCircuit: boolean;
    modelRequired: boolean;
    verdict?: Verdict | null;
    confidence?: number;
    reason?: string;
    evidenceKinds?: string[];
    summary?: string;
  };
  report?: {
    isAiGenerated?: boolean | null;
    platform?: string | null;
    confidence?: string;
    aiSourceKind?: string | null;
    aiFromMetadata?: boolean;
    watermarks?: string[];
    integrityClashes?: string[];
  };
}

export interface SynthIDResult {
  enabled: boolean;
  supported: boolean;
  detected: boolean | null;
  possiblyDetected?: boolean | null;
  detectionState?: "detected" | "possible" | "not_detected" | "unavailable";
  confidence: number;
  phaseMatch: number;
  profile: string | null;
  modelProfile: string | null;
  modelProfiles?: string[];
  candidateModelProfiles?: string[];
  attributedModelProfile?: string | null;
  modelAttribution?: "profile_candidate" | "ambiguous" | "none" | "unavailable" | string;
  modelResults?: Array<{
    modelProfile: string;
    modelLabel?: string;
    supported: boolean;
    detected: boolean | null;
    possiblyDetected?: boolean | null;
    detectionState?: "detected" | "possible" | "not_detected" | "unavailable";
    confidence: number;
    phaseMatch: number;
    profile: string | null;
    exactResolutionMatch?: boolean;
    evidenceLevel: "strong" | "medium" | "weak" | "none" | "unavailable";
    error?: string | null;
  }>;
  exactProfileMatch?: boolean;
  exactResolutionMatch?: boolean;
  detectionThreshold?: number;
  possibleThreshold?: number;
  evidenceLevel: "strong" | "medium" | "weak" | "none" | "unavailable";
  method?: string;
  verificationAuthority?: string;
  officialVerification?: boolean;
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
  model?: string | null;
  modelRevision?: string | null;
  decisive?: boolean;
  evidenceRole?: "provenance" | "localization" | string;
  localizationConfirmed?: boolean;
  localizationConfidence?: number;
  localizationModel?: string | null;
  localizationModelRevision?: string | null;
}

export interface VisibleWatermarkEngine {
  id: string;
  label: string;
  available: boolean;
  detected?: boolean;
  count?: number;
  model: string;
  version?: string | null;
  role?: "provenance" | "localization" | "corroboration" | string;
}

export interface WatermarkPipelineStage {
  id: "decode" | "metadata" | "registry" | "yolo" | "ocr" | "retrieval" | "fusion" | "verdict" | string;
  label: string;
  status: "success" | "hit" | "clean" | "warning" | "error" | "skipped" | string;
  elapsedMs: number;
  summary: string;
  parallelGroup?: string | null;
  details: Record<string, unknown>;
}

export interface WatermarkPipelineTrace {
  schemaVersion: "watermark_pipeline_trace_v1" | string;
  totalElapsedMs: number;
  parallelGroups?: Record<string, string[]>;
  stages: WatermarkPipelineStage[];
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
  pipelineTrace?: WatermarkPipelineTrace | null;
  reanalysis?: {
    reused: boolean;
    basis: string;
    sourceTaskId?: string | null;
    sourceCreatedAt?: string | null;
  };
  detector?: {
    available: boolean;
    model?: string | null;
    modelRevision?: string | null;
    confidenceThreshold?: number | null;
    roundTripMs?: number | null;
    engines?: VisibleWatermarkEngine[];
  };
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
  provenancePrecheck?: {
    configured?: boolean;
    available?: boolean | null;
    lastElapsedMs?: number | null;
  };
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

const DOCUMENT_REQUEST_KEYS = new WeakMap<File, string>();
const VIDEO_REQUEST_KEYS = new WeakMap<File, string>();

function documentRequestKey(file: File) {
  const existing = DOCUMENT_REQUEST_KEYS.get(file);
  if (existing) return existing;
  const key = globalThis.crypto.randomUUID();
  DOCUMENT_REQUEST_KEYS.set(file, key);
  return key;
}

function videoRequestKey(file: File) {
  const existing = VIDEO_REQUEST_KEYS.get(file);
  if (existing) return existing;
  const key = globalThis.crypto.randomUUID();
  VIDEO_REQUEST_KEYS.set(file, key);
  return key;
}

export async function detect(file: File, fileType?: FileType): Promise<DetectResult> {
  const fd = new FormData();
  fd.append("file", file);
  if (fileType) fd.append("fileType", fileType);
  appendUploadConsent(fd);
  const res = await fetch("/v2-api/detect", withSession({
    method: "POST",
    body: fd,
    headers: { "Idempotency-Key": documentRequestKey(file) },
  }));
  return normalizeDetectResult(await parseJson(res, `检测失败 (${res.status})`));
}

export interface AccountUser {
  Userid: number;
  account_uuid?: string;
  username: string;
  phone: string;
  openid?: string;
}

export interface AccountCounters {
  image_detect: number;
  video_detect: number;
}

export interface DeveloperApiKey {
  id: number;
  name: string;
  preview: string;
  scopes: string[];
  status: "active" | "revoked" | string;
  createdAt: string;
  lastUsedAt?: string;
  revokedAt?: string;
  expiresAt?: string;
  ipAllowlist: string[];
}

export interface DeveloperPricing {
  mode: "fast" | "swarm";
  name: string;
  unitPriceFen: number;
  unitPriceCny: string;
  enabled: boolean;
  updatedAt?: string;
}

export interface DeveloperAccount {
  userId: number;
  status: string;
  freeTotal: number;
  freeUsed: number;
  freeReserved: number;
  freeRemaining: number;
  balanceFen: number;
  balanceCny: string;
  balanceReservedFen: number;
  availableBalanceFen: number;
  createdAt: string;
  updatedAt: string;
}

export interface DeveloperUsageSummary {
  totalCalls: number;
  totalRequests: number;
  v1Calls: number;
  v2Calls: number;
  billableRequests: number;
  cacheHits: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  lastEventAt?: string | null;
}

export interface DeveloperUsage {
  days: number;
  summary: DeveloperUsageSummary;
  byDay: Array<{
    date: string;
    requests: number;
    billableRequests: number;
    totalTokens: number;
    v1Calls?: number;
    v2Calls?: number;
  }>;
  byEndpoint: Array<Record<string, string | number>>;
  byModel: Array<Record<string, string | number>>;
  byKey: Array<Record<string, string | number>>;
  byPipeline: Array<Record<string, string | number>>;
}

export interface DeveloperTaskSummary {
  id: string;
  status: string;
  mode: "fast" | "swarm";
  filename: string;
  progress: number;
  createdAt: string;
  updatedAt: string;
  completedAt?: string;
  billing?: {
    source: "free" | "balance";
    amountFen: number;
    amountCny: string;
    status: string;
  } | null;
}

export interface DeveloperAccountResponse {
  status: string;
  account: DeveloperAccount;
  pricing: DeveloperPricing[];
  modeSummary: {
    fast: { calls: number; spendFen: number };
    swarm: { calls: number; spendFen: number };
  };
  usage: DeveloperUsage;
  recentTasks: DeveloperTaskSummary[];
}

export interface DeveloperLedgerEntry {
  id: number;
  keyId?: number | null;
  taskId?: string | null;
  type: string;
  mode?: "fast" | "swarm" | null;
  freeCallsDelta: number;
  balanceDeltaFen: number;
  amountFen: number;
  balanceAfterFen: number;
  note: string;
  createdAt: string;
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
  generatedScore?: number | null;
  tamperScore?: number | null;
  recaptureScore?: number | null;
  riskVector?: {
    aiGenerated?: number | null;
    tampered?: number | null;
    recaptured?: number | null;
  };
  finalLabel?: string;
  confidence?: string;
  consensusLevel?: string;
  consensusScore?: number;
  disagreement?: boolean;
  effectiveExperts?: number;
  totalExperts?: number;
  experts?: ImageAgentExpert[];
  evidence?: string[];
  probabilityModel?: ProbabilityModel;
}

export interface ImageAgentResult {
  itemid: number;
  final_label: string;
  probability: number;
  detector_probability?: number;
  p_visual?: number | null;
  p_metadata?: number | null;
  confidence: string;
  decisionStatus?: "verdict" | "review_only";
  decisionAuthority?: "calibrated_model" | "decisive_provenance" | "none" | string;
  reviewRequired?: boolean;
  modelDecisionReady?: boolean | null;
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
  probabilityModel?: ProbabilityModel;
  synthid?: SynthIDResult;
  visibleWatermark?: VisibleWatermarkResult;
  evidenceCompleteness?: boolean;
  evidenceWarnings?: string[];
  capture_evidence?: CaptureEvidence;
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
  fake_percentage: number | null;
  real_percentage: number | null;
  final_label: string;
  confidence: string;
  confidence_score?: number;
  decisionStatus?: "verdict" | "review_only";
  decisionAuthority?: string;
  reviewRequired?: boolean;
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
  real_prob: number | null;
  fake_prob: number | null;
  final_label: string;
  confidence: string;
  createtime: string;
  report_url?: string;
}

export interface VideoHistoryRecord {
  itemid: number;
  filename: string;
  video_url: string;
  real_percentage: number | null;
  fake_percentage: number | null;
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

export async function fetchCurrentUser(): Promise<{
  status: string;
  authenticated: boolean;
  user: AccountUser | null;
  counters: AccountCounters;
}> {
  await ensureSessionCsrf();
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
        terms_version: LEGAL_CONSENT.version,
      }),
    },
    "注册失败",
  );
}

export function logoutAccount() {
  return accountJson<{ status: string }>("/api/logout", { method: "POST" }, "退出失败");
}

export function fetchDeveloperKeys() {
  return accountJson<{ status: string; keys: DeveloperApiKey[] }>(
    "/api/developer/keys",
    {},
    "API Key 暂时无法读取",
  );
}

export function createDeveloperKey(payload: {
  name: string;
  scopes: string[];
  expiresAt?: string | null;
  ipAllowlist?: string[];
}, idempotencyKey: string) {
  return accountJson<{ status: string; apiKey: string; key: DeveloperApiKey }>(
    "/api/developer/keys",
    {
      method: "POST",
      body: JSON.stringify(payload),
      headers: { "Idempotency-Key": idempotencyKey },
    },
    "API Key 创建失败",
  );
}

export function revokeDeveloperKey(keyId: number) {
  return accountJson<{ status: string; revoked: number }>(
    `/api/developer/keys/${encodeURIComponent(String(keyId))}`,
    { method: "DELETE" },
    "API Key 撤销失败",
  );
}

export function rotateDeveloperKey(keyId: number, idempotencyKey: string) {
  return accountJson<{ status: string; apiKey: string; key: DeveloperApiKey; revoked: number }>(
    `/api/developer/keys/${encodeURIComponent(String(keyId))}/rotate`,
    {
      method: "POST",
      body: JSON.stringify({}),
      headers: { "Idempotency-Key": idempotencyKey },
    },
    "API Key 轮换失败",
  );
}

export function fetchDeveloperAccount(days: 7 | 14 | 30 | 90 = 30) {
  return accountJson<DeveloperAccountResponse>(
    `/api/developer/account?days=${encodeURIComponent(String(days))}`,
    {},
    "开发者账户暂时无法读取",
  );
}

export function fetchDeveloperLedger(limit = 50) {
  return accountJson<{ status: string; entries: DeveloperLedgerEntry[] }>(
    `/api/developer/ledger?limit=${encodeURIComponent(String(limit))}`,
    {},
    "计费账本暂时无法读取",
  );
}

export function fetchDeveloperOpenApi() {
  return accountJson<Record<string, unknown>>(
    "/api/developer/openapi.json",
    {},
    "OpenAPI 文档暂时无法读取",
  );
}

export function startImageAgent(file: File, idempotencyKey: string, signal?: AbortSignal) {
  const body = new FormData();
  body.append("image", file);
  appendUploadConsent(body);
  return accountJson<{ status: string; job: ImageAgentJob }>(
    "/image_upload/detect_swarm",
    { method: "POST", body, signal, headers: { "Idempotency-Key": idempotencyKey } },
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

export function detectVideoWithAgent(file: File, signal?: AbortSignal) {
  const body = new FormData();
  body.append("video_file", file);
  body.append("fast_mode", "1");
  appendUploadConsent(body);
  return accountJson<{ status: string; result: VideoAgentResult }>(
    "/video_upload/detect",
    { method: "POST", body, signal, headers: { "Idempotency-Key": videoRequestKey(file) } },
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

async function deleteAccountHistory(path: string, fallback: string): Promise<void> {
  const res = await fetch(path, withSession({ method: "DELETE", cache: "no-store" }));
  if (!res.ok) await parseJson<Record<string, unknown>>(res, fallback);
}

export function deleteImageHistory(itemId: number): Promise<void> {
  return deleteAccountHistory(`/api/history/image-detections/${encodeURIComponent(String(itemId))}`, "删除图像记录失败");
}

export function deleteVideoHistory(itemId: number): Promise<void> {
  return deleteAccountHistory(`/api/history/video-detections/${encodeURIComponent(String(itemId))}`, "删除视频记录失败");
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

export async function downloadAccountReport(kind: "image" | "video", itemId: number): Promise<string> {
  const path = kind === "image" ? "/image_upload/report" : "/video_upload/report";
  const fallbackName = `huijian-${kind}-report-${itemId}.pdf`;
  const res = await fetchReport(`${path}?itemid=${encodeURIComponent(String(itemId))}`, withSession({ cache: "no-store" }));
  return downloadResponse(res, fallbackName, "下载报告失败");
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

export async function runForensics(file: File, signal?: AbortSignal, taskId?: string): Promise<ForensicReport> {
  const fd = new FormData();
  fd.append("file", file);
  if (taskId) fd.append("taskId", taskId);
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
  credentialTrusted?: boolean;
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
  captureEvidence?: CaptureEvidence;
  metadata?: Record<string, unknown>;
  synthid: { supported: boolean; detected: boolean | null; note: string };
  error: string | null;
  elapsedMs: number;
  fileMeta: { name: string; size: string };
}

export async function runProvenance(file: File, taskId?: string): Promise<ProvenanceReport> {
  const fd = new FormData();
  fd.append("file", file);
  if (taskId) fd.append("taskId", taskId);
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

export async function downloadReport(reportId: string): Promise<string> {
  const fallbackName = `huijian-report-${reportId}.pdf`;
  const res = await fetchReport(`/v2-api/report/${encodeURIComponent(reportId)}/download`, {
    credentials: "include",
    headers: withAuthHeaders(),
  });
  return downloadResponse(res, fallbackName, "下载报告失败");
}

export interface ReportShareLink {
  shareId: string;
  url: string;
  publicPath?: string;
  apiPath?: string;
  expiresAt: string;
  expiresInSeconds: number;
}

export interface ReportShareItem {
  shareId: string;
  reportId: string;
  createdAt: string;
  expiresAt: string;
  revokedAt?: string | null;
  active: boolean;
  legacy: boolean;
}

export async function createReportShareLink(reportId: string, expiresInSeconds = 7 * 24 * 60 * 60): Promise<ReportShareLink> {
  const res = await fetch(`/v2-api/report/${encodeURIComponent(reportId)}/share`, {
    ...withSession({
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }),
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

export async function listReportShares(reportId: string): Promise<ReportShareItem[]> {
  const res = await fetch(`/v2-api/report/${encodeURIComponent(reportId)}/shares`, withSession());
  const payload = await parseJson<{ items: ReportShareItem[] }>(res, "加载分享记录失败");
  return payload.items || [];
}

export async function revokeReportShare(reportId: string, shareId: string): Promise<void> {
  const res = await fetch(
    `/v2-api/report/${encodeURIComponent(reportId)}/share/${encodeURIComponent(shareId)}`,
    withSession({ method: "DELETE" }),
  );
  await parseJson(res, "撤销分享链接失败");
}

export async function fetchHealth(): Promise<HealthStatus> {
  const res = await fetch("/v2-api/health", withSession());
  return parseJson(res, "加载系统状态失败");
}

export const VERDICT_META: Record<Verdict, { label: string; color: string; ring: string }> = {
  real: { label: "真实", color: "#3fb6a8", ring: "verdict-real" },
  suspected_fake: { label: "疑似伪造", color: "#d99a2b", ring: "verdict-warn" },
  highly_suspected_fake: { label: "高度疑似伪造", color: "#d8412f", ring: "verdict-fake" },
  unknown: { label: "需要人工复核", color: "#7c8aa5", ring: "verdict-unknown" },
};

export const TYPE_LABEL: Record<FileType, string> = {
  image: "图像",
  video: "视频",
  audio: "音频",
  document: "文档",
};
