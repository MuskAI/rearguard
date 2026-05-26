import { useEffect, useRef, useState } from "react";
import {
  DetectResult,
  HistoryItem,
  FileType,
  ForensicReport,
  ProvenanceReport,
  detect,
  runForensics,
  runProvenance,
  fetchHistory,
  deleteHistory,
  TYPE_LABEL,
} from "./api";
import Sidebar from "./components/Sidebar";
import ResultCard from "./components/ResultCard";
import ForensicGallery from "./components/ForensicGallery";
import ProvenanceCard from "./components/ProvenanceCard";
import Logo from "./components/Logo";

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

function inferType(name: string): FileType {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (["mp4", "mov", "avi", "mkv", "webm"].includes(ext)) return "video";
  if (["mp3", "wav", "m4a", "flac", "aac"].includes(ext)) return "audio";
  if (["txt", "pdf", "doc", "docx", "md"].includes(ext)) return "document";
  return "image";
}

export default function App() {
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [forensicsBusy, setForensicsBusy] = useState(false);
  const [provenanceBusy, setProvenanceBusy] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [activeId, setActiveId] = useState<string>();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadHistory = () => fetchHistory().then(setHistory).catch(() => {});

  useEffect(() => {
    loadHistory();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

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
      loadHistory();
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

  const onForensics = async (file: File) => {
    if (forensicsBusy) return;
    setForensicsBusy(true);
    setMessages((m) => [
      ...m,
      { kind: "user", text: "请帮我做可解释性取证分析，提供可视化证据", fileName: file.name },
      { kind: "loading", text: "正在生成 7 项取证可视化证据并逐项判读…" },
    ]);
    try {
      const report = await runForensics(file);
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

  const onProvenance = async (file: File) => {
    if (provenanceBusy) return;
    setProvenanceBusy(true);
    setMessages((m) => [
      ...m,
      { kind: "user", text: "请验证这张图的内容凭证（C2PA）", fileName: file.name },
      { kind: "loading", text: "正在读取并验证 C2PA 内容凭证…" },
    ]);
    try {
      const report = await runProvenance(file);
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
    const res = await fetch(`/v2-api/history/${item.taskId}`);
    if (res.ok) {
      const result: DetectResult = await res.json();
      setMessages([
        { kind: "user", text: `历史记录：${item.name}`, fileName: item.name },
        { kind: "result", result },
      ]);
    }
  };

  const onDelete = async (taskId: string) => {
    await deleteHistory(taskId);
    if (activeId === taskId) {
      setMessages([]);
      setActiveId(undefined);
    }
    loadHistory();
  };

  const newChat = () => {
    setMessages([]);
    setActiveId(undefined);
  };

  return (
    <div className="h-full flex flex-col md:flex-row">
      <Sidebar
        history={history}
        activeId={activeId}
        onSelect={onSelectHistory}
        onNew={newChat}
        onDelete={onDelete}
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
            activeId={activeId}
            onSelect={onSelectHistory}
            onNew={newChat}
            onDelete={onDelete}
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
            <span className="hidden sm:inline text-xs px-2.5 py-1 rounded-full bg-jade/10 text-jade border border-jade/30">
              ● 模型在线
            </span>
          </div>
        </header>

        <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto bg-grid px-3 sm:px-6 py-4 sm:py-6 space-y-4 sm:space-y-5">
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
                    onForensics={
                      m.file && m.result.fileMeta.type === "image"
                        ? () => onForensics(m.file!)
                        : undefined
                    }
                    forensicsBusy={forensicsBusy}
                    onProvenance={
                      m.file && m.result.fileMeta.type === "image"
                        ? () => onProvenance(m.file!)
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
              支持 拖拽 / 点击上传 图像·视频·音频·文档，或粘贴链接
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
