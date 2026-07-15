import { useCallback, useEffect, useRef, useState } from "react";
import {
  DetectResult,
  HistoryFilterCounts,
  HealthStatus,
  HistoryItem,
  HistorySidebarFilter,
  FileType,
  ForensicReport,
  ProvenanceReport,
  detect,
  fetchHealth,
  runForensics,
  runProvenance,
  fetchHistory,
  fetchHistoryItem,
  deleteHistory,
  persistArtifacts,
} from "./api";
import { getInitialHistoryFilter, getInitialHistoryQuery } from "./historyParams";
import Sidebar from "./components/Sidebar";
import ResultCard from "./components/ResultCard";
import ForensicGallery from "./components/ForensicGallery";
import IconfontIcon from "./components/IconfontIcon";
import Logo from "./components/Logo";
import AdminDashboard from "./components/AdminDashboard";

type Message =
  | { kind: "user"; text: string; fileName: string; previewUrl?: string }
  | { kind: "progress"; stage: number }
  | { kind: "result"; result: DetectResult; previewUrl?: string; file?: File }
  | { kind: "loading"; text: string }
  | { kind: "forensics"; report: ForensicReport };

const PROGRESS_STEPS = ["文件已校验", "服务端模型与证据分析进行中"];
const AVAILABLE_CAPABILITIES = ["图像综合鉴伪", "TXT / MD / DOCX 文本检测"];
const HISTORY_PAGE_SIZE = 100;
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
const REVIEWER_HISTORY_FILTERS = new Set<HistorySidebarFilter>([
  "all",
  "real",
  "suspected",
  "highly",
  "unknownVerdict",
  "forensics",
  "provenance",
  "synthid",
  "watermark",
]);

