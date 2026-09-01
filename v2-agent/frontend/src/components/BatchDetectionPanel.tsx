import { useMemo, useState } from "react";
import {
  CheckCircle2,
  ChevronRight,
  CircleStop,
  File,
  FileWarning,
  FolderOpen,
  LoaderCircle,
  Search,
  XCircle,
} from "lucide-react";
import type { BatchDetectionItem, ImageAnalysisMode } from "../agentTypes";

const INITIAL_VISIBLE_ITEMS = 100;

function elapsed(item: BatchDetectionItem): string {
  if (!item.startedAt) return "";
  const end = item.finishedAt || Date.now();
  const seconds = Math.max(0, (end - item.startedAt) / 1000);
  return seconds < 10 ? `${seconds.toFixed(1)} 秒` : `${Math.round(seconds)} 秒`;
}

function statusCopy(item: BatchDetectionItem) {
  if (item.status === "completed") return item.verdictLabel || "检测完成";
  if (item.status === "failed") return "检测失败";
  if (item.status === "skipped") return "已跳过";
  if (item.status === "cancelled") return "已停止";
  if (item.status === "running") return "检测中";
  return "等待中";
}

export default function BatchDetectionPanel({
  items,
  running,
  mode,
  onStop,
  onOpen,
  onSelectFolder,
}: {
  items: BatchDetectionItem[];
  running: boolean;
  mode: ImageAnalysisMode;
  onStop: () => void;
  onOpen: (item: BatchDetectionItem) => void;
  onSelectFolder: () => void;
}) {
  const [query, setQuery] = useState("");
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE_ITEMS);
  const completed = items.filter((item) => item.status === "completed").length;
  const failed = items.filter((item) => item.status === "failed").length;
  const skipped = items.filter((item) => ["skipped", "cancelled"].includes(item.status)).length;
  const fake = items.filter((item) => item.verdict === "fake").length;
  const real = items.filter((item) => item.verdict === "real").length;
  const settled = completed + failed + skipped;
  const overallProgress = items.length === 0
    ? 0
    : Math.round(items.reduce((sum, item) => sum + item.progress, 0) / items.length);
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return normalized ? items.filter((item) => item.relativePath.toLowerCase().includes(normalized)) : items;
  }, [items, query]);
  const visible = filtered.slice(0, visibleCount);

  return (
    <section className="batch-detection" aria-labelledby="batch-detection-title">
      <header className="batch-detection-header">
        <div>
          <span><FolderOpen size={16} /> 文件夹批量检测</span>
          <h2 id="batch-detection-title">{running ? "正在逐项鉴伪" : "批量检测已完成"}</h2>
          <p>{items.length} 个文件 · 图片使用{mode === "swarm" ? " Swarm 模式" : "快速检测"} · 每项独立保存结果</p>
        </div>
        {running ? (
          <button type="button" className="secondary-button" onClick={onStop}><CircleStop size={16} /> 停止剩余任务</button>
        ) : (
          <button type="button" className="secondary-button" onClick={onSelectFolder}><FolderOpen size={16} /> 选择新文件夹</button>
        )}
      </header>

      <div className="batch-overall-progress" aria-label={`批量检测进度 ${overallProgress}%`}>
        <div><strong>{settled}/{items.length}</strong><span>{running ? "已处理" : "处理完成"}</span><b>{overallProgress}%</b></div>
        <i><span style={{ width: `${overallProgress}%` }} /></i>
      </div>

      <div className="batch-summary" aria-label="批量检测汇总">
        <div><span>真实</span><strong>{real}</strong></div>
        <div><span>AI 生成</span><strong>{fake}</strong></div>
        <div><span>失败</span><strong>{failed}</strong></div>
        <div><span>跳过</span><strong>{skipped}</strong></div>
      </div>

      <div className="batch-list-toolbar">
        <label><Search size={16} /><input type="search" value={query} onChange={(event) => { setQuery(event.target.value); setVisibleCount(INITIAL_VISIBLE_ITEMS); }} placeholder="搜索文件路径" aria-label="搜索批量文件" /></label>
        <span>{filtered.length} 项</span>
      </div>

      <div className="batch-file-list" role="region" aria-label="批量文件检测结果">
        {visible.map((item) => {
          const canOpen = item.status === "completed" && Boolean(item.outcome || item.documentTask);
          const StatusIcon = item.status === "completed" ? CheckCircle2
            : item.status === "failed" ? XCircle
              : item.status === "running" ? LoaderCircle
                : item.status === "skipped" || item.status === "cancelled" ? FileWarning
                  : File;
          return (
            <button
              type="button"
              className={`batch-file-row is-${item.status}${item.verdict ? ` verdict-${item.verdict}` : ""}`}
              key={item.id}
              disabled={!canOpen}
              onClick={() => canOpen && onOpen(item)}
              title={item.error || item.relativePath}
            >
              <span className="batch-file-status"><StatusIcon size={18} className={item.status === "running" ? "spin" : undefined} /></span>
              <span className="batch-file-copy"><strong>{item.relativePath}</strong><small>{item.error || `${item.kind === "image" ? "图像" : item.kind === "video" ? "视频" : item.kind === "document" ? "文档" : "不支持"}${elapsed(item) ? ` · ${elapsed(item)}` : ""}`}</small></span>
              <span className="batch-file-result"><strong>{statusCopy(item)}</strong>{item.probability != null && <small>{(item.probability * 100).toFixed(1)}%</small>}</span>
              {item.status === "running" && <i className="batch-item-progress"><span style={{ width: `${item.progress}%` }} /></i>}
              {canOpen && <ChevronRight className="batch-open-icon" size={17} />}
            </button>
          );
        })}
      </div>
      {visibleCount < filtered.length && (
        <button type="button" className="batch-load-more" onClick={() => setVisibleCount((value) => value + INITIAL_VISIBLE_ITEMS)}>再显示 {Math.min(INITIAL_VISIBLE_ITEMS, filtered.length - visibleCount)} 项</button>
      )}
      <p className="batch-retention-note">文件按队列逐个上传，每个文件仍受单文件大小与账号额度限制；浏览器不会把整个文件夹打包保存到服务器。</p>
    </section>
  );
}
