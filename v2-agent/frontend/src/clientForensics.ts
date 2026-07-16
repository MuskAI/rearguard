import type { ForensicItem, ForensicReport } from "./api";

interface JpegPoint {
  quality: number;
  error: number;
}

type WorkerMessage =
  | { type: "item"; item: ForensicItem; jpegPoints?: JpegPoint[]; elapsedMs: number }
  | { type: "complete"; jpegPoints: JpegPoint[]; elapsedMs: number }
  | { type: "error"; message: string };

function formatBytes(size: number): string {
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${(size / (1024 * 1024)).toFixed(1)}MB`;
}

function previewReport(
  file: File,
  items: ForensicItem[],
  jpegPoints: JpegPoint[],
  elapsedMs: number,
  complete: boolean,
): ForensicReport {
  return {
    verdict: "unknown",
    confidence: 0.5,
    summary: complete
      ? `浏览器已生成 7 项低分辨率预览（${(elapsedMs / 1000).toFixed(1)} 秒），服务端正在判读无损图谱。`
      : `浏览器正在生成本地预览（${items.length}/7），服务端已并行开始无损图谱判读。`,
    items,
    jpegPoints,
    modelVersion: "huijian-browser-forensics-v1",
    source: "browser-preview",
    elapsedMs,
    fileMeta: { name: file.name, type: "image", size: formatBytes(file.size) },
  };
}

export function generateForensicPreview(
  file: File,
  onProgress?: (report: ForensicReport) => void,
  signal?: AbortSignal,
): Promise<ForensicReport> {
  if (typeof Worker === "undefined" || typeof OffscreenCanvas === "undefined" || typeof createImageBitmap === "undefined") {
    return Promise.reject(new Error("当前浏览器不支持本地图谱计算"));
  }
  if (signal?.aborted) return Promise.reject(new DOMException("本地图谱计算已取消", "AbortError"));

  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./workers/forensics.worker.ts", import.meta.url), { type: "module" });
    const items = new Map<string, ForensicItem>();
    let jpegPoints: JpegPoint[] = [];
    let settled = false;
    const timeout = window.setTimeout(() => {
      fail(new Error("本地图谱计算超时"));
    }, 30_000);

    const finish = () => {
      window.clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
      worker.terminate();
    };

    const fail = (error: Error) => {
      if (settled) return;
      settled = true;
      finish();
      reject(error);
    };

    const abort = () => fail(new DOMException("本地图谱计算已取消", "AbortError"));
    signal?.addEventListener("abort", abort, { once: true });

    worker.onerror = (event) => {
      fail(new Error(event.message || "本地图谱计算失败"));
    };

    worker.onmessage = (event: MessageEvent<WorkerMessage>) => {
      if (settled) return;
      const message = event.data;
      if (message.type === "error") {
        fail(new Error(message.message));
        return;
      }
      if (message.type === "item") {
        items.set(message.item.key, message.item);
        if (message.jpegPoints) jpegPoints = message.jpegPoints;
        onProgress?.(previewReport(file, [...items.values()], jpegPoints, message.elapsedMs, false));
        return;
      }

      const report = previewReport(file, [...items.values()], message.jpegPoints, message.elapsedMs, true);
      onProgress?.(report);
      if (settled) return;
      settled = true;
      finish();
      resolve(report);
    };

    const deviceMemory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory;
    const lowMemory = typeof deviceMemory === "number" && deviceMemory <= 4;
    const constrainedDevice = lowMemory || window.innerWidth <= 600;
    const maxSide = constrainedDevice ? 512 : 640;
    const maxSourcePixels = constrainedDevice ? 12_000_000 : 24_000_000;
    void file.arrayBuffer().then(
      (buffer) => worker.postMessage({ buffer, mime: file.type, maxSide, maxSourcePixels }, [buffer]),
      (error: unknown) => {
        fail(error instanceof Error ? error : new Error("无法读取本地图像"));
      },
    );
  });
}
