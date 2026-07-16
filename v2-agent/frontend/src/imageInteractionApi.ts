import { ApiRequestError } from "./api";
import type { ImageAgentJob } from "./api";

function retryAfterMs(response: Response): number {
  const value = response.headers.get("Retry-After")?.trim();
  if (!value) return 0;
  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : 0;
}

async function requestJson<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {
    ...init,
    credentials: "include",
    cache: "no-store",
    headers,
  });
  if (!response.ok) {
    let message = fallback;
    try {
      const body = await response.json();
      message = body.detail || body.message || message;
    } catch {
      // Keep the concise fallback when a proxy returns HTML or an empty body.
    }
    throw new ApiRequestError(message, response.status, retryAfterMs(response));
  }
  try {
    return await response.json();
  } catch {
    throw new Error(fallback);
  }
}

export function startFastImageAgent(file: File, signal?: AbortSignal) {
  const body = new FormData();
  body.append("image", file);
  return requestJson<{ status: string; job: ImageAgentJob }>(
    "/image_upload/detect_async",
    { method: "POST", body, signal },
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
