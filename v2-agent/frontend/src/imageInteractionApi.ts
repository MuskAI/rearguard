import { apiRequestErrorFromResponse, ensureSessionCsrf, notifySessionExpired, sessionCsrfHeaders } from "./api";
import type { ImageAgentJob } from "./api";
import { appendUploadConsent } from "./legalConsent";

async function requestJson<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  await ensureSessionCsrf();
  const headers = sessionCsrfHeaders(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    cache: "no-store",
    headers,
  });
  if (!response.ok) {
    if (response.status === 401) notifySessionExpired();
    throw await apiRequestErrorFromResponse(response, fallback);
  }
  try {
    return await response.json();
  } catch {
    throw new Error(fallback);
  }
}

export function startFastImageAgent(file: File, idempotencyKey: string, signal?: AbortSignal) {
  const body = new FormData();
  body.append("image", file);
  appendUploadConsent(body);
  return requestJson<{ status: string; job: ImageAgentJob }>(
    "/image_upload/detect_async",
    { method: "POST", body, signal, headers: { "Idempotency-Key": idempotencyKey } },
    "快速检测任务启动失败",
  );
}

export function submitImageFeedback(itemId: number, feedback: 1 | -1 | 0) {
  return requestJson<{ status: string; message: string; feedback: 1 | -1 | null }>(
    "/image_upload/feedback",
    { method: "POST", body: JSON.stringify({ itemid: itemId, feedback }) },
    "反馈暂时无法提交",
  );
}
