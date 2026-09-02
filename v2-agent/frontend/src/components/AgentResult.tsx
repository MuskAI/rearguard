import {
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type RefCallback,
  type RefObject,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  Camera,
  CheckCircle2,
  ChevronDown,
  Copy,
  Download,
  FileSearch,
  FileText,
  Fingerprint,
  Gauge,
  Image as ImageIcon,
  Info,
  Layers3,
  Link2,
  LoaderCircle,
  Maximize2,
  MousePointer2,
  Play,
  Search,
  ScanSearch,
  ScanLine,
  ShieldCheck,
  ShieldOff,
  Video,
  X,
  ZoomIn,
} from "lucide-react";
import { createPortal } from "react-dom";
import type { AgentOutcome } from "../agentTypes";
import { binaryVerdictLabel, binaryVideoVerdictLabel, isFakeVerdict } from "../binaryVerdict";
import {
  createReportShareLink,
  listReportShares,
  revokeReportShare,
  type CaptureEvidence,
  type ProbabilityModel,
  type ProvenanceReport,
  type ReportShareItem,
  type SynthIDResult,
  type VisibleWatermarkHit,
  type VisibleWatermarkResult,
  type VideoEvidence,
} from "../api";
import {
  buildEvidenceExplanation,
  displayableWatermarkHits,
  hasDecisiveAiWatermark,
  type ExplanationPoint,
} from "../evidenceExplanation";
import { StatusIcon } from "./BrandSystem";
import WatermarkPipeline from "./WatermarkPipeline";

type ResultTab = "summary" | "evidence" | "file";

interface Props {
  outcome: AgentOutcome;
  provenanceAvailable: boolean;
  provenanceBusy: boolean;
  downloadBusy: boolean;
  actionError?: string;
  onRetryAction?: () => void;
  onProvenance: () => void;
  onDownload: () => void;
}

interface VerdictView {
  label: string;
  description: string;
  risk: number;
  modelProbability: number | null;
  riskLabel: string;
  tone: "real" | "warn" | "fake";
  confidence: string;
  reviewOnly: boolean;
}

const AI_WATERMARK_PROVIDERS = new Set(["gemini", "doubao", "jimeng", "jimeng_pill", "samsung"]);

function clamp01(value: number) {
  return Math.max(0, Math.min(value, 1));
}

function probabilityValue(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return clamp01(parsed > 1 ? parsed / 100 : parsed);
}

function legacyModelProbability(explanation: unknown): number | null {
  const match = String(explanation ?? "").match(
    /(?:原始(?:主模型)?\s*AI\s*(?:风险|分数)|raw\s+AI\s+(?:risk|score))\s*(?:为|[:：=])\s*([+-]?\d+(?:\.\d+)?)\s*(%)?/i,
  );
  if (!match) return null;
  const parsed = Number(match[1]);
  if (!Number.isFinite(parsed)) return null;
  return clamp01(match[2] || parsed > 1 ? parsed / 100 : parsed);
}

function probabilityText(value: number | null): string {
  if (value === null) return "未返回";
  const percentage = value * 100;
  return Number.isInteger(percentage) ? String(percentage) : percentage.toFixed(1);
}

function publicCopy(value: unknown) {
  return String(value ?? "")
    .replace(/\bDINO(?:v?3)?(?:[-\s_/]+ViT[-\s_/]*[A-Za-z0-9.]+)?/gi, "鉴伪分析")
    .replace(/\bViT(?:[-\s_/]+[A-Za-z0-9.]+)*/gi, "鉴伪分析")
    .replace(/\b(?:CUDA|CPU)ExecutionProvider\b/gi, "计算服务")
    .replace(/\bONNX(?:Runtime)?\b/gi, "推理服务")
    .replace(/\bYOLO(?:v?\d+)?[A-Za-z0-9_./-]*/gi, "区域定位")
    .replace(/\bRapidOCR\b/gi, "文字识别")
    .replace(/\b(?:FAISS|CLIP)(?:\/CLIP)?\b/gi, "图形检索")
    .replace(/wiltodelta\/remove-ai-watermarks/gi, "平台标记匹配")
    .replace(/corzent\/yolo11x_watermark_detection/gi, "区域定位")
    .replace(/\b(?:GPU|CPU)\b/gi, "计算服务")
    .replace(/\s{2,}/g, " ")
    .trim();
}

interface PreviewWatermarkMark {
  hit: VisibleWatermarkHit;
  index: number;
}

interface PreviewImageFrame {
  left: number;
  top: number;
  width: number;
  height: number;
}