function inferType(name: string): FileType {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (["mp4", "mov", "avi", "mkv", "webm"].includes(ext)) return "video";
  if (["mp3", "wav", "m4a", "flac", "aac"].includes(ext)) return "audio";
  if (["txt", "pdf", "doc", "docx", "md", "csv", "json", "log"].includes(ext)) return "document";
  return "image";
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size}B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)}KB`;
  return `${(size / (1024 * 1024)).toFixed(1)}MB`;
}

function formatCapabilitySummary(health: HealthStatus | null): string {
  if (!health) return "检测服务状态待确认";
  const caps = health?.capabilities || {};
  const available = [caps.image === "available" ? "图像" : "", ["available", "limited"].includes(caps.document || "") ? "文档文本" : ""].filter(Boolean);
  return available.length > 0 ? `${available.join(" / ")}检测可用` : "检测模型暂不可用";
}

function isAuthRequiredMessage(message: string): boolean {
  const normalized = message.toLowerCase();
  return message.includes("请登录") || message.includes("请先登录") || message.includes("认证") || message.includes("权限") || normalized.includes("unauthorized") || normalized.includes("forbidden");
}

function friendlyMessage(message: string, fallback: string): string {
  const text = message.trim();
  if (!text) return fallback;
  if (text.includes("继续使用 V2") || text.includes("继续使用深度分析")) return "请登录后继续检测";
  if (isAuthRequiredMessage(text)) return "请登录后继续检测";
  if (text.includes("Unexpected end of JSON")) return fallback;
  return text;
}

function getInitialReviewerHistoryFilter(): HistorySidebarFilter {
  const filter = getInitialHistoryFilter();
  return REVIEWER_HISTORY_FILTERS.has(filter) ? filter : "all";
}

export default function App() {
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyFilterCounts, setHistoryFilterCounts] = useState<HistoryFilterCounts>({
    all: 0,
    vlm: 0,
    mock: 0,
    "maps-only": 0,
    unknown: 0,
    real: 0,
    suspected: 0,
    highly: 0,
    unknownVerdict: 0,
    cache: 0,
    forensics: 0,
    provenance: 0,
    synthid: 0,
    watermark: 0,
  });
  const [historyMessage, setHistoryMessage] = useState("");
  const [historyBusy, setHistoryBusy] = useState(false);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [monitorReloadKey] = useState(0);
  const [forensicsByTask, setForensicsByTask] = useState<Record<string, ForensicReport>>({});
  const [provenanceByTask, setProvenanceByTask] = useState<Record<string, ProvenanceReport>>({});
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [forensicsBusy, setForensicsBusy] = useState(false);
  const [provenanceBusy, setProvenanceBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyQuery, setHistoryQuery] = useState(() => getInitialHistoryQuery());
  const [historyFilter, setHistoryFilter] = useState<HistorySidebarFilter>(() => getInitialReviewerHistoryFilter());
  const [historyLimit, setHistoryLimit] = useState(HISTORY_PAGE_SIZE);
  const [view, setView] = useState<"detect" | "monitor">("detect");
  const [activeId, setActiveId] = useState<string>();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const restoredHistoryItemRef = useRef(false);
  const historyLengthRef = useRef(0);
  const historyLimitRef = useRef(HISTORY_PAGE_SIZE);
  const historyRequestIdRef = useRef(0);
  const historyDetailRequestIdRef = useRef(0);

  useEffect(() => {
    historyLengthRef.current = history.length;
  }, [history.length]);

  useEffect(() => {
    historyLimitRef.current = historyLimit;
  }, [historyLimit]);

  const loadHealth = useCallback(() =>
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null)), []);

  const loadHistory = useCallback(async ({
    preserveOnError = historyLengthRef.current > 0,
    append = false,
    reset = false,
  }: {
    preserveOnError?: boolean;
    append?: boolean;
    reset?: boolean;
  } = {}) => {
    const requestId = historyRequestIdRef.current + 1;
    historyRequestIdRef.current = requestId;
    setHistoryBusy(true);
    try {
      const offset = append ? historyLengthRef.current : 0;
      const limit = append ? HISTORY_PAGE_SIZE : reset ? HISTORY_PAGE_SIZE : historyLimitRef.current;
      const data = await fetchHistory({ query: historyQuery, filter: historyFilter, limit, offset });
      if (historyRequestIdRef.current !== requestId) return;
      if (append) {
        setHistory((current) => {
          const seen = new Set(current.map((item) => item.taskId));
          return current.concat(data.items.filter((item) => !seen.has(item.taskId)));
        });
      } else {
        setHistory(data.items);
      }
      setHistoryTotal(data.total);
      setHistoryFilterCounts(data.filterCounts);
      setHistoryMessage("");
    } catch (error) {
      if (historyRequestIdRef.current !== requestId) return;
      if (!preserveOnError) {
        setHistory([]);
        setHistoryTotal(0);
        setHistoryFilterCounts({ all: 0, vlm: 0, mock: 0, "maps-only": 0, unknown: 0, real: 0, suspected: 0, highly: 0, unknownVerdict: 0, cache: 0, forensics: 0, provenance: 0, synthid: 0, watermark: 0 });
      }
      setHistoryMessage(friendlyMessage(error instanceof Error ? error.message : "", "历史记录暂不可用"));
    } finally {
      if (historyRequestIdRef.current === requestId) {
        setHistoryBusy(false);
      }
    }
  }, [historyFilter, historyQuery]);

  const showHistoryResult = useCallback((result: DetectResult) => {
    if (result.forensics) {
      setForensicsByTask((prev) => ({ ...prev, [result.taskId]: result.forensics! }));
    }
    if (result.provenance) {
      setProvenanceByTask((prev) => ({ ...prev, [result.taskId]: result.provenance! }));
    }
    const nextMessages: Message[] = [{ kind: "result", result }];
    if (result.forensics) {
      nextMessages.push({ kind: "forensics", report: result.forensics });
    }
    setMessages(nextMessages);
    setActiveId(result.taskId);
  }, []);

  const openHistoryItem = useCallback(async (itemId: string) => {
    const normalized = itemId.trim();
    if (!normalized) return;
    setActiveId(normalized);
    const requestId = historyDetailRequestIdRef.current + 1;
    historyDetailRequestIdRef.current = requestId;
    setMessages([{ kind: "loading", text: "正在加载历史详情…" }]);
    try {
      const result: DetectResult = await fetchHistoryItem(normalized);
      if (historyDetailRequestIdRef.current !== requestId) return;
      showHistoryResult(result);
    } catch (error) {
      if (historyDetailRequestIdRef.current !== requestId) return;
      setMessages([
        {
          kind: "user",
          text: `历史详情加载失败：${friendlyMessage(error instanceof Error ? error.message : "", "历史详情暂不可用")}`,
          fileName: "",
        },
      ]);
    }
  }, [showHistoryResult]);

  useEffect(() => {
    loadHealth();
  }, [loadHealth]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (historyQuery.trim()) params.set("historyQuery", historyQuery.trim());
    else params.delete("historyQuery");
    if (historyFilter !== "all") params.set("historyFilter", historyFilter);
    else params.delete("historyFilter");
    const next = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`);
  }, [historyFilter, historyQuery]);

  useEffect(() => {
    void loadHistory({ preserveOnError: false, reset: true });
  }, [historyFilter, historyQuery, loadHistory]);

  useEffect(() => {
    setHistoryLimit(HISTORY_PAGE_SIZE);
  }, [historyFilter, historyQuery]);

  useEffect(() => {
    if (restoredHistoryItemRef.current) return;
    restoredHistoryItemRef.current = true;
    const initialHistoryItem = getInitialHistoryItem();
    if (initialHistoryItem) void openHistoryItem(initialHistoryItem);
  }, [openHistoryItem]);

  useEffect(() => {
    const onHash = () => {
      if (window.location.hash === "#monitor") {
        window.history.replaceState({}, "", `${window.location.pathname}${window.location.search}`);
      }
      setView("detect");
    };
    onHash();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!activeId && !restoredHistoryItemRef.current) return;
    const params = new URLSearchParams(window.location.search);
    if (activeId) params.set("historyItem", activeId);
    else params.delete("historyItem");
    const next = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`);
  }, [activeId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const runDetect = async (file: File) => {
    const type = inferType(file.name);
    const previewUrl = type === "image" ? URL.createObjectURL(file) : undefined;

    setMessages((m) => [
      ...m,
      { kind: "progress", stage: 0 },
    ]);
    setBusy(true);

    try {
      setMessages((m) => m.map((msg) => (msg.kind === "progress" ? { ...msg, stage: 1 } : msg)));
      const result = await detect(file, type);
      if (result.provenance) {
        setProvenanceByTask((prev) => ({ ...prev, [result.taskId]: result.provenance! }));
      }
      setMessages((m) => [
        ...m.filter((msg) => msg.kind !== "progress"),
        { kind: "result", result, previewUrl, file: type === "image" ? file : undefined },
      ]);
      setActiveId(result.taskId);
      void loadHistory({ preserveOnError: true });
    } catch (e) {
      setMessages((m) => [
        ...m.filter((msg) => msg.kind !== "progress"),
        {
          kind: "user",
          text: `检测失败：${friendlyMessage(e instanceof Error ? e.message : "", "检测暂未完成，请稍后重试")}`,
          fileName: "",
        },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const onForensics = async (file: File, taskId: string) => {
    if (forensicsBusy) return;
    setForensicsBusy(true);
    setMessages((m) => [
      ...m,
      { kind: "loading", text: "正在生成 7 项取证可视化证据并逐项判读…" },
    ]);
    try {
      const report = await runForensics(file);
      setForensicsByTask((prev) => ({ ...prev, [taskId]: report }));
      try {
        await persistArtifacts(taskId, { forensics: report });
      } catch {
        // Keep the current analysis result visible even if persistence fails.
      }
      setMessages((m) => [
        ...m.filter((msg) => msg.kind !== "loading"),
        { kind: "forensics", report },
      ]);
    } catch (e) {
      setMessages((m) => [
        ...m.filter((msg) => msg.kind !== "loading"),
        { kind: "user", text: `取证分析失败：${friendlyMessage(e instanceof Error ? e.message : "", "取证分析暂未完成，请稍后重试")}`, fileName: "" },
      ]);
    } finally {
      setForensicsBusy(false);
    }
  };

  const onProvenance = async (file: File, taskId: string) => {
    if (provenanceBusy) return;
    setProvenanceBusy(true);
    setMessages((m) => [
      ...m,
      { kind: "loading", text: "正在核验内容凭证与文件信息…" },
    ]);
    try {
      const report = await runProvenance(file);
      setProvenanceByTask((prev) => ({ ...prev, [taskId]: report }));
      try {
        await persistArtifacts(taskId, { provenance: report });
      } catch {
        // Keep the current analysis result visible even if persistence fails.
      }
      setMessages((m) => m.filter((msg) => msg.kind !== "loading"));
    } catch (e) {
      setMessages((m) => [
        ...m.filter((msg) => msg.kind !== "loading"),
        { kind: "user", text: `凭证验证失败：${friendlyMessage(e instanceof Error ? e.message : "", "凭证验证暂未完成，请稍后重试")}`, fileName: "" },
      ]);
    } finally {
      setProvenanceBusy(false);
    }
  };

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      const type = inferType(f.name);
      const ext = f.name.split(".").pop()?.toLowerCase() || "";
      if (type === "video" || type === "audio" || ["pdf", "doc"].includes(ext)) {
        setMessages((m) => [
          ...m,
          {
            kind: "user",
            text: "该文件类型的真实检测能力尚未部署，本次不会生成模拟结论。请选择图片、TXT、MD、CSV、JSON、LOG 或 DOCX 文件。",
            fileName: f.name,
          },
        ]);
      } else if (f.size > uploadLimit) {
        setMessages((m) => [
          ...m,
          {
            kind: "user",
            text: `检测失败：文件不能超过 ${formatBytes(uploadLimit)}，当前文件为 ${formatBytes(f.size)}。`,
            fileName: f.name,
          },
        ]);
      } else {
        runDetect(f);
      }
    }
    e.target.value = "";
  };

  const onSelectHistory = async (item: HistoryItem) => {
    await openHistoryItem(item.taskId);
  };

  const onDelete = async (taskId: string) => {
    try {
      await deleteHistory(taskId);
      if (activeId === taskId) {
        setMessages([]);
        setActiveId(undefined);
      }
      setHistory((items) => items.filter((item) => item.taskId !== taskId));
      void loadHistory({ preserveOnError: true });
    } catch (error) {
      setHistoryMessage(friendlyMessage(error instanceof Error ? error.message : "", "删除历史失败"));
    }
  };

  const capabilitySummary = formatCapabilitySummary(health);
  const uploadLimit = health?.limits?.maxUploadBytes || MAX_UPLOAD_BYTES;
  const accessAttention = isAuthRequiredMessage(historyMessage);
  const requestUpload = () => {
    if (accessAttention) {
      window.location.href = "/";
      return;
    }
    fileInputRef.current?.click();
  };

  const newChat = () => {
    setMessages([]);
    setActiveId(undefined);
  };

  if (view === "monitor") {
    return (
      <div className="h-full flex">
        <AdminDashboard
          onBack={() => {
            window.location.hash = "";
            setView("detect");
          }}
          onHome={() => {
            window.location.href = "/";
          }}
          reloadKey={monitorReloadKey}
        />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col md:flex-row bg-[#f4f7f4] text-ink-950">
      <Sidebar
        history={history}
        historyBusy={historyBusy}
        totalCount={historyTotal}
        filterCounts={historyFilterCounts}
        message={historyMessage}
        query={historyQuery}
        filter={historyFilter}
        activeId={activeId}
        activeItem={history.find((item) => item.taskId === activeId)}
        onSelect={onSelectHistory}
        onQueryChange={setHistoryQuery}
        onFilterChange={setHistoryFilter}
        onNew={newChat}
        onDelete={onDelete}
        onClearSelection={newChat}
        onRetryHistory={() => loadHistory({ preserveOnError: false })}
        onRefreshHistory={() => loadHistory({ preserveOnError: true })}
        onLoadMore={history.length < historyTotal ? () => {
          setHistoryLimit((value) => value + HISTORY_PAGE_SIZE);
          void loadHistory({ preserveOnError: true, append: true });
        } : undefined}
        className="hidden md:flex"
      />

      {historyOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <button
            className="absolute inset-0 bg-black/35"
            onClick={() => setHistoryOpen(false)}
            aria-label="关闭历史记录遮罩"
          />
          <Sidebar
            history={history}
            historyBusy={historyBusy}
            totalCount={historyTotal}
            filterCounts={historyFilterCounts}
            message={historyMessage}
            query={historyQuery}
            filter={historyFilter}
            activeId={activeId}
            activeItem={history.find((item) => item.taskId === activeId)}
            onSelect={onSelectHistory}
            onQueryChange={setHistoryQuery}
            onFilterChange={setHistoryFilter}
            onNew={newChat}
            onDelete={onDelete}
            onClearSelection={newChat}
            onRetryHistory={() => loadHistory({ preserveOnError: false })}
            onRefreshHistory={() => loadHistory({ preserveOnError: true })}
            onLoadMore={history.length < historyTotal ? () => {
              setHistoryLimit((value) => value + HISTORY_PAGE_SIZE);
              void loadHistory({ preserveOnError: true, append: true });
            } : undefined}
            onClose={() => setHistoryOpen(false)}
            className="relative h-full w-[86vw] max-w-80"
          />
        </div>
      )}

      <main className="flex-1 flex flex-col min-w-0 min-h-0">
        <header className="px-4 sm:px-6 py-3 border-b border-ink-700 bg-white/95 backdrop-blur flex items-center justify-between gap-3 shadow-sm">
          <div className="min-w-0">
            <h1 className="text-lg sm:text-xl font-semibold text-rice truncate">深度证据分析</h1>
            <p className="text-[11px] sm:text-xs text-ink-500 truncate">上传文件后汇总鉴伪结论、取证线索、内容凭证与报告归档</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => setHistoryOpen(true)}
              className="md:hidden h-9 px-3 rounded-lg border border-ink-600 bg-ink-900 text-xs text-ink-950 inline-flex items-center gap-1.5"
            >
              <IconfontIcon name="history" size={14} />
              历史
            </button>
            <span
              className={`hidden sm:inline text-xs px-2.5 py-1 rounded-full border ${
                health == null
                  ? "bg-ink-900 text-ink-500 border-ink-600"
                  : health.vlmEnabled
                  ? "bg-jade/10 text-jade border-jade/30"
                  : "bg-cinnabar/10 text-cinnabar border-cinnabar/30"
              }`}
            >
              ● {health == null ? "状态待确认" : health.vlmEnabled ? "检测服务可用" : "部分能力暂不可用"}
            </span>
            <button
              onClick={() => {
                window.location.href = "/";
              }}
              className="hidden sm:inline-flex h-9 items-center gap-1.5 px-3 rounded-lg border border-ink-600 bg-ink-900 text-xs text-ink-950 hover:border-brand-cyan/50"
            >
              <IconfontIcon name="home" size={14} />
              首页
            </button>
            <button
              onClick={newChat}
              className="hidden sm:inline-flex h-9 items-center gap-1.5 px-3 rounded-lg border border-ink-600 bg-ink-900 text-xs text-ink-950 hover:border-jade/50"
            >
              <IconfontIcon name="plus" size={14} />
              新建检测
            </button>
          </div>
        </header>

        <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto bg-grid px-3 sm:px-6 py-4 sm:py-6 space-y-4 sm:space-y-5">
          {accessAttention && (
            <AccessPanel />
          )}
          {messages.length === 0 && (
            <EmptyState
              capabilitySummary={capabilitySummary}
            />
          )}

          {messages.map((m, i) => {
            if (m.kind === "user") {
              return (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[88%] sm:max-w-[70%] rounded-lg bg-cinnabar/10 border border-cinnabar/25 px-3 sm:px-4 py-2.5 shadow-sm">
                    <p className="text-sm text-ink-950">{m.text}</p>
                    {m.fileName && (
                      <div className="mt-2 flex items-center gap-2 min-w-0">
                        {m.previewUrl && (
                          <img src={m.previewUrl} className="h-16 w-16 object-cover rounded-md" />
                        )}
                        <span className="text-xs text-ink-500 truncate">文件：{m.fileName}</span>
                      </div>
                    )}
                  </div>
                </div>
              );
            }
            if (m.kind === "progress") {
              return (
                <div key={i} className="flex gap-2 sm:gap-3">
                  <AgentAvatar />
                  <div className="min-w-0 flex-1 rounded-lg bg-ink-800 border border-ink-600 px-3 sm:px-4 py-3 space-y-1.5 shadow-sm">
                    {PROGRESS_STEPS.map((step, idx) => (
                      <div
                        key={idx}
                        className={`flex items-center gap-2 text-sm ${
                          idx < m.stage
                            ? "text-ink-500"
                            : idx === m.stage
                            ? "text-brand-cyan"
                            : "text-ink-500/60"
                        }`}
                      >
                        <span
                          className={`h-2 w-2 rounded-full ${
                            idx < m.stage
                              ? "bg-jade"
                              : idx === m.stage
                              ? "bg-brand-cyan animate-pulse"
                              : "bg-ink-600"
                          }`}
                        />
                        {step}
                      </div>
                    ))}
                  </div>
                </div>
              );
            }
            if (m.kind === "loading") {
              return (
                <div key={i} className="flex gap-2 sm:gap-3">
                  <AgentAvatar />
                  <div className="min-w-0 flex-1 rounded-lg bg-ink-800 border border-ink-600 px-3 sm:px-4 py-3 flex items-center gap-2 text-sm text-brand-cyan shadow-sm">
                    <span className="h-2 w-2 rounded-full bg-brand-cyan animate-pulse" /> {m.text}
                  </div>
                </div>
              );
            }
            if (m.kind === "forensics") {
              return (
                <div key={i} className="mx-auto w-full max-w-6xl">
                  <div className="min-w-0">
                    <ForensicGallery report={m.report} />
                  </div>
                </div>
              );
            }
            return (
              <div key={i} className="mx-auto w-full max-w-6xl">
                <div className="min-w-0">
                  <ResultCard
                    result={m.result}
                    previewUrl={m.previewUrl}
                    forensicsReport={forensicsByTask[m.result.taskId]}
                    provenanceReport={provenanceByTask[m.result.taskId] || m.result.provenance || undefined}
                    onForensics={
                      m.file && m.result.fileMeta?.type === "image"
                        ? () => onForensics(m.file!, m.result.taskId)
                        : undefined
                    }
                    forensicsBusy={forensicsBusy}
                    onProvenance={
                      m.file && m.result.fileMeta?.type === "image"
                        ? () => onProvenance(m.file!, m.result.taskId)
                        : undefined
                    }
                    provenanceBusy={provenanceBusy}
                  />
                </div>
              </div>
            );
          })}
        </div>

        <div className="border-t border-ink-700 bg-ink-800/95 px-3 sm:px-6 py-3 sm:py-4">
          {accessAttention ? (
            <div className="rounded-lg bg-ink-900 border border-ink-600 px-3 sm:px-4 py-3">
              <a
                href="/"
                className="flex w-full items-center justify-center rounded-lg bg-brand-blue px-4 py-2.5 text-sm font-medium text-white shadow-sm hover:bg-brand-cyan"
              >
                登录后开始检测
              </a>
              <p className="mt-2 text-center text-xs text-ink-500">
                登录后可上传单个 {formatBytes(uploadLimit)} 以内的文件并查看完整检测结果。
              </p>
            </div>
          ) : (
            <>
              <div className={`${messages.length > 0 ? "hidden sm:flex" : "flex"} gap-2 mb-3 flex-wrap`} aria-label="当前可用检测能力">
                {AVAILABLE_CAPABILITIES.map((label) => (
                  <span key={label} className="inline-flex min-h-8 items-center rounded-md border border-ink-600 bg-ink-900 px-3 text-xs text-ink-500">
                    <IconfontIcon name="shield-check" size={13} className="mr-1.5 text-jade" />{label}
                  </span>
                ))}
              </div>
              <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 rounded-lg bg-ink-900 border border-ink-600 px-3 sm:px-4 py-3">
                <button
                  disabled={busy}
                  onClick={requestUpload}
                  className="flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-brand-blue text-white font-medium text-sm disabled:opacity-50 shadow-sm hover:bg-brand-cyan"
                >
                  {!busy && <IconfontIcon name="upload" size={16} />}
                  {busy ? "检测中…" : "选择文件开始鉴伪"}
                </button>
                <span className="text-xs sm:text-sm text-ink-500 leading-relaxed truncate">
                  单文件上限 {formatBytes(uploadLimit)} · {capabilitySummary}
                </span>
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  accept="image/*,.txt,.md,.csv,.json,.log,.docx"
                  onChange={onFile}
                />
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}

function AgentAvatar() {
  return (
    <div className="h-8 w-8 shrink-0 rounded-lg bg-ink-800 border border-ink-600 flex items-center justify-center shadow-sm">
      <Logo size={26} idSuffix="avatar" />
    </div>
  );
}

function getInitialHistoryItem() {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("historyItem") || "";
}

function AccessPanel() {
  return (
    <section className="mx-auto w-full max-w-5xl rounded-lg border border-gold/30 bg-gold/10 px-4 py-2.5 shadow-sm">
      <div className="text-xs leading-relaxed text-ink-500">
        当前会话需要登录后使用。登录后可上传文件并查看历史记录。
      </div>
    </section>
  );
}

function EmptyState({
  capabilitySummary,
}: {
  capabilitySummary: string;
}) {
  return (
    <div className="mx-auto grid w-full max-w-6xl gap-4 py-6 sm:py-10 lg:grid-cols-[minmax(0,1fr)_320px]">
      <section className="min-h-[320px] rounded-lg border border-dashed border-brand-blue/35 bg-white/90 p-5 sm:p-7 shadow-sm">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="inline-flex items-center gap-2 rounded-full border border-brand-cyan/25 bg-brand-cyan/10 px-3 py-1 text-[11px] font-medium text-brand-cyan">
              <IconfontIcon name="deep-analysis" size={14} />
              深度证据分析
            </div>
            <h2 className="mt-4 text-2xl sm:text-3xl font-semibold leading-tight text-rice">新建鉴伪任务</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-500">
              检测结论、取证线索、内容凭证和报告编号会汇总到同一张结果卡，方便后续复核与归档。
            </p>
            <div className="mt-4 flex flex-wrap gap-2 text-xs text-ink-500">
              <span className="rounded-md border border-ink-600 bg-ink-900 px-2.5 py-1">服务状态同步</span>
              <span className="rounded-md border border-ink-600 bg-ink-900 px-2.5 py-1">历史记录已接入</span>
              <span className="rounded-md border border-ink-600 bg-ink-900 px-2.5 py-1">{capabilitySummary}</span>
            </div>
          </div>
          <div className="flex shrink-0 items-end gap-2">
            <img src="/v2/brand/huijian-mascot.webp" alt="慧鉴 AI 品牌助手小鉴" className="h-28 w-20 object-contain drop-shadow-md" />
          </div>
        </div>
      </section>
      <aside className="rounded-lg border border-ink-700 bg-ink-900 p-4 shadow-sm">
        <div className="flex items-center gap-2 text-sm font-semibold text-ink-950">
          <IconfontIcon name="shield-check" size={17} />
          任务流程
        </div>
        <div className="mt-4 space-y-3 text-xs text-ink-500">
          {[
            ["01", "上传文件", "图像或可提取正文的文档"],
            ["02", "查看证据", "结论、置信度与取证线索"],
            ["03", "归档报告", "历史记录与报告编号"],
          ].map((item) => (
            <div key={item[0]} className="rounded-lg border border-ink-700 bg-white px-3 py-3">
              <div className="flex items-center gap-2">
                <span className="rounded-md bg-brand-blue/10 px-2 py-1 font-mono text-[10px] text-brand-blue">{item[0]}</span>
                <strong className="text-ink-950">{item[1]}</strong>
              </div>
              <p className="mt-1 pl-11 leading-relaxed">{item[2]}</p>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}
