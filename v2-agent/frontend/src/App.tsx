import { ChangeEvent, DragEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  BadgeCheck,
  Bot,
  Check,
  CircleDashed,
  Code2,
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
  ApiRequestError,
  DetectResult,
  FileType,
  HealthStatus,
  ImageAgentJob,
  ImageHistoryRecord,
  VideoHistoryRecord,
  detect,
  deleteHistory,
  deleteImageHistory,
  deleteVideoHistory,
  detectVideoWithAgent,
  downloadAccountReport,
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
  isRateLimitedError,
  logoutAccount,
  runForensics,
  runProvenance,
  SESSION_EXPIRED_EVENT,
  startImageAgent,
} from "./api";
import type { AgentHistoryEntry, AgentOutcome, AgentProgress, ImageAnalysisMode, PendingFile } from "./agentTypes";
import { generateForensicPreview } from "./clientForensics";
import { startFastImageAgent, submitImageFeedback } from "./imageInteractionApi";
import AgentHistory, { MobileHistoryButton } from "./components/AgentHistory";
import AnalysisModeSwitch from "./components/AnalysisModeSwitch";
import AgentResult from "./components/AgentResult";
import AuthDialog from "./components/AuthDialog";
import DeveloperPlatform from "./components/DeveloperPlatform";
import OfficialHome from "./components/OfficialHome";
import ResultFeedback from "./components/ResultFeedback";
import { trackPageview } from "./analytics";
import "./interaction.css";

const MAX_DOCUMENT_BYTES = 25 * 1024 * 1024;
const MAX_VIDEO_BYTES = 256 * 1024 * 1024;
const AGENT_POLL_INITIAL_MS = 1_200;
const AGENT_POLL_MAX_MS = 2_400;
const AGENT_POLL_RATE_LIMIT_RETRIES = 8;
const ACCEPTED_FILES = "image/jpeg,image/png,image/webp,image/bmp,image/gif,image/heic,image/heif,.heic,.heif,video/mp4,video/quicktime,video/webm,.txt,.md,.csv,.json,.log,.docx,.mp4,.mov,.webm";

type UploadKind = "image" | "video" | "audio" | "document" | "unknown";
type AppView = "home" | "workspace" | "developer";
type HealthCheckState = "checking" | "ready" | "failed";
type FallbackOffer = {
  file: File;
  previewUrl?: string;
  mode: ImageAnalysisMode;
  reason: string;
  submitted: boolean;
  jobId?: string;
};

function initialAppView(): AppView {
  const params = new URLSearchParams(window.location.search);
  if (params.get("developer") === "1") return "developer";
  return params.get("workspace") === "1" ? "workspace" : "home";
}

function inferKind(name: string): UploadKind {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "webp", "bmp", "gif", "heic", "heif"].includes(ext)) return "image";
  if (["mp4", "mov", "webm", "avi", "mkv", "flv", "wmv"].includes(ext)) return "video";
  if (["mp3", "wav", "m4a", "flac", "aac", "ogg"].includes(ext)) return "audio";
  if (["txt", "md", "csv", "json", "log", "docx"].includes(ext)) return "document";
  return "unknown";
}

function isHeifImage(name: string) {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  return ext === "heic" || ext === "heif";
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
  const score = record.fake_prob == null
    ? null
    : Math.max(0, Math.min(Number(record.fake_prob) / 100, 1));
  return {
    key: `image:${record.itemid}`,
    origin: "image",
    recordId: String(record.itemid),
    title: record.filename || `图像任务 ${record.itemid}`,
    typeLabel: "图像",
    verdictLabel: record.final_label || (score == null ? "待复核" : score >= 0.5 ? "疑似生成" : "更倾向真实"),
    score,
    createdAt: record.createtime || "",
    thumbnail: record.thumbnail_url || record.image_url,
  };
}

function videoHistoryEntry(record: VideoHistoryRecord): AgentHistoryEntry {
  const score = record.fake_percentage == null
    ? null
    : Math.max(0, Math.min(Number(record.fake_percentage) / 100, 1));
  return {
    key: `video:${record.itemid}`,
    origin: "video",
    recordId: String(record.itemid),
    title: record.filename || `视频任务 ${record.itemid}`,
    typeLabel: "视频",
    verdictLabel: record.final_label || (score == null ? "待复核" : score >= 0.5 ? "疑似合成" : "更倾向真实"),
    score,
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
    score: record.confidence == null ? null : Number(record.confidence),
    createdAt: record.createdAt || "",
    thumbnail: record.thumbnail,
  };
}

function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

const AUTHENTICATION_ERROR_CODES = new Set([
  "authentication_required",
  "account_identity_required",
  "guest_detection_limit_reached",
  "guest_limit_reached",
  "session_expired",
  "unauthorized",
]);

function isAuthenticationRequiredError(error: unknown): error is ApiRequestError {
  return error instanceof ApiRequestError
    && (error.status === 401 || AUTHENTICATION_ERROR_CODES.has(error.code));
}

