import { ChangeEvent, DragEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  LogIn,
  PanelLeftOpen,
  Paperclip,
  RefreshCw,
  Send,
  ShieldCheck,
} from "lucide-react";
import {
  AccountUser,
  ApiRequestError,
  DetectResult,
  DocumentDetectionTask,
  FileType,
  HealthStatus,
  ImageAgentJob,
  ImageHistoryRecord,
  VideoHistoryRecord,
  detect,
  cancelDocumentDetection,
  deleteHistory,
  deleteImageHistory,
  deleteVideoHistory,
  detectVideoWithAgent,
  downloadAccountReport,
  downloadReport,
  fetchCurrentUser,
  fetchDocumentDetection,
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
  runProvenance,
  SESSION_EXPIRED_EVENT,
  startImageAgent,
  startDocumentDetection,
  waitForImageAgentJob,
} from "./api";
import type { AgentHistoryEntry, AgentOutcome, AgentProgress, ImageAnalysisMode, PendingFile } from "./agentTypes";
import { binaryVerdictLabel } from "./binaryVerdict";
import { startFastImageAgent, submitImageFeedback } from "./imageInteractionApi";
import AgentHistory, { MobileHistoryButton } from "./components/AgentHistory";
import AnalysisModeSwitch from "./components/AnalysisModeSwitch";
import AccountMenu from "./components/AccountMenu";
import AgentResult from "./components/AgentResult";
import AuthDialog from "./components/AuthDialog";
import BrandArtIcon from "./components/BrandArtIcon";
import { AgentAvatar } from "./components/BrandSystem";
import DeveloperPlatform from "./components/DeveloperPlatform";
import DocumentBatchResult from "./components/DocumentBatchResult";
import HuijianBrand from "./components/HuijianBrand";
import OfficialHome from "./components/OfficialHome";
import ResultFeedback from "./components/ResultFeedback";
import {
  analyticsConsent,
  setAnalyticsConsent,
  trackPageview,
} from "./analytics";
import "./interaction.css";
import "./experience.css";
import "./c-scheme.css";

const MAX_DOCUMENT_BYTES = 25 * 1024 * 1024;
const MAX_VIDEO_BYTES = 256 * 1024 * 1024;
const AGENT_POLL_INITIAL_MS = 1_200;
const AGENT_POLL_RATE_LIMIT_RETRIES = 8;
const DOCUMENT_TASK_SESSION_KEY = "huijian-active-document-task";
const ACCEPTED_FILES = "image/jpeg,image/png,image/webp,image/bmp,image/gif,image/heic,image/heif,.heic,.heif,video/mp4,video/quicktime,video/webm,application/pdf,.txt,.md,.csv,.json,.log,.docx,.pdf,.mp4,.mov,.webm";

type UploadKind = "image" | "video" | "audio" | "document" | "unknown";
type AppView = "home" | "workspace" | "developer";
type FallbackOffer = {
  file: File;
  previewUrl?: string;
  mode: ImageAnalysisMode;
  reason: string;
  submitted: boolean;
  jobId?: string;
};

function documentSessionOwner(account: AccountUser | null) {
  return account?.account_uuid ? `account:${account.account_uuid}` : account ? `user:${account.Userid}` : "guest";
}

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
  if (["txt", "md", "csv", "json", "log", "docx", "pdf"].includes(ext)) return "document";
  return "unknown";
}

function isHeifImage(name: string) {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  return ext === "heic" || ext === "heif";
}

