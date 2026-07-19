import { useEffect, useState } from "react";
import { HistoryFilterCounts, HistoryItem, HistorySidebarFilter, VERDICT_META, TYPE_LABEL } from "../api";
import IconfontIcon from "./IconfontIcon";
import type { IconfontName } from "./IconfontIcon";
import Logo from "./Logo";

const FILTER_OPTIONS = [
  { key: "all", label: "全部" },
  { key: "highly", label: "高风险" },
  { key: "suspected", label: "疑似风险" },
  { key: "real", label: "未见异常" },
  { key: "unknownVerdict", label: "待复核" },
  { key: "forensics", label: "含鉴伪线索" },
  { key: "provenance", label: "含内容凭证" },
  { key: "watermark", label: "含可见水印" },
  { key: "synthid", label: "含隐式水印" },
] as const;
type SidebarFilterKey = HistorySidebarFilter;
const PRIMARY_FILTER_KEYS = new Set<SidebarFilterKey>(["all", "highly", "suspected", "real", "unknownVerdict"]);
const PRIMARY_FILTER_OPTIONS = FILTER_OPTIONS.filter((item) => PRIMARY_FILTER_KEYS.has(item.key as SidebarFilterKey));
const MORE_FILTER_OPTIONS = FILTER_OPTIONS.filter((item) => !PRIMARY_FILTER_KEYS.has(item.key as SidebarFilterKey));
const FILTER_ICONS: Partial<Record<SidebarFilterKey, IconfontName>> = {
  all: "history",
  highly: "shield-check",
  suspected: "deep-analysis",
  real: "shield-check",
  unknownVerdict: "report",
  forensics: "deep-analysis",
  provenance: "archive",
  watermark: "image-forensics",
  synthid: "deep-analysis",
};

function isHistoryNoticeMessage(value?: string) {
  if (!value) return false;
  const text = value.toLowerCase();
  return (
    value.includes("请登录") ||
    value.includes("请先登录") ||
    value.includes("认证") ||
    value.includes("权限") ||
    value.includes("历史记录暂不可用") ||
    text.includes("unauthorized") ||
    text.includes("forbidden")
  );
}

interface Props {
  history: HistoryItem[];
  historyBusy?: boolean;
  totalCount?: number;
  filterCounts?: Partial<HistoryFilterCounts>;
  message?: string;
  query: string;
  filter: SidebarFilterKey;
  activeId?: string;
  activeItem?: HistoryItem;
  onSelect: (item: HistoryItem) => void;
  onQueryChange: (value: string) => void;
  onFilterChange: (value: SidebarFilterKey) => void;
  onNew: () => void;
  onDelete: (taskId: string) => void;
  onClearSelection?: () => void;
  onRetryHistory?: () => void;
  onRefreshHistory?: () => void;
  onLoadMore?: () => void;
  className?: string;
  onClose?: () => void;
}