function AnnotatedImagePreview({
  src,
  alt,
  marks,
  onOpen,
}: {
  src: string;
  alt: string;
  marks: PreviewWatermarkMark[];
  onOpen?: () => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const [frame, setFrame] = useState<PreviewImageFrame | null>(null);

  const updateFrame = useCallback(() => {
    const host = hostRef.current;
    const image = imageRef.current;
    if (!host || !image || image.naturalWidth <= 0 || image.naturalHeight <= 0) return;
    const scale = Math.min(host.clientWidth / image.naturalWidth, host.clientHeight / image.naturalHeight);
    const width = image.naturalWidth * scale;
    const height = image.naturalHeight * scale;
    setFrame({
      left: (host.clientWidth - width) / 2,
      top: (host.clientHeight - height) / 2,
      width,
      height,
    });
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(updateFrame) : null;
    observer?.observe(host);
    window.addEventListener("resize", updateFrame);
    updateFrame();
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateFrame);
    };
  }, [src, updateFrame]);

  return (
    <div
      className={`result-preview-image ${onOpen ? "is-interactive" : ""}`}
      ref={hostRef}
      role={onOpen ? "button" : undefined}
      tabIndex={onOpen ? 0 : undefined}
      aria-label={onOpen ? `放大查看${alt}` : undefined}
      onClick={onOpen}
      onKeyDown={onOpen ? (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      } : undefined}
    >
      <img ref={imageRef} src={src} alt={alt} onLoad={updateFrame} />
      {frame && marks.length > 0 && (
        <div
          className="result-preview-watermarks"
          role="group"
          aria-label={`图中已标注 ${marks.length} 处可见水印`}
          style={{ left: frame.left, top: frame.top, width: frame.width, height: frame.height }}
        >
          {marks.map(({ hit, index }) => {
            const x = clamp01(Number(hit.bbox?.x || 0));
            const y = clamp01(Number(hit.bbox?.y || 0));
            const width = Math.min(clamp01(Number(hit.bbox?.w || 0)), 1 - x);
            const height = Math.min(clamp01(Number(hit.bbox?.h || 0)), 1 - y);
            const label = hit.label || (AI_WATERMARK_PROVIDERS.has(hit.provider) ? "AI 平台水印" : "可见水印");
            return (
              <span
                className={`result-preview-watermark-box ${AI_WATERMARK_PROVIDERS.has(hit.provider) ? "is-platform" : ""}`}
                key={`${hit.provider}-${index}-${x}-${y}`}
                style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` }}
                aria-label={`第 ${index} 处水印：${label}，置信度 ${Math.round(hit.confidence * 100)}%`}
                title={`${label} · ${Math.round(hit.confidence * 100)}%`}
              >
                <b>{String(index).padStart(2, "0")}</b>
              </span>
            );
          })}
        </div>
      )}
      {onOpen && <span className="result-preview-zoom" aria-hidden="true"><ZoomIn size={15} /></span>}
    </div>
  );
}

function formatVideoTime(value: number) {
  const safe = Math.max(0, Number(value) || 0);
  const minutes = Math.floor(safe / 60);
  const seconds = safe - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${seconds.toFixed(1).padStart(4, "0")}`;
}

function VideoResultPreview({
  sources,
  name,
  videoRef,
  openButtonRef,
  onTimeChange,
  onOpen,
}: {
  sources: string[];
  name: string;
  videoRef: RefCallback<HTMLVideoElement>;
  openButtonRef: RefObject<HTMLButtonElement>;
  onTimeChange: (time: number) => void;
  onOpen: (src: string) => void;
}) {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [sourceIndex, setSourceIndex] = useState(0);
  const [retryToken, setRetryToken] = useState(0);
  const sourceSignature = sources.join("\n");
  const src = sources[sourceIndex] || sources[0];

  useEffect(() => {
    setSourceIndex(0);
    setRetryToken(0);
    setState("loading");
  }, [sourceSignature]);

  if (!src) return null;

  return (
    <div className={`video-result-preview is-${state}`} aria-busy={state === "loading"}>
      <video
        key={`${src}:${retryToken}`}
        ref={videoRef}
        src={src}
        controls
        playsInline
        preload="metadata"
        aria-label={`预览视频 ${name}`}
        onLoadedMetadata={(event) => {
          setState("ready");
          onTimeChange(event.currentTarget.currentTime);
        }}
        onCanPlay={() => setState("ready")}
        onTimeUpdate={(event) => onTimeChange(event.currentTarget.currentTime)}
        onWaiting={() => setState((current) => current === "error" ? current : "loading")}
        onPlaying={() => setState("ready")}
        onError={() => {
          if (sourceIndex + 1 < sources.length) {
            setSourceIndex((current) => current + 1);
            setState("loading");
            return;
          }
          setState("error");
        }}
      />
      <button
        ref={openButtonRef}
        type="button"
        className="video-preview-expand"
        onClick={() => onOpen(src)}
        aria-label={`放大预览视频 ${name}`}
        title="放大预览"
      >
        <Maximize2 size={17} />
      </button>
      {state === "loading" && (
        <span className="video-preview-status" role="status">
          <LoaderCircle size={18} className="spin" />
          <b>正在读取预览</b>
        </span>
      )}
      {state === "error" && (
        <div className="video-preview-error" role="alert">
          <AlertTriangle size={19} />
          <span><strong>当前浏览器无法播放</strong><small>可能是媒体暂不可用或编码不兼容</small></span>
          <button type="button" onClick={() => {
            setSourceIndex(0);
            setRetryToken((current) => current + 1);
            setState("loading");
          }}>重试</button>
          <a href={src} target="_blank" rel="noreferrer">打开原视频</a>
        </div>
      )}
    </div>
  );
}

function VideoLightbox({ src, name, onClose }: { src: string; name: string; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(panelRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), video[controls], [href], [tabindex]:not([tabindex="-1"])',
      ) || []);
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
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus({ preventScroll: true });
    };
  }, []);

  return createPortal(
    <div className="video-lightbox" role="dialog" aria-modal="true" aria-label={`放大预览视频 ${name}`}>
      <button type="button" tabIndex={-1} className="video-lightbox-backdrop" onClick={onClose} aria-label="关闭视频预览" />
      <div ref={panelRef} className="video-lightbox-panel">
        <header>
          <div><span>视频预览</span><strong>{name}</strong></div>
          <button ref={closeRef} type="button" className="video-lightbox-close" onClick={onClose} aria-label="关闭视频预览" title="关闭">
            <X size={22} />
          </button>
        </header>
        <video src={src} controls playsInline preload="auto" aria-label={`大画面预览 ${name}`} />
      </div>
    </div>,
    document.body,
  );
}

function VideoEvidenceSection({
  evidence,
  frameCount,
  currentTime,
  onSeek,
}: {
  evidence?: VideoEvidence;
  frameCount?: number;
  currentTime: number;
  onSeek: (timestamp: number) => void;
}) {
  const frames = evidence?.sampledFrames || [];
  const keyEvidence = evidence?.keyEvidence || [];
  const limitations = evidence?.limitations || [];
  const start = evidence?.sampleWindow?.start;
  const end = evidence?.sampleWindow?.end;
  const windowText = typeof start === "number" && typeof end === "number"
    ? `${formatVideoTime(start)} - ${formatVideoTime(end)}`
    : "未返回";

  return (
    <section className="result-band video-evidence-section">
      <div className="section-title">
        <Video size={18} />
        <div>
          <h3>视频采样与时序证据</h3>
          <p>展示模型实际读取的时间点；点击时间点可跳到原视频核对。</p>
        </div>
      </div>
      <dl className="video-evidence-facts">
        <div><dt>联合分析帧</dt><dd>{frames.length || frameCount || "未返回"}</dd></div>
        <div><dt>采样时间窗</dt><dd>{windowText}</dd></div>
        <div><dt>处理耗时</dt><dd>{evidence?.processingMs ? `${(evidence.processingMs / 1000).toFixed(1)} 秒` : "未返回"}</dd></div>
      </dl>
      {frames.length > 0 && (
        <div className="video-sample-timeline" aria-label="模型采样时间点">
          {frames.map((frame) => {
            const active = Math.abs(currentTime - frame.timestamp) < 0.35;
            return (
              <button
                type="button"
                className={active ? "is-active" : ""}
                key={`${frame.index}-${frame.timestamp}`}
                onClick={() => onSeek(frame.timestamp)}
                aria-label={`跳转到采样帧 ${frame.index}，${formatVideoTime(frame.timestamp)}`}
              >
                <span><Play size={13} fill="currentColor" /></span>
                <time>{formatVideoTime(frame.timestamp)}</time>
                <small>{frame.label || `联合输入帧 ${frame.index}`}</small>
              </button>
            );
          })}
        </div>
      )}
      {keyEvidence.length > 0 && (
        <div className="video-evidence-list">
          {keyEvidence.map((item, index) => (
            <div key={`${item.label}-${index}`}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <p><strong>{item.label}</strong>{publicCopy(item.detail)}</p>
            </div>
          ))}
        </div>
      )}
      {limitations.length > 0 && (
        <details className="video-evidence-limitations">
          <summary>查看检测边界 <span>{limitations.length} 项</span><ChevronDown size={15} /></summary>
          <ul>{limitations.map((item, index) => <li key={`${index}-${item}`}>{publicCopy(item)}</li>)}</ul>
        </details>
      )}
    </section>
  );
}

function ImageLightbox({
  src,
  alt,
  marks,
  onClose,
}: {
  src: string;
  alt: string;
  marks: PreviewWatermarkMark[];
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const lensRef = useRef<HTMLDivElement>(null);
  const lensPositionRef = useRef({ x: 0.5, y: 0.5 });
  const onCloseRef = useRef(onClose);
  const [mode, setMode] = useState<"view" | "lens">("view");

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  const positionLens = useCallback((normalizedX: number, normalizedY: number) => {
    const stage = stageRef.current;
    const lens = lensRef.current;
    const image = stage?.querySelector("img");
    if (!stage || !lens || !image || image.naturalWidth <= 0 || image.naturalHeight <= 0) return;
    const rect = stage.getBoundingClientRect();
    const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
    const imageWidth = image.naturalWidth * scale;
    const imageHeight = image.naturalHeight * scale;
    const imageLeft = (rect.width - imageWidth) / 2;
    const imageTop = (rect.height - imageHeight) / 2;
    const clampedX = clamp01(normalizedX);
    const clampedY = clamp01(normalizedY);
    const localX = imageLeft + clampedX * imageWidth;
    const localY = imageTop + clampedY * imageHeight;
    const lensSize = lens.offsetWidth;
    const zoom = 3;
    lensPositionRef.current = { x: clampedX, y: clampedY };
    lens.style.opacity = "1";
    lens.style.transform = `translate(${localX - lensSize / 2}px, ${localY - lensSize / 2}px)`;
    lens.style.backgroundSize = `${imageWidth * zoom}px ${imageHeight * zoom}px`;
    lens.style.backgroundPosition = `${lensSize / 2 - clampedX * imageWidth * zoom}px ${lensSize / 2 - clampedY * imageHeight * zoom}px`;
  }, []);

  const moveLens = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (mode !== "lens") return;
    const stage = stageRef.current;
    const image = stage?.querySelector("img");
    if (!stage || !image || image.naturalWidth <= 0 || image.naturalHeight <= 0) return;
    const rect = stage.getBoundingClientRect();
    const scale = Math.min(rect.width / image.naturalWidth, rect.height / image.naturalHeight);
    const imageWidth = image.naturalWidth * scale;
    const imageHeight = image.naturalHeight * scale;
    const imageLeft = (rect.width - imageWidth) / 2;
    const imageTop = (rect.height - imageHeight) / 2;
    positionLens(
      (event.clientX - rect.left - imageLeft) / imageWidth,
      (event.clientY - rect.top - imageTop) / imageHeight,
    );
  }, [mode, positionLens]);

  function handleLensKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (mode !== "lens" || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const current = lensPositionRef.current;
    const step = event.shiftKey ? 0.1 : 0.03;
    positionLens(
      current.x + (event.key === "ArrowRight" ? step : event.key === "ArrowLeft" ? -step : 0),
      current.y + (event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0),
    );
  }

  useEffect(() => {
    if (mode !== "lens") {
      if (lensRef.current) lensRef.current.style.opacity = "0";
      return;
    }
    window.requestAnimationFrame(() => {
      stageRef.current?.focus();
      positionLens(lensPositionRef.current.x, lensPositionRef.current.y);
    });
  }, [mode, positionLens]);

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const handleDialogKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(panelRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) || []).filter((element) => !element.hasAttribute("hidden"));
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
    window.addEventListener("keydown", handleDialogKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleDialogKey);
      previouslyFocused?.focus({ preventScroll: true });
    };
  }, []);

  return createPortal(
    <div className="image-lightbox" role="dialog" aria-modal="true" aria-label={`放大查看${alt}`}>
      <button type="button" tabIndex={-1} className="image-lightbox-backdrop" onClick={onClose} aria-label="关闭图片预览" />
      <div ref={panelRef} className="image-lightbox-panel">
        <div className="image-lightbox-modebar" role="group" aria-label="图片查看方式">
          <button type="button" className={mode === "view" ? "is-active" : ""} aria-pressed={mode === "view"} onClick={() => setMode("view")} title="普通查看">
            <MousePointer2 size={16} /><span>普通查看</span>
          </button>
          <button type="button" className={mode === "lens" ? "is-active" : ""} aria-pressed={mode === "lens"} onClick={() => setMode("lens")} title="局部放大 3 倍">
            <ScanSearch size={17} /><span>局部放大</span>
          </button>
        </div>
        <button ref={closeRef} type="button" className="image-lightbox-close" onClick={onClose} aria-label="关闭图片预览" title="关闭">
          <X size={20} />
        </button>
        <div
          ref={stageRef}
          className={`image-lightbox-stage ${mode === "lens" ? "is-lens-active" : ""}`}
          tabIndex={mode === "lens" ? 0 : -1}
          role="group"
          aria-label={mode === "lens" ? "局部放大区域，使用方向键移动放大镜，按住 Shift 可大步移动" : "图片预览区域"}
          onKeyDown={handleLensKeyDown}
          onPointerMove={moveLens}
          onPointerDown={(event) => {
            if (mode !== "lens") return;
            event.currentTarget.setPointerCapture(event.pointerId);
            moveLens(event);
          }}
          onPointerLeave={() => {
            if (lensRef.current) lensRef.current.style.opacity = "0";
          }}
        >
          <AnnotatedImagePreview src={src} alt={alt} marks={marks} />
          <div
            ref={lensRef}
            className="image-detail-lens"
            aria-hidden="true"
            style={{ backgroundImage: `url("${src.replace(/"/g, "%22")}")` }}
          />
        </div>
        <p>{alt}</p>
      </div>
    </div>,
    document.body,
  );
}

