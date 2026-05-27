import { useEffect, useMemo, useState } from "react";
import { HistoryItem, VERDICT_META, TYPE_LABEL } from "../api";
import Logo from "./Logo";

interface Props {
  history: HistoryItem[];
  message?: string;
  activeId?: string;
  onSelect: (item: HistoryItem) => void;
  onNew: () => void;
  onDelete: (taskId: string) => void;
  className?: string;
  onClose?: () => void;
}

export default function Sidebar({ history, message, activeId, onSelect, onNew, onDelete, className = "", onClose }: Props) {
  const [query, setQuery] = useState(() => getInitialHistoryQuery());
  const [filter, setFilter] = useState<"all" | "vlm" | "mock" | "forensics" | "provenance" | "watermark">(() => getInitialHistoryFilter());

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (query.trim()) params.set("historyQuery", query.trim());
    else params.delete("historyQuery");
    if (filter !== "all") params.set("historyFilter", filter);
    else params.delete("historyFilter");
    const next = params.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${next ? `?${next}` : ""}${window.location.hash}`);
  }, [filter, query]);

  const filteredHistory = useMemo(() => {
    const q = query.trim().toLowerCase();
    return history.filter((item) => {
      if (filter === "vlm" && item.source !== "vlm") return false;
      if (filter === "mock" && item.source !== "mock") return false;
      if (filter === "forensics" && !item.hasForensics) return false;
      if (filter === "provenance" && !item.hasProvenance) return false;
      if (filter === "watermark" && !item.hasVisibleWatermark && !item.hasSynthid) return false;
      if (!q) return true;
      const fields = [
        item.name,
        item.reportId,
        item.source || "",
        item.visibleWatermarkProvider || "",
      ];
      return fields.some((field) => String(field).toLowerCase().includes(q));
    });
  }, [filter, history, query]);

  return (
    <aside className={`w-64 shrink-0 bg-ink-900 border-r border-ink-700 flex flex-col shadow-sm ${className}`}>
      <div className="p-4 flex items-center gap-2.5">
        <Logo size={36} idSuffix="side" />
        <div className="flex-1 min-w-0">
          <div className="font-serif text-xl font-semibold text-rice leading-tight tracking-[0.15em]">鉴真</div>
          <div className="text-[10px] text-cinnabar-light tracking-[0.2em]">AI 鉴伪智能体</div>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="md:hidden h-8 w-8 rounded-lg border border-ink-600 text-ink-500"
            aria-label="关闭历史记录"
          >
            ✕
          </button>
        )}
      </div>

      <button
        onClick={() => {
          onNew();
          onClose?.();
        }}
        className="mx-4 mb-3 py-2 rounded-lg bg-cinnabar text-white text-sm hover:bg-cinnabar-dark transition shadow-sm"
      >
        + 新建检测
      </button>

      <div className="px-4 pb-1 text-[11px] text-ink-500 uppercase tracking-wider">历史记录</div>
      <div className="flex-1 overflow-y-auto px-2 space-y-1">
        <div className="mx-2 mb-2 space-y-2">
          <div className="rounded-lg border border-ink-600 bg-ink-800 px-2.5 py-2">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索名称 / 报告号"
              className="w-full bg-transparent text-xs text-ink-950 placeholder:text-ink-500 outline-none"
            />
          </div>
          <div className="flex flex-wrap gap-1">
            {[
              { key: "all", label: "全部" },
              { key: "vlm", label: "VLM" },
              { key: "mock", label: "Mock" },
              { key: "forensics", label: "取证" },
              { key: "provenance", label: "凭证" },
              { key: "watermark", label: "水印" },
            ].map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setFilter(item.key as typeof filter)}
                className={`px-2 py-1 rounded-md text-[10px] border ${
                  filter === item.key
                    ? "border-cinnabar/40 bg-cinnabar/10 text-cinnabar"
                    : "border-ink-600 bg-ink-800 text-ink-500"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="text-[10px] text-ink-500">
            当前显示 {filteredHistory.length} / {history.length}
          </div>
        </div>
        {message && (
          <div className="mx-2 mb-2 rounded-lg border border-ink-600 bg-ink-800 px-2.5 py-2 text-[11px] text-ink-500">
            {message}
          </div>
        )}
        {history.length === 0 && (
          <div className="px-2 py-4 text-xs text-ink-500">{message ? "配置完成后会显示历史记录" : "暂无记录"}</div>
        )}
        {history.length > 0 && filteredHistory.length === 0 && (
          <div className="px-2 py-4 text-xs text-ink-500">当前搜索/筛选条件下暂无记录</div>
        )}
        {filteredHistory.map((h) => {
          const meta = VERDICT_META[h.verdict];
          return (
            <div
              key={h.taskId}
              onClick={() => {
                onSelect(h);
                onClose?.();
              }}
              className={`group px-2.5 py-2 rounded-lg cursor-pointer flex items-center gap-2 ${
                activeId === h.taskId ? "bg-ink-700" : "hover:bg-ink-800"
              }`}
            >
              {h.thumbnail ? (
                <img
                  src={h.thumbnail}
                  alt={h.name}
                  className="h-9 w-9 shrink-0 rounded-md object-cover border border-ink-600"
                  loading="lazy"
                />
              ) : (
                <span className="h-9 w-9 shrink-0 rounded-md bg-ink-800 border border-ink-600 grid place-items-center text-xs text-ink-500">
                  {TYPE_LABEL[h.type].slice(0, 1)}
                </span>
              )}
              <div className="flex-1 min-w-0">
                <div className="text-sm text-ink-950 truncate">{h.name}</div>
                <div className="text-[10px] flex items-center gap-1.5">
                  <span style={{ color: meta.color }}>{meta.label}</span>
                  <span className="text-ink-500">· {TYPE_LABEL[h.type]}</span>
                  {h.source === "vlm" && <span className="text-brand-cyan">VLM</span>}
                  {h.source === "mock" && <span className="text-cinnabar">Mock</span>}
                  {h.cacheHit && <span className="text-jade">缓存</span>}
                </div>
                {(h.hasForensics || h.hasProvenance || h.hasVisibleWatermark || h.hasSynthid) && (
                  <div className="mt-1 flex items-center gap-1.5 text-[10px]">
                    {h.hasForensics && (
                      <span className="px-1.5 py-0.5 rounded-full bg-brand-magenta/10 text-brand-magenta border border-brand-magenta/30">
                        取证
                      </span>
                    )}
                    {h.hasProvenance && (
                      <span className="px-1.5 py-0.5 rounded-full bg-jade/10 text-jade border border-jade/30">
                        凭证
                      </span>
                    )}
                    {h.hasVisibleWatermark && (
                      <span className="px-1.5 py-0.5 rounded-full bg-cinnabar/10 text-cinnabar border border-cinnabar/30">
                        {h.visibleWatermarkProvider ? `${h.visibleWatermarkProvider} 水印` : "水印"}
                      </span>
                    )}
                    {h.hasSynthid && (
                      <span className="px-1.5 py-0.5 rounded-full bg-sky-500/10 text-sky-300 border border-sky-400/30">
                        SynthID
                      </span>
                    )}
                  </div>
                )}
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(h.taskId);
                }}
                className="opacity-0 group-hover:opacity-100 text-ink-500 hover:text-verdict-fake text-xs px-1"
              >
                ✕
              </button>
            </div>
          );
        })}
      </div>

      <div className="p-3 text-[10px] text-ink-500 border-t border-ink-700">
        鉴真伪 · 明真相
      </div>
    </aside>
  );
}

function getInitialHistoryQuery() {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get("historyQuery") || "";
}

function getInitialHistoryFilter() {
  if (typeof window === "undefined") return "all";
  const value = new URLSearchParams(window.location.search).get("historyFilter");
  return value === "vlm" || value === "mock" || value === "forensics" || value === "provenance" || value === "watermark"
    ? value
    : "all";
}