function extractsEmbeddedImages(name: string) {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  return ext === "pdf" || ext === "docx";
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
  return binaryVerdictLabel(verdict);
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
    verdictLabel: binaryVerdictLabel(record.final_label, score),
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
    verdictLabel: binaryVerdictLabel(record.final_label, score),
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

function isGuestLimitError(error: unknown): error is ApiRequestError {
  return error instanceof ApiRequestError
    && (error.code === "guest_detection_limit_reached" || error.code === "guest_limit_reached");
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
  const publicStage = job.publicStage || (job.status === "success" ? "report_ready" : job.status === "running" ? "authenticity_analysis" : "secure_receive");
  if (publicStage === "report_ready") {
    return { title: "鉴伪报告已经就绪", detail: "结论与关键证据已完成整理", percent: 100, stage: "report", experts: job.experts, analysisMode: mode };
  }
  if (publicStage === "evidence_summary") {
    return { title: "正在汇总关键证据", detail: mode === "swarm" ? "整理一致意见、分歧与来源线索" : "核对真实性信号、可见水印与文件信息", percent: Math.max(progress, 82), stage: "report", experts: job.experts, analysisMode: mode };
  }
  if (publicStage === "authenticity_analysis") {
    return { title: "正在核验内容真实性", detail: mode === "swarm" ? "多个证据源正在并行复核" : "正在分析生成痕迹、水印与来源线索", percent: Math.max(progress, 34), stage: "evidence", experts: job.experts, analysisMode: mode };
  }
  return { title: "文件已安全接收", detail: "正在确认格式并安排分析能力", percent: Math.min(progress, 28), stage: "validate", experts: job.experts, analysisMode: mode };
}

function progressFromDocument(task: DocumentDetectionTask): AgentProgress {
  const details: Record<string, { title: string; detail: string; stage: AgentProgress["stage"] }> = {
    queued: { title: "文档已进入队列", detail: "正在安排安全解析任务", stage: "validate" },
    validating: { title: "正在校验文档", detail: "检查格式、结构和安全限制", stage: "validate" },
    extracting: { title: "正在提取图片", detail: "解析正文、页眉、页脚与 PDF 页面资源", stage: "dispatch" },
    detecting: { title: "正在逐张检测", detail: `已完成 ${task.completed}/${task.discovered || "待确认"} 张，结果将持续出现`, stage: "evidence" },
    aggregating: { title: "正在汇总文档结论", detail: "整理真实、AI 生成与失败项目", stage: "report" },
    completed: { title: "文档检测完成", detail: `已完成 ${task.succeeded} 张图片检测`, stage: "report" },
    partial_success: { title: "文档检测部分完成", detail: `${task.succeeded} 张成功，${task.failed} 张失败`, stage: "report" },
    failed: { title: "文档检测未完成", detail: task.error || "文档解析或检测失败", stage: "report" },
    cancelled: { title: "文档检测已取消", detail: "已停止继续提交图片", stage: "report" },
  };
  const copy = details[task.stage] || details[task.status] || details.queued;
  return { ...copy, percent: Math.max(4, Math.min(Number(task.progress || 0), 100)) };
}

export default function App() {
  const [view, setView] = useState<AppView>(initialAppView);
  const [user, setUser] = useState<AccountUser | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authOpen, setAuthOpen] = useState(false);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [desktopHistoryVisible, setDesktopHistoryVisible] = useState(() => window.localStorage.getItem("huijian-history-sidebar") !== "hidden");
  const [history, setHistory] = useState<AgentHistoryEntry[]>([]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyDetailLoading, setHistoryDetailLoading] = useState(false);
  const [historyMessage, setHistoryMessage] = useState("");
  const [deletingHistoryKey, setDeletingHistoryKey] = useState<string>();
  const [mobileHistoryOpen, setMobileHistoryOpen] = useState(false);
  const [activeKey, setActiveKey] = useState<string>();
  const [pendingFile, setPendingFile] = useState<PendingFile | null>(null);
  const [progress, setProgress] = useState<AgentProgress | null>(null);
  const [outcome, setOutcome] = useState<AgentOutcome | null>(null);
  const [documentTask, setDocumentTask] = useState<DocumentDetectionTask | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [actionError, setActionError] = useState("");
  const [failedAction, setFailedAction] = useState<"provenance" | "download" | null>(null);
  const [busy, setBusy] = useState(false);
  const [provenanceBusy, setProvenanceBusy] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [imageAnalysisMode, setImageAnalysisMode] = useState<ImageAnalysisMode>("fast");
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [feedbackError, setFeedbackError] = useState("");
  const [fallbackOffer, setFallbackOffer] = useState<FallbackOffer | null>(null);
  const [guestConsent, setGuestConsent] = useState(false);
  const [consentWarning, setConsentWarning] = useState(false);
  const [guestLimitReached, setGuestLimitReached] = useState(false);
  const [analyticsEnabled, setAnalyticsEnabled] = useState(() => analyticsConsent() !== "denied");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const runControllerRef = useRef<AbortController | null>(null);
  const runTokenRef = useRef(0);
  const historyTokenRef = useRef(0);
  const detailTokenRef = useRef(0);
  const userIdRef = useRef<number | null>(null);
  const previewUrlRef = useRef<string | null>(null);
  const retryFileRef = useRef<File | null>(null);
  const retryModeRef = useRef<ImageAnalysisMode>("fast");
  const feedbackTokenRef = useRef(0);
  const pendingSwarmFileRef = useRef<File | null>(null);
  const pendingGuestFileRef = useRef<File | null>(null);
  const activeJobIdRef = useRef<string | null>(null);
  const activeDocumentTaskIdRef = useRef<string | null>(null);
  const documentTaskTokenRef = useRef("");
  const documentRestoreAttemptRef = useRef("");
  const webRequestKeysRef = useRef(new WeakMap<File, Partial<Record<ImageAnalysisMode, string>>>());
  const historyOutcomeCacheRef = useRef(new Map<string, AgentOutcome>());
  const lastTrackedPageRef = useRef<string | null>(null);

  const refreshHealth = useCallback(async () => {
    try {
      const value = await fetchHealth();
      setHealth(value);
    } catch {
      setHealth(null);
    }
  }, []);

  const setHistorySidebarVisible = useCallback((visible: boolean) => {
    setDesktopHistoryVisible(visible);
    window.localStorage.setItem("huijian-history-sidebar", visible ? "visible" : "hidden");
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
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    };
  }, [loadHistoryForUser, refreshHealth]);

  useEffect(() => {
    const syncViewFromUrl = () => setView(initialAppView());
    window.addEventListener("popstate", syncViewFromUrl);
    return () => window.removeEventListener("popstate", syncViewFromUrl);
  }, []);

  useEffect(() => {
    if (!authReady || view !== "workspace") return;
    const owner = documentSessionOwner(user);
    if (documentRestoreAttemptRef.current === owner || activeDocumentTaskIdRef.current) return;
    documentRestoreAttemptRef.current = owner;
    let saved: { id?: string; accessToken?: string; owner?: string } | null = null;
    try {
      saved = JSON.parse(window.sessionStorage.getItem(DOCUMENT_TASK_SESSION_KEY) || "null");
    } catch {
      window.sessionStorage.removeItem(DOCUMENT_TASK_SESSION_KEY);
    }
    if (!saved?.id || saved.owner !== owner) {
      if (saved && saved.owner !== owner) window.sessionStorage.removeItem(DOCUMENT_TASK_SESSION_KEY);
      return;
    }

    const controller = new AbortController();
    const token = ++runTokenRef.current;
    runControllerRef.current = controller;
    activeDocumentTaskIdRef.current = saved.id;
    documentTaskTokenRef.current = saved.accessToken || "";
    setBusy(true);
    void (async () => {
      let task = await fetchDocumentDetection(saved.id!, saved.accessToken || "", {
        limit: 100,
        signal: controller.signal,
      });
      if (runTokenRef.current !== token) return;
      setPendingFile({ name: task.filename, size: task.size, typeLabel: "文档" });
      setDocumentTask(task);
      setProgress(["queued", "running"].includes(task.status) ? progressFromDocument(task) : null);
      while (["queued", "running"].includes(task.status)) {
        task = await fetchDocumentDetection(task.id, saved.accessToken || "", {
          after: task.updatedAt,
          wait: 20,
          limit: 100,
          signal: controller.signal,
        });
        if (runTokenRef.current !== token) return;
        setDocumentTask(task);
        setProgress(progressFromDocument(task));
      }
      if (task.assetTotal > task.assets.length) {
        const assets = [...task.assets];
        for (let offset = assets.length; offset < task.assetTotal; offset += 100) {
          const page = await fetchDocumentDetection(task.id, saved.accessToken || "", {
            offset,
            limit: 100,
            signal: controller.signal,
          });
          assets.push(...page.assets);
        }
        task = { ...task, assets };
      }
      activeDocumentTaskIdRef.current = null;
      setDocumentTask(task);
      setProgress(null);
    })().catch((error) => {
      if (isAbort(error) || runTokenRef.current !== token) return;
      activeDocumentTaskIdRef.current = null;
      if (error instanceof Error && /不存在|404/.test(error.message)) {
        window.sessionStorage.removeItem(DOCUMENT_TASK_SESSION_KEY);
      }
    }).finally(() => {
      if (runTokenRef.current === token) {
        runControllerRef.current = null;
        setBusy(false);
      }
    });
    return () => controller.abort();
  }, [authReady, user, view]);

  useEffect(() => {
    document.title = view === "home" ? "慧鉴AI - 数字内容鉴伪" : view === "developer" ? "开发者平台 - 慧鉴AI" : "鉴伪工作台 - 慧鉴AI";
    window.requestAnimationFrame(() => {
      const selector = view === "home" ? "#official-home-title" : view === "developer" ? ".developer-topbar h1" : ".topbar-title h1";
      document.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true });
    });
  }, [view]);

  useEffect(() => {
    if (!analyticsEnabled || !authReady) return;
    const page = view === "home" ? "home" : view === "developer" ? "developer" : "workspace";
    const trackingKey = `${page}:${user?.Userid ?? "guest"}`;
    if (lastTrackedPageRef.current === trackingKey) return;
    const forceNew = lastTrackedPageRef.current !== null;
    lastTrackedPageRef.current = trackingKey;
    trackPageview(page, forceNew);
  }, [analyticsEnabled, authReady, user?.Userid, view]);

  const outcomeId = outcome?.id;
  const documentTaskId = documentTask?.id;
  useEffect(() => {
    if (!user || !outcome || !activeKey || outcome.id !== activeKey) return;
    historyOutcomeCacheRef.current.set(`${user.Userid}:${activeKey}`, outcome);
  }, [activeKey, outcome, user]);

  useEffect(() => {
    if (!progress && !outcomeId && !documentTaskId && !errorMessage && !fallbackOffer) return;
    window.requestAnimationFrame(() => {
      const behavior: ScrollBehavior = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
      if (outcomeId || (documentTaskId && ["completed", "partial_success", "failed"].includes(documentTask?.status || ""))) {
        resultRef.current?.scrollIntoView({ block: "start", behavior });
        resultRef.current?.focus({ preventScroll: true });
        return;
      }
      workspaceRef.current?.scrollTo({ top: workspaceRef.current.scrollHeight, behavior });
    });
  }, [documentTask?.status, documentTaskId, errorMessage, fallbackOffer, outcomeId, progress]);

  const resetTask = useCallback(() => {
    runTokenRef.current += 1;
    detailTokenRef.current += 1;
    runControllerRef.current?.abort();
    runControllerRef.current = null;
    feedbackTokenRef.current += 1;
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = null;
    retryFileRef.current = null;
    setPendingFile(null);
    setHistoryDetailLoading(false);
    setProgress(null);
    setOutcome(null);
    setDocumentTask(null);
    setErrorMessage("");
    setActionError("");
    setFailedAction(null);
    setBusy(false);
    setFeedbackBusy(false);
    setFeedbackError("");
    setFallbackOffer(null);
    setGuestLimitReached(false);
    pendingGuestFileRef.current = null;
    setActiveKey(undefined);
    activeJobIdRef.current = null;
    activeDocumentTaskIdRef.current = null;
    documentTaskTokenRef.current = "";
    window.sessionStorage.removeItem(DOCUMENT_TASK_SESSION_KEY);
  }, []);

  useEffect(() => {
    const handleSessionExpired = () => {
      if (userIdRef.current == null) return;
      resetTask();
      historyTokenRef.current += 1;
      historyOutcomeCacheRef.current.clear();
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
    url.searchParams.delete("developerTab");
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

  const navigateToDeveloper = useCallback((tab: "overview" | "tester" | "docs" = "overview") => {
    const url = new URL(window.location.href);
    url.searchParams.delete("workspace");
    url.searchParams.set("developer", "1");
    url.searchParams.set("developerTab", tab);
    url.hash = "";
    window.history.pushState({ view: "developer", developerTab: tab }, "", url);
    setView("developer");
  }, []);

  function authenticated(nextUser: AccountUser) {
    const pendingSwarmFile = pendingSwarmFileRef.current;
    const pendingGuestFile = pendingGuestFileRef.current;
    pendingSwarmFileRef.current = null;
    pendingGuestFileRef.current = null;
    resetTask();
    historyTokenRef.current += 1;
    historyOutcomeCacheRef.current.clear();
    userIdRef.current = nextUser.Userid;
    setHistory([]);
    setUser(nextUser);
    setAuthOpen(false);
    setAuthReady(true);
    void loadHistoryForUser(nextUser);
    if (pendingSwarmFile) void analyzeFile(pendingSwarmFile, "swarm", nextUser);
    else if (pendingGuestFile) void analyzeFile(pendingGuestFile, retryModeRef.current, nextUser);
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
    historyOutcomeCacheRef.current.clear();
    userIdRef.current = null;
    setUser(null);
    setHistory([]);
    setHistoryMessage("");
    setHistoryQuery("");
    setMobileHistoryOpen(false);
    setGuestConsent(false);
    setConsentWarning(false);
    setGuestLimitReached(false);
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
      let rateLimitRetries = 0;
      while (Date.now() - startedAt < 180_000) {
        if (job.status === "success") {
          const result = job.result?.result;
          if (!result) throw new Error("任务已完成，但没有返回可展示的鉴伪结果");
          setProgress({
            title: "鉴伪完成",
            detail: mode === "swarm" ? "综合结论与证据已经整理完成" : "检测结论与关键证据已经整理完成",
            percent: 100,
            stage: "report",
            experts: job.experts,
            analysisMode: mode,
          });
          setOutcome({ kind: "image", id: `image:${result.itemid}`, result, file, previewUrl, analysisMode: mode });
          if (result.visualReview && ["queued", "running"].includes(result.visualReview.status)) {
            void watchVisualReview(job.id, job.version || "", result.itemid, token, controller.signal);
          }
          activeJobIdRef.current = null;
          return;
        }
        if (job.status === "failed") {
          terminalFailure = true;
          activeJobIdRef.current = null;
          throw new Error(job.error || (mode === "swarm" ? "Swarm 模式暂不可用" : "快速检测暂不可用"));
        }
        let polled: Awaited<ReturnType<typeof fetchImageAgentJob>>;
        try {
          polled = await waitForImageAgentJob(job.id, job.version || "", controller.signal);
        } catch (error) {
          if (!isRateLimitedError(error) || rateLimitRetries >= AGENT_POLL_RATE_LIMIT_RETRIES) throw error;
          rateLimitRetries += 1;
          const cooldown = Math.max(error.retryAfterMs, Math.min(6_000, 1_800 * rateLimitRetries));
          setProgress({
            ...progressFromJob(job, mode),
            title: "任务仍在运行",
            detail: "状态连接正在自动恢复，不会重新提交文件",
          });
          await wait(cooldown, controller.signal);
          continue;
        }
        if (runTokenRef.current !== token) return;
        rateLimitRetries = 0;
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
      const message = error instanceof Error ? error.message : (mode === "swarm" ? "Swarm 模式暂不可用" : "快速检测暂不可用");
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

  async function watchVisualReview(
    jobId: string,
    initialVersion: string,
    itemId: number,
    token: number,
    signal: AbortSignal,
  ) {
    let version = initialVersion;
    const deadline = Date.now() + 180_000;
    while (Date.now() < deadline && runTokenRef.current === token && !signal.aborted) {
      try {
        const response = await waitForImageAgentJob(jobId, version, signal);
        if (runTokenRef.current !== token || signal.aborted) return;
        const nextJob = response.job;
        version = nextJob.version || version;
        const nextResult = nextJob.result?.result;
        if (!nextResult || nextResult.itemid !== itemId) continue;
        setOutcome((current) => {
          if (!current || current.kind !== "image" || current.result.itemid !== itemId) return current;
          return { ...current, result: nextResult };
        });
        const status = nextResult.visualReview?.status;
        if (!status || ["success", "failed"].includes(status)) return;
      } catch (error) {
        if (isAbort(error) || runTokenRef.current !== token) return;
        await wait(AGENT_POLL_INITIAL_MS, signal);
        try {
          const response = await fetchImageAgentJob(jobId, signal);
          version = response.job.version || version;
        } catch (fallbackError) {
          if (isAbort(fallbackError) || runTokenRef.current !== token) return;
        }
      }
    }
  }

  function stopWaitingForTask() {
    const documentTaskId = activeDocumentTaskIdRef.current;
    if (documentTaskId) {
      const accessToken = documentTaskTokenRef.current;
      runTokenRef.current += 1;
      runControllerRef.current?.abort();
      runControllerRef.current = null;
      activeDocumentTaskIdRef.current = null;
      setBusy(false);
      setProgress(null);
      void cancelDocumentDetection(documentTaskId, accessToken)
        .then((task) => setDocumentTask(task))
        .catch(() => setErrorMessage("已停止本地等待，但服务器取消请求未确认。任务状态仍可稍后查询。"));
      return;
    }
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

  async function runDocument(
    file: File,
    token: number,
    controller: AbortController,
    ownerAccount: AccountUser | null,
  ) {
    setProgress({ title: "正在安全接收文档", detail: "将提取全部可用图片，并逐张调用快速模型", percent: 8, stage: "validate" });
    let task = await startDocumentDetection(file, "fast", controller.signal);
    if (runTokenRef.current !== token) return;
    const accessToken = task.accessToken || "";
    documentTaskTokenRef.current = accessToken;
    activeDocumentTaskIdRef.current = task.id;
    window.sessionStorage.setItem(DOCUMENT_TASK_SESSION_KEY, JSON.stringify({
      id: task.id,
      accessToken,
      owner: documentSessionOwner(ownerAccount),
    }));
    setDocumentTask(task);
    setProgress(progressFromDocument(task));

    while (["queued", "running"].includes(task.status)) {
      task = await fetchDocumentDetection(task.id, accessToken, {
        after: task.updatedAt,
        wait: 20,
        limit: 100,
        signal: controller.signal,
      });
      if (runTokenRef.current !== token) return;
      setDocumentTask(task);
      setProgress(progressFromDocument(task));
    }

    if (task.assetTotal > task.assets.length) {
      const assets = [...task.assets];
      for (let offset = assets.length; offset < task.assetTotal; offset += 100) {
        const page = await fetchDocumentDetection(task.id, accessToken, {
          offset,
          limit: 100,
          signal: controller.signal,
        });
        assets.push(...page.assets);
      }
      task = { ...task, assets };
    }
    activeDocumentTaskIdRef.current = null;
    setDocumentTask(task);
    setProgress(null);
    if (task.status === "failed") throw new Error(task.error || "文档图片检测失败");
    if (task.status === "cancelled") throw new Error("文档检测已取消");
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
      setErrorMessage("暂不支持这个文件格式。可上传常见图片和手机实况照片、MP4/MOV/WEBM 视频，以及 PDF、TXT、MD、CSV、JSON、LOG、DOCX 文档。");
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
    setProgress({ title: "正在安全接收文件", detail: "确认格式、大小与处理授权", percent: 12, stage: "validate", analysisMode: kind === "image" ? modeOverride : undefined });

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
        if (extractsEmbeddedImages(file.name)) {
          await runDocument(file, token, controller, accountOverride || user);
        } else {
          setProgress({ title: "正在分析文档", detail: "提取正文并检查生成式写作线索", percent: 48, stage: "evidence" });
          const result = await detect(file, "document", controller.signal);
          if (runTokenRef.current !== token) return;
          setProgress({ title: "鉴伪完成", detail: "文本结论与证据维度已经整理完成", percent: 100, stage: "report" });
          setOutcome({ kind: "evidence", id: `evidence:${result.taskId}`, result, file });
        }
      }
      const historyUser = accountOverride || user;
      if (historyUser && userIdRef.current === historyUser.Userid) void loadHistoryForUser(historyUser);
    } catch (error) {
      if (isAbort(error) || runTokenRef.current !== token) return;
      setProgress(null);
      if (!user && isGuestLimitError(error)) {
        pendingGuestFileRef.current = file;
        setPendingFile(null);
        setFallbackOffer(null);
        setErrorMessage("");
        setGuestLimitReached(true);
      } else {
        const message = error instanceof Error ? error.message : "鉴伪任务未完成，请稍后重试";
        setErrorMessage(message);
        if (isAuthenticationRequiredError(error)) setAuthOpen(true);
      }
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
    const cacheKey = `${expectedUserId}:${entry.key}`;
    const cachedOutcome = historyOutcomeCacheRef.current.get(cacheKey);
    runControllerRef.current?.abort();
    feedbackTokenRef.current += 1;
    setFeedbackBusy(false);
    setFeedbackError("");
    setBusy(false);
    setMobileHistoryOpen(false);
    setActiveKey(entry.key);
    setPendingFile({ name: entry.title, size: 0, typeLabel: entry.typeLabel, previewUrl: entry.thumbnail || undefined });
    setErrorMessage("");
    setProgress(null);
    setFallbackOffer(null);
    setHistoryDetailLoading(false);
    if (cachedOutcome) {
      setOutcome(cachedOutcome);
      return;
    }
    setOutcome(null);
    setHistoryDetailLoading(true);
    try {
      let nextOutcome: AgentOutcome;
      if (entry.origin === "image") {
        const response = await fetchImageAgentResult(Number(entry.recordId));
        if (detailTokenRef.current !== requestToken || userIdRef.current !== expectedUserId) return;
        const analysisMode: ImageAnalysisMode = response.result.swarm?.enabled ? "swarm" : "fast";
        nextOutcome = { kind: "image", id: entry.key, result: response.result, analysisMode };
      } else if (entry.origin === "video") {
        const response = await fetchVideoAgentResult(Number(entry.recordId));
        if (detailTokenRef.current !== requestToken || userIdRef.current !== expectedUserId) return;
        nextOutcome = { kind: "video", id: entry.key, result: response.result };
      } else {
        const result = await fetchHistoryItem(entry.recordId);
        if (detailTokenRef.current !== requestToken || userIdRef.current !== expectedUserId) return;
        nextOutcome = { kind: "evidence", id: entry.key, result, provenance: result.provenance || undefined };
      }
      historyOutcomeCacheRef.current.set(cacheKey, nextOutcome);
      setOutcome(nextOutcome);
    } catch (error) {
      if (detailTokenRef.current !== requestToken || userIdRef.current !== expectedUserId) return;
      setErrorMessage(error instanceof Error ? error.message : "历史任务暂时无法读取");
    } finally {
      if (detailTokenRef.current === requestToken && userIdRef.current === expectedUserId) setHistoryDetailLoading(false);
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
      historyOutcomeCacheRef.current.delete(`${user.Userid}:${entry.key}`);
      setHistory((current) => current.filter((item) => item.key !== entry.key));
      if (activeKey === entry.key) resetTask();
    } catch (error) {
      setHistoryMessage(error instanceof Error ? error.message : "记录删除失败，请稍后重试");
    } finally {
      setDeletingHistoryKey(undefined);
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
    if (failedAction === "provenance") void verifyProvenance();
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

  const screenTitle = pendingFile?.name || "新建鉴伪任务";
  const historyAvailable = Boolean(user);
  const historyLayoutClass = !historyAvailable
    ? "history-unavailable"
    : desktopHistoryVisible
      ? ""
      : "history-collapsed";

  return (
    <>
      {view === "home" ? (
        <OfficialHome
          authReady={authReady}
          user={user}
          analyticsEnabled={analyticsEnabled}
          onEnterWorkspace={() => navigateToView("workspace")}
          onDeveloper={(entry) => {
            navigateToDeveloper(entry);
            if (!user) setAuthOpen(true);
          }}
          onLogin={() => setAuthOpen(true)}
          onLogout={() => void logout()}
          onToggleAnalytics={() => {
            const next = !analyticsEnabled;
            setAnalyticsConsent(next ? "granted" : "denied");
            lastTrackedPageRef.current = null;
            setAnalyticsEnabled(next);
          }}
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
      <div className={`agent-app ${historyLayoutClass}`}>
      {historyAvailable && <AgentHistory
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
        onRetry={() => user && void loadHistoryForUser(user)}
        deletingKey={deletingHistoryKey}
        onNew={resetTask}
        onCollapse={() => setHistorySidebarVisible(false)}
        onHome={() => navigateToView("home")}
        onLogin={() => setAuthOpen(true)}
        onLogout={logout}
        onCloseMobile={() => setMobileHistoryOpen(false)}
      />}

      <main className="agent-main">
        <header className="agent-topbar">
          <div className="topbar-title">
            {historyAvailable && <MobileHistoryButton onClick={() => setMobileHistoryOpen(true)} />}
            {historyAvailable && !desktopHistoryVisible && (
              <button type="button" className="icon-button desktop-history-open" onClick={() => setHistorySidebarVisible(true)} aria-label="显示最近任务" title="显示最近任务">
                <PanelLeftOpen size={19} />
              </button>
            )}
            <HuijianBrand compact onClick={() => navigateToView("home")} />
            <AnalysisModeSwitch mode={imageAnalysisMode} disabled={busy || historyDetailLoading} onChange={setImageAnalysisMode} />
            <div>
              <h1 tabIndex={-1}><span className="desktop-task-title">{screenTitle}</span><span className="mobile-task-title">{pendingFile?.name || "慧鉴AI"}</span></h1>
              <p>{pendingFile ? "慧鉴AI 正在为这份内容整理可信证据" : "一个入口完成检测、取证、凭证核验与报告归档"}</p>
            </div>
          </div>
          <div className="topbar-actions">
            {authReady && (user ? (
              <AccountMenu compact user={user} onWorkspace={() => setMobileHistoryOpen(true)} onDeveloper={() => navigateToDeveloper("overview")} onLogout={() => void logout()} className="workspace-account-menu" />
            ) : (
              <button type="button" className="secondary-button topbar-login" onClick={() => setAuthOpen(true)}><LogIn size={16} /> 登录</button>
            ))}
            <button type="button" className="workspace-developer-button" onClick={() => navigateToDeveloper("overview")} title="开发者平台">
              <img className="workspace-developer-artwork" src="/brand/huijian-developer-gpt.webp" width={256} height={256} alt="" aria-hidden="true" draggable={false} />
              <span>开发者</span>
            </button>
          </div>
        </header>

        <div className="agent-workspace" ref={workspaceRef}>
          {guestLimitReached && !user && (
            <GuestLimitGate fileName={pendingGuestFileRef.current?.name} onLogin={() => setAuthOpen(true)} />
          )}

          {!guestLimitReached && !pendingFile && !outcome && !documentTask && !errorMessage && (
            <WelcomeWorkspace
              busy={busy}
              dragging={dragging}
              user={user}
              guestConsent={guestConsent}
              consentWarning={consentWarning}
              maxUploadBytes={Number(health?.limits?.maxUploadBytes || MAX_DOCUMENT_BYTES)}
              onGuestConsentChange={(checked) => {
                setGuestConsent(checked);
                if (checked) setConsentWarning(false);
              }}
              onOpenFile={requestFileSelection}
              onDragEnter={() => setDragging(true)}
              onDragLeave={() => setDragging(false)}
              onDrop={dropFile}
            />
          )}

          {!guestLimitReached && !pendingFile && !outcome && !documentTask && errorMessage && (
            <div className="agent-error-message workspace-error-state" role="alert">
              <span><AgentAvatar size={34} state="error" label="小鉴提示工作台连接异常" /></span>
              <div>
                <strong>工作台需要重新连接</strong>
                <p>{errorMessage}</p>
                <div className="workspace-error-actions">
                  {!user && <button type="button" className="primary-button" onClick={() => setAuthOpen(true)}><LogIn size={15} /> 重新登录</button>}
                  <button type="button" className="secondary-button" onClick={requestFileSelection}><Paperclip size={15} /> 选择新的内容</button>
                </div>
              </div>
            </div>
          )}

          {pendingFile && (
            <div className="conversation-flow">
              <div className="user-file-message">
                <div className="file-message-copy"><span>请帮我鉴别这份内容</span><strong>{pendingFile.name}</strong><small>{pendingFile.typeLabel}{pendingFile.size ? ` · ${formatBytes(pendingFile.size)}` : " · 已归档任务"}{pendingFile.analysisMode ? <span className="pending-mode-chip">{pendingFile.analysisMode === "swarm" ? "Swarm 模式" : "快速检测"}</span> : null}</small></div>
                {pendingFile.previewUrl ? <img src={pendingFile.previewUrl} alt="待检测文件预览" /> : <span className="file-message-icon"><Paperclip size={20} /></span>}
              </div>
              {(progress || busy) && !outcome && <AgentProgressPanel progress={progress} onStopWaiting={stopWaitingForTask} />}
              {historyDetailLoading && !outcome && (
                <AgentProgressPanel progress={{ title: "正在打开历史记录", detail: "读取已归档结论与证据，不会重新检测", percent: 72, stage: "report" }} />
              )}
              {fallbackOffer && !busy && (
                <div className="fallback-choice" role="alert" aria-live="polite">
                  <span><ShieldCheck size={19} /></span>
                  <div>
                    <strong>{fallbackOffer.jobId ? "任务仍在服务器运行" : fallbackOffer.mode === "swarm" ? "Swarm 模式未完成" : "快速检测未完成"}</strong>
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
                  <span><AgentAvatar size={34} state="error" label="小鉴提示任务异常" /></span>
                  <div><strong>这次任务没有完成</strong><p>{errorMessage}</p><button type="button" className="text-button" onClick={retryCurrentFile}><RefreshCw size={15} /> {retryFileRef.current ? "重试当前文件" : "重新选择文件"}</button></div>
                </div>
              )}
              {outcome && (
                <div ref={resultRef} className="result-anchor" role="region" aria-label="检测结果" aria-live="polite" tabIndex={-1}>
                  <AgentResult
                    outcome={outcome}
                    provenanceBusy={provenanceBusy}
                    downloadBusy={downloadBusy}
                    actionError={actionError}
                    onRetryAction={failedAction ? retryFailedAction : undefined}
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
              {documentTask && (
                <div ref={outcome ? undefined : resultRef} className="result-anchor" role="region" aria-label="文档图片检测结果" aria-live="polite" tabIndex={-1}>
                  <DocumentBatchResult task={documentTask} />
                </div>
              )}
            </div>
          )}
        </div>

        {(pendingFile || outcome || documentTask) && (
          <div className="composer-dock">
            <button type="button" className="composer-compact" disabled={busy} onClick={() => fileInputRef.current?.click()}>
              <span className="composer-attach"><Paperclip size={18} /></span>
              <span><strong>{busy ? "小鉴正在分析，请稍候" : "继续上传新的内容"}</strong><small>图片使用{imageAnalysisMode === "swarm" ? " Swarm 模式" : "快速检测"}，视频与文档自动分流</small></span>
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

function GuestLimitGate({ fileName, onLogin }: { fileName?: string; onLogin: () => void }) {
  return (
    <section className="guest-limit-gate" aria-labelledby="guest-limit-title">
      <div className="guest-limit-visual" aria-hidden="true">
        <AgentAvatar size={68} state="idle" />
        <span><ShieldCheck size={18} /></span>
      </div>
      <p>访客体验已完成</p>
      <h2 id="guest-limit-title">登录后继续这次鉴伪</h2>
      <span>每位访客可免费体验一次。登录后，你可以继续处理{fileName ? `“${fileName}”` : "刚才选择的文件"}，并保存个人历史与报告。</span>
      <div className="guest-limit-benefits" aria-label="登录后的能力">
        <b><Check size={14} /> 继续当前文件</b>
        <b><Check size={14} /> 保存鉴伪记录</b>
        <b><Check size={14} /> 下载完整报告</b>
      </div>
      <button type="button" className="primary-button" onClick={onLogin}><LogIn size={17} /> 登录或注册后继续</button>
      <small>登录成功后将自动恢复当前任务，无需再次选择文件。</small>
    </section>
  );
}

function WelcomeWorkspace({
  busy,
  dragging,
  user,
  guestConsent,
  consentWarning,
  maxUploadBytes,
  onOpenFile,
  onDragEnter,
  onDragLeave,
  onDrop,
  onGuestConsentChange,
}: {
  busy: boolean;
  dragging: boolean;
  user: AccountUser | null;
  guestConsent: boolean;
  consentWarning: boolean;
  maxUploadBytes: number;
  onOpenFile: () => void;
  onDragEnter: () => void;
  onDragLeave: () => void;
  onDrop: (event: DragEvent<HTMLElement>) => void;
  onGuestConsentChange: (checked: boolean) => void;
}) {
  return (
    <div className="welcome-page">
      <section className="welcome-workspace">
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
          <button type="button" className="upload-stage-core" disabled={busy} onClick={onOpenFile}>
            <div className="upload-stage-icon"><AgentAvatar size={88} state={dragging ? "receiving" : "idle"} label="小鉴文件接收入口" /></div>
            <h3>{dragging ? "松开即可开始鉴伪" : "上传或拖放待鉴别内容"}</h3>
            <p>图片、视频和文档将自动进入对应证据链路</p>
            <span className="primary-button upload-button"><Paperclip size={17} /> 选择文件</span>
          </button>
          <div className="capability-strip compact-capability-strip" aria-label="支持的内容类型">
            <div><BrandArtIcon name="image" /><span><strong>图片</strong><small>真假与水印</small></span></div>
            <div><BrandArtIcon name="video" /><span><strong>视频</strong><small>关键帧分析</small></span></div>
            <div><BrandArtIcon name="document" /><span><strong>PDF / Word</strong><small>提图逐张检测</small></span></div>
          </div>
          <div className="upload-policy-footer">
            {!user && (
              <label className={`guest-upload-consent ${consentWarning ? "has-error" : ""}`}>
                <input type="checkbox" checked={guestConsent} onChange={(event) => onGuestConsentChange(event.target.checked)} />
                <span>我授权平台处理本次上传文件，并已阅读 <a href="/legal/terms.html" target="_blank" rel="noreferrer">用户协议</a> 与 <a href="/legal/privacy.html" target="_blank" rel="noreferrer">隐私政策</a></span>
              </label>
            )}
            {consentWarning && !user && <p className="guest-consent-warning" role="alert">勾选授权后即可选择或拖放文件。</p>}
            <small className="upload-limits">支持手机实况照片、PDF 与 DOCX 图片提取 · 图片与文档最高 {formatBytes(maxUploadBytes)} · 视频最高 {formatBytes(MAX_VIDEO_BYTES)}</small>
          </div>
        </section>
      </section>
    </div>
  );
}

function AgentProgressPanel({ progress, onStopWaiting }: { progress: AgentProgress | null; onStopWaiting?: () => void }) {
  const current = progress || { title: "正在准备鉴伪任务", detail: "请稍候", percent: 8, stage: "validate" as const };
  const stages = [
    { key: "receive", label: "安全接收", note: "格式与权限", icon: "document" as const },
    { key: "analyze", label: "真实性分析", note: current.analysisMode === "swarm" ? "多源并行复核" : "痕迹与水印", icon: current.analysisMode === "swarm" ? "swarm" as const : "fast" as const },
    { key: "report", label: "证据成稿", note: "结论与依据", icon: "report" as const },
  ] as const;
  const stageIndex = current.stage === "report" ? 2 : current.stage === "evidence" ? 1 : 0;
  return (
    <div className="agent-progress-message" role="status" aria-live="polite">
      <div className="agent-avatar"><AgentAvatar size={40} state={current.percent >= 100 ? "complete" : current.stage === "validate" ? "receiving" : "processing"} /></div>
      <div className="progress-panel">
        <div className="progress-heading">
          <span className={`progress-scan-artwork ${current.percent >= 100 ? "is-complete" : "is-active"}`}>
            <img src="/brand/huijian-progress-scan-gpt.webp" width={256} height={256} alt="" aria-hidden="true" draggable={false} />
          </span>
          <div><strong>{current.title}</strong><p>{current.detail}</p></div>
          <b>{Math.round(current.percent)}%</b>
        </div>
        <div className="progress-track" role="progressbar" aria-label={current.title} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(current.percent)}><i style={{ width: `${current.percent}%` }} /></div>
        <div className="progress-stages progress-system">
          {stages.map((stage, index) => {
            return (
              <span key={stage.key} className={index < stageIndex ? "done" : index === stageIndex ? "active" : ""}>
                <i>{index < stageIndex ? <Check size={16} /> : <BrandArtIcon name={stage.icon} />}</i>
                <b>{stage.label}</b>
                <small>{stage.note}</small>
              </span>
            );
          })}
        </div>
        {current.experts && current.experts.length > 0 && (
          <div className="progress-experts">
            {current.experts.slice(0, 6).map((expert, index) => <span key={expert.publicId || expert.id || index} className={expert.status || "queued"}><i />{expert.publicName || `复核角色 ${index + 1}`}</span>)}
          </div>
        )}
        {current.fallback && <div className="fallback-note"><ShieldCheck size={14} /> 已切换至可用的可信检测链路，不会返回模拟结论。</div>}
        {onStopWaiting && <button type="button" className="cancel-analysis-button" onClick={onStopWaiting}>停止等待</button>}
        {onStopWaiting && <small className="stop-waiting-note">仅停止当前页面查询；服务器任务可能继续运行。</small>}
      </div>
    </div>
  );
}