function verdictFor(outcome: AgentOutcome): VerdictView {
  if (outcome.kind === "image") {
    const reviewOnly = outcome.result.decisionStatus !== "verdict" || outcome.result.reviewRequired === true;
    const recoveredLegacyScore = legacyModelProbability(outcome.result.explanation);
    const structuredModelProbability = probabilityValue(outcome.result.modelScore ?? outcome.result.detector_probability);
    const modelProbability = recoveredLegacyScore !== null
      && (structuredModelProbability === null || (structuredModelProbability === 0 && recoveredLegacyScore > 0))
      ? recoveredLegacyScore
      : structuredModelProbability ?? recoveredLegacyScore ?? probabilityValue(outcome.result.probability);
    const rawModelValue = modelProbability;
    const finalProbability = probabilityValue(outcome.result.probability) ?? modelProbability ?? 0;
    const localizedWatermark = hasDecisiveAiWatermark(outcome.result.visibleWatermark);
    const risk = Math.max(finalProbability, localizedWatermark ? 0.95 : 0);
    const label = binaryVerdictLabel(
      localizedWatermark ? "AI生成图像" : outcome.result.final_label,
      rawModelValue,
    );
    const tone = isFakeVerdict(label) ? "fake" : "real";
    return {
      label,
      description: reviewOnly
        ? outcome.result.explanation || `系统给出“${label}”二元结论；模型分数尚未用独立测试集验证，建议结合原图和证据复核。`
        : localizedWatermark
          ? "显式水印模型直接检出高置信度水印区域，该证据已参与最终判断。"
          : tone === "real"
            ? "本次多源分析未发现足以支持 AI 生成的强证据。"
            : "检测到需要关注的生成或编辑线索，建议结合原始来源复核。",
      risk,
      modelProbability,
      riskLabel: outcome.result.swarm?.enabled ? "综合异常风险" : "AI 生成风险",
      tone,
      confidence: reviewOnly ? "尚未验证" : outcome.result.confidence || "未标注",
      reviewOnly,
    };
  }
  if (outcome.kind === "video") {
    const reviewOnly = outcome.result.decisionStatus !== "verdict" || outcome.result.reviewRequired === true;
    const risk = clamp01(Number(outcome.result.fake_percentage ?? 0) / 100);
    const label = binaryVideoVerdictLabel(outcome.result.final_label, outcome.result.fake_percentage);
    const tone = isFakeVerdict(label) ? "fake" : "real";
    const sampledCount = outcome.result.evidence?.sampledFrames?.length || outcome.result.frame_count || 0;
    return {
      label,
      description: reviewOnly
        ? `模型给出“${label}”二元结论${sampledCount ? `，本次联合分析了 ${sampledCount} 个采样帧` : ""}。该模型分数尚未经过独立测试集验证，建议结合下方时间点与原视频核对。`
        : tone === "real"
          ? "抽帧与时序分析未发现明确的合成证据。"
          : "视频中存在需要人工复核的合成线索。",
      risk,
      modelProbability: risk,
      riskLabel: "合成风险",
      tone,
      confidence: reviewOnly ? "尚未验证" : outcome.result.confidence || "未标注",
      reviewOnly,
    };
  }
  const reviewOnly = outcome.result.decisionStatus !== "verdict" || outcome.result.reviewRequired === true;
  const localizedWatermark = hasDecisiveAiWatermark(outcome.result.visibleWatermark);
  const vector = outcome.result.riskVector;
  const aiRisk = clamp01(Number(outcome.result.aiProbability ?? vector?.aiGenerated ?? outcome.result.confidence ?? 0));
  const tamperRisk = clamp01(Number(vector?.tampered ?? 0));
  const deepfakeRisk = clamp01(Number(vector?.deepfake ?? 0));
  const risk = Math.max(
    clamp01(Number(outcome.result.riskScore ?? outcome.result.confidence ?? 0)),
    aiRisk,
    tamperRisk,
    deepfakeRisk,
    localizedWatermark ? 0.95 : 0,
  );
  const label = binaryVerdictLabel(
    localizedWatermark ? "AI生成图像" : outcome.result.verdict,
    risk,
  );
  const tone = isFakeVerdict(label) ? "fake" : "real";
  return {
    label,
    description: reviewOnly
      ? outcome.result.explanation || `系统给出“${label}”二元结论；当前证据有限，建议结合原始来源复核。`
      : outcome.result.explanation || "请结合证据维度与原始来源进行判断。",
    risk,
    modelProbability: aiRisk || risk,
    riskLabel: tamperRisk >= Math.max(aiRisk, deepfakeRisk, 0.62) || deepfakeRisk >= Math.max(aiRisk, tamperRisk, 0.62)
      ? "综合异常风险"
      : "AI 生成风险",
    tone,
    confidence: reviewOnly ? "尚未验证" : outcome.result.source === "vlm"
      ? "模型分析完成"
      : outcome.result.source === "provenance"
        ? "来源证据直接命中"
        : "证据有限",
    reviewOnly,
  };
}

function fileName(outcome: AgentOutcome) {
  if (outcome.kind === "image" || outcome.kind === "video") return outcome.result.filename || "未命名文件";
  return outcome.result.fileMeta.name;
}

function filePreview(outcome: AgentOutcome) {
  if (outcome.previewUrl) return outcome.previewUrl;
  if (outcome.kind === "image") return outcome.result.image_url;
  if (outcome.kind === "video") return outcome.result.video_url;
  return outcome.result.fileMeta.preview || outcome.result.fileMeta.thumbnail || undefined;
}

function hasImageFile(outcome: AgentOutcome) {
  if (!outcome.file) return false;
  return outcome.kind === "image" || (outcome.kind === "evidence" && outcome.result.fileMeta.type === "image");
}

function ExpertStatus({ status }: { status?: string }) {
  if (status === "success") return <StatusIcon name="real" size={15} className="status-success" />;
  if (status === "failed") return <StatusIcon name="error" size={15} className="status-danger" />;
  if (status === "running") return <StatusIcon name="processing" size={15} className="status-running" />;
  return <StatusIcon name="partial" size={15} className="status-muted" />;
}

function EvidenceList({ items }: { items: string[] }) {
  if (items.length === 0) {
    return <div className="evidence-empty"><Info size={17} /> 暂无更多可展示的证据条目。</div>;
  }
  return (
    <ul className="evidence-list">
      {items.map((item, index) => (
        <li key={`${index}-${item}`}><span>{index + 1}</span><p>{publicCopy(item)}</p></li>
      ))}
    </ul>
  );
}

function ResultDisclosure({
  icon,
  title,
  description,
  children,
  open = false,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  children: ReactNode;
  open?: boolean;
}) {
  return (
    <details className="result-disclosure" open={open || undefined}>
      <summary>
        <span className="result-disclosure-icon">{icon}</span>
        <span><strong>{title}</strong><small>{description}</small></span>
        <ChevronDown className="result-disclosure-chevron" size={17} />
      </summary>
      <div className="result-disclosure-content">{children}</div>
    </details>
  );
}

const MODEL_EVIDENCE_PATTERN = /真实性分析|模型|决策授权|视觉复核|画面|视频|时序|抽帧|采样/i;

type VerificationTone = "real" | "fake" | "warning" | "neutral" | "pending";

interface VerificationView {
  label: string;
  detail: string;
  tone: VerificationTone;
  evidence?: MetadataEvidenceBadge[];
}

interface MetadataRow {
  path: string;
  value: string;
}

interface MetadataEvidenceBadge {
  label: string;
  value?: string;
  tone: "fake" | "real";
}

const CAPTURE_METADATA_ALIASES = [
  "exifmake", "tiffmake", "cameramake",
  "exifmodel", "tiffmodel", "cameramodel",
  "exiflensmodel", "lensmodel", "lensinfo", "lensspec",
  "exifdatetimeoriginal", "datetimeoriginal",
  "exifexposuretime", "exposuretime", "shutterspeedvalue",
  "exiffnumber", "fnumber", "aperturevalue",
  "exifiso", "photographicsensitivity", "isospeedratings",
  "exiffocallength", "focallength", "focallengthin35mmformat",
  "makernote", "makernotes", "gpsinfo", "gpsposition",
] as const;

const EXPLICIT_AI_METADATA_PATTERN = /(?:tc260(?::|\.|\/)?(?:aigc|contentproducer|produceid|label)|aigc[ _.-]?(?:disclosure|label|metadata|标识|披露)|trainedalgorithmicmedia|compositewithtrainedalgorithmicmedia|midjourney|stable[ _.-]?diffusion|comfyui|automatic1111|dall[ ._-]?e|seedream|豆包|即梦|firefly|runwayml|sora|flux[ ._-]?(?:pro|dev|schnell)|negative prompt|cfg[ _.-]?scale|ksampler)/i;

