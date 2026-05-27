import { useEffect, useMemo, useState } from "react";
import { HistoryItem, VERDICT_META, TYPE_LABEL } from "../api";
import Logo from "./Logo";

const FILTER_OPTIONS = [
  { key: "all", label: "全部" },
  { key: "vlm", label: "VLM" },
  { key: "mock", label: "Mock" },
  { key: "forensics", label: "取证" },
  { key: "provenance", label: "凭证" },
  { key: "watermark", label: "水印" },
] as const;
type SidebarFilterKey = (typeof FILTER_OPTIONS)[number]["key"];

interface Props {
  history: HistoryItem[];
  historyBusy?: boolean;
  message?: string;
  accessProtectionEnabled?: boolean;
  activeId?: string;
  activeItem?: HistoryItem;
  onSelect: (item: HistoryItem) => void;
  onNew: () => void;
  onDelete: (taskId: string) => void;
  onClearSelection?: () => void;
  onConfigureAccess?: () => void;
  onRetryHistory?: () => void;
  onRefreshHistory?: () => void;
  className?: string;
  onClose?: () => void;
}

export default function Sidebar({
  history,
  historyBusy = false,
  message,
  accessProtectionEnabled = false,
  activeId,
  activeItem,
  onSelect,
  onNew,
  onDelete,
  onClearSelection,
  onConfigureAccess,
  onRetryHistory,
  onRefreshHistory,
  className = "",
  onClose,
}: Props) {
  const [query, setQuery] = useState(() => getInitialHistoryQuery());
  const [filter, setFilter] = useState<SidebarFilterKey>(() => getInitialHistoryFilter());
  const [copied, setCopied] = useState(false);

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

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1800);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const filteredHistory = useMemo(() => {
    const q = query.trim().toLowerCase();
    return history.filter((item) => {
      if (!matchesFilter(item, filter)) return false;
      if (!q) return true;
      return getSearchableHistoryFields(item).some((field) => field.toLowerCase().includes(q));
    });
  }, [filter, history, query]);
  const filterCounts = useMemo(() => {
    const q = query.trim().toLowerCase();
    return Object.fromEntries(
      FILTER_OPTIONS.map((item) => [
        item.key,
        history.filter((entry) => {
          if (!matchesFilter(entry, item.key)) return false;
          if (!q) return true;
          return getSearchableHistoryFields(entry).some((field) => field.toLowerCase().includes(q));
        }).length,
      ]),
    ) as Record<SidebarFilterKey, number>;
  }, [history, query]);
  const activeFilterLabel = FILTER_OPTIONS.find((item) => item.key === filter)?.label || "全部";
  const activeSummary = [
    { label: "筛选", value: activeFilterLabel },
    { label: "搜索", value: query.trim() || "未设置" },
  ];

  async function copyCurrentView() {
    const url = window.location.href;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      window.prompt("复制当前历史视图链接", url);
    }
  }

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
          <div className="rounded-lg border border-ink-600 bg-ink-800 px-2.5 py-2 flex items-center gap-2">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索名称 / 报告号 / 判定 / 来源 / 证据"
              className="min-w-0 flex-1 bg-transparent text-xs text-ink-950 placeholder:text-ink-500 outline-none"
            />
            {query.trim() && (
              <button
                type="button"
                onClick={() => setQuery("")}
                className="shrink-0 rounded-md border border-ink-600 bg-ink-900 px-2 py-1 text-[10px] text-ink-500"
              >
                清空
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-1">
            {FILTER_OPTIONS.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setFilter(item.key as typeof filter)}
                className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] border ${
                  filter === item.key
                    ? "border-cinnabar/40 bg-cinnabar/10 text-cinnabar"
                    : "border-ink-600 bg-ink-800 text-ink-500"
                }`}
              >
                <span>{item.label}</span>
                <span
                  className={`rounded-full px-1.5 py-0.5 text-[9px] ${
                    filter === item.key
                      ? "bg-cinnabar/15 text-cinnabar"
                      : "bg-ink-900 text-ink-500"
                  }`}
                >
                  {filterCounts[item.key]}
                </span>
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={copyCurrentView}
            className={`w-full px-2 py-1.5 rounded-md text-[10px] border ${
              copied
                ? "border-jade/40 bg-jade/10 text-jade"
                : "border-ink-600 bg-ink-800 text-ink-500"
            }`}
          >
            {copied ? "已复制当前视图链接" : "复制当前视图链接"}
          </button>
          {onRefreshHistory && (
            <button
              type="button"
              onClick={onRefreshHistory}
              disabled={historyBusy}
              className="w-full px-2 py-1.5 rounded-md text-[10px] border border-ink-600 bg-ink-800 text-ink-500"
            >
              {historyBusy ? "刷新中" : "刷新历史"}
            </button>
          )}
          <div className="flex flex-wrap gap-1">
            {activeSummary.map((item) => (
              <span key={item.label} className="px-2 py-1 rounded-md text-[10px] border border-ink-600 bg-ink-800 text-ink-500">
                <strong className="text-ink-950">{item.label}</strong>
                <span className="ml-1">{item.value}</span>
              </span>
            ))}
          </div>
          {(filter !== "all" || query.trim()) && (
            <button
              type="button"
              onClick={() => {
                setFilter("all");
                setQuery("");
              }}
              className="w-full px-2 py-1.5 rounded-md text-[10px] border border-ink-600 bg-ink-800 text-ink-500"
            >
              重置条件
            </button>
          )}
          <div className="text-[10px] text-ink-500">
            当前显示 {filteredHistory.length} / {history.length}
          </div>
          {activeItem && (
            <div className="rounded-lg border border-ink-600 bg-ink-800 px-2.5 py-2 text-[10px] text-ink-500">
              <div className="font-medium text-ink-950 truncate">{activeItem.name}</div>
              <div className="mt-1 flex items-center justify-between gap-2">
                <span className="truncate">{activeItem.reportId}</span>
                {onClearSelection && (
                  <button
                    type="button"
                    onClick={onClearSelection}
                    className="px-2 py-1 rounded-md border border-ink-600 bg-ink-900 text-[10px]"
                  >
                    清除选中
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
        {message && (
          <div className="mx-2 mb-2 rounded-lg border border-ink-600 bg-ink-800 px-2.5 py-2 text-[11px] text-ink-500">
            <div>{message}</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {onRetryHistory && (
                <button
                  type="button"
                  onClick={onRetryHistory}
                  className="rounded-md border border-ink-600 bg-ink-900 px-2 py-1 text-[10px]"
                >
                  {historyBusy ? "加载中" : "重试加载"}
                </button>
              )}
              {accessProtectionEnabled && onConfigureAccess && (
                <button
                  type="button"
                  onClick={onConfigureAccess}
                  className="rounded-md border border-ink-600 bg-ink-900 px-2 py-1 text-[10px]"
                >
                  配置令牌
                </button>
              )}
            </div>
          </div>
        )}
        {history.length === 0 && (
          <div className="px-2 py-4 text-xs text-ink-500">{message ? "配置完成后会显示历史记录" : "暂无记录"}</div>
        )}
        {history.length > 0 && filteredHistory.length === 0 && (
          <div className="px-2 py-4 space-y-2">
            <div className="text-xs text-ink-500">当前搜索/筛选条件下暂无记录</div>
            <div className="flex flex-wrap gap-2">
              {query.trim() && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  className="rounded-md border border-ink-600 bg-ink-800 px-2 py-1 text-[10px] text-ink-500"
                >
                  清空搜索
                </button>
              )}
              {(filter !== "all" || query.trim()) && (
                <button
                  type="button"
                  onClick={() => {
                    setFilter("all");
                    setQuery("");
                  }}
                  className="rounded-md border border-ink-600 bg-ink-800 px-2 py-1 text-[10px] text-ink-500"
                >
                  查看全部
                </button>
              )}
            </div>
          </div>
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
                <div className="text-sm text-ink-950 truncate">{renderHighlightedText(h.name, query)}</div>
                <div className="text-[10px] text-ink-500 truncate">#{renderHighlightedText(h.reportId, query)}</div>
                <div className="text-[10px] flex items-center gap-1.5">
                  <span style={{ color: meta.color }}>{renderHighlightedText(meta.label, query)}</span>
                  <span className="text-ink-500">· {TYPE_LABEL[h.type]}</span>
                  {h.source === "vlm" && <span className="text-brand-cyan">{renderHighlightedText("VLM", query)}</span>}
                  {h.source === "mock" && <span className="text-cinnabar">{renderHighlightedText("Mock", query)}</span>}
                  {h.cacheHit && <span className="text-jade">{renderHighlightedText("缓存", query)}</span>}
                </div>
                {(h.hasForensics || h.hasProvenance || h.hasVisibleWatermark || h.hasSynthid) && (
                  <div className="mt-1 flex items-center gap-1.5 text-[10px]">
                    {h.hasForensics && (
                      <span className="px-1.5 py-0.5 rounded-full bg-brand-magenta/10 text-brand-magenta border border-brand-magenta/30">
                        {renderHighlightedText("取证", query)}
                      </span>
                    )}
                    {h.hasProvenance && (
                      <span className="px-1.5 py-0.5 rounded-full bg-jade/10 text-jade border border-jade/30">
                        {renderHighlightedText("凭证", query)}
                      </span>
                    )}
                    {h.hasVisibleWatermark && (
                      <span className="px-1.5 py-0.5 rounded-full bg-cinnabar/10 text-cinnabar border border-cinnabar/30">
                        {renderHighlightedText(h.visibleWatermarkProvider ? `${h.visibleWatermarkProvider} 水印` : "水印", query)}
                      </span>
                    )}
                    {h.hasSynthid && (
                      <span className="px-1.5 py-0.5 rounded-full bg-sky-500/10 text-sky-300 border border-sky-400/30">
                        {renderHighlightedText("SynthID", query)}
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

function getInitialHistoryFilter(): SidebarFilterKey {
  if (typeof window === "undefined") return "all";
  const value = new URLSearchParams(window.location.search).get("historyFilter");
  return value === "vlm" || value === "mock" || value === "forensics" || value === "provenance" || value === "watermark"
    ? value
    : "all";
}

function matchesFilter(item: HistoryItem, filter: SidebarFilterKey) {
  if (filter === "vlm") return item.source === "vlm";
  if (filter === "mock") return item.source === "mock";
  if (filter === "forensics") return Boolean(item.hasForensics);
  if (filter === "provenance") return Boolean(item.hasProvenance);
  if (filter === "watermark") return Boolean(item.hasVisibleWatermark || item.hasSynthid);
  return true;
}

function getSearchableHistoryFields(item: HistoryItem) {
  const sourceLabels = {
    vlm: ["vlm", "VLM", "真实模型"],
    mock: ["mock", "Mock", "mock 回退"],
    "maps-only": ["maps-only", "仅证据图"],
    unknown: ["unknown", "未知来源"],
  } as const;
  const meta = VERDICT_META[item.verdict];
  return [
    item.name,
    item.reportId,
    meta.label,
    TYPE_LABEL[item.type],
    item.source || "",
    ...(item.source ? sourceLabels[item.source as keyof typeof sourceLabels] || [] : []),
    item.visibleWatermarkProvider || "",
    item.visibleWatermarkProvider ? `${item.visibleWatermarkProvider} 水印` : "",
    item.hasForensics ? "取证" : "",
    item.hasProvenance ? "凭证" : "",
    item.hasVisibleWatermark ? "水印" : "",
    item.hasSynthid ? "SynthID" : "",
    item.cacheHit ? "缓存" : "",
  ].map((field) => String(field));
}

function renderHighlightedText(text: string, query: string) {
  const trimmed = query.trim();
  if (!trimmed) return text;
  const escaped = trimmed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(`(${escaped})`, "ig");
  const lower = trimmed.toLowerCase();
  return text.split(pattern).map((part, index) =>
    part.toLowerCase() === lower ? (
      <mark
        key={`${part}-${index}`}
        className="rounded bg-cinnabar/20 px-0.5 text-cinnabar"
      >
        {part}
      </mark>
    ) : (
      <span key={`${part}-${index}`}>{part}</span>
    ),
  );
}
