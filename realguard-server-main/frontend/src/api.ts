import { appendUploadConsent } from "./legalConsent";

export type ApiResult<T> = T & {
  status?: "success" | "error";
  message?: string;
};

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const IMAGE_REQUEST_KEYS = new WeakMap<File, Partial<Record<"fast" | "swarm", string>>>();
const VIDEO_REQUEST_KEYS = new WeakMap<File, string>();

function imageRequestKey(file: File, mode: "fast" | "swarm") {
  const keys = IMAGE_REQUEST_KEYS.get(file) || {};
  const key = keys[mode] || globalThis.crypto.randomUUID();
  keys[mode] = key;
  IMAGE_REQUEST_KEYS.set(file, keys);
  return key;
}

function videoRequestKey(file?: File) {
  if (!file) return globalThis.crypto.randomUUID();
  const existing = VIDEO_REQUEST_KEYS.get(file);
  if (existing) return existing;
  const key = globalThis.crypto.randomUUID();
  VIDEO_REQUEST_KEYS.set(file, key);
  return key;
}

async function parseResponse<T>(response: Response): Promise<ApiResult<T>> {
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : { status: "error", message: await response.text() };

  if (!response.ok || data.status === "error" || data.success === false) {
    throw new Error(data.message || `请求失败：${response.status}`);
  }
  return data;
}

export async function jsonRequest<T>(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include"
  });
  return parseResponse<T>(response);
}

export function sendSmsCode(phone: string, scene: "login" | "register" | "reset") {
  return jsonRequest<{ success: boolean; debug_code?: string; expires_in?: number }>("/sms/send_code", {
    method: "POST",
    body: JSON.stringify({ phone, scene })
  });
}

export function loginByPassword(phone: string, secret: string, acceptedTerms: boolean) {
  return jsonRequest<{ user: User }>("/api/login/password", {
    method: "POST",
    body: JSON.stringify({ phone, secret, accepted_terms: acceptedTerms })
  });
}

export function loginBySms(phone: string, smsCode: string, acceptedTerms: boolean) {
  return jsonRequest<{
    user?: User;
    requiresPasswordSetup?: boolean;
    passwordSetupExpiresIn?: number;
  }>("/api/login/sms", {
    method: "POST",
    body: JSON.stringify({ phone, sms_code: smsCode, accepted_terms: acceptedTerms })
  });
}

export function completeSmsPasswordSetup(secret: string, secretConfirm: string) {
  return jsonRequest<{ user: User; message: string }>("/api/login/sms/complete", {
    method: "POST",
    body: JSON.stringify({ secret, secret_confirm: secretConfirm })
  });
}