function normalizedMetadataPath(path: string): string {
  return path.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function isCaptureMetadataPath(path: string): boolean {
  const normalized = normalizedMetadataPath(path);
  return CAPTURE_METADATA_ALIASES.some((alias) => normalized === alias || normalized.endsWith(alias));
}

function metadataRowDirection(row: MetadataRow, aiSignalPaths: Set<string>): MetadataEvidenceBadge["tone"] | null {
  const path = normalizedMetadataPath(row.path);
  const explicitlyMatched = [...aiSignalPaths].some((signalPath) => (
    path === signalPath || path.endsWith(signalPath) || signalPath.endsWith(path)
  ));
  if (explicitlyMatched || EXPLICIT_AI_METADATA_PATTERN.test(`${row.path} ${row.value}`)) return "fake";
  if (isCaptureMetadataPath(row.path)) return "real";
  return null;
}

function metadataEvidenceBadges(
  aiMetadata: ProvenanceReport["aiMetadata"] | undefined,
  metadataAiGenerated: boolean | undefined,
  captureEvidence: CaptureEvidence | undefined,
  rows: MetadataRow[],
): MetadataEvidenceBadge[] {
  const badges: MetadataEvidenceBadge[] = [];
  const seen = new Set<string>();
  const add = (badge: MetadataEvidenceBadge) => {
    const key = `${badge.tone}:${badge.label}:${badge.value || ""}`.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    badges.push(badge);
  };

  const aiLabels = [
    ...(aiMetadata?.matchedTools || []),
    ...(aiMetadata?.matchedProviders || []).map((provider) => provider.label),
  ];
  aiLabels.slice(0, 3).forEach((label) => add({ label, tone: "fake" }));
  if (metadataAiGenerated && aiLabels.length === 0) add({ label: "AI 生成元数据", tone: "fake" });

  if (!badges.some((badge) => badge.tone === "fake")) {
    rows.filter((row) => EXPLICIT_AI_METADATA_PATTERN.test(`${row.path} ${row.value}`)).slice(0, 2).forEach((row) => {
      const label = /tc260/i.test(`${row.path} ${row.value}`) ? "TC260 AIGC 标识" : "AI 生成标记";
      add({ label, value: row.value.length <= 36 ? row.value : undefined, tone: "fake" });
    });
  }

  (captureEvidence?.evidence || []).slice(0, 3).forEach((item) => {
    add({ label: item.label, value: item.value, tone: "real" });
  });
  const cameraMake = findMetadataRow(rows, ["image.exif.Make", "EXIF:Make", "EXIF_Make", "TIFF:Make", "cameraMake"]);
  const cameraModel = findMetadataRow(rows, ["image.exif.Model", "EXIF:Model", "EXIF_Model", "TIFF:Model", "cameraModel"]);
  const cameraDevice = [cameraMake?.value, cameraModel?.value]
    .filter((value, index, values): value is string => Boolean(value) && values.indexOf(value) === index)
    .join(" ");
  if (cameraDevice) add({ label: "相机型号", value: cameraDevice, tone: "real" });
  const lens = findMetadataRow(rows, ["image.exif.LensModel", "EXIF:LensModel", "EXIF:LensInfo", "LensModel", "LensInfo"]);
  if (lens) add({ label: "镜头信息", value: lens.value, tone: "real" });
  const captureTime = findMetadataRow(rows, ["image.exif.DateTimeOriginal", "EXIF:DateTimeOriginal", "DateTimeOriginal"]);
  if (captureTime) add({ label: "原始拍摄时间", value: "已记录", tone: "real" });

  const fake = badges.filter((badge) => badge.tone === "fake").slice(0, 3);
  const real = badges.filter((badge) => badge.tone === "real").slice(0, 3);
  return [...fake, ...real];
}

function provenanceView(report?: ProvenanceReport, busy = false): VerificationView {
  if (busy && !report) return { label: "正在核验", detail: "读取内容凭证与签名状态", tone: "pending" };
  if (!report) return { label: "尚未核验", detail: "等待读取文件内的 C2PA 凭证", tone: "neutral" };
  const validation = String(report.validationState || "").trim().toLowerCase();
  if (report.error === "c2pa_unavailable") return { label: "服务不可用", detail: "C2PA 解析器未正常启动", tone: "warning" };
  if (validation === "invalid") return { label: "凭证异常", detail: "签名或内容完整性校验未通过", tone: "warning" };
  if (report.credentialTrusted || validation === "trusted") {
    return report.isAiGenerated === true
      ? { label: "可信 AI 声明", detail: report.generator || report.issuer || "凭证明确声明 AI 生成", tone: "fake" }
      : { label: "可信来源凭证", detail: report.issuer || "签名与信任链均已通过", tone: "real" };
  }
  if (report.hasCredentials) return { label: "发现内容凭证", detail: "签名已读取，信任关系仍需核对", tone: "neutral" };
  return { label: "未发现凭证", detail: "C2PA 已检查；缺少凭证不代表图片为假", tone: "neutral" };
}

function ResultDecisionCard({
  points,
  verdict,
  provenance,
  provenanceBusy,
  provenanceAvailable,
  visibleWatermark,
  captureEvidence,
  cameraDeviceHint,
  metadataCount,
  metadataRows,
}: {
  points: ExplanationPoint[];
  verdict: VerdictView;
  provenance?: ProvenanceReport;
  provenanceBusy: boolean;
  provenanceAvailable: boolean;
  visibleWatermark?: VisibleWatermarkResult;
  captureEvidence?: CaptureEvidence;
  cameraDeviceHint?: string;
  metadataCount: number;
  metadataRows: MetadataRow[];
}) {
  const modelPoint = points.find((point) => point.label !== "综合结论" && MODEL_EVIDENCE_PATTERN.test(point.label));
  const c2pa = provenanceAvailable
    ? provenanceView(provenance, provenanceBusy)
    : { label: "登录后核验", detail: "登录后自动读取 C2PA 内容凭证", tone: "neutral" as const };
  const decisiveWatermark = hasDecisiveAiWatermark(visibleWatermark);
  const aiMetadata = provenance?.aiMetadata;
  const metadataProviders = (aiMetadata?.matchedProviders || []).map((item) => item.label);
  const metadataTools = (metadataProviders.length > 0 ? metadataProviders : aiMetadata?.matchedTools || []).slice(0, 2).join("、");
  const cameraDevice = captureEvidence?.camera?.device
    || captureEvidence?.evidence?.find((item) => item.key === "device")?.value
    || cameraDeviceHint;
  const metadataEvidence = metadataEvidenceBadges(aiMetadata, provenance?.metadataAiGenerated, captureEvidence, metadataRows);
  const hasAiMetadata = metadataEvidence.some((item) => item.tone === "fake");
  const hasCaptureMetadata = metadataEvidence.some((item) => item.tone === "real");
  const metadata: VerificationView = hasAiMetadata && hasCaptureMetadata
    ? { label: "两类线索并存", detail: "红色指向 AI 生成，绿色支持实拍；元数据可编辑，仅作来源线索", tone: "warning", evidence: metadataEvidence }
    : hasAiMetadata || aiMetadata?.isAiLikely || provenance?.metadataAiGenerated
    ? { label: "发现 AI 工具标记", detail: `${metadataTools || "生成工具关键词"} · 元数据可编辑，仅作来源线索`, tone: "fake", evidence: metadataEvidence }
    : captureEvidence?.level === "conflict"
      ? { label: "拍摄信息冲突", detail: "元数据字段之间存在不一致", tone: "warning", evidence: metadataEvidence }
    : cameraDevice
      ? {
          label: "发现相机型号",
          detail: `${cameraDevice} · 属于支持实拍来源的辅助线索`,
          tone: "real",
          evidence: metadataEvidence,
        }
      : captureEvidence?.supportsRealCapture
        ? { label: `${metadataCount} 项 · 实拍线索`, detail: "发现可核对的相机或拍摄流程信息", tone: "real", evidence: metadataEvidence }
      : metadataCount > 0
        ? { label: `${metadataCount} 项已读取`, detail: "完整字段可在文件信息中查看", tone: "neutral", evidence: metadataEvidence }
        : { label: "未读取到字段", detail: "元数据缺失本身不参与判假", tone: "neutral" };
  const watermark: VerificationView = !visibleWatermark
    ? { label: "尚未返回", detail: "本次结果没有水印扫描数据", tone: "warning" }
    : !visibleWatermark.supported
      ? { label: "检测未完成", detail: "本项不会生成替代性结论", tone: "warning" }
      : decisiveWatermark
        ? { label: "确认 AI 水印", detail: "平台匹配、位置与文字/图形证据相互印证", tone: "fake" }
        : visibleWatermark.detected
          ? { label: "发现可见标记", detail: "平台归属未确认，不单独判假", tone: "neutral" }
          : { label: "未检出水印", detail: "扫描已完成；未检出不等同于真实", tone: "neutral" };
  const checks = [
    { id: "c2pa", title: "C2PA 内容凭证", icon: <Fingerprint size={19} />, ...c2pa },
    { id: "metadata", title: "元数据与实拍信息", icon: <Camera size={19} />, ...metadata },
    { id: "watermark", title: "显式水印", icon: <ScanSearch size={19} />, ...watermark },
  ];

  return (
    <section className={`result-decision-card tone-${verdict.tone}`} aria-labelledby="decision-card-title">
      <header className="decision-card-heading">
        <div>
          <h3 id="decision-card-title">关键证据</h3>
          <p>先看文件自身的来源与完整性，再看模型输出。</p>
        </div>
        <span>{checks.length} 项来源核验</span>
      </header>

      <div className="decision-card-grid">
        <section className="decision-source" aria-labelledby="decision-source-title">
          <header>
            <div><small>来源与完整性</small><h4 id="decision-source-title">文件自身证据</h4></div>
          </header>
          <ul className="decision-check-list">
            {checks.map((check) => (
              <li className={`is-${check.tone}`} key={check.id}>
                <span>{check.icon}</span>
                <div>
                  <strong>{check.title}</strong><small>{check.detail}</small>
                  {check.evidence && check.evidence.length > 0 && (
                    <div className="metadata-evidence-tags" aria-label="元数据证据方向">
                      {check.evidence.map((item, index) => (
                        <span className={`is-${item.tone}`} key={`${item.tone}-${item.label}-${index}`}>
                          {item.tone === "fake" ? <AlertTriangle size={12} /> : <CheckCircle2 size={12} />}
                          <em>{item.label}</em>{item.value && <small>{item.value}</small>}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <b>{check.label}</b>
              </li>
            ))}
          </ul>
        </section>

        <section className={`decision-model is-${verdict.tone}`} aria-labelledby="decision-model-title">
          <header>
            <div><small>模型分析</small><h4 id="decision-model-title">真实性信号</h4></div>
            <span>{verdict.reviewOnly ? "待验证" : "已完成"}</span>
          </header>
          <div className="decision-model-signal">
            <small>模型输出方向</small>
            <strong>{verdict.tone === "fake" ? "偏向 AI 生成" : "偏向真实图像"}</strong>
          </div>
          <p className="decision-model-copy">{publicCopy(modelPoint?.text || verdict.description)}</p>
          {verdict.reviewOnly && <div className="decision-calibration-note"><Info size={15} />模型分数尚未用独立测试集验证；分数高表示模型倾向强，不代表实际准确率已经得到验证。</div>}
        </section>
      </div>
    </section>
  );
}

function ProvenanceSection({ report }: { report?: ProvenanceReport }) {
  if (!report) return null;
  const validationState = report.validationState?.trim().toLowerCase();
  const credentialLabel = !report.hasCredentials
    ? report.error === "remote_manifest_blocked"
      ? "远程凭证未自动获取"
      : report.metadataAiGenerated ? "发现 AI 元数据线索" : "未发现可验证凭证"
    : report.credentialTrusted || validationState === "trusted"
      ? "内容凭证可信"
      : validationState === "valid"
        ? "凭证签名有效，信任链未建立"
        : validationState === "invalid"
          ? "内容凭证校验失败"
          : "发现内容凭证，状态待确认";
  return (
    <section className="result-band provenance-band">
      <div className="section-title"><Fingerprint size={18} /><div><h3>内容凭证</h3><p>{credentialLabel}</p></div></div>
      <dl className="fact-grid compact">
        <div><dt>签名状态</dt><dd>{report.validationState || "无"}</dd></div>
        <div><dt>生成工具</dt><dd>{report.generator || "未声明"}</dd></div>
        <div><dt>签发者</dt><dd>{report.issuer || "未声明"}</dd></div>
        <div><dt>AI 声明</dt><dd>{report.isAiGenerated === true ? "有" : report.isAiGenerated === false ? "无" : "未声明"}</dd></div>
      </dl>
    </section>
  );
}

function CaptureEvidenceSection({ report }: { report?: CaptureEvidence }) {
  if (!report) return null;
  const items = [...(report.evidence || []), ...(report.conflicts || [])];
  const privacyProtected = Boolean(
    report.privacy?.gpsRedacted
    || report.privacy?.serialRedacted
    || report.privacy?.captureTimeRedacted,
  );
  const stateLabel = report.level === "conflict"
    ? "证据冲突"
    : report.adjustmentEligible
      ? "可参与边界校正"
    : report.supportsRealCapture
      ? `${report.levelText || "辅助"}强度支持`
      : "保持中性";

  return (
    <section className={`result-band capture-chain-band level-${report.level}`}>
      <div className="capture-chain-heading">
        <div className="section-title"><Camera size={18} /><div><h3>实拍来源证据</h3><p>核对设备、光学参数、原始时间与可信来源凭证的一致性。</p></div></div>
        <span className="capture-chain-state">{stateLabel}</span>
      </div>
      <div className="capture-chain-summary">
        <span aria-hidden="true"><Camera size={20} /></span>
        <div><strong>{report.title}</strong><p>{report.summary}</p></div>
        <dl><dt>证据完整度</dt><dd>{Math.round(clamp01(report.score) * 100)}%</dd></dl>
      </div>
      {items.length > 0 && (
        <div className="capture-chain-items" role="list" aria-label="实拍来源证据条目">
          {items.map((item) => {
            const conflict = (report.conflicts || []).some((entry) => entry.key === item.key);
            return (
              <div className={conflict ? "is-conflict" : ""} role="listitem" key={`${conflict ? "conflict" : "evidence"}-${item.key}`}>
                <span>{conflict ? <AlertTriangle size={14} /> : <CheckCircle2 size={14} />}</span>
                <strong>{item.label}</strong>
                <p>{item.value}</p>
              </div>
            );
          })}
        </div>
      )}
      <div className="capture-chain-boundary">
        <Info size={15} />
        <p>{(report.limitations || ["普通 EXIF 可以被修改或复制，因此不能单独证明图片真实。"]).join(" ")}</p>
        {privacyProtected && <span><ShieldCheck size={13} /> 证据摘要已脱敏</span>}
      </div>
    </section>
  );
}

function WatermarkSection({ report, preview }: { report?: VisibleWatermarkResult; preview?: string }) {
  if (!report || !preview) return null;
  const hits = (report.hits || []).slice(0, 8);
  const localizedHits = displayableWatermarkHits(report).slice(0, 8);
  const platformHits = hits.filter((hit) => AI_WATERMARK_PROVIDERS.has(hit.provider));
  const decisiveWatermark = hasDecisiveAiWatermark(report);
  const genericHits = hits.filter((hit) => !AI_WATERMARK_PROVIDERS.has(hit.provider));
  const detector = report.detector;
  const modelDirect = detector?.mode === "model_direct" || report.explicitWatermark?.mode === "model_direct";
  const detected = hits.length > 0;
  const hasPlatformHit = platformHits.length > 0;
  const reusedFromSameFile = report.reanalysis?.reused === true;
  const reusedLegacyResult = report.reanalysis?.basis === "legacy-unowned-exact-sha256";
  const confirmedHits = platformHits.filter((hit) => hit.localizationConfirmed === true);
  const providerLabels = Array.from(new Set(platformHits.map((hit) => hit.label || hit.provider))).join("、");
  const statusText = !report.supported
    ? "可见水印检测本次不可用，未影响主鉴伪结论"
    : modelDirect && detected
      ? `显式水印模型检出 ${hits.length} 处区域`
      : modelDirect
        ? "显式水印模型扫描完成，本次未检出"
    : hasPlatformHit
      ? `识别到 ${platformHits.length} 处已知 AI 平台水印`
      : detected
        ? `检测到 ${genericHits.length} 处可见水印，平台归属待确认`
        : "可见水印扫描完成，本次未检出";
  const elapsed = Number(report.elapsedMs || detector?.roundTripMs || 0);
  const suppliedRegistry = detector?.engines?.find((engine) => engine.id === "known_ai_registry");
  const suppliedFusion = detector?.engines?.find((engine) => engine.id === "explicit_ai_watermark_fusion");
  const suppliedYolo = detector?.engines?.find((engine) => engine.id.includes("yolo"));
  const suppliedDirect = detector?.engines?.find((engine) => engine.id === "explicit_watermark_model_direct");
  const engines = modelDirect ? [{
    ...(suppliedDirect || {}),
    id: "explicit_watermark_model_direct",
    label: "显式水印模型",
    available: Boolean(suppliedDirect?.available ?? report.supported),
    detected,
    count: hits.length,
    role: "direct_detection",
  }] : [
    {
      ...(suppliedRegistry || {}),
      id: "known_ai_registry",
      label: "AI 平台标记匹配",
      available: Boolean(suppliedRegistry?.available ?? report.supported),
      detected: hasPlatformHit,
      count: platformHits.length,
      role: "attribution",
    },
    {
      ...(suppliedFusion || {}),
      id: "explicit_ai_watermark_fusion",
      label: "文字与图形联合核验",
      available: Boolean(suppliedFusion?.available ?? report.explicitWatermark?.available),
      detected: Boolean(suppliedFusion?.detected ?? decisiveWatermark),
      count: suppliedFusion?.count ?? (decisiveWatermark ? platformHits.filter((hit) => hit.decisive).length : 0),
      role: "attribution",
    },
    {
      ...(suppliedYolo || {}),
      id: "yolo_visible_watermark",
      label: "可见水印区域定位",
      available: Boolean(suppliedYolo?.available),
      detected: Boolean(suppliedYolo?.detected ?? (genericHits.length > 0 || confirmedHits.length > 0)),
      count: suppliedYolo?.count ?? (genericHits.length + confirmedHits.length),
      role: "localization",
    },
  ];
  const displayNote = !report.supported
    ? "检测服务不可用时不会生成替代性水印结论。"
    : modelDirect
      ? detected
        ? `结果由显式水印模型直接输出，最高置信度 ${Math.round(report.confidence * 100)}%；未使用平台模板、文字识别或图像检索。${decisiveWatermark ? " 该分数已达到强证据门槛。" : " 当前分数未达到强证据门槛，仅展示定位结果。"}`
        : "模型已直接完成水印扫描；本次未使用平台模板、文字识别或图像检索。"
    : reusedFromSameFile
      ? reusedLegacyResult
        ? "该定位证据来自完全相同文件（SHA-256 一致）的最近一次成功扫描；系统会按当前水印规则重新计算结论。"
        : "该定位证据来自同一账号对完全相同文件（SHA-256 一致）的最近一次成功扫描；系统会按当前水印规则重新计算结论。"
      : hasPlatformHit
        ? decisiveWatermark
          ? `匹配到 ${platformHits.length} 处强 AI 水印证据，并通过平台匹配、区域定位与文字/图形联合核验；该证据已参与最终判定。`
          : `匹配到 ${platformHits.length} 处 AI 平台标记${confirmedHits.length > 0 ? `，其中 ${confirmedHits.length} 处通过区域复核` : ""}${genericHits.length > 0 ? `；另有 ${genericHits.length} 处可见水印的平台归属待确认` : ""}，但尚未通过强水印授权门槛。`
        : detected
          ? "已定位到可见水印但尚不能确认平台归属；定位框仅作为上下文线索，不会单独改变鉴伪结论。"
          : "平台标记库与可见水印区域扫描均未发现水印。";
  return (
    <section className="result-band watermark-band">
      <div className="section-title">
        <ScanLine size={18} />
        <div><h3>可见水印检测</h3><p>{statusText}</p></div>
      </div>
      <div className={`watermark-status ${decisiveWatermark || hasPlatformHit ? "is-detected" : detected ? "is-possible" : report.supported ? "is-clear" : "is-unavailable"}`}>
        <span>{modelDirect ? detected ? `模型检出 ${hits.length}` : report.supported ? "未检出" : "暂不可用" : hasPlatformHit ? `已知平台 ${platformHits.length}` : detected ? `可见水印 ${genericHits.length}` : report.supported ? "未检出" : "暂不可用"}</span>
        <strong>{modelDirect ? detected ? `最高置信度 ${Math.round(report.confidence * 100)}% · ${decisiveWatermark ? "强证据" : "低于判定门槛"}` : "显式水印模型已完成扫描" : hasPlatformHit ? `${providerLabels} · ${decisiveWatermark ? "强证据确认" : "平台规则确认"}` : detected ? `${reusedFromSameFile ? "同一文件复核补充 · " : ""}通用水印线索，不单独判假` : "已完成平台规则与通用水印扫描"}</strong>
        {elapsed > 0 ? <time>{elapsed} ms</time> : null}
      </div>
      {report.supported && (
        <div className="watermark-layout">
          <div className="watermark-visual">
            <div className="watermark-canvas">
              <img src={preview} alt="带有可见水印定位框的原始图像" />
              {localizedHits.map((hit) => {
                const index = Math.max(hits.indexOf(hit), 0);
                const x = clamp01(Number(hit.bbox?.x || 0));
                const y = clamp01(Number(hit.bbox?.y || 0));
                const width = Math.min(clamp01(Number(hit.bbox?.w || 0)), 1 - x);
                const height = Math.min(clamp01(Number(hit.bbox?.h || 0)), 1 - y);
                return (
                  <span
                    className={`watermark-box ${AI_WATERMARK_PROVIDERS.has(hit.provider) ? "is-platform" : ""}`}
                    key={`${hit.provider}-${index}-${x}-${y}`}
                    style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` }}
                    aria-label={`第 ${index + 1} 处可见水印，置信度 ${Math.round(hit.confidence * 100)}%`}
                  >
                    <b>水印 {Math.round(hit.confidence * 100)}%</b>
                  </span>
                );
              })}
            </div>
          </div>
          <div className="watermark-details">
            {hits.length > 0 ? (
              <ol>
                {hits.map((hit, index) => (
                  <li className={AI_WATERMARK_PROVIDERS.has(hit.provider) ? "is-platform" : ""} key={`${hit.provider}-detail-${index}`}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <strong>{hit.label || (modelDirect ? "显式水印" : AI_WATERMARK_PROVIDERS.has(hit.provider) ? "已知 AI 平台水印" : "可见水印（平台待确认）")}</strong>
                      <small>
                        {modelDirect
                          ? `模型直接检测 · ${hit.decisive ? "达到强证据门槛" : "仅展示定位"}`
                          : AI_WATERMARK_PROVIDERS.has(hit.provider)
                          ? hit.method === "explicit_ai_watermark_fusion"
                            ? "文字生成语义 · 平台图形检索 · 区域定位"
                            : `平台标记匹配${hit.localizationConfirmed ? " · 区域复核" : " · 视觉归属线索"}`
                          : "可见水印区域定位 · 仅作上下文线索"}
                      </small>
                      <i><em style={{ width: `${clamp01(hit.confidence) * 100}%` }} /></i>
                    </div>
                    <b>{Math.round(hit.confidence * 100)}%</b>
                  </li>
                ))}
              </ol>
            ) : (
              <div className="watermark-clear-state"><CheckCircle2 size={18} /><span><strong>未发现可见水印</strong><small>{modelDirect ? "显式水印模型已完成扫描" : "已完成平台标记与区域扫描"}</small></span></div>
            )}
            <div className="watermark-model-meta">
              <span>检测能力</span>
              <div className="watermark-engine-list">
                {engines.map((engine) => (
                  <div key={engine.id}>
                    <span>
                      <strong>{engine.label}</strong>
                    </span>
                    <b className={engine.available ? engine.detected ? "is-hit" : "is-ready" : "is-offline"}>
                      {engine.available ? engine.detected ? `${engine.count || 0} 处` : "已扫描" : "不可用"}
                    </b>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
      <div className="watermark-note"><Info size={15} /><p>{displayNote}</p></div>
      <WatermarkPipeline trace={report.pipelineTrace} />
    </section>
  );
}

function SynthIDSection({ report }: { report?: SynthIDResult }) {
  if (!report) return null;
  const state = report.detected ? "detected" : report.possiblyDetected ? "possible" : report.supported ? "clear" : "unavailable";
  const stateLabel = state === "detected" ? "检出" : state === "possible" ? "疑似信号" : state === "clear" ? "未检出" : "暂不可用";
  const summary = state === "detected"
    ? "检测到可复核的 Google 内容标记信号"
    : state === "possible"
      ? "发现低强度内容标记线索"
      : state === "clear"
        ? "内容标记核验完成，本次未检出"
        : "本次未能完成内容标记核验";
  return (
    <section className="result-band synthid-band">
      <div className="section-title"><Fingerprint size={18} /><div><h3>Google 内容标记核验</h3><p>检查文件中可复核的隐式平台标记。</p></div></div>
      <div className={`watermark-status is-${state}`}>
        <span>{stateLabel}</span>
        <strong>{summary}</strong>
        {report.elapsedMs ? <time>{report.elapsedMs} ms</time> : null}
      </div>
      <div className="watermark-note"><Info size={15} /><p>{publicCopy(report.note)} 本项属于辅助来源证据，不等同于平台官方验证，也不会凭低强度信号单独定案。</p></div>
    </section>
  );
}

function ProbabilitySection({ model }: { model?: ProbabilityModel }) {
  if (!model || !Array.isArray(model.factors) || model.factors.length === 0) return null;
  const baseline = clamp01(Number(model.pixelBaseline ?? model.adjustedBaseline ?? model.baseRate ?? 0.1));
  const posterior = clamp01(Number(model.posterior));
  const groups = new Set(model.factors.map((factor) => factor.group).filter(Boolean)).size;

  return (
    <section className="result-band probability-band">
      <div className="section-title">
        <Gauge size={18} />
        <div><h3>综合风险依据</h3><p>真实性分析形成风险基线，独立来源证据按公开规则更新结果。</p></div>
      </div>
      <div className="probability-flow" aria-label={`策略风险分从 ${Math.round(baseline * 100)} 更新到 ${Math.round(posterior * 100)}`}>
        <div>
          <span>{model.pixelBaseline != null ? "像素基线" : "基础风险"}</span>
          <strong>{(baseline * 100).toFixed(1)}%</strong>
        </div>
        <i aria-hidden="true"><span /></i>
        <div>
          <span>独立证据组</span>
          <strong>{Math.max(groups, 1)} 组</strong>
        </div>
        <i aria-hidden="true"><span /></i>
        <div className="is-final">
          <span>融合后风险</span>
          <strong>{(posterior * 100).toFixed(2)}%</strong>
        </div>
      </div>
      <div className="probability-factors">
        {model.factors.slice(0, 4).map((factor, index) => {
          const lowersRisk = factor.direction === "real" || Number(factor.effectiveLikelihoodRatio ?? factor.likelihoodRatio ?? 1) < 1;
          return (
            <div className={lowersRisk ? "is-supporting-real" : "is-supporting-fake"} key={`${factor.kind}-${factor.source || index}`}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{publicCopy(factor.label)}</strong>
              <small>{Number(factor.correlationExponent ?? 1) < 1 ? "同源折扣" : lowersRisk ? "降低风险" : "抬高风险"}</small>
            </div>
          );
        })}
      </div>
      <div className="probability-note">
        <Info size={15} />
        <p>{model.conflicting ? "当前同时存在支持实拍与支持生成的证据，系统按证据强度融合并标记冲突。" : "该数值是尚待数据集校准的自动化策略风险分，不是统计概率或司法鉴定置信度；普通 Logo 与缺失元数据不参与抬分。"}</p>
      </div>
    </section>
  );
}

function metadataRows(value: unknown, path = "", rows: MetadataRow[] = []): MetadataRow[] {
  if (Array.isArray(value)) {
    if (value.length === 0 && path) rows.push({ path, value: "[]" });
    value.forEach((item, index) => metadataRows(item, `${path}[${index}]`, rows));
    return rows;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0 && path) rows.push({ path, value: "{}" });
    entries.forEach(([key, item]) => metadataRows(item, path ? `${path}.${key}` : key, rows));
    return rows;
  }
  const text = value == null ? String(value) : typeof value === "boolean" ? (value ? "true" : "false") : String(value);
  rows.push({ path: path || "metadata", value: text });
  return rows;
}

function mergedMetadataRows(...sources: unknown[]): MetadataRow[] {
  const rows: MetadataRow[] = [];
  const seen = new Set<string>();
  sources.forEach((source) => {
    metadataRows(source).forEach((row) => {
      const key = `${row.path.trim().toLowerCase()}\u0000${row.value}`;
      if (seen.has(key)) return;
      seen.add(key);
      rows.push(row);
    });
  });
  return rows;
}

function findMetadataRow(rows: MetadataRow[], aliases: string[]): MetadataRow | undefined {
  const normalizedAliases = aliases.map(normalizedMetadataPath).sort((a, b) => b.length - a.length);
  return rows.find((row) => {
    const path = normalizedMetadataPath(row.path);
    return normalizedAliases.some((alias) => path === alias || path.endsWith(alias));
  });
}

export default function AgentResult(props: Props) {
  const [tab, setTab] = useState<ResultTab>("summary");
  const [shareBusy, setShareBusy] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareMessage, setShareMessage] = useState("");
  const [createdShareUrl, setCreatedShareUrl] = useState("");
  const [shares, setShares] = useState<ReportShareItem[]>([]);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [videoLightboxSource, setVideoLightboxSource] = useState<string | null>(null);
  const [videoCurrentTime, setVideoCurrentTime] = useState(0);
  const [videoElement, setVideoElement] = useState<HTMLVideoElement | null>(null);
  const [metadataQuery, setMetadataQuery] = useState("");
  const videoExpandButtonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    setTab("summary");
    setShareOpen(false);
    setShareMessage("");
    setCreatedShareUrl("");
    setShares([]);
    setLightboxOpen(false);
    setVideoLightboxSource(null);
    setVideoCurrentTime(0);
    setMetadataQuery("");
  }, [props.outcome.id]);
  const closeVideoLightbox = useCallback(() => {
    setVideoLightboxSource(null);
    window.requestAnimationFrame(() => videoExpandButtonRef.current?.focus({ preventScroll: true }));
  }, []);
  const verdict = useMemo(() => verdictFor(props.outcome), [props.outcome]);
  const explanationPoints = useMemo(
    () => buildEvidenceExplanation(props.outcome, verdict.risk, verdict.label),
    [props.outcome, verdict.label, verdict.risk],
  );
  const videoSources = props.outcome.kind === "video"
    ? [props.outcome.previewUrl, props.outcome.result.video_url]
        .filter((value): value is string => Boolean(value))
        .filter((value, index, items) => items.indexOf(value) === index)
    : [];
  const preview = props.outcome.kind === "video" ? videoSources[0] : filePreview(props.outcome);
  const canDeepAnalyze = props.provenanceAvailable && hasImageFile(props.outcome);
  const provenance = props.outcome.kind === "image" || props.outcome.kind === "evidence"
    ? props.outcome.provenance || (props.outcome.kind === "evidence" ? props.outcome.result.provenance || undefined : undefined)
    : undefined;
  const visibleWatermark = props.outcome.kind === "image" || props.outcome.kind === "evidence"
    ? props.outcome.result.visibleWatermark
    : undefined;
  const previewWatermarkMarks = useMemo(() => {
    if (!visibleWatermark) return [];
    const allHits = visibleWatermark.hits || [];
    return displayableWatermarkHits(visibleWatermark).slice(0, 8).map((hit, localizedIndex) => ({
      hit,
      index: Math.max(allHits.indexOf(hit) + 1, localizedIndex + 1),
    }));
  }, [visibleWatermark]);
  const synthid = props.outcome.kind === "image" || props.outcome.kind === "evidence"
    ? props.outcome.result.synthid
    : undefined;
  const probabilityModel = props.outcome.kind === "image" || props.outcome.kind === "evidence"
    ? props.outcome.result.probabilityModel || (props.outcome.kind === "image" ? props.outcome.result.swarm?.probabilityModel : undefined)
    : undefined;
  const captureEvidence = props.outcome.kind === "image"
    ? props.outcome.result.capture_evidence
    : props.outcome.kind === "evidence"
      ? props.outcome.result.captureEvidence || provenance?.captureEvidence
      : undefined;
  const visualReview = props.outcome.kind === "image"
    ? props.outcome.result.visualReview
    : undefined;
  const completeMetadata = useMemo(() => {
    if (props.outcome.kind === "image") {
      return mergedMetadataRows(provenance?.metadata || {}, props.outcome.result.all_metadata || {});
    }
    if (props.outcome.kind === "evidence") return mergedMetadataRows(provenance?.metadata || {});
    if (props.outcome.kind === "video") return mergedMetadataRows(props.outcome.result.meta || {});
    return [];
  }, [props.outcome, provenance]);
  const cameraMake = findMetadataRow(completeMetadata, [
    "image.exif.Make", "EXIF:Make", "EXIF_Make", "TIFF:Make", "cameraMake",
  ]);
  const cameraModel = findMetadataRow(completeMetadata, [
    "image.exif.Model", "EXIF:Model", "EXIF_Model", "TIFF:Model", "cameraModel",
  ]);
  const lensModel = findMetadataRow(completeMetadata, [
    "image.exif.LensModel", "EXIF:LensModel", "EXIF:LensInfo", "LensModel", "LensInfo",
  ]);
  const cameraDevice = [cameraMake?.value, cameraModel?.value]
    .filter((value, index, values): value is string => Boolean(value) && values.indexOf(value) === index)
    .join(" ");
  const metadataFieldCount = Math.max(completeMetadata.length, Number(provenance?.metadataSummary?.fieldCount || 0));
  const metadataSourceLabel = provenance?.metadata && Object.keys(provenance.metadata).length > 0
    ? "来源核验读取"
    : props.outcome.kind === "image" && Object.keys(props.outcome.result.all_metadata || {}).length > 0
      ? "检测服务读取"
      : props.outcome.kind === "video"
        ? "视频容器信息"
        : "暂无数据";
  const normalizedMetadataQuery = metadataQuery.trim().toLowerCase();
  const filteredMetadata = normalizedMetadataQuery
    ? completeMetadata.filter((row) => `${row.path} ${row.value}`.toLowerCase().includes(normalizedMetadataQuery))
    : completeMetadata;
  const aiMetadataSignalPaths = new Set((provenance?.aiMetadata?.signals || []).map((signal) => normalizedMetadataPath(signal.path)));
  const c2paStatus = props.provenanceAvailable
    ? provenanceView(provenance, props.provenanceBusy)
    : { label: "登录后核验", detail: "登录后自动读取 C2PA 内容凭证", tone: "neutral" as const };

  async function refreshShares() {
    if (props.outcome.kind !== "evidence") return;
    setShares(await listReportShares(props.outcome.result.reportId));
  }

  async function createShare() {
    if (props.outcome.kind !== "evidence" || shareBusy) return;
    if (!window.confirm("将创建一个 7 天有效的访问链接。任何获得该链接的人都能查看这份报告，无需登录；请勿发送到公开群聊或不可信渠道。确认继续？")) return;
    setShareBusy(true);
    setShareMessage("");
    try {
      const link = await createReportShareLink(props.outcome.result.reportId);
      await refreshShares();
      setCreatedShareUrl(link.url);
      setShareMessage("链接已创建；确认接收方后再复制，持有者可在 7 天内查看报告");
    } catch (error) {
      setShareMessage(error instanceof Error ? error.message : "生成分享链接失败");
    } finally {
      setShareBusy(false);
    }
  }

  async function toggleShares() {
    if (props.outcome.kind !== "evidence" || shareBusy) return;
    if (shareOpen) {
      setShareOpen(false);
      return;
    }
    setShareBusy(true);
    setShareMessage("");
    try {
      await refreshShares();
      setShareOpen(true);
    } catch (error) {
      setShareMessage(error instanceof Error ? error.message : "加载分享记录失败");
    } finally {
      setShareBusy(false);
    }
  }

  async function revokeShare(shareId: string) {
    if (props.outcome.kind !== "evidence" || shareBusy) return;
    if (!window.confirm("撤销后，已发出的该链接将立即失效。确认撤销？")) return;
    setShareBusy(true);
    try {
      await revokeReportShare(props.outcome.result.reportId, shareId);
      await refreshShares();
      setShareMessage("分享链接已撤销");
    } catch (error) {
      setShareMessage(error instanceof Error ? error.message : "撤销分享链接失败");
    } finally {
      setShareBusy(false);
    }
  }

  function seekVideo(timestamp: number) {
    const video = videoElement;
    if (!video) return;
    try {
      video.currentTime = Math.max(0, timestamp);
      setVideoCurrentTime(Math.max(0, timestamp));
      video.scrollIntoView({
        block: "center",
        behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      });
    } catch {
      // The player will become seekable after metadata is available.
    }
  }

  const evidenceItems = props.outcome.kind === "image"
    ? [...(props.outcome.result.swarm?.evidence || []), ...(props.outcome.result.visual_issues || [])]
    : props.outcome.kind === "video"
      ? [
          ...(props.outcome.result.evidence?.keyEvidence || []).map((item) => `${item.label}：${item.detail}`),
          props.outcome.result.explanation,
        ].filter((item, index, items): item is string => Boolean(item) && items.indexOf(item) === index)
      : props.outcome.result.dimensions.map((item) => `${item.label}：${item.result}`);

  return (
    <article className={`agent-result tone-${verdict.tone}${verdict.reviewOnly ? " is-review-only" : ""}${props.outcome.kind === "video" ? " is-video" : ""}`} aria-labelledby="detection-result-title">
      <header className="result-hero result-presentation-hero">
        <div className="result-preview">
          {props.outcome.kind === "video" && preview ? (
            <VideoResultPreview
              sources={videoSources}
              name={fileName(props.outcome)}
              videoRef={setVideoElement}
              openButtonRef={videoExpandButtonRef}
              onTimeChange={setVideoCurrentTime}
              onOpen={setVideoLightboxSource}
            />
          ) : preview ? (
            <AnnotatedImagePreview src={preview} alt={fileName(props.outcome)} marks={previewWatermarkMarks} onOpen={() => setLightboxOpen(true)} />
          ) : (
            <span>{props.outcome.kind === "video" ? <Video size={30} /> : props.outcome.kind === "image" ? <ImageIcon size={30} /> : <FileText size={30} />}</span>
          )}
          <div
            className={`result-verdict-stamp is-${verdict.tone}`}
            aria-label={`检测结论：${verdict.tone === "fake" ? "假" : "真"}`}
          >
            <strong>{verdict.tone === "fake" ? "假" : "真"}</strong>
            <small>鉴伪结论</small>
          </div>
        </div>
        <div className="result-verdict">
          <div className="verdict-kicker"><StatusIcon name={verdict.tone === "fake" ? "fake" : "real"} size={17} /> 小鉴综合判断</div>
          <h2 id="detection-result-title">{verdict.label}</h2>
          <p>{publicCopy(verdict.description)}</p>
          <dl className="result-overview-metrics">
            <div className="result-probability-metric">
              <dt>AI 生成概率</dt>
              <dd>{probabilityText(verdict.modelProbability)}{verdict.modelProbability !== null && <small>%</small>}</dd>
              <i aria-hidden="true"><span style={{ width: `${Math.round((verdict.modelProbability ?? 0) * 100)}%` }} /></i>
              <small>{verdict.modelProbability === null ? "模型未返回可用分数" : verdict.reviewOnly ? "模型直接输出 · 尚未独立验证" : "模型直接输出"}</small>
            </div>
            <div>
              <dt>结论可靠性</dt>
              <dd>{verdict.confidence}</dd>
              <small>{verdict.reviewOnly ? "需经独立测试集验证" : "已形成可发布结论"}</small>
            </div>
            <div>
              <dt>来源核验</dt>
              <dd>{props.provenanceBusy ? "正在核验" : "3/3 项已扫描"}</dd>
              <small>水印、元数据与 C2PA</small>
            </div>
          </dl>
        </div>
      </header>

      <nav className="result-tabs" aria-label="检测结果视图" role="tablist" onKeyDown={(event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
        const current = tabs.indexOf(document.activeElement as HTMLButtonElement);
        if (current < 0) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
        tabs[next]?.focus();
        tabs[next]?.click();
      }}>
        <button id="result-tab-summary" role="tab" aria-selected={tab === "summary"} aria-controls="result-panel-summary" tabIndex={tab === "summary" ? 0 : -1} type="button" className={tab === "summary" ? "active" : ""} onClick={() => setTab("summary")}><ShieldCheck size={16} /> 结论</button>
        <button id="result-tab-evidence" role="tab" aria-selected={tab === "evidence"} aria-controls="result-panel-evidence" tabIndex={tab === "evidence" ? 0 : -1} type="button" className={tab === "evidence" ? "active" : ""} onClick={() => setTab("evidence")}><Layers3 size={16} /> 证据</button>
        <button id="result-tab-file" role="tab" aria-selected={tab === "file"} aria-controls="result-panel-file" tabIndex={tab === "file" ? 0 : -1} type="button" className={tab === "file" ? "active" : ""} onClick={() => setTab("file")}><FileSearch size={16} /> 文件信息</button>
      </nav>

      {tab === "summary" && (
        <div className="result-tab-panel" id="result-panel-summary" role="tabpanel" aria-labelledby="result-tab-summary" tabIndex={0}>
          <ResultDecisionCard
            points={explanationPoints}
            verdict={verdict}
            provenance={provenance}
            provenanceBusy={props.provenanceBusy}
            provenanceAvailable={props.provenanceAvailable}
            visibleWatermark={visibleWatermark}
            captureEvidence={captureEvidence}
            cameraDeviceHint={cameraDevice}
            metadataCount={metadataFieldCount}
            metadataRows={completeMetadata}
          />
          {visualReview && (
            <section className="result-band result-priority-band">
              <details className="rationale-disclosure">
                <summary>
                  {["queued", "running"].includes(visualReview.status)
                    ? "视觉复核正在后台补充"
                    : visualReview.status === "success"
                      ? "视觉复核补充已完成"
                      : "视觉复核补充未完成"}
                  <span>不改变主结论</span><ChevronDown size={15} />
                </summary>
                <div className="result-explanation result-rationale" role="list">
                  <div role="listitem">
                    <strong>补充说明</strong>
                    <p>{publicCopy(visualReview.note || "视觉复核仅提供补充解释，不回写或推翻已经发布的主结论。")}</p>
                  </div>
                  {(visualReview.evidence || []).map((item, index) => (
                    <div role="listitem" key={`visual-review-${index}`}>
                      <strong>视觉线索 {index + 1}</strong>
                      <p>{publicCopy(item)}</p>
                    </div>
                  ))}
                </div>
              </details>
            </section>
          )}
          {props.outcome.kind === "video" && (
            <VideoEvidenceSection
              evidence={props.outcome.result.evidence}
              frameCount={props.outcome.result.frame_count}
              currentTime={videoCurrentTime}
              onSeek={seekVideo}
            />
          )}
          <div className="result-actions">
            <button type="button" className="primary-button" onClick={props.onDownload} disabled={props.downloadBusy}>
              {props.downloadBusy ? <LoaderCircle size={17} className="spin" /> : <Download size={17} />}
              {props.downloadBusy ? "正在整理报告" : "下载鉴伪报告"}
            </button>
            {props.outcome.kind === "evidence" && (
              <>
                <button type="button" className="secondary-button" onClick={() => void createShare()} disabled={shareBusy}>
                  {shareBusy ? <LoaderCircle size={17} className="spin" /> : <Link2 size={17} />}
                  创建 7 天分享链接
                </button>
                <button type="button" className="icon-button" onClick={() => void toggleShares()} disabled={shareBusy} aria-label={shareOpen ? "关闭分享管理" : "管理分享链接"} title={shareOpen ? "关闭分享管理" : "管理分享链接"}>
                  <ShieldOff size={17} />
                </button>
              </>
            )}
          </div>
          {props.outcome.kind === "evidence" && (shareMessage || shareOpen) && (
            <section className="report-share-panel" aria-label="报告分享管理">
              {shareMessage && <p role="status">{shareMessage}</p>}
              {createdShareUrl && (
                <div className="report-share-created">
                  <code>{`${createdShareUrl.slice(0, 28)}...${createdShareUrl.slice(-8)}`}</code>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(createdShareUrl);
                        setShareMessage("分享链接已复制，请仅发送给可信接收方");
                      } catch {
                        window.prompt("复制报告分享链接", createdShareUrl);
                      }
                    }}
                  >
                    <Copy size={15} /> 复制链接
                  </button>
                </div>
              )}
              {shareOpen && (
                <div className="report-share-list">
                  <div><strong>已创建的链接</strong><span>{shares.filter((item) => item.active).length} 个有效</span></div>
                  {shares.length === 0 ? <p>尚未创建分享链接</p> : shares.map((item) => (
                    <div className="report-share-row" key={item.shareId}>
                      <span><code>{item.shareId}</code><small>{item.active ? `有效至 ${new Date(item.expiresAt).toLocaleString()}` : "已失效"}</small></span>
                      {item.active && (
                        <button type="button" className="icon-button danger" onClick={() => void revokeShare(item.shareId)} disabled={shareBusy} aria-label="撤销分享链接" title="撤销分享链接">
                          <ShieldOff size={16} />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}
          {props.actionError && (
            <div className="result-action-error" role="alert">
              <AlertTriangle size={16} /><span>{props.actionError}</span>
              {props.onRetryAction && <button type="button" onClick={props.onRetryAction}>重试此操作</button>}
            </div>
          )}
          {(!verdict.reviewOnly && (probabilityModel || (props.outcome.kind === "image" && props.outcome.result.swarm?.enabled))) && (
            <ResultDisclosure
              icon={<Gauge size={18} />}
              title="风险与证据计算"
              description="查看多源共识、风险基线和证据融合过程"
            >
              {!verdict.reviewOnly && props.outcome.kind === "image" && props.outcome.result.swarm?.enabled && (
                <section className="result-band consensus-band">
                  <div className="section-title"><ScanLine size={18} /><div><h3>多源复核共识</h3><p>{props.outcome.result.swarm.disagreement ? "不同证据源存在分歧，建议人工复核原始文件。" : "有效证据源的判断方向较一致。"}</p></div></div>
                  <div className="consensus-line">
                    <span>有效复核 {props.outcome.result.swarm.effectiveExperts || 0}/{props.outcome.result.swarm.totalExperts || props.outcome.result.swarm.experts?.length || 0}</span>
                    <strong>{Math.round(Number(props.outcome.result.swarm.consensusScore || 0) * 100)}% 共识</strong>
                  </div>
                  <div className="consensus-track"><i style={{ width: `${Math.round(Number(props.outcome.result.swarm.consensusScore || 0) * 100)}%` }} /></div>
                </section>
              )}
              {!verdict.reviewOnly && <ProbabilitySection model={probabilityModel} />}
            </ResultDisclosure>
          )}
          {(captureEvidence || synthid || visibleWatermark || provenance) && (
            <ResultDisclosure
              icon={<Fingerprint size={18} />}
              title="水印与来源证据"
              description="查看平台水印、实拍信息、内容凭证与来源核验"
            >
              <CaptureEvidenceSection report={captureEvidence} />
              <SynthIDSection report={synthid} />
              <WatermarkSection report={visibleWatermark} preview={preview} />
              <ProvenanceSection report={provenance} />
            </ResultDisclosure>
          )}
        </div>
      )}

      {tab === "evidence" && (
        <div className="result-tab-panel" id="result-panel-evidence" role="tabpanel" aria-labelledby="result-tab-evidence" tabIndex={0}>
          {props.outcome.kind !== "video" && (
            <section className="result-band">
              <div className="section-title"><Layers3 size={18} /><div><h3>证据摘要</h3><p>证据条目用于解释模型判断，不应脱离原始文件单独使用。</p></div></div>
              <EvidenceList items={evidenceItems} />
            </section>
          )}
          {props.outcome.kind === "video" && (
            <VideoEvidenceSection
              evidence={props.outcome.result.evidence}
              frameCount={props.outcome.result.frame_count}
              currentTime={videoCurrentTime}
              onSeek={seekVideo}
            />
          )}
          <CaptureEvidenceSection report={captureEvidence} />
          {!verdict.reviewOnly && <ProbabilitySection model={probabilityModel} />}
          <SynthIDSection report={synthid} />
          <WatermarkSection report={visibleWatermark} preview={preview} />
          {props.outcome.kind === "image" && props.outcome.result.swarm?.experts && (
            <section className="result-band">
              <div className="section-title"><ScanLine size={18} /><div><h3>复核队列</h3><p>仅展示匿名角色与公开状态。</p></div></div>
              <div className="expert-list">
                {props.outcome.result.swarm.experts.map((expert, index) => (
                  <div key={expert.publicId || expert.id || index}>
                    <ExpertStatus status={expert.status} />
                    <span><strong>{expert.publicName || `复核角色 ${index + 1}`}</strong><small>{expert.publicMessage || expert.publicVerdict || "等待公开结论"}</small></span>
                  </div>
                ))}
              </div>
            </section>
          )}
          {props.outcome.kind === "evidence" && (
            <section className="result-band">
              <div className="dimension-list">
                {props.outcome.result.dimensions.map((dimension) => (
                  <div key={dimension.key}>
                    <span><strong>{dimension.label}</strong><small>{publicCopy(dimension.result)}</small></span>
                    <b>{Math.round(clamp01(Number(dimension.score || 0)) * 100)}%</b>
                    <i><em style={{ width: `${Math.round(clamp01(Number(dimension.score || 0)) * 100)}%` }} /></i>
                  </div>
                ))}
              </div>
            </section>
          )}
          <ProvenanceSection report={provenance} />
        </div>
      )}

      {tab === "file" && (
        <div className="result-tab-panel" id="result-panel-file" role="tabpanel" aria-labelledby="result-tab-file" tabIndex={0}>
          <section className="result-band">
            <div className="section-title"><FileSearch size={18} /><div><h3>原始文件信息</h3><p>文件名与基础属性只用于本次任务和个人历史归档。</p></div></div>
            <dl className="fact-grid">
              <div><dt>文件名</dt><dd>{fileName(props.outcome)}</dd></div>
              <div><dt>内容类型</dt><dd>{props.outcome.kind === "image" ? "图像" : props.outcome.kind === "video" ? "视频" : props.outcome.result.fileMeta.type === "document" ? "文档" : "图像"}</dd></div>
              {props.outcome.kind === "image" && <><div><dt>文件大小</dt><dd>{props.outcome.result.file_size || "未返回"}</dd></div><div><dt>分辨率</dt><dd>{props.outcome.result.resolution || "未返回"}</dd></div><div><dt>格式</dt><dd>{props.outcome.result.img_format || "未返回"}</dd></div><div><dt>任务编号</dt><dd>{props.outcome.result.itemid}</dd></div></>}
              {props.outcome.kind === "image" && cameraDevice && <div><dt>拍摄设备</dt><dd>{cameraDevice}</dd></div>}
              {props.outcome.kind === "image" && cameraModel && <div><dt>相机型号</dt><dd>{cameraModel.value}<small className="fact-source-path">{cameraModel.path}</small></dd></div>}
              {props.outcome.kind === "image" && lensModel && <div><dt>镜头信息</dt><dd>{lensModel.value}<small className="fact-source-path">{lensModel.path}</small></dd></div>}
              {props.outcome.kind === "video" && <>
                <div><dt>文件大小</dt><dd>{props.outcome.result.meta?.file_size || "未返回"}</dd></div>
                <div><dt>分辨率</dt><dd>{props.outcome.result.meta?.resolution || "未返回"}</dd></div>
                <div><dt>时长</dt><dd>{props.outcome.result.meta?.duration || "未返回"}</dd></div>
                <div><dt>帧率</dt><dd>{props.outcome.result.meta?.fps ? `${props.outcome.result.meta.fps} FPS` : "未返回"}</dd></div>
                <div><dt>视频编码</dt><dd>{props.outcome.result.meta?.codec || props.outcome.result.meta?.video_format || "未返回"}</dd></div>
                <div><dt>总帧数</dt><dd>{props.outcome.result.meta?.total_frames || "未返回"}</dd></div>
                <div><dt>模型输入帧</dt><dd>{props.outcome.result.frame_count || "未返回"}</dd></div>
                <div><dt>任务编号</dt><dd>{props.outcome.result.itemid}</dd></div>
              </>}
              {props.outcome.kind === "evidence" && <><div><dt>文件大小</dt><dd>{props.outcome.result.fileMeta.size}</dd></div><div><dt>分辨率</dt><dd>{props.outcome.result.fileMeta.resolution || "不适用"}</dd></div><div><dt>文件指纹</dt><dd className="mono-value">{props.outcome.result.fileMeta.sha256 || "未返回"}</dd></div><div><dt>报告编号</dt><dd>{props.outcome.result.reportId}</dd></div></>}
            </dl>
            {(props.outcome.kind === "image" || props.outcome.kind === "evidence") && (
              <div className={`file-provenance-state is-${c2paStatus.tone}`}>
                <span><Fingerprint size={18} /></span>
                <div><strong>C2PA 内容凭证</strong><small>{c2paStatus.detail}</small></div>
                <b>{c2paStatus.label}</b>
              </div>
            )}
          </section>
          <section className="result-band metadata-band">
            <div className="metadata-band-heading">
              <div className="section-title"><Fingerprint size={18} /><div><h3>完整元数据</h3><p>展示服务器允许返回的全部原始字段；敏感定位与设备标识仍遵循隐私规则。</p></div></div>
              <span>{metadataFieldCount} 项 · {metadataSourceLabel}</span>
            </div>
            {completeMetadata.length > 0 ? (
              <>
                <label className="metadata-search">
                  <Search size={17} />
                  <input
                    type="search"
                    value={metadataQuery}
                    onChange={(event) => setMetadataQuery(event.target.value)}
                    placeholder="搜索字段或值"
                    aria-label="搜索完整元数据"
                  />
                  <span>{filteredMetadata.length}/{completeMetadata.length}</span>
                </label>
                {filteredMetadata.length > 0 ? (
                  <dl className="metadata-list">
                    {filteredMetadata.map((row, index) => {
                      const direction = metadataRowDirection(row, aiMetadataSignalPaths);
                      return (
                        <div className={direction ? `is-${direction}` : undefined} key={`${row.path}-${index}`}>
                          <dt>
                            <span>{row.path}</span>
                            {direction && <b>{direction === "fake" ? "AI 生成线索" : "实拍线索"}</b>}
                          </dt>
                          <dd>{row.value}</dd>
                        </div>
                      );
                    })}
                  </dl>
                ) : (
                  <div className="metadata-empty"><Info size={16} /> 没有匹配“{metadataQuery}”的元数据字段。</div>
                )}
              </>
            ) : (
              <div className="metadata-empty metadata-empty-action">
                {props.provenanceBusy ? <LoaderCircle size={17} className="spin" /> : <Info size={16} />}
                <span>{props.provenanceBusy ? "正在从原始文件读取元数据与 C2PA 凭证。" : "当前文件未读取到可展示的元数据；字段缺失本身不代表内容为假。"}</span>
                {canDeepAnalyze && !props.provenanceBusy && (
                  <button type="button" onClick={props.onProvenance}>重新读取</button>
                )}
              </div>
            )}
          </section>
          <div className="result-disclaimer"><Info size={16} /><p>鉴伪结果是辅助判断，不等同于司法鉴定结论。高风险场景请结合原始文件、来源链路和人工复核。</p></div>
        </div>
      )}
      {lightboxOpen && preview && (
        <ImageLightbox src={preview} alt={fileName(props.outcome)} marks={previewWatermarkMarks} onClose={() => setLightboxOpen(false)} />
      )}
      {videoLightboxSource && props.outcome.kind === "video" && (
        <VideoLightbox src={videoLightboxSource} name={fileName(props.outcome)} onClose={closeVideoLightbox} />
      )}
    </article>
  );
}
