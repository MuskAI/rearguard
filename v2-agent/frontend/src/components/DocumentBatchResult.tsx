import { FileImage, Maximize2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DocumentDetectionAsset, DocumentDetectionTask } from "../api";
import "../document-batch.css";
import { StatusIcon } from "./BrandSystem";

interface Props {
  task: DocumentDetectionTask;
}

type Filter = "all" | "fake" | "real" | "skipped" | "failed";

function percent(value?: number | null) {
  return `${Math.round(Math.max(0, Math.min(Number(value || 0), 1)) * 100)}%`;
}

function locationLabel(asset: DocumentDetectionAsset) {
  if (asset.pageNumber) return `第 ${asset.pageNumber} 页`;
  if (asset.partPath?.includes("header")) return "页眉";
  if (asset.partPath?.includes("footer")) return "页脚";
  return `图片 ${asset.ordinal}`;
}

export default function DocumentBatchResult({ task }: Props) {
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<DocumentDetectionAsset | null>(null);
  const lightboxPanelRef = useRef<HTMLDivElement>(null);
  const lightboxCloseRef = useRef<HTMLButtonElement>(null);
  const lightboxTriggerRef = useRef<HTMLButtonElement | null>(null);
  const terminal = ["completed", "partial_success", "failed", "cancelled"].includes(task.status);
  const summary = task.summary;
  const routedOnly = Boolean(terminal && summary?.verdict === "no_result" && task.skipped && !task.failed);
  const incompleteTerminal = Boolean(
    terminal && (
      task.status === "failed"
      || task.status === "cancelled"
      || (summary?.verdict === "no_result" && !routedOnly)
    )
  );
  const filtered = useMemo(() => task.assets.filter((asset) => {
    if (filter === "failed") return asset.status === "failed";
    if (filter === "skipped") return asset.status === "skipped";
    if (filter === "fake") return asset.status === "completed" && asset.verdict !== "real";
    if (filter === "real") return asset.status === "completed" && asset.verdict === "real";
    return true;
  }), [filter, task.assets]);

  const isFake = Boolean(summary?.verdict && summary.verdict !== "real" && summary.verdict !== "no_result");
  const heading = routedOnly ? "未发现需要鉴伪的图片" : summary?.verdictLabel
    || (task.status === "failed" ? "文档检测未完成" : task.status === "cancelled" ? "文档检测已取消" : task.discovered ? `已提取 ${task.discovered} 张图片` : "正在解析文档图片");
  const description = incompleteTerminal
    ? task.error || (task.status === "cancelled" ? "任务已停止，未生成文档图像结论。" : "本次没有形成可用的文档图像结论。")
    : routedOnly
      ? `共提取 ${task.discovered} 张图片，Router 判断这些对象均为蒙版、装饰或重复内容。`
    : terminal
      ? `共提取 ${task.discovered} 张图片，送检 ${task.succeeded} 张${task.skipped ? `，Router 跳过 ${task.skipped} 张` : ""}${task.failed ? `，${task.failed} 张失败` : ""}。`
      : `已完成 ${task.completed}/${task.discovered || "待确认"} 张，结果会逐张出现。`;
  const statusIcon = task.status === "failed" ? "error" : incompleteTerminal ? "partial" : isFake ? "fake" : terminal ? "real" : "processing";

  const closeLightbox = useCallback(() => {
    setSelected(null);
    window.requestAnimationFrame(() => lightboxTriggerRef.current?.focus());
  }, []);

  useEffect(() => {
    if (!selected) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusFrame = window.requestAnimationFrame(() => lightboxCloseRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeLightbox();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(lightboxPanelRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])') || []);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [closeLightbox, selected]);

  return (
    <section className="document-result" aria-label="文档图片检测结果">
      <header className={`document-result-summary ${incompleteTerminal ? "is-failed" : isFake ? "is-fake" : "is-real"}`}>
        <span className="document-result-symbol" aria-hidden="true"><StatusIcon name={statusIcon} size={28} /></span>
        <div>
          <small>{incompleteTerminal ? "文档任务状态" : terminal ? "文档图像综合结论" : "文档图像正在检测"}</small>
          <h2>{heading}</h2>
          <p>{description}</p>
        </div>
        <div className="document-result-score">
          <strong>{incompleteTerminal ? "—" : routedOnly ? "0" : summary?.averageAiProbability == null ? `${task.progress}%` : percent(summary.averageAiProbability)}</strong>
          <span>{incompleteTerminal ? "未形成完整结论" : routedOnly ? "鉴伪模型调用" : summary?.averageAiProbability == null ? "任务进度" : "平均 AI 风险"}</span>
        </div>
      </header>

      <div className="document-result-metrics" aria-label="文档处理统计">
        <div><span>页数</span><strong>{task.pageCount ?? "解析中"}</strong></div>
        <div><span>提取图片</span><strong>{task.discovered}</strong></div>
        <div><span>真实</span><strong>{summary?.realCount ?? task.assets.filter((item) => item.verdict === "real").length}</strong></div>
        <div><span>AI 生成</span><strong>{summary?.fakeCount ?? task.assets.filter((item) => item.status === "completed" && item.verdict !== "real").length}</strong></div>
        <div><span>无需检测</span><strong>{summary?.skipCount ?? task.skipped ?? task.assets.filter((item) => item.status === "skipped").length}</strong></div>
      </div>

      {task.warnings.length > 0 && <p className="document-result-warning"><StatusIcon name="warning" size={16} /> {task.warnings.join("；")}</p>}

      <div className="document-result-toolbar">
        <div>
          {(["all", "fake", "real", "skipped", "failed"] as const).map((value) => (
            <button key={value} type="button" className={filter === value ? "is-active" : ""} onClick={() => setFilter(value)}>
              {{ all: "全部", fake: "AI 生成", real: "真实", skipped: "无需检测", failed: "失败" }[value]}
            </button>
          ))}
        </div>
        <span>{filtered.length} 项</span>
      </div>

      {filtered.length ? (
        <div className="document-asset-grid">
          {filtered.map((asset) => (
            <article key={`${asset.ordinal}-${asset.occurrenceIndex}`} className={`document-asset ${asset.status === "failed" ? "is-failed" : asset.status === "skipped" ? "is-skipped" : asset.verdict === "real" ? "is-real" : "is-fake"}`}>
              <button type="button" className="document-asset-preview" onClick={(event) => { if (asset.preview) { lightboxTriggerRef.current = event.currentTarget; setSelected(asset); } }} disabled={!asset.preview} aria-label={`放大查看${locationLabel(asset)}`}>
                {asset.preview ? <img src={asset.preview} alt={`${locationLabel(asset)}提取图片`} /> : <FileImage aria-hidden="true" />}
                {asset.preview && <span><Maximize2 size={16} /> 放大</span>}
              </button>
              <div className="document-asset-copy">
                <span>{locationLabel(asset)}{asset.reused ? " · 复用结果" : ""}</span>
                <strong>{asset.status === "failed" ? "检测失败" : asset.status === "skipped" ? asset.router?.categoryLabel || "无需检测" : asset.verdictLabel || (asset.verdict === "real" ? "真实图像" : "AI 生成图像")}</strong>
                <small>{asset.status === "failed" ? asset.error : asset.status === "skipped" ? `Router 置信度 ${percent(asset.router?.confidence)} · ${asset.width}×${asset.height}` : `AI 风险 ${percent(asset.aiProbability)} · ${asset.width}×${asset.height}`}</small>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <div className="document-result-empty"><FileImage /><strong>当前筛选下没有图片</strong><p>切换筛选条件查看其他检测结果。</p></div>
      )}

      {selected && (
        <div className="document-lightbox" role="dialog" aria-modal="true" aria-label={`${locationLabel(selected)}放大图`} onMouseDown={(event) => event.target === event.currentTarget && closeLightbox()}>
          <div ref={lightboxPanelRef} className="document-lightbox-panel">
            <header><div><strong>{locationLabel(selected)}</strong><span>{selected.status === "skipped" ? `${selected.router?.categoryLabel || "无需检测"} · Router 置信度 ${percent(selected.router?.confidence)}` : `${selected.verdictLabel} · AI 风险 ${percent(selected.aiProbability)}`}</span></div><button ref={lightboxCloseRef} type="button" onClick={closeLightbox} aria-label="关闭放大视图"><X /></button></header>
            <div className="document-lightbox-canvas">
              <img src={selected.preview || ""} alt={`${locationLabel(selected)}放大图`} />
              {(selected.regions || []).map((region, index) => (
                <span key={index} className="document-region" style={{ left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.w * 100}%`, height: `${region.h * 100}%` }}><b>{region.label}</b></span>
              ))}
            </div>
            {selected.explanation && <p>{selected.explanation}</p>}
          </div>
        </div>
      )}
    </section>
  );
}