export default function Sidebar({
  history,
  historyBusy = false,
  totalCount,
  filterCounts,
  message,
  query,
  filter,
  activeId,
  activeItem,
  onSelect,
  onQueryChange,
  onFilterChange,
  onNew,
  onDelete,
  onClearSelection,
  onRetryHistory,
  onRefreshHistory,
  onLoadMore,
  className = "",
  onClose,
}: Props) {
  const [copied, setCopied] = useState(false);
  const [showMoreFilters, setShowMoreFilters] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1800);
    return () => window.clearTimeout(timer);
  }, [copied]);
  const activeFilterLabel = FILTER_OPTIONS.find((item) => item.key === filter)?.label || "全部";
  const activeSummary = [
    { label: "筛选", value: activeFilterLabel },
    { label: "搜索", value: query.trim() || "未设置" },
  ];
  const activeFilterInMore = MORE_FILTER_OPTIONS.some((item) => item.key === filter);
  const showHistoryNotice = isHistoryNoticeMessage(message) && history.length === 0;

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
    <aside className={`w-72 shrink-0 bg-white/95 border-r border-ink-700 flex flex-col shadow-sm ${className}`}>
      <div className="p-4 flex items-center gap-2.5">
        <Logo size={36} idSuffix="side" />
        <div className="flex-1 min-w-0">
          <div className="text-lg font-semibold text-rice leading-tight">慧鉴 AI</div>
          <div className="text-[10px] text-brand-cyan">证据分析工作台</div>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="md:hidden h-8 px-2 rounded-lg border border-ink-600 text-xs text-ink-500"
            aria-label="关闭历史记录"
          >
            关闭
          </button>
        )}
      </div>

      <button
        onClick={() => {
          onNew();
          onClose?.();
        }}
        className="mx-4 mb-3 inline-flex items-center justify-center gap-2 py-2 rounded-lg bg-brand-blue text-white text-sm hover:bg-brand-cyan transition shadow-sm"
      >
        <IconfontIcon name="plus" size={16} />
        <span>新建检测</span>
      </button>

      <div className="px-4 pb-1 inline-flex items-center gap-1.5 text-[11px] text-ink-500 uppercase">
        <IconfontIcon name="history" size={13} />
        <span>历史记录</span>
      </div>
      <div className="flex-1 overflow-y-auto px-2 space-y-1">
        {showHistoryNotice ? (
          <div className="mx-2 mb-2 rounded-lg border border-ink-600 bg-ink-800 px-3 py-3">
            <div className="text-sm font-medium text-ink-950">登录后查看历史</div>
            <p className="mt-1 text-xs leading-relaxed text-ink-500">
              登录后可查看检测记录、继续打开报告并归档结果。
            </p>
            <a
              href="/"
              className="mt-3 flex w-full items-center justify-center rounded-lg bg-brand-blue px-3 py-2 text-xs font-medium text-white"
            >
              前往登录
            </a>
          </div>
        ) : (
          <div className="mx-2 mb-2 space-y-2">
            <div className="rounded-lg border border-ink-600 bg-ink-800 px-2.5 py-2 flex items-center gap-2">
              <IconfontIcon name="search" size={15} className="text-ink-500" />
              <input
                value={query}
                onChange={(event) => onQueryChange(event.target.value)}
                placeholder="搜索名称 / 报告号 / 判定 / 证据"
                className="min-w-0 flex-1 bg-transparent text-xs text-ink-950 placeholder:text-ink-500 outline-none"
              />
              {query.trim() && (
                <button
                  type="button"
                  onClick={() => onQueryChange("")}
                  className="shrink-0 rounded-md border border-ink-600 bg-ink-900 px-2 py-1 text-[10px] text-ink-500"
                >
                  清空
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1">
              {PRIMARY_FILTER_OPTIONS.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  onClick={() => onFilterChange(item.key as SidebarFilterKey)}
                  className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] border ${
                    filter === item.key
                      ? "border-cinnabar/40 bg-cinnabar/10 text-cinnabar"
                      : "border-ink-600 bg-ink-800 text-ink-500"
                  }`}
                >
                  <IconfontIcon name={FILTER_ICONS[item.key as SidebarFilterKey] || "history"} size={12} />
                  <span>{item.label}</span>
                  <span
                    className={`rounded-full px-1.5 py-0.5 text-[9px] ${
                      filter === item.key
                        ? "bg-cinnabar/15 text-cinnabar"
                        : "bg-ink-900 text-ink-500"
                    }`}
                  >
                    {filterCounts?.[item.key] ?? 0}
                  </span>
                </button>
              ))}
              <button
                type="button"
                onClick={() => setShowMoreFilters((value) => !value)}
                className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] border ${
                  activeFilterInMore || showMoreFilters
                    ? "border-brand-cyan/40 bg-brand-cyan/10 text-brand-cyan"
                    : "border-ink-600 bg-ink-800 text-ink-500"
                  }`}
              >
                <IconfontIcon name="deep-analysis" size={12} />
                更多筛选
                {activeFilterInMore && <span className="rounded-full bg-brand-cyan/15 px-1.5 py-0.5 text-[9px]">{activeFilterLabel}</span>}
              </button>
            </div>
            {(showMoreFilters || activeFilterInMore) && (
              <div className="flex flex-wrap gap-1 rounded-lg border border-ink-600 bg-ink-800 p-1.5">
                {MORE_FILTER_OPTIONS.map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => onFilterChange(item.key as SidebarFilterKey)}
                    className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] border ${
                      filter === item.key
                        ? "border-cinnabar/40 bg-cinnabar/10 text-cinnabar"
                        : "border-ink-600 bg-ink-900 text-ink-500"
                    }`}
                  >
                    <IconfontIcon name={FILTER_ICONS[item.key as SidebarFilterKey] || "deep-analysis"} size={12} />
                    <span>{item.label}</span>
                    <span
                      className={`rounded-full px-1.5 py-0.5 text-[9px] ${
                        filter === item.key
                          ? "bg-cinnabar/15 text-cinnabar"
                          : "bg-ink-800 text-ink-500"
                      }`}
                    >
                      {filterCounts?.[item.key] ?? 0}
                    </span>
                  </button>
                ))}
              </div>
            )}
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
                  onFilterChange("all");
                  onQueryChange("");
                }}
                className="w-full px-2 py-1.5 rounded-md text-[10px] border border-ink-600 bg-ink-800 text-ink-500"
              >
                重置条件
              </button>
            )}
            <div className="text-[10px] text-ink-500">
              当前显示 {history.length} / {totalCount ?? history.length}
            </div>
            {onLoadMore && (
              <button
                type="button"
                onClick={onLoadMore}
                disabled={historyBusy}
                className="w-full px-2 py-1.5 rounded-md text-[10px] border border-ink-600 bg-ink-800 text-ink-500"
              >
                {historyBusy ? "加载中" : "加载更多"}
              </button>
            )}
            {activeItem && (
              <div className="rounded-lg border border-ink-600 bg-ink-800 px-2.5 py-2 text-[10px] text-ink-500">
                <div className="font-medium text-ink-950 truncate">{activeItem.name}</div>
                <div className="mt-1 flex items-center justify-between gap-2">
                  <span className="truncate">{activeItem.reportId}</span>
                  {onClearSelection && (
                    <button
                      type="button"
                      onClick={onClearSelection}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-ink-600 bg-ink-900 text-[10px]"
                    >
                      <IconfontIcon name="close" size={11} />
                      清除选中
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
        {!showHistoryNotice && message && (
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
            </div>
          </div>
        )}
        {!showHistoryNotice && history.length === 0 && (
          <div className="px-2 py-4 text-xs text-ink-500">{message ? "登录后会显示历史记录" : "暂无记录"}</div>
        )}
        {!showHistoryNotice && totalCount !== 0 && history.length === 0 && (
          <div className="px-2 py-4 space-y-2">
            <div className="text-xs text-ink-500">当前搜索/筛选条件下暂无记录</div>
            <div className="flex flex-wrap gap-2">
              {query.trim() && (
                <button
                  type="button"
                  onClick={() => onQueryChange("")}
                  className="rounded-md border border-ink-600 bg-ink-800 px-2 py-1 text-[10px] text-ink-500"
                >
                  清空搜索
                </button>
              )}
              {(filter !== "all" || query.trim()) && (
                <button
                  type="button"
                  onClick={() => {
                    onFilterChange("all");
                    onQueryChange("");
                  }}
                  className="rounded-md border border-ink-600 bg-ink-800 px-2 py-1 text-[10px] text-ink-500"
                >
                  查看全部
                </button>
              )}
            </div>
          </div>
        )}
        {history.map((h) => {
          const meta = VERDICT_META[h.verdict];
          const confidence = h.confidence == null
            ? "未发布"
            : `${Math.round(h.confidence * 100)}%`;
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
                  <IconfontIcon name={historyTypeIcon(h)} size={18} />
                </span>
              )}
              <div className="flex-1 min-w-0">
                <div className="text-sm text-ink-950 truncate">{renderHighlightedText(h.name, query)}</div>
                <div className="text-[10px] text-ink-500 flex items-center gap-1 truncate">
                  <span className="truncate">#{renderHighlightedText(h.reportId, query)}</span>
                  <span className="shrink-0">·</span>
                  <span className="truncate">{renderHighlightedText(formatHistoryTime(h.createdAt), query)}</span>
                </div>
                <div className="text-[10px] flex items-center gap-1.5">
                  <span style={{ color: meta.color }}>{renderHighlightedText(meta.label, query)}</span>
                  <span className="text-ink-500">· {TYPE_LABEL[h.type]}</span>
                  <span className="text-ink-500">· 置信度 {renderHighlightedText(confidence, query)}</span>
                </div>
                {(h.hasForensics || h.hasProvenance || h.hasVisibleWatermark || h.hasSynthid) && (
                  <div className="mt-1 flex items-center gap-1.5 text-[10px]">
                    {h.hasForensics && (
                      <span className="px-1.5 py-0.5 rounded-full bg-brand-magenta/10 text-brand-magenta border border-brand-magenta/30">
                        {renderHighlightedText("深度取证", query)}
                      </span>
                    )}
                    {h.hasProvenance && (
                      <span className="px-1.5 py-0.5 rounded-full bg-jade/10 text-jade border border-jade/30">
                        {renderHighlightedText("内容凭证", query)}
                      </span>
                    )}
                    {h.hasVisibleWatermark && (
                      <span className="px-1.5 py-0.5 rounded-full bg-cinnabar/10 text-cinnabar border border-cinnabar/30">
                        {renderHighlightedText(h.visibleWatermarkProvider ? `${h.visibleWatermarkProvider} 水印` : "水印", query)}
                      </span>
                    )}
                    {h.hasSynthid && (
                      <span className="px-1.5 py-0.5 rounded-full bg-sky-500/10 text-sky-300 border border-sky-400/30">
                        {renderHighlightedText("隐式水印", query)}
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
                aria-label="删除历史记录"
              >
                <IconfontIcon name="close" size={14} />
              </button>
            </div>
          );
        })}
      </div>

      <div className="border-t border-ink-700 p-3 text-[10px] text-ink-500">
        <div>核证据 · 慎结论</div>
        <a
          href="https://beian.miit.gov.cn/"
          target="_blank"
          rel="noreferrer"
          className="mt-1 inline-block hover:text-ink-950"
        >
          浙ICP备2026051442号
        </a>
      </div>
    </aside>
  );
}

function historyTypeIcon(item: HistoryItem): IconfontName {
  if (item.type === "video") return "video-forensics";
  if (item.type === "document") return "report";
  return "image-forensics";
}

function formatHistoryTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
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
