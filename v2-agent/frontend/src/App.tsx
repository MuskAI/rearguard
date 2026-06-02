import { useEffect, useRef, useState } from "react";
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
  getAccessToken,
  persistArtifacts,
  setAccessToken,
  TYPE_LABEL,
} from "./api";
import Sidebar, { getInitialHistoryFilter, getInitialHistoryQuery } from "./components/Sidebar";
import ResultCard from "./components/ResultCard";
import ForensicGallery from "./components/ForensicGallery";
import ProvenanceCard from "./components/ProvenanceCard";
import Logo from "./components/Logo";
import AdminDashboard from "./components/AdminDashboard";

type Message =
  | { kind: "user"; text: string; fileName: string; previewUrl?: string }
  | { kind: "progress"; stage: number }
  | { kind: "result"; result: DetectResult; previewUrl?: string; file?: File }
  | { kind: "loading"; text: string }
  | { kind: "forensics"; report: ForensicReport }
  | { kind: "provenance"; report: ProvenanceReport };

const PROGRESS_STEPS = ["正在解析文件…", "正在提取多模态特征…", "正在运行鉴伪模型…", "正在生成检测报告…"];

const QUICK_COMMANDS = [
  { label: "检测AI生成", hint: "判断是否为 AI 生成内容" },
  { label: "检测换脸", hint: "检测人脸深度伪造" },
  { label: "检测PS篡改", hint: "检测拼接/局部重绘痕迹" },
  { label: "出具鉴定报告", hint: "生成可下载报告" },
];
const HISTORY_PAGE_SIZE = 100;
const SKILL_NAME = "$realguard-forensics";
const SKILL_URL = "http://124.222.3.205/v2/skills/realguard-forensics/SKILL.md";
const SKILL_COMMAND =
  "python3 scripts/realguard_cli.py detect <file> --base-url http://124.222.3.205 --api-prefix /v2-api --pretty";
const SKILL_HANDOFF =
  `Use ${SKILL_NAME}; read ${SKILL_URL}; call POST http://124.222.3.205/v2-api/detect with multipart field file, or run ${SKILL_COMMAND} if the repo CLI is available; then return a concise verdict with confidence, evidence, model version, cache version, and report id.`;