export function registerUser(payload: { phone: string; secret: string; secret_confirm: string; username: string; sms_code: string; accepted_terms: boolean; terms_version: string }) {
  return jsonRequest<{ message: string }>("/api/register", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function resetPassword(payload: { phone: string; secret: string; sms_code: string }) {
  return jsonRequest<{ message: string }>("/api/password/reset", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function getMe() {
  return jsonRequest<{ user: User; counters: Counters }>("/api/me");
}

export function logout() {
  return jsonRequest<Record<string, never>>("/api/logout", { method: "POST" });
}

export function detectImage(file: File, signal?: AbortSignal) {
  const body = new FormData();
  body.append("image", file);
  appendUploadConsent(body);
  return jsonRequest<{ job: DetectionJob }>("/image_upload/detect_async", {
    method: "POST",
    body,
    signal,
    headers: { "Idempotency-Key": imageRequestKey(file, "fast") },
  });
}

export function startExpertReviewImageDetection(file: File, signal?: AbortSignal) {
  const body = new FormData();
  body.append("image", file);
  appendUploadConsent(body);
  return jsonRequest<{ job: DetectionJob }>("/image_upload/detect_swarm", {
    method: "POST",
    body,
    signal,
    headers: { "Idempotency-Key": imageRequestKey(file, "swarm") },
  });
}

export function getImageDetectionJob(jobId: string, signal?: AbortSignal) {
  return jsonRequest<{ job: DetectionJob }>(`/image_upload/jobs/${encodeURIComponent(jobId)}`, { signal });
}

function triggerDownload(path: string) {
  const link = document.createElement("a");
  link.href = `${API_BASE}${path}`;
  link.rel = "noreferrer";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export function downloadImageReport(itemid: number) {
  triggerDownload(`/image_upload/report?itemid=${encodeURIComponent(String(itemid))}`);
}

export function submitImageFeedback(itemid: number, feedback: 1 | -1 | 0) {
  return jsonRequest<{ feedback: 1 | -1 | null; message: string }>("/image_upload/feedback", {
    method: "POST",
    body: JSON.stringify({ itemid, feedback }),
  });
}

export function detectVideo(payload: { file?: File; videoUrl?: string; fastMode: boolean }) {
  const body = new FormData();
  if (payload.file) body.append("video_file", payload.file);
  if (payload.videoUrl) body.append("video_url", payload.videoUrl);
  body.append("fast_mode", payload.fastMode ? "1" : "0");
  appendUploadConsent(body);
  return jsonRequest<{ result: VideoDetectionResult }>("/video_upload/detect", {
    method: "POST",
    body,
    headers: { "Idempotency-Key": videoRequestKey(payload.file) },
  });
}

export function downloadVideoReport(itemid: number) {
  triggerDownload(`/video_upload/report?itemid=${encodeURIComponent(String(itemid))}`);
}

export type HistoryFilterKey = "all" | "guest" | "metadata" | "issues" | "review";

export interface HistoryListResponse {
  records: HistoryRecord[];
  total?: number;
  filter_counts?: Partial<Record<HistoryFilterKey, number>>;
}

export function getHistory(
  kind: "image-detections" | "video-detections",
  params?: { query?: string; filter?: HistoryFilterKey; limit?: number; offset?: number },
) {
  const search = new URLSearchParams();
  if (params?.query?.trim()) search.set("query", params.query.trim());
  if (params?.filter && params.filter !== "all") search.set("filter", params.filter);
  if (params?.limit) search.set("limit", String(params.limit));
  if (params?.offset) search.set("offset", String(params.offset));
  const qs = search.toString();
  return jsonRequest<HistoryListResponse>(`/api/history/${kind}${qs ? `?${qs}` : ""}`);
}

export type User = {
  Userid: number;
  username: string;
  phone: string;
  openid?: string;
};

export type Counters = {
  image_detect: number;
  video_detect: number;
};

export type CaptureEvidenceItem = {
  key: string;
  label: string;
  value: string;
  strength: "strong" | "medium" | "weak" | string;
};

export type CaptureEvidence = {
  version: string;
  level: "strong" | "medium" | "weak" | "none" | "conflict";
  levelText: string;
  supportsRealCapture: boolean;
  score: number;
  likelihoodRatio?: number;
  title: string;
  summary: string;
  evidence: CaptureEvidenceItem[];
  conflicts: CaptureEvidenceItem[];
  limitations: string[];
  groups?: string[];
};

export type WatermarkPipelineStage = {
  id: "decode" | "metadata" | "registry" | "yolo" | "ocr" | "retrieval" | "fusion" | "verdict" | string;
  label: string;
  status: "success" | "hit" | "clean" | "warning" | "error" | "skipped" | string;
  elapsedMs: number;
  summary: string;
  parallelGroup?: string | null;
  details: Record<string, any>;
};

export type WatermarkPipelineTrace = {
  schemaVersion: "watermark_pipeline_trace_v1" | string;
  totalElapsedMs: number;
  parallelGroups?: Record<string, string[]>;
  stages: WatermarkPipelineStage[];
};

export type ImageDetectionResult = {
  itemid: number;
  final_label: string;
  probability: number | null;
  detector_probability?: number | null;
  modelDecisionReady?: boolean;
  reviewRequired?: boolean;
  decisionStatus?: "verdict" | "review_only";
  scorePublished?: boolean;
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
  capture_evidence?: CaptureEvidence;
  evidenceCompleteness?: boolean;
  evidenceWarnings?: string[];
  visibleWatermark?: {
    supported: boolean;
    detected: boolean;
    provider?: string | null;
    confidence?: number;
    evidenceLevel?: string;
    note?: string;
    hits?: Array<{
      label?: string;
      confidence?: number;
      bbox?: { x?: number; y?: number; w?: number; h?: number };
    }>;
    pipelineTrace?: WatermarkPipelineTrace | null;
  };
  llm_used?: boolean;
  feedback?: 1 | -1 | null;
  swarm?: ExpertReviewResult;
};

export type PublicExpertReviewExpert = {
  id: string;
  publicId?: string;
  status?: "queued" | "running" | "success" | "failed" | "skipped" | string;
  publicName?: string;
  publicMessage?: string;
  publicVerdict?: string;
};

export type ExpertReviewResult = {
  enabled?: boolean;
  score?: number;
  finalLabel?: string;
  confidence?: string;
  consensusLevel?: string;
  consensusScore?: number;
  disagreement?: boolean;
  effectiveExperts?: number;
  totalExperts?: number;
  experts?: PublicExpertReviewExpert[];
  evidence?: string[];
};

export type DetectionJob = {
  id: string;
  kind?: string;
  filename?: string;
  mode?: string;
  status: "queued" | "running" | "success" | "failed" | string;
  createdAt?: string;
  updatedAt?: string;
  progress?: number;
  experts?: PublicExpertReviewExpert[];
  summary?: string;
  error?: string;
  result?: {
    status?: string;
    result?: ImageDetectionResult;
    message?: string;
  } | null;
};

export type VideoDetectionResult = {
  itemid: number;
  filename: string;
  video_url: string;
  fake_percentage: number | null;
  real_percentage: number | null;
  final_label: string;
  confidence: string;
  explanation: string;
  decisionStatus?: "verdict" | "review_only";
  reviewRequired?: boolean;
  frame_count?: number;
  d3_std?: number;
  encoder?: string;
  meta?: Record<string, string>;
};

export type HistoryRecord = Record<string, unknown>;
