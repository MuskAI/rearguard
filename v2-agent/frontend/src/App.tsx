import { ChangeEvent, DragEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  BadgeCheck,
  Bot,
  Check,
  CircleDashed,
  FileText,
  Home,
  Image as ImageIcon,
  LoaderCircle,
  LogIn,
  Paperclip,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  UserRound,
  Video,
  Volume2,
} from "lucide-react";
import {
  AccountUser,
  DetectResult,
  FileType,
  HealthStatus,
  ImageAgentJob,
  ImageHistoryRecord,
  VideoHistoryRecord,
  detect,
  detectVideoWithAgent,
  downloadReport,
  fetchCurrentUser,
  fetchHealth,
  fetchHistory,
  fetchHistoryItem,
  fetchImageAgentJob,
  fetchImageAgentResult,
  fetchImageHistory,
  fetchVideoAgentResult,
  fetchVideoHistory,
  imageReportUrl,
  logoutAccount,
  persistArtifacts,
  runForensics,
  runProvenance,
  startImageAgent,
  videoReportUrl,
} from "./api";
import type { AgentHistoryEntry, AgentOutcome, AgentProgress, PendingFile } from "./agentTypes";
import { generateForensicPreview } from "./clientForensics";
import AgentHistory, { MobileHistoryButton } from "./components/AgentHistory";
import AgentResult from "./components/AgentResult";
import AuthDialog from "./components/AuthDialog";
import OfficialHome from "./components/OfficialHome";

const MAX_DOCUMENT_BYTES = 25 * 1024 * 1024;
const MAX_VIDEO_BYTES = 100 * 1024 * 1024;
const ACCEPTED_FILES = "image/jpeg,image/png,image/webp,image/bmp,image/gif,video/mp4,video/quicktime,video/webm,.txt,.md,.csv,.json,.log,.docx,.mp4,.mov,.webm";

type UploadKind = "image" | "video" | "audio" | "document" | "unknown";
type AppView = "home" | "workspace";

function initialAppView(): AppView {
  return new URLSearchParams(window.location.search).get("workspace") === "1" ? "workspace" : "home";
}

function inferKind(name: string): UploadKind {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "webp", "bmp", "gif"].includes(ext)) return "image";
  if (["mp4", "mov", "webm", "avi", "mkv", "flv", "wmv"].includes(ext)) return "video";
  if (["mp3", "wav", "m4a", "flac", "aac", "ogg"].includes(ext)) return "audio";
  if (["txt", "md", "csv", "json", "log", "docx"].includes(ext)) return "document";
  return "unknown";
}

function kindLabel(kind: UploadKind) {
  return { image: "图像", video: "视频", audio: "音频", document: "文档", unknown: "文件" }[kind];
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function verdictLabel(verdict: DetectResult["verdict"]) {
  return { real: "更倾向真实", suspected_fake: "疑似生成", highly_suspected_fake: "高度疑似", unknown: "待复核" }[verdict];
}

function timestamp(value: string) {
  const parsed = Date.parse(value.replace(/\./g, "-").replace(" ", "T"));
  return Number.isFinite(parsed) ? parsed : 0;
}

function imageHistoryEntry(record: ImageHistoryRecord): AgentHistoryEntry {
  return {
    key: `image:${record.itemid}`,
    origin: "image",
    recordId: String(record.itemid),
    title: record.filename || `图像任务 ${record.itemid}`,
    typeLabel: "图像",
    verdictLabel: record.final_label || (record.fake_prob >= 50 ? "疑似生成" : "更倾向真实"),
    score: Math.max(0, Math.min(Number(record.fake_prob || 0) / 100, 1)),
    createdAt: record.createtime || "",
    thumbnail: record.thumbnail_url || record.image_url,
  };
}

function videoHistoryEntry(record: VideoHistoryRecord): AgentHistoryEntry {
  return {
    key: `video:${record.itemid}`,
    origin: "video",
    recordId: String(record.itemid),
    title: record.filename || `视频任务 ${record.itemid}`,
    typeLabel: "视频",
    verdictLabel: record.final_label || (record.fake_percentage >= 50 ? "疑似合成" : "更倾向真实"),
    score: Math.max(0, Math.min(Number(record.fake_percentage || 0) / 100, 1)),
    createdAt: record.createtime || "",
  };
}

function evidenceHistoryEntry(record: Awaited<ReturnType<typeof fetchHistory>>["items"][number]): AgentHistoryEntry {
  const typeNames: Record<FileType, string> = { image: "图像", video: "视频", audio: "音频", document: "文档" };
  return {
    key: `evidence:${record.taskId}`,
    origin: "evidence",
    recordId: record.taskId,
    title: record.name || "未命名任务",
    typeLabel: typeNames[record.type],
    verdictLabel: verdictLabel(record.verdict),
    score: Number(record.confidence || 0),
    createdAt: record.createdAt || "",
    thumbnail: record.thumbnail,
  };
}

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    const abort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", abort, { once: true });
  });
}