function inferType(name: string): FileType {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (["mp4", "mov", "avi", "mkv", "webm"].includes(ext)) return "video";
  if (["mp3", "wav", "m4a", "flac", "aac"].includes(ext)) return "audio";
  if (["txt", "pdf", "doc", "docx", "md"].includes(ext)) return "document";
  return "image";
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
  const [monitorReloadKey, setMonitorReloadKey] = useState(0);
  const [forensicsByTask, setForensicsByTask] = useState<Record<string, ForensicReport>>({});
  const [provenanceByTask, setProvenanceByTask] = useState<Record<string, ProvenanceReport>>({});
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [forensicsBusy, setForensicsBusy] = useState(false);
  const [provenanceBusy, setProvenanceBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyQuery, setHistoryQuery] = useState(() => getInitialHistoryQuery());
  const [historyFilter, setHistoryFilter] = useState<HistorySidebarFilter>(() => getInitialHistoryFilter());
  const [historyLimit, setHistoryLimit] = useState(HISTORY_PAGE_SIZE);
  const [view, setView] = useState<"detect" | "monitor">(() => (window.location.hash === "#monitor" ? "monitor" : "detect"));
  const [activeId, setActiveId] = useState<string>();
  const [skillCopied, setSkillCopied] = useState<"handoff" | "command" | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const restoredHistoryItemRef = useRef(false);
  const historyRequestIdRef = useRef(0);
  const historyDetailRequestIdRef = useRef(0);

  const loadHealth = () =>
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null));

  const loadHistory = async ({
    preserveOnError = history.length > 0,
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
      const offset = append ? history.length : 0;
      const limit = append ? HISTORY_PAGE_SIZE : reset ? HISTORY_PAGE_SIZE : historyLimit;
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
      setHistoryMessage(error instanceof Error ? error.message : "历史记录暂不可用");
    } finally {
      if (historyRequestIdRef.current === requestId) {
        setHistoryBusy(false);
      }
    }
  };

  useEffect(() => {
    loadHealth();
    void loadHistory({ preserveOnError: false });
  }, []);

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
  }, [historyFilter, historyQuery]);

  useEffect(() => {
    setHistoryLimit(HISTORY_PAGE_SIZE);
  }, [historyFilter, historyQuery]);

  useEffect(() => {
    if (restoredHistoryItemRef.current || history.length === 0) return;
    const initialHistoryItem = getInitialHistoryItem();
    if (!initialHistoryItem) {
      restoredHistoryItemRef.current = true;
      return;
    }
    const target = history.find((item) => item.taskId === initialHistoryItem || item.reportId === initialHistoryItem);
    restoredHistoryItemRef.current = true;
    if (target) {
      void onSelectHistory(target);
    }
  }, [history]);

  useEffect(() => {
    const onHash = () => setView(window.location.hash === "#monitor" ? "monitor" : "detect");
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (activeId) params.set("historyItem", activeId);
    else params.delete("historyItem");
    const next = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`);
  }, [activeId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const configureAccessToken = async () => {
    const next = window.prompt("输入访问令牌。留空可清除本地保存的令牌。", getAccessToken());
    if (next === null) return;
    setAccessToken(next);
    setMonitorReloadKey((value) => value + 1);
    await Promise.allSettled([loadHealth(), loadHistory({ preserveOnError: true })]);
  };

  const copySkillText = async (kind: "handoff" | "command", text: string) => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
      }
      setSkillCopied(kind);
      window.setTimeout(() => setSkillCopied(null), 1800);
    } catch {
      window.prompt("复制失败，请手动复制：", text);
    }
  };

  const runDetect = async (file: File) => {
    const type = inferType(file.name);
    const previewUrl = type === "image" ? URL.createObjectURL(file) : undefined;

    setMessages((m) => [
      ...m,
      { kind: "user", text: `请鉴定这个${TYPE_LABEL[type]}文件`, fileName: file.name, previewUrl },
      { kind: "progress", stage: 0 },
    ]);
    setBusy(true);

    for (let s = 1; s < PROGRESS_STEPS.length; s++) {
      await new Promise((r) => setTimeout(r, 550));
      setMessages((m) => m.map((msg) => (msg.kind === "progress" ? { ...msg, stage: s } : msg)));
    }

    try {
      const result = await detect(file, type);
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
          text: `检测失败：${e instanceof Error ? e.message : "未知错误"}`,
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
      { kind: "user", text: "请帮我做可解释性取证分析，提供可视化证据", fileName: file.name },
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
        { kind: "user", text: `取证分析失败：${e instanceof Error ? e.message : "未知错误"}`, fileName: "" },
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
      { kind: "user", text: "请验证这张图的内容凭证（C2PA）", fileName: file.name },
      { kind: "loading", text: "正在读取并验证 C2PA 内容凭证…" },
    ]);
    try {
      const report = await runProvenance(file);
      setProvenanceByTask((prev) => ({ ...prev, [taskId]: report }));
      try {
        await persistArtifacts(taskId, { provenance: report });
      } catch {
        // Keep the current analysis result visible even if persistence fails.
      }
      setMessages((m) => [
        ...m.filter((msg) => msg.kind !== "loading"),
        { kind: "provenance", report },
      ]);
    } catch (e) {
      setMessages((m) => [
        ...m.filter((msg) => msg.kind !== "loading"),
        { kind: "user", text: `凭证验证失败：${e instanceof Error ? e.message : "未知错误"}`, fileName: "" },
      ]);
    } finally {
      setProvenanceBusy(false);
    }
  };

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) runDetect(f);
    e.target.value = "";
  };

  const onSelectHistory = async (item: HistoryItem) => {
    setActiveId(item.taskId);
    const requestId = historyDetailRequestIdRef.current + 1;
    historyDetailRequestIdRef.current = requestId;
    try {
      const result: DetectResult = await fetchHistoryItem(item.taskId);
      if (historyDetailRequestIdRef.current !== requestId) return;
      if (result.forensics) {
        setForensicsByTask((prev) => ({ ...prev, [result.taskId]: result.forensics! }));
      }
      if (result.provenance) {
        setProvenanceByTask((prev) => ({ ...prev, [result.taskId]: result.provenance! }));
      }
      const nextMessages: Message[] = [
        { kind: "user", text: `历史记录：${item.name}`, fileName: item.name },
        { kind: "result", result },
      ];
      if (result.forensics) {
        nextMessages.push({ kind: "forensics", report: result.forensics });
      }
      if (result.provenance) {
        nextMessages.push({ kind: "provenance", report: result.provenance });
      }
      setMessages([
        ...nextMessages,
      ]);
    } catch (error) {
      if (historyDetailRequestIdRef.current !== requestId) return;
      setMessages([
        {
          kind: "user",
          text: error instanceof Error ? error.message : "加载历史详情失败",
          fileName: "",
        },
      ]);
    }
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
      setHistoryMessage(error instanceof Error ? error.message : "删除历史失败");
    }
  };

  const newChat = () => {
    setMessages([]);
    setActiveId(undefined);
  };

  if (view === "monitor") {
    return (
      <div className="h-full flex">
        <AdminDashboard
          accessProtectionEnabled={Boolean(health?.accessProtectionEnabled)}
          onBack={() => {
            window.location.hash = "";
            setView("detect");
          }}
          onConfigureAccess={configureAccessToken}
          reloadKey={monitorReloadKey}
        />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col md:flex-row">
      <Sidebar
        history={history}
        historyBusy={historyBusy}
        totalCount={historyTotal}
        filterCounts={historyFilterCounts}
        message={historyMessage}
        accessProtectionEnabled={Boolean(health?.accessProtectionEnabled)}
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
        onConfigureAccess={configureAccessToken}
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
            accessProtectionEnabled={Boolean(health?.accessProtectionEnabled)}
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
            onConfigureAccess={configureAccessToken}
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
        <header className="px-4 sm:px-6 py-3 border-b border-ink-700 bg-ink-800/95 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="font-serif text-lg sm:text-xl font-semibold text-rice tracking-wide truncate">AI 鉴伪工作台</h1>
            <p className="text-[11px] sm:text-xs text-ink-500 truncate">图像 / 视频 / 音频 / 文档的伪造与 AIGC 检测</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => setHistoryOpen(true)}
              className="md:hidden h-9 px-3 rounded-lg border border-ink-600 bg-ink-900 text-xs text-ink-950"
            >
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
              ● {health == null ? "状态未知" : health.vlmEnabled ? "VLM 在线" : "Mock 回退"}
            </span>
            {health?.accessProtectionEnabled && (
              <button
                onClick={configureAccessToken}
                className="h-9 px-3 rounded-lg border border-ink-600 bg-ink-900 text-xs text-ink-950 hover:border-brand-cyan/50"
              >
                令牌
              </button>
            )}
            <button
              onClick={() => {
                window.location.href = "/?page=developer";
              }}
              className="h-9 px-3 rounded-lg border border-ink-600 bg-ink-900 text-xs text-ink-950 hover:border-brand-cyan/50"
            >
              开发者
            </button>
            <button
              onClick={() => {
                window.location.hash = "monitor";
                setView("monitor");
              }}
              className="h-9 px-3 rounded-lg border border-ink-600 bg-ink-900 text-xs text-ink-950 hover:border-jade/50"
            >
              监控
            </button>
          </div>
        </header>

        <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto bg-grid px-3 sm:px-6 py-4 sm:py-6 space-y-4 sm:space-y-5">
          <CapabilityBanner health={health} />
          <SkillInterventionCard
            copied={skillCopied}
            onCopyHandoff={() => copySkillText("handoff", SKILL_HANDOFF)}
            onCopyCommand={() => copySkillText("command", SKILL_COMMAND)}
          />
          {messages.length === 0 && <EmptyState onUpload={() => fileInputRef.current?.click()} />}

          {messages.map((m, i) => {
            if (m.kind === "user") {
              return (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[88%] sm:max-w-[70%] rounded-2xl rounded-tr-sm bg-cinnabar/10 border border-cinnabar/25 px-3 sm:px-4 py-2.5 shadow-sm">
                    <p className="text-sm text-ink-950">{m.text}</p>
                    {m.fileName && (
                      <div className="mt-2 flex items-center gap-2 min-w-0">
                        {m.previewUrl && (
                          <img src={m.previewUrl} className="h-16 w-16 object-cover rounded-md" />
                        )}
                        <span className="text-xs text-ink-500 truncate">📎 {m.fileName}</span>
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
                  <div className="min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-ink-800 border border-ink-600 px-3 sm:px-4 py-3 space-y-1.5 shadow-sm">
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
                        <span>{idx < m.stage ? "✓" : idx === m.stage ? "◌" : "○"}</span>
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
                  <div className="min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-ink-800 border border-ink-600 px-3 sm:px-4 py-3 flex items-center gap-2 text-sm text-brand-cyan shadow-sm">
                    <span className="animate-pulse">◌</span> {m.text}
                  </div>
                </div>
              );
            }
            if (m.kind === "forensics") {
              return (
                <div key={i} className="flex gap-2 sm:gap-3">
                  <AgentAvatar />
                  <div className="flex-1 min-w-0 max-w-4xl">
                    <ForensicGallery report={m.report} />
                  </div>
                </div>
              );
            }
            if (m.kind === "provenance") {
              return (
                <div key={i} className="flex gap-2 sm:gap-3">
                  <AgentAvatar />
                  <div className="flex-1 min-w-0 max-w-2xl">
                    <ProvenanceCard report={m.report} />
                  </div>
                </div>
              );
            }
            return (
              <div key={i} className="flex gap-2 sm:gap-3">
                <AgentAvatar />
                <div className="flex-1 min-w-0 max-w-3xl">
                  <ResultCard
                    result={m.result}
                    previewUrl={m.previewUrl}
                    forensicsReport={forensicsByTask[m.result.taskId]}
                    provenanceReport={provenanceByTask[m.result.taskId]}
                    onForensics={
                      m.file && m.result.fileMeta.type === "image"
                        ? () => onForensics(m.file!, m.result.taskId)
                        : undefined
                    }
                    forensicsBusy={forensicsBusy}
                    onProvenance={
                      m.file && m.result.fileMeta.type === "image"
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
          <div className="grid grid-cols-2 gap-2 mb-3 sm:flex sm:flex-wrap">
            {QUICK_COMMANDS.map((q) => (
              <button
                key={q.label}
                title={q.hint}
                onClick={() => fileInputRef.current?.click()}
                className="text-xs px-3 py-1.5 rounded-full bg-ink-900 border border-ink-600 text-ink-950 hover:border-brand-cyan/50 hover:text-brand-cyan transition"
              >
                {q.label}
              </button>
            ))}
          </div>
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 rounded-xl bg-ink-900 border border-ink-600 px-3 sm:px-4 py-3">
            <button
              disabled={busy}
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-gradient-to-r from-brand-cyan to-brand-blue text-white font-medium text-sm disabled:opacity-50 shadow-sm"
            >
              {busy ? "检测中…" : "上传文件检测"}
            </button>
            <span className="text-xs sm:text-sm text-ink-500 leading-relaxed">
              支持点击上传。图像和可提取正文的文档（txt/md/docx）默认走模型；视频、音频和其他复杂文档当前可能回退为演示判定。
            </span>
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              accept="image/*,video/*,audio/*,.txt,.pdf,.doc,.docx,.md"
              onChange={onFile}
            />
          </div>
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

function CapabilityBanner({ health }: { health: HealthStatus | null }) {
  const tokenProtected = Boolean(health?.accessProtectionEnabled);
  const capabilityText =
    health == null
      ? "尚未获取到后端状态，检测能力与访问控制信息可能不完整。"
      : health.vlmEnabled
      ? "图像与可提取正文的文档（txt/md/docx）使用真实模型；视频、音频和其他复杂文档仍为演示判定。"
      : "当前处于 Mock 回退模式，检测结果仅用于演示流程。";
  const cacheVersion = health?.analysisCacheVersion?.trim();
  return (
    <div className="rounded-xl border border-ink-600 bg-ink-800 px-4 py-3 text-xs sm:text-sm text-ink-500 leading-relaxed">
      <span className="text-ink-950 font-medium">当前能力：</span>
      {capabilityText}
      {cacheVersion && ` 分析缓存版本：${cacheVersion}。`}
      {tokenProtected && " 历史记录、报告与监控指标需要访问令牌。"}
    </div>
  );
}

function SkillInterventionCard({
  copied,
  onCopyHandoff,
  onCopyCommand,
}: {
  copied: "handoff" | "command" | null;
  onCopyHandoff: () => void;
  onCopyCommand: () => void;
}) {
  return (
    <section className="overflow-hidden rounded-3xl border border-brand-cyan/30 bg-ink-800 shadow-md shadow-brand-cyan/5">
      <div className="flex flex-col gap-5 p-5 sm:p-7 lg:flex-row lg:items-stretch lg:justify-between">
        <div className="min-w-0 flex-1">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-jade/30 bg-jade/10 px-3 py-1.5 text-xs font-semibold tracking-[0.18em] text-jade">
              SKILL 已介入
            </span>
            <span className="rounded-full border border-brand-cyan/30 bg-brand-cyan/10 px-3 py-1.5 text-xs text-brand-cyan">
              OpenClaw / AI Agent 可调用
            </span>
          </div>
          <h2 className="font-serif text-2xl font-semibold text-rice sm:text-3xl">
            {SKILL_NAME} 是给外部 Agent 的公开鉴伪入口
          </h2>
          <p className="mt-3 max-w-4xl text-base leading-relaxed text-ink-500">
            必须公开 skill 的原因是：OpenClaw 等外部 agent 无法读取你的本地仓库路径，也不知道 RealGuard 的 API、输出字段和解释边界。
            让它读取 <span className="mx-1 font-mono text-ink-950">{SKILL_URL}</span> 后，就能直接调用公开 V2 API 或仓库 CLI，稳定输出可审计的鉴伪结论。
          </p>
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            {[
              ["1", "读取公网 Skill", "通过 URL 获取鉴伪流程、API 和解释边界。"],
              ["2", "调用 V2 API / CLI", "上传文件，输出 agentSummary 与证据字段。"],
              ["3", "返回带证据结论", "引用 verdict、confidence、模型版本和报告号。"],
            ].map(([step, title, desc]) => (
              <div key={step} className="rounded-xl border border-ink-600 bg-ink-900 px-3 py-3">
                <div className="mb-2 flex items-center gap-2">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand-cyan/15 text-xs font-semibold text-brand-cyan">
                    {step}
                  </span>
                  <span className="text-sm font-medium text-ink-950">{title}</span>
                </div>
                <p className="text-xs leading-relaxed text-ink-500">{desc}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="flex w-full shrink-0 flex-col gap-3 lg:w-[420px]">
          <div className="rounded-xl border border-ink-600 bg-ink-900 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-ink-950">公开 Skill URL</span>
              <button
                onClick={() => navigator.clipboard?.writeText(SKILL_URL)}
                className="rounded-lg border border-jade/30 px-2.5 py-1 text-xs text-jade hover:bg-jade/10"
              >
                复制
              </button>
            </div>
            <code className="block whitespace-pre-wrap break-words rounded-lg bg-ink-800 px-3 py-2 font-mono text-[11px] leading-relaxed text-ink-950">
              {SKILL_URL}
            </code>
          </div>
          <div className="rounded-xl border border-ink-600 bg-ink-900 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-ink-950">给 OpenClaw 的一句话</span>
              <button
                onClick={onCopyHandoff}
                className="rounded-lg border border-brand-cyan/30 px-2.5 py-1 text-xs text-brand-cyan hover:bg-brand-cyan/10"
              >
                {copied === "handoff" ? "已复制" : "复制"}
              </button>
            </div>
            <code className="block whitespace-pre-wrap break-words rounded-lg bg-ink-800 px-3 py-2 font-mono text-[11px] leading-relaxed text-ink-950">
              {SKILL_HANDOFF}
            </code>
          </div>
          <div className="rounded-xl border border-ink-600 bg-ink-900 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-ink-950">CLI 命令</span>
              <button
                onClick={onCopyCommand}
                className="rounded-lg border border-jade/30 px-2.5 py-1 text-xs text-jade hover:bg-jade/10"
              >
                {copied === "command" ? "已复制" : "复制"}
              </button>
            </div>
            <code className="block whitespace-pre-wrap break-words rounded-lg bg-ink-800 px-3 py-2 font-mono text-[11px] leading-relaxed text-ink-950">
              {SKILL_COMMAND}
            </code>
          </div>
        </div>
      </div>
    </section>
  );
}

function getInitialHistoryItem() {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("historyItem") || "";
}

function EmptyState({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-center gap-4 py-16 sm:py-20 px-4">
      <Logo size={76} idSuffix="hero" />
      <div className="w-full max-w-sm">
        <h2 className="font-serif text-2xl font-semibold text-rice tracking-wide">鉴真伪 · 明真相</h2>
        <p className="text-sm text-ink-500 mt-2 leading-relaxed">
          上传任意图像、视频、音频或文档，我会判断它是否为 AI 生成、深度伪造或经过篡改，并给出可信度与依据。
        </p>
      </div>
      <button
        onClick={onUpload}
        className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-brand-cyan to-brand-blue text-white font-medium shadow-sm"
      >
        上传文件开始检测
      </button>
    </div>
  );
}