function isUploadConsentRequiredError(error: unknown): error is ApiRequestError {
  return error instanceof ApiRequestError
    && (error.status === 428 || error.code === "upload_consent_required" || error.code === "legal_documents_changed");
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

function progressFromJob(job: ImageAgentJob, mode: ImageAnalysisMode): AgentProgress {
  const progress = Math.max(mode === "fast" ? 30 : 8, Math.min(Number(job.progress || 0), 98));
  if (mode === "fast") {
    if (progress >= 78) return { title: "正在校验检测结果", detail: job.summary || "核对主模型输出与文件信息", percent: progress, stage: "report", analysisMode: mode };
    if (progress >= 42) return { title: "主模型正在 GPU 推理", detail: job.summary || "正在提取图像鉴伪特征", percent: progress, stage: "evidence", analysisMode: mode };
    return { title: "快速检测任务已启动", detail: job.summary || "主鉴伪模型正在接收图像", percent: progress, stage: "dispatch", analysisMode: mode };
  }
  if (progress >= 78) return { title: "正在形成综合意见", detail: job.summary || "汇总共识、分歧与关键证据", percent: progress, stage: "report", experts: job.experts, analysisMode: mode };
  if (progress >= 42) return { title: "正在交叉核验证据", detail: job.summary || "比对模型、元数据与内容凭证", percent: progress, stage: "evidence", experts: job.experts, analysisMode: mode };
  return { title: "已调度鉴伪角色", detail: job.summary || "多源检测正在并行执行", percent: progress, stage: "dispatch", experts: job.experts, analysisMode: mode };
}

export default function App() {
  const [view, setView] = useState<AppView>(initialAppView);
  const [user, setUser] = useState<AccountUser | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [healthCheckState, setHealthCheckState] = useState<HealthCheckState>("checking");
  const [history, setHistory] = useState<AgentHistoryEntry[]>([]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyMessage, setHistoryMessage] = useState("");
  const [deletingHistoryKey, setDeletingHistoryKey] = useState<string>();
  const [mobileHistoryOpen, setMobileHistoryOpen] = useState(false);
  const [activeKey, setActiveKey] = useState<string>();
  const [pendingFile, setPendingFile] = useState<PendingFile | null>(null);
  const [progress, setProgress] = useState<AgentProgress | null>(null);
  const [outcome, setOutcome] = useState<AgentOutcome | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [actionError, setActionError] = useState("");
  const [failedAction, setFailedAction] = useState<"forensics" | "provenance" | "download" | null>(null);
  const [busy, setBusy] = useState(false);
  const [forensicsBusy, setForensicsBusy] = useState(false);
  const [forensicsPreviewState, setForensicsPreviewState] = useState<"idle" | "running" | "complete" | "skipped">("idle");
  const [provenanceBusy, setProvenanceBusy] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [imageAnalysisMode, setImageAnalysisMode] = useState<ImageAnalysisMode>("fast");
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [feedbackError, setFeedbackError] = useState("");
  const [fallbackOffer, setFallbackOffer] = useState<FallbackOffer | null>(null);
  const [guestConsent, setGuestConsent] = useState(false);
  const [consentWarning, setConsentWarning] = useState(false);
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
  const retryFileRef = useRef<File | null>(null);
  const retryModeRef = useRef<ImageAnalysisMode>("fast");
  const feedbackTokenRef = useRef(0);
  const pendingSwarmFileRef = useRef<File | null>(null);
  const activeJobIdRef = useRef<string | null>(null);
  const webRequestKeysRef = useRef(new WeakMap<File, Partial<Record<ImageAnalysisMode, string>>>());

  const refreshHealth = useCallback(async () => {
    setHealthCheckState("checking");
    try {
      const value = await fetchHealth();
      setHealth(value);
      setHealthCheckState("ready");
    } catch {
      setHealth(null);
      setHealthCheckState("failed");
    }
  }, []);

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
    const failedSources = results.filter((result) => result.status === "rejected").length;
    const truncatedSources = [
      evidenceResult.status === "fulfilled" && evidenceResult.value.total > evidenceResult.value.items.length,
      imageResult.status === "fulfilled" && imageResult.value.total > imageResult.value.records.length,
      videoResult.status === "fulfilled" && videoResult.value.total > videoResult.value.records.length,
    ].filter(Boolean).length;
    if (failedSources === results.length) {
      setHistoryMessage("个人历史暂时无法读取，请稍后刷新");
    } else if (failedSources > 0) {
      setHistoryMessage(`部分记录未加载（${failedSources} 个数据源失败），当前列表不完整，请稍后刷新`);
    } else if (truncatedSources > 0) {
      setHistoryMessage("当前仅显示各数据源最近 100 条记录，较早记录尚未加载");
    }
    setHistoryLoading(false);
  }, []);

  useEffect(() => {
    let active = true;
    void refreshHealth();
    fetchCurrentUser()
      .then((response) => {
        if (!active) return;
        if (!response.authenticated || !response.user) {
          userIdRef.current = null;
          setUser(null);
          return;
        }
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
  }, [loadHistoryForUser, refreshHealth]);

  useEffect(() => {
    const syncViewFromUrl = () => setView(initialAppView());
    window.addEventListener("popstate", syncViewFromUrl);
    return () => window.removeEventListener("popstate", syncViewFromUrl);
  }, []);

  useEffect(() => {
    document.title = view === "home" ? "慧鉴AI - 数字内容鉴伪" : view === "developer" ? "开发者平台 - 慧鉴AI" : "鉴伪工作台 - 慧鉴AI";
    window.requestAnimationFrame(() => {
      const selector = view === "home" ? "#official-home-title" : view === "developer" ? ".developer-topbar h1" : ".topbar-title h1";
      document.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true });
    });
  }, [view]);

  useEffect(() => {
    const page = view === "home" ? "home" : view === "developer" ? "history" : "image";
    trackPageview(page);
  }, [view]);

  const outcomeId = outcome?.id;
  useEffect(() => {
    if (!progress && !outcomeId && !errorMessage && !fallbackOffer) return;
    window.requestAnimationFrame(() => {
      if (outcomeId) {
        resultRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
        resultRef.current?.focus({ preventScroll: true });
        return;
      }
      workspaceRef.current?.scrollTo({ top: workspaceRef.current.scrollHeight, behavior: "smooth" });
    });
  }, [errorMessage, fallbackOffer, outcomeId, progress]);

  const resetTask = useCallback(() => {
    runTokenRef.current += 1;
    detailTokenRef.current += 1;
    runControllerRef.current?.abort();
    runControllerRef.current = null;
    forensicsTokenRef.current += 1;
    feedbackTokenRef.current += 1;
    forensicsControllerRef.current?.request.abort();
    forensicsControllerRef.current?.preview.abort();
    forensicsControllerRef.current = null;
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = null;
    retryFileRef.current = null;
    setPendingFile(null);
    setProgress(null);
    setOutcome(null);
    setErrorMessage("");
    setActionError("");
    setFailedAction(null);
    setBusy(false);
    setForensicsBusy(false);
    setForensicsPreviewState("idle");
    setFeedbackBusy(false);
    setFeedbackError("");
    setFallbackOffer(null);
    setActiveKey(undefined);
    activeJobIdRef.current = null;
  }, []);

  useEffect(() => {
    const handleSessionExpired = () => {
      if (userIdRef.current == null) return;
      resetTask();
      historyTokenRef.current += 1;
      userIdRef.current = null;
      setUser(null);
      setHistory([]);
      setHistoryMessage("登录状态已过期，请重新登录后查看个人历史");
      setHistoryQuery("");
      setMobileHistoryOpen(false);
      setErrorMessage("登录状态已过期，请重新登录后继续");
      setAuthOpen(true);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, handleSessionExpired);
  }, [resetTask]);

  const navigateToView = useCallback((nextView: AppView) => {
    const url = new URL(window.location.href);
    url.searchParams.delete("workspace");
    url.searchParams.delete("developer");
    if (nextView === "workspace") {
      url.searchParams.set("workspace", "1");
      url.hash = "";
    } else if (nextView === "developer") {
      url.searchParams.set("developer", "1");
      url.hash = "";
    } else {
      url.hash = "home";
    }
    window.history.pushState({ view: nextView }, "", url);
    setView(nextView);
  }, []);

  function authenticated(nextUser: AccountUser) {
    const pendingSwarmFile = pendingSwarmFileRef.current;
    pendingSwarmFileRef.current = null;
    resetTask();
    historyTokenRef.current += 1;
    userIdRef.current = nextUser.Userid;
    setHistory([]);
    setUser(nextUser);
    setAuthOpen(false);
    setAuthReady(true);
    void loadHistoryForUser(nextUser);
    if (pendingSwarmFile) void analyzeFile(pendingSwarmFile, "swarm", nextUser);
  }

  async function logout() {
    try {
      await logoutAccount();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "退出失败，请检查网络后重试");
      return;
    }
    resetTask();
    historyTokenRef.current += 1;
    userIdRef.current = null;
    setUser(null);
    setHistory([]);
    setHistoryMessage("");
    setHistoryQuery("");
    setMobileHistoryOpen(false);
    setGuestConsent(false);
    setConsentWarning(false);
  }

  async function runImage(
    file: File,
    previewUrl: string | undefined,
    token: number,
    controller: AbortController,
    mode: ImageAnalysisMode,
    existingJobId?: string,
  ) {
    let submitted = Boolean(existingJobId);
    let terminalFailure = false;
    try {
      const keys = webRequestKeysRef.current.get(file) || {};
      const idempotencyKey = keys[mode] || globalThis.crypto.randomUUID();
      keys[mode] = idempotencyKey;
      webRequestKeysRef.current.set(file, keys);
      const started = existingJobId
        ? await fetchImageAgentJob(existingJobId, controller.signal)
        : mode === "swarm"
          ? await startImageAgent(file, idempotencyKey, controller.signal)
          : await startFastImageAgent(file, idempotencyKey, controller.signal);
      if (runTokenRef.current !== token) return;
      submitted = true;
      let job = started.job;
      activeJobIdRef.current = job.id;
      setProgress(progressFromJob(job, mode));
      const startedAt = Date.now();
      let pollDelay = AGENT_POLL_INITIAL_MS;
      let rateLimitRetries = 0;
      while (Date.now() - startedAt < 180_000) {
        if (job.status === "success") {
          const result = job.result?.result;
          if (!result) throw new Error("任务已完成，但没有返回可展示的鉴伪结果");
          setProgress({
            title: "鉴伪完成",
            detail: mode === "swarm" ? "综合结论与证据已经整理完成" : "主模型结论已经整理完成",
            percent: 100,
            stage: "report",
            experts: job.experts,
            analysisMode: mode,
          });
          setOutcome({ kind: "image", id: `image:${result.itemid}`, result, file, previewUrl, analysisMode: mode });
          activeJobIdRef.current = null;
          return;
        }
        if (job.status === "failed") {
          terminalFailure = true;
          activeJobIdRef.current = null;
          throw new Error(job.error || (mode === "swarm" ? "Swarm 复核暂不可用" : "快速检测暂不可用"));
        }
        await wait(pollDelay, controller.signal);
        let polled: Awaited<ReturnType<typeof fetchImageAgentJob>>;
        try {
          polled = await fetchImageAgentJob(job.id, controller.signal);
        } catch (error) {
          if (!isRateLimitedError(error) || rateLimitRetries >= AGENT_POLL_RATE_LIMIT_RETRIES) throw error;
          rateLimitRetries += 1;
          const cooldown = Math.max(error.retryAfterMs, Math.min(6_000, 1_800 * rateLimitRetries));
          setProgress({
            ...progressFromJob(job, mode),
            title: "任务仍在运行",
            detail: "查询较多，已自动放慢进度刷新，不会重新提交文件",
          });
          pollDelay = Math.min(AGENT_POLL_MAX_MS, pollDelay + 300);
          await wait(cooldown, controller.signal);
          continue;
        }
        if (runTokenRef.current !== token) return;
        rateLimitRetries = 0;
        pollDelay = Math.min(AGENT_POLL_MAX_MS, pollDelay + 150);
        job = polled.job;
        setProgress(progressFromJob(job, mode));
      }
      setProgress(null);
      setFallbackOffer({
        file,
        previewUrl,
        mode,
        jobId: job.id,
        submitted: true,
        reason: `服务器任务 ${job.id} 仍在运行，页面已暂停高频刷新`,
      });
      activeJobIdRef.current = null;
      return;
    } catch (error) {
      if (isAbort(error) || runTokenRef.current !== token) throw error;
      const message = error instanceof Error ? error.message : (mode === "swarm" ? "Swarm 复核暂不可用" : "快速检测暂不可用");
      if (isAuthenticationRequiredError(error) || isUploadConsentRequiredError(error)) throw error;
      if (isRateLimitedError(error)) {
        throw new Error("当前提交任务较多，请稍候几秒后重试当前文件");
      }
      setProgress(null);
      const jobId = terminalFailure ? undefined : activeJobIdRef.current || undefined;
      activeJobIdRef.current = null;
      setFallbackOffer({
        file,
        previewUrl,
        mode,
        jobId,
        submitted,
        reason: submitted
          ? (terminalFailure ? `文件已提交，但服务器处理失败：${message}` : `${message}；服务器任务 ${jobId} 可能仍在运行`)
          : message,
      });
    }
  }

  function stopWaitingForTask() {
    const jobId = activeJobIdRef.current;
    const file = retryFileRef.current;
    const previewUrl = previewUrlRef.current || undefined;
    const mode = retryModeRef.current;
    runTokenRef.current += 1;
    runControllerRef.current?.abort();
    runControllerRef.current = null;
    activeJobIdRef.current = null;
    setBusy(false);
    setProgress(null);
    if (jobId && file) {
      setFallbackOffer({
        file,
        previewUrl,
        mode,
        jobId,
        submitted: Boolean(jobId),
        reason: `已停止等待；服务器任务 ${jobId} 可能仍在运行`,
      });
      return;
    }
    setErrorMessage("已停止等待。服务器端若已接收文件，任务仍可能继续运行。当前服务暂不支持真正取消任务。");
  }

  async function analyzeFile(file: File, modeOverride = imageAnalysisMode, accountOverride?: AccountUser) {
    if (!(accountOverride || user) && !guestConsent) {
      setConsentWarning(true);
      return;
    }
    resetTask();
    retryFileRef.current = file;
    const kind = inferKind(file.name);
    if (kind === "image") {
      retryModeRef.current = modeOverride;
      setImageAnalysisMode(modeOverride);
    }
    if (kind === "unknown") {
      setPendingFile({ name: file.name, size: file.size, typeLabel: kindLabel(kind) });
      setErrorMessage("暂不支持这个文件格式。可上传 JPG、PNG、WebP、HEIC/HEIF 实况照片静态帧、MP4/MOV/WEBM 视频，以及 TXT、MD、CSV、JSON、LOG、DOCX 文档。");
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
    const previewUrl = (kind === "image" && !isHeifImage(file.name)) || kind === "video"
      ? URL.createObjectURL(file)
      : undefined;
    if (previewUrl) previewUrlRef.current = previewUrl;
    setPendingFile({
      name: file.name,
      size: file.size,
      typeLabel: kindLabel(kind),
      previewUrl: kind === "image" ? previewUrl : undefined,
      analysisMode: kind === "image" ? modeOverride : undefined,
    });
    setBusy(true);
    setErrorMessage("");
    setProgress({ title: "正在校验文件", detail: "确认格式、大小与可用检测能力", percent: 12, stage: "validate", analysisMode: kind === "image" ? modeOverride : undefined });

    try {
      if (kind === "image") {
        await runImage(file, previewUrl, token, controller, modeOverride);
      } else if (kind === "video") {
        setProgress({ title: "正在分析视频", detail: "抽取关键帧并检查时序合成线索", percent: 42, stage: "evidence" });
        const response = await detectVideoWithAgent(file, controller.signal);
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
      const historyUser = accountOverride || user;
      if (historyUser && userIdRef.current === historyUser.Userid) void loadHistoryForUser(historyUser);
    } catch (error) {
      if (isAbort(error) || runTokenRef.current !== token) return;
      const message = error instanceof Error ? error.message : "鉴伪任务未完成，请稍后重试";
      setProgress(null);
      setErrorMessage(message);
      if (isAuthenticationRequiredError(error)) setAuthOpen(true);
      if (!user && isUploadConsentRequiredError(error)) {
        setGuestConsent(false);
        setConsentWarning(true);
      }
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
    if (!user && !guestConsent) {
      setConsentWarning(true);
      return;
    }
    const file = event.dataTransfer.files?.[0];
    if (file) void analyzeFile(file);
  }

  function retryCurrentFile() {
    const file = retryFileRef.current;
    if (file) {
      void analyzeFile(file, retryModeRef.current);
      return;
    }
    fileInputRef.current?.click();
  }

  function requestFileSelection() {
    if (!user && !guestConsent) {
      setConsentWarning(true);
      return;
    }
    fileInputRef.current?.click();
  }

  async function runFallbackChain() {
    const offer = fallbackOffer;
    if (!offer || offer.jobId || busy) return;
    const controller = new AbortController();
    runControllerRef.current = controller;
    const token = ++runTokenRef.current;
    setFallbackOffer(null);
    setErrorMessage("");
    setBusy(true);
    setProgress({
      title: "正在使用备用证据链",
      detail: "已按你的选择切换；最终报告会明确标注本次检测来源",
      percent: 46,
      stage: "dispatch",
      fallback: true,
      analysisMode: offer.mode,
    });
    try {
      const result = await detect(offer.file, "image");
      if (runTokenRef.current !== token) return;
      setProgress({
        title: "鉴伪完成",
        detail: "备用模型结果与内容凭证已整理完成",
        percent: 100,
        stage: "report",
        fallback: true,
        analysisMode: offer.mode,
      });
      setOutcome({
        kind: "evidence",
        id: `evidence:${result.taskId}`,
        result,
        file: offer.file,
        previewUrl: offer.previewUrl,
        provenance: result.provenance || undefined,
        analysisMode: offer.mode,
        fallbackFromImage: true,
      });
      if (user && userIdRef.current === user.Userid) void loadHistoryForUser(user);
    } catch (error) {
      if (isAbort(error) || runTokenRef.current !== token) return;
      setProgress(null);
      setErrorMessage(error instanceof Error ? error.message : "备用证据链未完成，请稍后重试");
    } finally {
      if (runTokenRef.current === token) setBusy(false);
    }
  }

  async function resumePendingImageJob() {
    const offer = fallbackOffer;
    if (!offer?.jobId || busy) return;
    const controller = new AbortController();
    runControllerRef.current = controller;
    const token = ++runTokenRef.current;
    setFallbackOffer(null);
    setErrorMessage("");
    setBusy(true);
    setProgress({
      title: "正在继续查询原任务",
      detail: `不会重新上传文件 · ${offer.jobId}`,
      percent: 82,
      stage: "report",
      analysisMode: offer.mode,
    });
    try {
      await runImage(offer.file, offer.previewUrl, token, controller, offer.mode, offer.jobId);
    } catch (error) {
      if (!isAbort(error) && runTokenRef.current === token) {
        setProgress(null);
        setErrorMessage(error instanceof Error ? error.message : "原任务状态查询失败，请稍后再试");
      }
    } finally {
      if (runTokenRef.current === token) setBusy(false);
    }
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
    feedbackTokenRef.current += 1;
    forensicsControllerRef.current?.request.abort();
    forensicsControllerRef.current?.preview.abort();
    forensicsControllerRef.current = null;
    setForensicsBusy(false);
    setForensicsPreviewState("idle");
    setFeedbackBusy(false);
    setFeedbackError("");
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
        const analysisMode: ImageAnalysisMode = response.result.swarm?.enabled ? "swarm" : "fast";
        setOutcome({ kind: "image", id: entry.key, result: response.result, analysisMode });
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

  async function removeHistoryEntry(entry: AgentHistoryEntry) {
    if (!user || deletingHistoryKey) return;
    if (!window.confirm(`确认永久删除“${entry.title}”及其归档证据吗？此操作无法撤销。`)) return;
    setDeletingHistoryKey(entry.key);
    setHistoryMessage("");
    try {
      if (entry.origin === "evidence") await deleteHistory(entry.recordId);
      else if (entry.origin === "image") await deleteImageHistory(Number(entry.recordId));
      else await deleteVideoHistory(Number(entry.recordId));
      setHistory((current) => current.filter((item) => item.key !== entry.key));
      if (activeKey === entry.key) resetTask();
    } catch (error) {
      setHistoryMessage(error instanceof Error ? error.message : "记录删除失败，请稍后重试");
    } finally {
      setDeletingHistoryKey(undefined);
    }
  }

  async function createForensics() {
    if (!outcome?.file || forensicsBusy) return;
    const outcomeId = outcome.id;
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
    setActionError("");
    setFailedAction(null);

    const serverRequest = runForensics(file, requestController.signal, targetTaskId || undefined);
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
        setActionError("本地预览已生成，但服务端模型判读失败，请稍后重试。");
        setFailedAction("forensics");
      } else {
        setActionError(serverResult.error instanceof Error ? serverResult.error.message : "取证图谱生成失败");
        setFailedAction("forensics");
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
    setActionError("");
    setFailedAction(null);
    try {
      const report = await runProvenance(
        outcome.file,
        outcome.kind === "evidence" ? outcome.result.taskId : undefined,
      );
      setOutcome((current) => current && current.id === outcomeId && (current.kind === "image" || current.kind === "evidence") ? { ...current, provenance: report } : current);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "内容凭证验证失败");
      setFailedAction("provenance");
    } finally {
      setProvenanceBusy(false);
    }
  }

  async function downloadOutcome() {
    if (!outcome || downloadBusy) return;
    setDownloadBusy(true);
    setActionError("");
    setFailedAction(null);
    try {
      if (outcome.kind === "evidence") {
        await downloadReport(outcome.result.reportId);
      } else {
        await downloadAccountReport(outcome.kind, outcome.result.itemid);
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "报告下载失败");
      setFailedAction("download");
    } finally {
      setDownloadBusy(false);
    }
  }

  function retryFailedAction() {
    if (failedAction === "forensics") void createForensics();
    else if (failedAction === "provenance") void verifyProvenance();
    else if (failedAction === "download") void downloadOutcome();
  }

  async function recordImageFeedback(value: 1 | -1) {
    if (!outcome || outcome.kind !== "image" || feedbackBusy) return;
    const requestToken = ++feedbackTokenRef.current;
    const targetId = outcome.id;
    const itemId = outcome.result.itemid;
    const previous = outcome.result.feedback ?? null;
    const next: 1 | -1 | 0 = previous === value ? 0 : value;
    setFeedbackBusy(true);
    setFeedbackError("");
    setOutcome((current) => current?.kind === "image" && current.id === targetId
      ? { ...current, result: { ...current.result, feedback: next === 0 ? null : next } }
      : current);
    try {
      const response = await submitImageFeedback(itemId, next);
      setOutcome((current) => current?.kind === "image" && current.id === targetId
        ? { ...current, result: { ...current.result, feedback: response.feedback } }
        : current);
    } catch (error) {
      if (feedbackTokenRef.current !== requestToken) return;
      if (next !== -1) {
        setOutcome((current) => current?.kind === "image" && current.id === targetId
          ? { ...current, result: { ...current.result, feedback: previous } }
          : current);
      }
      setFeedbackError(next === -1 ? "反馈未保存，不影响重新复核" : (error instanceof Error ? error.message : "反馈暂时无法提交"));
    } finally {
      if (feedbackTokenRef.current === requestToken) setFeedbackBusy(false);
    }
  }

  function upgradeToSwarm() {
    if (!outcome || (outcome.kind !== "image" && !(outcome.kind === "evidence" && outcome.fallbackFromImage))) return;
    setFeedbackError("");
    if (!user) {
      pendingSwarmFileRef.current = outcome.file || null;
      setAuthOpen(true);
      return;
    }
    setImageAnalysisMode("swarm");
    retryModeRef.current = "swarm";
    if (outcome.file) {
      void analyzeFile(outcome.file, "swarm");
      return;
    }
    fileInputRef.current?.click();
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
          healthCheckState={healthCheckState}
          user={user}
          onEnterWorkspace={() => navigateToView("workspace")}
          onDeveloper={() => {
            navigateToView("developer");
            if (!user) setAuthOpen(true);
          }}
          onLogin={() => setAuthOpen(true)}
        />
      ) : view === "developer" ? (
        <DeveloperPlatform
          authReady={authReady}
          user={user}
          onLogin={() => setAuthOpen(true)}
          onHome={() => navigateToView("home")}
          onWorkspace={() => navigateToView("workspace")}
          onLogout={logout}
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
        onDelete={(entry) => void removeHistoryEntry(entry)}
        deletingKey={deletingHistoryKey}
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
              <h1 tabIndex={-1}><span className="desktop-task-title">{screenTitle}</span><span className="mobile-task-title">{pendingFile?.name || "慧鉴AI"}</span></h1>
              <p>{pendingFile ? "慧鉴AI 正在为这份内容整理可信证据" : "一个入口完成检测、取证、凭证核验与报告归档"}</p>
            </div>
          </div>
          <div className="topbar-actions">
            <button className={`service-pill ${healthCheckState === "checking" ? "checking" : healthCheckState === "failed" ? "limited" : serviceAvailable ? "online" : "limited"}`} type="button" onClick={() => void refreshHealth()} aria-label={healthCheckState === "checking" ? "服务检查中" : healthCheckState === "failed" ? "服务状态读取失败，点击重试" : serviceAvailable ? "检测服务可用，点击刷新" : "部分能力受限，点击刷新"} title="点击刷新服务状态">
              <i /> {healthCheckState === "checking" ? "服务检查中" : healthCheckState === "failed" ? "服务状态不可用" : serviceAvailable ? "检测服务可用" : "部分能力受限"}
            </button>
            {authReady && (user ? (
              <button type="button" className="user-pill" onClick={() => setMobileHistoryOpen(true)} aria-label={`打开${user.username || "慧鉴用户"}的个人任务`}><UserRound size={16} /><span>{user.username || "慧鉴用户"}</span></button>
            ) : (
              <button type="button" className="secondary-button topbar-login" onClick={() => setAuthOpen(true)}><LogIn size={16} /> 登录</button>
            ))}
            <button type="button" className="workspace-developer-button" onClick={() => navigateToView("developer")} title="开发者平台"><Code2 size={16} /><span>开发者</span></button>
          </div>
        </header>

        <div className="agent-workspace" ref={workspaceRef}>
          {!pendingFile && !outcome && !errorMessage && (
            <WelcomeWorkspace
              busy={busy}
              dragging={dragging}
              user={user}
              analysisMode={imageAnalysisMode}
              onAnalysisModeChange={setImageAnalysisMode}
              guestConsent={guestConsent}
              consentWarning={consentWarning}
              onGuestConsentChange={(checked) => {
                setGuestConsent(checked);
                if (checked) setConsentWarning(false);
              }}
              onOpenFile={requestFileSelection}
              onDragEnter={() => setDragging(true)}
              onDragLeave={() => setDragging(false)}
              onDrop={dropFile}
              onLogin={() => setAuthOpen(true)}
            />
          )}

          {pendingFile && (
            <div className="conversation-flow">
              <div className="user-file-message">
                <div className="file-message-copy"><span>请帮我鉴别这份内容</span><strong>{pendingFile.name}</strong><small>{pendingFile.typeLabel}{pendingFile.size ? ` · ${formatBytes(pendingFile.size)}` : " · 已归档任务"}{pendingFile.analysisMode ? <span className="pending-mode-chip">{pendingFile.analysisMode === "swarm" ? "Swarm 复核" : "快速检测"}</span> : null}</small></div>
                {pendingFile.previewUrl ? <img src={pendingFile.previewUrl} alt="待检测文件预览" /> : <span className="file-message-icon"><Paperclip size={20} /></span>}
              </div>
              {(progress || busy) && !outcome && <AgentProgressPanel progress={progress} onStopWaiting={stopWaitingForTask} />}
              {fallbackOffer && !busy && (
                <div className="fallback-choice" role="alert" aria-live="polite">
                  <span><ShieldCheck size={19} /></span>
                  <div>
                    <strong>{fallbackOffer.jobId ? "任务仍在服务器运行" : fallbackOffer.mode === "swarm" ? "Swarm 复核未完成" : "快速检测未完成"}</strong>
                    <p>{fallbackOffer.reason}。{fallbackOffer.jobId ? "继续查询不会重复提交，也不会重复扣减额度。" : fallbackOffer.submitted ? "文件已经提交到服务器，本次未形成可用结论；你可以重试原模式，或明确选择备用证据链。" : "文件尚未提交到备用模型，你可以重试原模式，或明确选择备用证据链。"}</p>
                    <div className="fallback-choice-actions">
                      {fallbackOffer.jobId ? (
                        <button type="button" className="primary-button" onClick={() => void resumePendingImageJob()}><RefreshCw size={15} /> 继续查询原任务</button>
                      ) : (
                        <>
                          <button type="button" className="secondary-button" onClick={retryCurrentFile}><RefreshCw size={15} /> 重试原模式</button>
                          <button type="button" className="primary-button" onClick={() => void runFallbackChain()}><ShieldCheck size={15} /> 使用备用证据链</button>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              )}
              {errorMessage && (
                <div className="agent-error-message" role="alert">
                  <span><Bot size={18} /></span>
                  <div><strong>这次任务没有完成</strong><p>{errorMessage}</p><button type="button" className="text-button" onClick={retryCurrentFile}><RefreshCw size={15} /> {retryFileRef.current ? "重试当前文件" : "重新选择文件"}</button></div>
                </div>
              )}
              {outcome && (
                <div ref={resultRef} className="result-anchor" role="region" aria-label="检测结果" aria-live="polite" tabIndex={-1}>
                  <AgentResult
                    outcome={outcome}
                    forensicsBusy={forensicsBusy}
                    forensicsPreviewState={forensicsPreviewState}
                    provenanceBusy={provenanceBusy}
                    downloadBusy={downloadBusy}
                    actionError={actionError}
                    onRetryAction={failedAction ? retryFailedAction : undefined}
                    onForensics={() => void createForensics()}
                    onProvenance={() => void verifyProvenance()}
                    onDownload={() => void downloadOutcome()}
                  />
                  <ResultFeedback
                    outcome={outcome}
                    submitting={feedbackBusy}
                    upgradeBusy={busy}
                    requiresLogin={!user}
                    error={feedbackError}
                    onFeedback={(value) => void recordImageFeedback(value)}
                    onUpgrade={upgradeToSwarm}
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
              <span><strong>{busy ? "小鉴正在分析，请稍候" : "继续上传新的内容"}</strong><small>图片使用{imageAnalysisMode === "swarm" ? " Swarm 复核" : "快速检测"}，视频与文档自动分流</small></span>
              <span className="composer-send"><Send size={17} /></span>
            </button>
            <p>检测结果仅作辅助判断，高风险场景请结合原始来源和人工复核。</p>
          </div>
        )}
      </main>
      </div>
      )}

      <input ref={fileInputRef} className="sr-only" type="file" accept={ACCEPTED_FILES} onChange={chooseFile} tabIndex={-1} aria-hidden="true" />
      <AuthDialog open={authOpen} onClose={() => { pendingSwarmFileRef.current = null; setAuthOpen(false); }} onAuthenticated={authenticated} />
    </>
  );
}

function WelcomeWorkspace({
  busy,
  dragging,
  user,
  guestConsent,
  consentWarning,
  analysisMode,
  onAnalysisModeChange,
  onOpenFile,
  onDragEnter,
  onDragLeave,
  onDrop,
  onLogin,
  onGuestConsentChange,
}: {
  busy: boolean;
  dragging: boolean;
  user: AccountUser | null;
  guestConsent: boolean;
  consentWarning: boolean;
  analysisMode: ImageAnalysisMode;
  onAnalysisModeChange: (mode: ImageAnalysisMode) => void;
  onOpenFile: () => void;
  onDragEnter: () => void;
  onDragLeave: () => void;
  onDrop: (event: DragEvent<HTMLElement>) => void;
  onLogin: () => void;
  onGuestConsentChange: (checked: boolean) => void;
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
          aria-disabled={busy}
          aria-label="统一鉴伪上传区域"
        >
          <div className="upload-stage-topline">
            <span><i /> 统一鉴伪入口</span>
            <small>按所选模式调度</small>
          </div>
          <AnalysisModeSwitch mode={analysisMode} disabled={busy} onChange={onAnalysisModeChange} />
          {!user && (
            <label className={`guest-upload-consent ${consentWarning ? "has-error" : ""}`}>
              <input type="checkbox" checked={guestConsent} onChange={(event) => onGuestConsentChange(event.target.checked)} />
              <span>我同意将文件上传用于本次鉴伪处理，并已阅读 <a href="/legal/terms.html" target="_blank" rel="noreferrer">用户协议</a> 与 <a href="/legal/privacy.html" target="_blank" rel="noreferrer">隐私政策</a></span>
            </label>
          )}
          {consentWarning && !user && <p className="guest-consent-warning" role="alert">请先确认文件处理与隐私授权，再选择或拖放文件。</p>}
          <button type="button" className="upload-stage-core" disabled={busy} onClick={onOpenFile}>
            <div className="upload-stage-icon"><UploadCloud size={28} /></div>
            <h3>{dragging ? "松开即可开始鉴伪" : "上传或拖放待鉴别内容"}</h3>
            <p>图片、视频或文档，会自动进入对应的分析链路</p>
            <span className="primary-button upload-button"><Paperclip size={17} /> 选择文件</span>
          </button>
          <div className="capability-strip" aria-label="支持的内容类型">
            <div><ImageIcon size={18} /><span><strong>图像</strong><small>智能鉴伪</small></span><Check size={14} /></div>
            <div><Video size={18} /><span><strong>视频</strong><small>抽帧分析</small></span><Check size={14} /></div>
            <div><FileText size={18} /><span><strong>文档</strong><small>正文检测</small></span><Check size={14} /></div>
            <div className="unavailable"><Volume2 size={18} /><span><strong>音频</strong><small>尚未部署</small></span><CircleDashed size={14} /></div>
          </div>
          <small className="upload-limits">图片支持 HEIC/HEIF 实况照片静态帧 · 图片/文档不超过 25 MB · 视频不超过 256 MB</small>
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

function AgentProgressPanel({ progress, onStopWaiting }: { progress: AgentProgress | null; onStopWaiting: () => void }) {
  const current = progress || { title: "正在准备鉴伪任务", detail: "请稍候", percent: 8, stage: "validate" as const };
  const stages = current.analysisMode === "fast" ? [
    { key: "validate", label: "文件校验" },
    { key: "dispatch", label: "模型准备" },
    { key: "evidence", label: "GPU 推理" },
    { key: "report", label: "结果校验" },
  ] as const : current.analysisMode === "swarm" ? [
    { key: "validate", label: "文件校验" },
    { key: "dispatch", label: "角色调度" },
    { key: "evidence", label: "证据核验" },
    { key: "report", label: "综合意见" },
  ] as const : [
    { key: "validate", label: "文件校验" },
    { key: "dispatch", label: "能力调度" },
    { key: "evidence", label: "证据核验" },
    { key: "report", label: "结论整理" },
  ] as const;
  const stageIndex = stages.findIndex((stage) => stage.key === current.stage);
  return (
    <div className="agent-progress-message" role="status" aria-live="polite">
      <div className="agent-avatar"><img src="/brand/huijian-mascot.webp" alt="" /></div>
      <div className="progress-panel">
        <div className="progress-heading"><span><LoaderCircle size={17} className={current.percent < 100 ? "spin" : ""} /></span><div><strong>{current.title}</strong><p>{current.detail}</p></div><b>{Math.round(current.percent)}%</b></div>
        <div className="progress-track" role="progressbar" aria-label={current.title} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(current.percent)}><i style={{ width: `${current.percent}%` }} /></div>
        <div className="progress-stages">
          {stages.map((stage, index) => <span key={stage.key} className={index < stageIndex ? "done" : index === stageIndex ? "active" : ""}><i>{index < stageIndex ? <Check size={11} /> : index + 1}</i>{stage.label}</span>)}
        </div>
        {current.experts && current.experts.length > 0 && (
          <div className="progress-experts">
            {current.experts.slice(0, 6).map((expert, index) => <span key={expert.publicId || expert.id || index} className={expert.status || "queued"}><i />{expert.publicName || `复核角色 ${index + 1}`}</span>)}
          </div>
        )}
        {current.fallback && <div className="fallback-note"><ShieldCheck size={14} /> 已切换至可用的可信检测链路，不会返回模拟结论。</div>}
        <button type="button" className="cancel-analysis-button" onClick={onStopWaiting}>停止等待</button>
        <small className="stop-waiting-note">仅停止当前页面查询；服务器任务可能继续运行。</small>
      </div>
    </div>
  );
}