function progressFromJob(job: ImageAgentJob): AgentProgress {
  const progress = Math.max(8, Math.min(Number(job.progress || 0), 98));
  if (progress >= 78) return { title: "正在形成综合意见", detail: job.summary || "汇总共识、分歧与关键证据", percent: progress, stage: "report", experts: job.experts };
  if (progress >= 42) return { title: "正在交叉核验证据", detail: job.summary || "比对模型、元数据与内容凭证", percent: progress, stage: "evidence", experts: job.experts };
  return { title: "已调度鉴伪角色", detail: job.summary || "多源检测正在并行执行", percent: progress, stage: "dispatch", experts: job.experts };
}

export default function App() {
  const [view, setView] = useState<AppView>(initialAppView);
  const [user, setUser] = useState<AccountUser | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [history, setHistory] = useState<AgentHistoryEntry[]>([]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyMessage, setHistoryMessage] = useState("");
  const [mobileHistoryOpen, setMobileHistoryOpen] = useState(false);
  const [activeKey, setActiveKey] = useState<string>();
  const [pendingFile, setPendingFile] = useState<PendingFile | null>(null);
  const [progress, setProgress] = useState<AgentProgress | null>(null);
  const [outcome, setOutcome] = useState<AgentOutcome | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [forensicsBusy, setForensicsBusy] = useState(false);
  const [forensicsPreviewState, setForensicsPreviewState] = useState<"idle" | "running" | "complete" | "skipped">("idle");
  const [provenanceBusy, setProvenanceBusy] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const runControllerRef = useRef<AbortController | null>(null);
  const forensicsControllerRef = useRef<{ request: AbortController; preview: AbortController } | null>(null);
  const runTokenRef = useRef(0);
  const forensicsTokenRef = useRef(0);
  const historyTokenRef = useRef(0);
  const detailTokenRef = useRef(0);
  const userIdRef = useRef<number | null>(null);
  const previewUrlRef = useRef<string | null>(null);

  const loadHistoryForUser = useCallback(async (account: AccountUser) => {
    const requestToken = ++historyTokenRef.current;
    const expectedUserId = account.Userid;
    setHistoryLoading(true);
    setHistoryMessage("");
    const results = await Promise.allSettled([
      fetchHistory({ limit: 100 }),
      fetchImageHistory(100),
      fetchVideoHistory(100),
    ]);
    if (requestToken !== historyTokenRef.current || userIdRef.current !== expectedUserId) return;

    const merged: AgentHistoryEntry[] = [];
    const [evidenceResult, imageResult, videoResult] = results;
    if (evidenceResult.status === "fulfilled") merged.push(...evidenceResult.value.items.map(evidenceHistoryEntry));
    if (imageResult.status === "fulfilled") merged.push(...(imageResult.value.records || []).map(imageHistoryEntry));
    if (videoResult.status === "fulfilled") merged.push(...(videoResult.value.records || []).map(videoHistoryEntry));
    merged.sort((a, b) => timestamp(b.createdAt) - timestamp(a.createdAt));
    setHistory(merged);
    if (results.every((result) => result.status === "rejected")) setHistoryMessage("个人历史暂时无法读取，请稍后刷新");
    setHistoryLoading(false);
  }, []);

  useEffect(() => {
    let active = true;
    fetchHealth().then((value) => active && setHealth(value)).catch(() => active && setHealth(null));
    fetchCurrentUser()
      .then((response) => {
        if (!active) return;
        userIdRef.current = response.user.Userid;
        setUser(response.user);
        void loadHistoryForUser(response.user);
      })
      .catch(() => {
        if (!active) return;
        userIdRef.current = null;
        setUser(null);
      })
      .finally(() => active && setAuthReady(true));
    return () => {
      active = false;
      runControllerRef.current?.abort();
      forensicsTokenRef.current += 1;
      forensicsControllerRef.current?.request.abort();
      forensicsControllerRef.current?.preview.abort();
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    };
  }, [loadHistoryForUser]);

  useEffect(() => {
    const syncViewFromUrl = () => setView(initialAppView());
    window.addEventListener("popstate", syncViewFromUrl);
    return () => window.removeEventListener("popstate", syncViewFromUrl);
  }, []);

  const outcomeId = outcome?.id;
  useEffect(() => {
    if (!progress && !outcomeId && !errorMessage) return;
    window.requestAnimationFrame(() => {
      if (outcomeId) {
        resultRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
        return;
      }
      workspaceRef.current?.scrollTo({ top: workspaceRef.current.scrollHeight, behavior: "smooth" });
    });
  }, [errorMessage, outcomeId, progress]);

  const resetTask = useCallback(() => {
    runTokenRef.current += 1;
    detailTokenRef.current += 1;
    runControllerRef.current?.abort();
    runControllerRef.current = null;
    forensicsTokenRef.current += 1;
    forensicsControllerRef.current?.request.abort();
    forensicsControllerRef.current?.preview.abort();
    forensicsControllerRef.current = null;
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = null;
    setPendingFile(null);
    setProgress(null);
    setOutcome(null);
    setErrorMessage("");
    setBusy(false);
    setForensicsBusy(false);
    setForensicsPreviewState("idle");
    setActiveKey(undefined);
  }, []);

  const navigateToView = useCallback((nextView: AppView) => {
    const url = new URL(window.location.href);
    if (nextView === "workspace") {
      url.searchParams.set("workspace", "1");
      url.hash = "";
    } else {
      url.searchParams.delete("workspace");
      url.hash = "home";
    }
    window.history.pushState({ view: nextView }, "", url);
    setView(nextView);
  }, []);

  function authenticated(nextUser: AccountUser) {
    resetTask();
    historyTokenRef.current += 1;
    userIdRef.current = nextUser.Userid;
    setHistory([]);
    setUser(nextUser);
    setAuthOpen(false);
    setAuthReady(true);
    void loadHistoryForUser(nextUser);
  }

  function logout() {
    resetTask();
    historyTokenRef.current += 1;
    userIdRef.current = null;
    setUser(null);
    setHistory([]);
    setHistoryMessage("");
    setHistoryQuery("");
    setMobileHistoryOpen(false);
    void logoutAccount().catch(() => undefined);
  }

  async function runImage(file: File, previewUrl: string | undefined, token: number, controller: AbortController) {
    try {
      const started = await startImageAgent(file, controller.signal);
      if (runTokenRef.current !== token) return;
      let job = started.job;
      setProgress(progressFromJob(job));
      const startedAt = Date.now();
      while (Date.now() - startedAt < 180_000) {
        if (job.status === "success") {
          const result = job.result?.result;
          if (!result) throw new Error("任务已完成，但没有返回可展示的鉴伪结果");
          setProgress({ title: "鉴伪完成", detail: "综合结论与证据已经整理完成", percent: 100, stage: "report", experts: job.experts });
          setOutcome({ kind: "image", id: `image:${result.itemid}`, result, file, previewUrl });
          return;
        }
        if (job.status === "failed") throw new Error(job.error || "多源鉴伪暂不可用");
        await wait(760, controller.signal);
        const polled = await fetchImageAgentJob(job.id, controller.signal);
        if (runTokenRef.current !== token) return;
        job = polled.job;
        setProgress(progressFromJob(job));
      }
      throw new Error("多源鉴伪超时，请稍后重试");
    } catch (error) {
      if (isAbort(error) || runTokenRef.current !== token) throw error;
      const message = error instanceof Error ? error.message : "多源鉴伪暂不可用";
      if (message.includes("登录") || message.includes("次数")) throw error;
      setProgress({ title: "正在切换可用检测链路", detail: "多源服务未完成，改用可信视觉模型继续分析", percent: 46, stage: "dispatch", fallback: true });
      const result = await detect(file, "image");
      if (runTokenRef.current !== token) return;
      setProgress({ title: "鉴伪完成", detail: "检测结果与内容凭证已整理完成", percent: 100, stage: "report", fallback: true });
      setOutcome({ kind: "evidence", id: `evidence:${result.taskId}`, result, file, previewUrl, provenance: result.provenance || undefined });
    }
  }

  async function analyzeFile(file: File) {
    resetTask();
    const kind = inferKind(file.name);
    if (kind === "unknown") {
      setPendingFile({ name: file.name, size: file.size, typeLabel: kindLabel(kind) });
      setErrorMessage("暂不支持这个文件格式。可上传常见图片、MP4/MOV/WEBM 视频，以及 TXT、MD、CSV、JSON、LOG、DOCX 文档。");
      return;
    }
    if (kind === "audio") {
      setPendingFile({ name: file.name, size: file.size, typeLabel: kindLabel(kind) });
      setErrorMessage("音频鉴伪模型尚未部署，本次不会生成模拟结论。请先上传图像、视频或可提取正文的文档。");
      return;
    }
    const maxBytes = kind === "video" ? MAX_VIDEO_BYTES : Number(health?.limits?.maxUploadBytes || MAX_DOCUMENT_BYTES);
    if (file.size > maxBytes) {
      setPendingFile({ name: file.name, size: file.size, typeLabel: kindLabel(kind) });
      setErrorMessage(`${kindLabel(kind)}文件不能超过 ${formatBytes(maxBytes)}，当前文件为 ${formatBytes(file.size)}。`);
      return;
    }

    const controller = new AbortController();
    runControllerRef.current = controller;
    const token = ++runTokenRef.current;
    const previewUrl = kind === "image" || kind === "video" ? URL.createObjectURL(file) : undefined;
    if (previewUrl) previewUrlRef.current = previewUrl;
    setPendingFile({ name: file.name, size: file.size, typeLabel: kindLabel(kind), previewUrl: kind === "image" ? previewUrl : undefined });
    setBusy(true);
    setErrorMessage("");
    setProgress({ title: "正在校验文件", detail: "确认格式、大小与可用检测能力", percent: 12, stage: "validate" });

    try {
      if (kind === "image") {
        await runImage(file, previewUrl, token, controller);
      } else if (kind === "video") {
        setProgress({ title: "正在分析视频", detail: "抽取关键帧并检查时序合成线索", percent: 42, stage: "evidence" });
        const response = await detectVideoWithAgent(file);
        if (runTokenRef.current !== token) return;
        setProgress({ title: "鉴伪完成", detail: "视频风险与关键指标已经整理完成", percent: 100, stage: "report" });
        setOutcome({ kind: "video", id: `video:${response.result.itemid}`, result: response.result, file, previewUrl });
      } else {
        setProgress({ title: "正在分析文档", detail: "提取正文并检查生成式写作线索", percent: 48, stage: "evidence" });
        const result = await detect(file, "document");
        if (runTokenRef.current !== token) return;
        setProgress({ title: "鉴伪完成", detail: "文本结论与证据维度已经整理完成", percent: 100, stage: "report" });
        setOutcome({ kind: "evidence", id: `evidence:${result.taskId}`, result, file });
      }
      if (user && userIdRef.current === user.Userid) void loadHistoryForUser(user);
    } catch (error) {
      if (isAbort(error) || runTokenRef.current !== token) return;
      const message = error instanceof Error ? error.message : "鉴伪任务未完成，请稍后重试";
      setProgress(null);
      setErrorMessage(message);
      if (message.includes("登录") || message.includes("次数")) setAuthOpen(true);
    } finally {
      if (runTokenRef.current === token) setBusy(false);
    }
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void analyzeFile(file);
  }

  function dropFile(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    if (busy) return;
    const file = event.dataTransfer.files?.[0];
    if (file) void analyzeFile(file);
  }

  async function selectHistory(entry: AgentHistoryEntry) {
    if (!user) {
      setAuthOpen(true);
      return;
    }
    const requestToken = ++detailTokenRef.current;
    const expectedUserId = user.Userid;
    runControllerRef.current?.abort();
    forensicsTokenRef.current += 1;
    forensicsControllerRef.current?.request.abort();
    forensicsControllerRef.current?.preview.abort();
    forensicsControllerRef.current = null;
    setForensicsBusy(false);
    setForensicsPreviewState("idle");
    setBusy(true);
    setMobileHistoryOpen(false);
    setActiveKey(entry.key);
    setPendingFile({ name: entry.title, size: 0, typeLabel: entry.typeLabel, previewUrl: entry.thumbnail || undefined });
    setOutcome(null);
    setErrorMessage("");
    setProgress({ title: "正在读取个人归档", detail: "校验任务归属并恢复检测结果", percent: 64, stage: "report" });
    try {
      if (entry.origin === "image") {
        const response = await fetchImageAgentResult(Number(entry.recordId));
        if (detailTokenRef.current !== requestToken || userIdRef.current !== expectedUserId) return;
        setOutcome({ kind: "image", id: entry.key, result: response.result });
      } else if (entry.origin === "video") {
        const response = await fetchVideoAgentResult(Number(entry.recordId));
        if (detailTokenRef.current !== requestToken || userIdRef.current !== expectedUserId) return;
        setOutcome({ kind: "video", id: entry.key, result: response.result });
      } else {
        const result = await fetchHistoryItem(entry.recordId);
        if (detailTokenRef.current !== requestToken || userIdRef.current !== expectedUserId) return;
        setOutcome({ kind: "evidence", id: entry.key, result, forensics: result.forensics || undefined, provenance: result.provenance || undefined });
      }
      setProgress(null);
    } catch (error) {
      if (detailTokenRef.current !== requestToken || userIdRef.current !== expectedUserId) return;
      setProgress(null);
      setErrorMessage(error instanceof Error ? error.message : "历史任务暂时无法读取");
    } finally {
      if (detailTokenRef.current === requestToken) setBusy(false);
    }
  }

  async function createForensics() {
    if (!outcome?.file || forensicsBusy) return;
    const outcomeId = outcome.id;
    const targetKind = outcome.kind;
    const targetTaskId = outcome.kind === "evidence" ? outcome.result.taskId : null;
    const file = outcome.file;
    const requestController = new AbortController();
    const previewController = new AbortController();
    const requestToken = ++forensicsTokenRef.current;
    forensicsControllerRef.current?.request.abort();
    forensicsControllerRef.current?.preview.abort();
    forensicsControllerRef.current = { request: requestController, preview: previewController };
    let authoritativeReady = false;
    let previewRendered = false;
    const isCurrent = () => forensicsTokenRef.current === requestToken && !requestController.signal.aborted;

    const updateReport = (report: Awaited<ReturnType<typeof runForensics>>) => {
      if (!isCurrent()) return;
      setOutcome((current) => current && current.id === outcomeId && (current.kind === "image" || current.kind === "evidence")
        ? { ...current, forensics: report }
        : current);
    };

    setForensicsBusy(true);
    setForensicsPreviewState("running");
    setErrorMessage("");

    const serverRequest = runForensics(file, requestController.signal);
    const previewTask = generateForensicPreview(file, (report) => {
      if (authoritativeReady || !isCurrent()) return;
      previewRendered = true;
      updateReport(report);
    }, previewController.signal).then(
      (report) => {
        if (isCurrent()) setForensicsPreviewState("complete");
        return { ok: true as const, report };
      },
      (error: unknown) => {
        if (isCurrent() && !isAbort(error)) setForensicsPreviewState("skipped");
        return { ok: false as const, error };
      },
    );
    const serverTask = serverRequest.then(async (report) => {
      if (!isCurrent()) return { ok: false as const, cancelled: true as const };
      authoritativeReady = true;
      previewController.abort();
      updateReport(report);
      if (targetKind === "evidence" && targetTaskId) {
        try {
          await persistArtifacts(targetTaskId, { forensics: report });
        } catch {
          if (isCurrent()) setErrorMessage("模型判读已完成，但取证图谱暂时无法写入历史归档。");
        }
      }
      return { ok: true as const, report };
    }).catch((error: unknown) => ({
      ok: false as const,
      error,
      cancelled: isAbort(error) || forensicsTokenRef.current !== requestToken,
    }));

    const [serverResult, previewResult] = await Promise.all([serverTask, previewTask]);
    if (!isCurrent()) return;
    if (!serverResult.ok && !("cancelled" in serverResult && serverResult.cancelled)) {
      if (previewResult.ok || previewRendered) {
        setOutcome((current) => {
          if (!current || current.id !== outcomeId || (current.kind !== "image" && current.kind !== "evidence")) return current;
          if (current.forensics?.source !== "browser-preview") return current;
          return {
            ...current,
            forensics: {
              ...current.forensics,
              summary: "低分辨率预览已在浏览器本地生成；服务端模型判读暂时不可用，当前预览不作为最终鉴伪结论。",
            },
          };
        });
        setErrorMessage("本地预览已生成，但服务端模型判读失败，请稍后重试。");
      } else {
        setErrorMessage(serverResult.error instanceof Error ? serverResult.error.message : "取证图谱生成失败");
      }
    }
    if (forensicsTokenRef.current === requestToken) {
      forensicsControllerRef.current = null;
      setForensicsBusy(false);
    }
  }

  async function verifyProvenance() {
    if (!outcome?.file || provenanceBusy) return;
    const outcomeId = outcome.id;
    setProvenanceBusy(true);
    try {
      const report = await runProvenance(outcome.file);
      setOutcome((current) => current && current.id === outcomeId && (current.kind === "image" || current.kind === "evidence") ? { ...current, provenance: report } : current);
      if (outcome.kind === "evidence") await persistArtifacts(outcome.result.taskId, { provenance: report });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "内容凭证验证失败");
    } finally {
      setProvenanceBusy(false);
    }
  }

  async function downloadOutcome() {
    if (!outcome || downloadBusy) return;
    setDownloadBusy(true);
    try {
      if (outcome.kind === "evidence") {
        await downloadReport(outcome.result.reportId, { forensics: outcome.forensics, provenance: outcome.provenance || outcome.result.provenance });
      } else {
        const link = document.createElement("a");
        link.href = outcome.kind === "image" ? imageReportUrl(outcome.result.itemid) : videoReportUrl(outcome.result.itemid);
        link.rel = "noopener";
        document.body.appendChild(link);
        link.click();
        link.remove();
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "报告下载失败");
    } finally {
      setDownloadBusy(false);
    }
  }

  const reportedCapabilities = Object.values(health?.capabilities || {});
  const serviceAvailable = health?.status === "ok"
    && health.vlmEnabled === true
    && reportedCapabilities.every((state) => state === "available");
  const screenTitle = pendingFile?.name || "新建鉴伪任务";

  return (
    <>
      {view === "home" ? (
        <OfficialHome
          authReady={authReady}
          health={health}
          user={user}
          onEnterWorkspace={() => navigateToView("workspace")}
          onLogin={() => setAuthOpen(true)}
        />
      ) : (
      <div className="agent-app">
      <AgentHistory
        entries={history}
        activeKey={activeKey}
        query={historyQuery}
        loading={historyLoading}
        message={historyMessage}
        user={user}
        mobileOpen={mobileHistoryOpen}
        onQueryChange={setHistoryQuery}
        onSelect={(entry) => void selectHistory(entry)}
        onNew={resetTask}
        onLogin={() => setAuthOpen(true)}
        onLogout={logout}
        onCloseMobile={() => setMobileHistoryOpen(false)}
      />

      <main className="agent-main">
        <header className="agent-topbar">
          <div className="topbar-title">
            <MobileHistoryButton onClick={() => setMobileHistoryOpen(true)} />
            <button type="button" className="workspace-home-button" onClick={() => navigateToView("home")} aria-label="返回慧鉴AI官网首页" title="官网首页">
              <Home size={16} /><span>官网首页</span>
            </button>
            <div>
              <h1><span className="desktop-task-title">{screenTitle}</span><span className="mobile-task-title">{pendingFile?.name || "慧鉴AI"}</span></h1>
              <p>{pendingFile ? "慧鉴AI 正在为这份内容整理可信证据" : "一个入口完成检测、取证、凭证核验与报告归档"}</p>
            </div>
          </div>
          <div className="topbar-actions">
            <span className={`service-pill ${health == null ? "checking" : serviceAvailable ? "online" : "limited"}`}>
              <i /> {health == null ? "服务检查中" : serviceAvailable ? "检测服务可用" : "部分能力受限"}
            </span>
            {authReady && (user ? (
              <button type="button" className="user-pill" onClick={() => setMobileHistoryOpen(true)} aria-label={`打开${user.username || "慧鉴用户"}的个人任务`}><UserRound size={16} /><span>{user.username || "慧鉴用户"}</span></button>
            ) : (
              <button type="button" className="secondary-button topbar-login" onClick={() => setAuthOpen(true)}><LogIn size={16} /> 登录</button>
            ))}
          </div>
        </header>

        <div className="agent-workspace" ref={workspaceRef}>
          {!pendingFile && !outcome && !errorMessage && (
            <WelcomeWorkspace
              busy={busy}
              dragging={dragging}
              user={user}
              onOpenFile={() => fileInputRef.current?.click()}
              onDragEnter={() => setDragging(true)}
              onDragLeave={() => setDragging(false)}
              onDrop={dropFile}
              onLogin={() => setAuthOpen(true)}
            />
          )}

          {pendingFile && (
            <div className="conversation-flow">
              <div className="user-file-message">
                <div className="file-message-copy"><span>请帮我鉴别这份内容</span><strong>{pendingFile.name}</strong><small>{pendingFile.typeLabel}{pendingFile.size ? ` · ${formatBytes(pendingFile.size)}` : " · 已归档任务"}</small></div>
                {pendingFile.previewUrl ? <img src={pendingFile.previewUrl} alt="待检测文件预览" /> : <span className="file-message-icon"><Paperclip size={20} /></span>}
              </div>
              {(progress || busy) && !outcome && <AgentProgressPanel progress={progress} />}
              {errorMessage && (
                <div className="agent-error-message" role="alert">
                  <span><Bot size={18} /></span>
                  <div><strong>这次任务没有完成</strong><p>{errorMessage}</p><button type="button" className="text-button" onClick={() => fileInputRef.current?.click()}><RefreshCw size={15} /> 重新选择文件</button></div>
                </div>
              )}
              {outcome && (
                <div ref={resultRef} className="result-anchor">
                  <AgentResult
                    outcome={outcome}
                    forensicsBusy={forensicsBusy}
                    forensicsPreviewState={forensicsPreviewState}
                    provenanceBusy={provenanceBusy}
                    downloadBusy={downloadBusy}
                    onForensics={() => void createForensics()}
                    onProvenance={() => void verifyProvenance()}
                    onDownload={() => void downloadOutcome()}
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {(pendingFile || outcome || errorMessage) && (
          <div className="composer-dock">
            <button type="button" className="composer-compact" disabled={busy} onClick={() => fileInputRef.current?.click()}>
              <span className="composer-attach"><Paperclip size={18} /></span>
              <span><strong>{busy ? "小鉴正在分析，请稍候" : "继续上传新的内容"}</strong><small>图片、视频或文档会自动选择合适的鉴伪能力</small></span>
              <span className="composer-send"><Send size={17} /></span>
            </button>
            <p>检测结果仅作辅助判断，高风险场景请结合原始来源和人工复核。</p>
          </div>
        )}
      </main>
      </div>
      )}

      <input ref={fileInputRef} className="sr-only" type="file" accept={ACCEPTED_FILES} onChange={chooseFile} tabIndex={-1} aria-hidden="true" />
      <AuthDialog open={authOpen} onClose={() => setAuthOpen(false)} onAuthenticated={authenticated} />
    </>
  );
}

function WelcomeWorkspace({
  busy,
  dragging,
  user,
  onOpenFile,
  onDragEnter,
  onDragLeave,
  onDrop,
  onLogin,
}: {
  busy: boolean;
  dragging: boolean;
  user: AccountUser | null;
  onOpenFile: () => void;
  onDragEnter: () => void;
  onDragLeave: () => void;
  onDrop: (event: DragEvent<HTMLElement>) => void;
  onLogin: () => void;
}) {
  return (
    <div className="welcome-page">
      <section className="welcome-workspace" aria-labelledby="welcome-title">
        <div className="welcome-copy workspace-welcome-copy">
          <div className="workspace-agent-badge">
            <img src="/brand/huijian-mascot.webp" alt="慧鉴AI 品牌助手小鉴" width="72" height="96" />
            <span><Sparkles size={14} /> 工作台已就绪</span>
          </div>
          <p className="welcome-eyebrow">新建鉴伪任务</p>
          <h2 id="welcome-title">上传内容，<br />开始证据分析。</h2>
          <p className="welcome-description">选择一份图片、视频或文档，慧鉴AI 会根据内容类型进入相应的检测与证据核验链路。</p>
          <div className="hero-proof-row" aria-label="慧鉴AI 分析链路">
            <span><i>01</i> 自动识别</span>
            <span><i>02</i> 多路核验</span>
            <span><i>03</i> 报告归档</span>
          </div>
          {!user && <button type="button" className="welcome-login-link" onClick={onLogin}><BadgeCheck size={16} /> 登录后，历史记录只对你本人可见</button>}
        </div>

        <section
          className={`upload-stage ${dragging ? "dragging" : ""}`}
          onDragEnter={(event) => { event.preventDefault(); onDragEnter(); }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={(event) => { if (event.currentTarget === event.target) onDragLeave(); }}
          onDrop={onDrop}
          onClick={() => { if (!busy) onOpenFile(); }}
          onKeyDown={(event) => {
            if (!busy && (event.key === "Enter" || event.key === " ")) {
              event.preventDefault();
              onOpenFile();
            }
          }}
          role="button"
          tabIndex={busy ? -1 : 0}
          aria-disabled={busy}
          aria-label="统一鉴伪上传入口"
        >
          <div className="upload-stage-topline">
            <span><i /> 统一鉴伪入口</span>
            <small>自动调度可用能力</small>
          </div>
          <div className="upload-stage-core">
            <div className="upload-stage-icon"><UploadCloud size={28} /></div>
            <h3>{dragging ? "松开即可开始鉴伪" : "上传或拖放待鉴别内容"}</h3>
            <p>图片、视频或文档，会自动进入对应的分析链路</p>
            <span className="primary-button upload-button"><Paperclip size={17} /> 选择文件</span>
          </div>
          <div className="capability-strip" aria-label="支持的内容类型">
            <div><ImageIcon size={18} /><span><strong>图像</strong><small>多源鉴伪</small></span><Check size={14} /></div>
            <div><Video size={18} /><span><strong>视频</strong><small>抽帧分析</small></span><Check size={14} /></div>
            <div><FileText size={18} /><span><strong>文档</strong><small>正文检测</small></span><Check size={14} /></div>
            <div className="unavailable"><Volume2 size={18} /><span><strong>音频</strong><small>尚未部署</small></span><CircleDashed size={14} /></div>
          </div>
          <small className="upload-limits">图片/文档不超过 25 MB · 视频不超过 100 MB</small>
        </section>

        <div className="trust-notes">
          <span><ShieldCheck size={16} /> 不生成随机结论</span>
          <span><BadgeCheck size={16} /> 个人任务严格隔离</span>
          <span><FileText size={16} /> 支持报告归档</span>
        </div>
      </section>
    </div>
  );
}

function AgentProgressPanel({ progress }: { progress: AgentProgress | null }) {
  const current = progress || { title: "正在准备鉴伪任务", detail: "请稍候", percent: 8, stage: "validate" as const };
  const stages = [
    { key: "validate", label: "文件校验" },
    { key: "dispatch", label: "能力调度" },
    { key: "evidence", label: "证据核验" },
    { key: "report", label: "结论整理" },
  ] as const;
  const stageIndex = stages.findIndex((stage) => stage.key === current.stage);
  return (
    <div className="agent-progress-message">
      <div className="agent-avatar"><img src="/brand/huijian-mascot.webp" alt="" /></div>
      <div className="progress-panel">
        <div className="progress-heading"><span><LoaderCircle size={17} className={current.percent < 100 ? "spin" : ""} /></span><div><strong>{current.title}</strong><p>{current.detail}</p></div><b>{Math.round(current.percent)}%</b></div>
        <div className="progress-track"><i style={{ width: `${current.percent}%` }} /></div>
        <div className="progress-stages">
          {stages.map((stage, index) => <span key={stage.key} className={index < stageIndex ? "done" : index === stageIndex ? "active" : ""}><i>{index < stageIndex ? <Check size={11} /> : index + 1}</i>{stage.label}</span>)}
        </div>
        {current.experts && current.experts.length > 0 && (
          <div className="progress-experts">
            {current.experts.slice(0, 6).map((expert, index) => <span key={expert.publicId || expert.id || index} className={expert.status || "queued"}><i />{expert.publicName || `复核角色 ${index + 1}`}</span>)}
          </div>
        )}
        {current.fallback && <div className="fallback-note"><ShieldCheck size={14} /> 已切换至可用的可信检测链路，不会返回模拟结论。</div>}
      </div>
    </div>
  );
}
