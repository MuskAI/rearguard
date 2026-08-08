import {
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  BadgeCheck,
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
  MousePointer2,
  ScanSearch,
  ScanLine,
  ShieldCheck,
  ShieldOff,
  Sparkles,
  Video,
  X,
  ZoomIn,
} from "lucide-react";
import { createPortal } from "react-dom";
import type { AgentOutcome } from "../agentTypes";
import { binaryVerdictLabel, isFakeVerdict } from "../binaryVerdict";
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
} from "../api";
import { buildEvidenceExplanation, hasDecisiveAiWatermark, localizedWatermarkHits } from "../evidenceExplanation";
import { StatusIcon } from "./BrandSystem";
import WatermarkPipeline from "./WatermarkPipeline";

type ResultTab = "summary" | "evidence" | "file";

interface Props {
  outcome: AgentOutcome;
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
  riskLabel: string;
  tone: "real" | "warn" | "fake";
  confidence: string;
  reviewOnly: boolean;
}

const AI_WATERMARK_PROVIDERS = new Set(["gemini", "doubao", "jimeng", "jimeng_pill", "samsung"]);

function clamp01(value: number) {
  return Math.max(0, Math.min(value, 1));
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
    const rawValue = outcome.result.probability ?? outcome.result.detector_probability;
    const raw = Number(rawValue ?? 0);
    const localizedWatermark = hasDecisiveAiWatermark(outcome.result.visibleWatermark);
    const risk = Math.max(clamp01(raw > 1 ? raw / 100 : raw), localizedWatermark ? 0.95 : 0);
    const label = binaryVerdictLabel(
      localizedWatermark ? "AI生成图像" : outcome.result.final_label,
      rawValue,
    );
    const tone = isFakeVerdict(label) ? "fake" : "real";
    return {
      label,
      description: reviewOnly
        ? outcome.result.explanation || `系统给出“${label}”二元结论；当前置信度较低，建议结合原图和证据复核。`
        : localizedWatermark
          ? "已确认强 AI 平台水印，平台匹配、区域定位与 OCR/检索证据相互印证。"
          : tone === "real"
            ? "本次多源分析未发现足以支持 AI 生成的强证据。"
            : "检测到需要关注的生成或编辑线索，建议结合原始来源复核。",
      risk,
      riskLabel: outcome.result.swarm?.enabled ? "综合异常风险" : "AI 生成风险",
      tone,
      confidence: reviewOnly ? "低，建议复核" : outcome.result.confidence || "未标注",
      reviewOnly,
    };
  }
  if (outcome.kind === "video") {
    const reviewOnly = outcome.result.decisionStatus !== "verdict" || outcome.result.reviewRequired === true;
    const risk = clamp01(Number(outcome.result.fake_percentage ?? 0) / 100);
    const label = binaryVerdictLabel(outcome.result.final_label, outcome.result.fake_percentage);
    const tone = isFakeVerdict(label) ? "fake" : "real";
    return {
      label,
      description: reviewOnly
        ? outcome.result.explanation || `系统给出“${label}”二元结论；当前置信度较低，建议结合原视频复核。`
        : tone === "real"
          ? "抽帧与时序分析未发现明确的合成证据。"
          : "视频中存在需要人工复核的合成线索。",
      risk,
      riskLabel: "合成风险",
      tone,
      confidence: reviewOnly ? "低，建议复核" : outcome.result.confidence || "未标注",
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
    riskLabel: tamperRisk >= Math.max(aiRisk, deepfakeRisk, 0.62) || deepfakeRisk >= Math.max(aiRisk, tamperRisk, 0.62)
      ? "综合异常风险"
      : "AI 生成风险",
    tone,
    confidence: reviewOnly ? "低，建议复核" : outcome.result.source === "vlm"
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

function ProvenanceSection({ report }: { report?: ProvenanceReport }) {
  if (!report) return null;
  const credentialLabel = report.hasCredentials
    ? report.validationState === "valid" ? "凭证签名有效" : "发现内容凭证"
    : report.metadataAiGenerated ? "发现 AI 元数据线索" : "未发现可验证凭证";
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
  const localizedHits = localizedWatermarkHits(report).slice(0, 8);
  const platformHits = hits.filter((hit) => AI_WATERMARK_PROVIDERS.has(hit.provider));
  const decisiveWatermark = hasDecisiveAiWatermark(report);
  const genericHits = hits.filter((hit) => !AI_WATERMARK_PROVIDERS.has(hit.provider));
  const detector = report.detector;
  const detected = hits.length > 0;
  const hasPlatformHit = platformHits.length > 0;
  const reusedFromSameFile = report.reanalysis?.reused === true;
  const reusedLegacyResult = report.reanalysis?.basis === "legacy-unowned-exact-sha256";
  const confirmedHits = platformHits.filter((hit) => hit.localizationConfirmed === true);
  const providerLabels = Array.from(new Set(platformHits.map((hit) => hit.label || hit.provider))).join("、");
  const statusText = !report.supported
    ? "可见水印检测本次不可用，未影响主鉴伪结论"
    : hasPlatformHit
      ? `识别到 ${platformHits.length} 处已知 AI 平台水印`
      : detected
        ? `检测到 ${genericHits.length} 处可见水印，平台归属待确认`
        : "可见水印扫描完成，本次未检出";
  const elapsed = Number(report.elapsedMs || detector?.roundTripMs || 0);
  const suppliedRegistry = detector?.engines?.find((engine) => engine.id === "known_ai_registry");
  const suppliedFusion = detector?.engines?.find((engine) => engine.id === "explicit_ai_watermark_fusion");
  const suppliedYolo = detector?.engines?.find((engine) => engine.id.includes("yolo"));
  const engines = [
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
      <div className={`watermark-status ${hasPlatformHit ? "is-detected" : detected ? "is-possible" : report.supported ? "is-clear" : "is-unavailable"}`}>
        <span>{hasPlatformHit ? `已知平台 ${platformHits.length}` : detected ? `可见水印 ${genericHits.length}` : report.supported ? "未检出" : "暂不可用"}</span>
        <strong>{hasPlatformHit ? `${providerLabels} · ${decisiveWatermark ? "强证据确认" : "平台规则确认"}` : detected ? `${reusedFromSameFile ? "同一文件复核补充 · " : ""}通用水印线索，不单独判假` : "已完成平台规则与通用水印扫描"}</strong>
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
                      <strong>{hit.label || (AI_WATERMARK_PROVIDERS.has(hit.provider) ? "已知 AI 平台水印" : "可见水印（平台待确认）")}</strong>
                      <small>
                        {AI_WATERMARK_PROVIDERS.has(hit.provider)
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
              <div className="watermark-clear-state"><CheckCircle2 size={18} /><span><strong>未发现可见水印</strong><small>已完成平台标记与区域扫描</small></span></div>
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

interface MetadataRow {
  path: string;
  value: string;
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

export default function AgentResult(props: Props) {
  const [tab, setTab] = useState<ResultTab>("summary");
  const [shareBusy, setShareBusy] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareMessage, setShareMessage] = useState("");
  const [createdShareUrl, setCreatedShareUrl] = useState("");
  const [shares, setShares] = useState<ReportShareItem[]>([]);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  useEffect(() => {
    setTab("summary");
    setShareOpen(false);
    setShareMessage("");
    setCreatedShareUrl("");
    setShares([]);
    setLightboxOpen(false);
  }, [props.outcome.id]);
  const verdict = useMemo(() => verdictFor(props.outcome), [props.outcome]);
  const explanationPoints = useMemo(
    () => buildEvidenceExplanation(props.outcome, verdict.risk, verdict.label),
    [props.outcome, verdict.label, verdict.risk],
  );
  const keyExplanationPoints = useMemo(() => {
    const evidencePoints = explanationPoints.filter((point) => point.label !== "综合结论");
    const decisive = evidencePoints.filter((point) => point.decisive);
    const supporting = evidencePoints.filter((point) => !point.decisive);
    const prioritized = [...decisive, ...supporting];
    const fallback = explanationPoints.filter((point) => point.label === "综合结论");
    return [...(prioritized.length > 0 ? prioritized : fallback)]
      .filter((point, index, items) => items.findIndex((item) => item.label === point.label && item.text === point.text) === index)
      .slice(0, 3);
  }, [explanationPoints]);
  const additionalExplanationPoints = useMemo(() => {
    const shown = new Set(keyExplanationPoints);
    return explanationPoints.filter((point) => !shown.has(point));
  }, [explanationPoints, keyExplanationPoints]);
  const preview = filePreview(props.outcome);
  const canDeepAnalyze = hasImageFile(props.outcome);
  const provenance = props.outcome.kind === "image" || props.outcome.kind === "evidence"
    ? props.outcome.provenance || (props.outcome.kind === "evidence" ? props.outcome.result.provenance || undefined : undefined)
    : undefined;
  const visibleWatermark = props.outcome.kind === "image" || props.outcome.kind === "evidence"
    ? props.outcome.result.visibleWatermark
    : undefined;
  const previewWatermarkMarks = useMemo(() => {
    if (!visibleWatermark) return [];
    const allHits = visibleWatermark.hits || [];
    return localizedWatermarkHits(visibleWatermark).slice(0, 8).map((hit, localizedIndex) => ({
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
    if (props.outcome.kind === "image") return metadataRows(props.outcome.result.all_metadata || {});
    if (props.outcome.kind === "video") return metadataRows(props.outcome.result.meta || {});
    return metadataRows(provenance?.metadata || {});
  }, [props.outcome, provenance]);

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

  const evidenceItems = props.outcome.kind === "image"
    ? [...(props.outcome.result.swarm?.evidence || []), ...(props.outcome.result.visual_issues || [])]
    : props.outcome.kind === "video"
      ? [props.outcome.result.explanation].filter(Boolean)
      : props.outcome.result.dimensions.map((item) => `${item.label}：${item.result}`);

  return (
    <article className={`agent-result tone-${verdict.tone}${verdict.reviewOnly ? " is-review-only" : ""}`} aria-labelledby="detection-result-title">
      <header className="result-hero">
        <div className="result-preview">
          {props.outcome.kind === "video" && preview ? (
            <video src={preview} controls preload="metadata" />
          ) : preview ? (
            <AnnotatedImagePreview src={preview} alt={fileName(props.outcome)} marks={previewWatermarkMarks} onOpen={() => setLightboxOpen(true)} />
          ) : (
            <span>{props.outcome.kind === "video" ? <Video size={30} /> : props.outcome.kind === "image" ? <ImageIcon size={30} /> : <FileText size={30} />}</span>
          )}
        </div>
        <div className="result-verdict">
          <div className="verdict-kicker"><StatusIcon name={verdict.tone === "fake" ? "fake" : "real"} size={17} /> 小鉴综合判断</div>
          <h2 id="detection-result-title">{verdict.label}</h2>
          <p>{verdict.description}</p>
          <div className="verdict-meta">
            <span>
              {verdict.reviewOnly ? <FileSearch size={15} /> : <BadgeCheck size={15} />}
              置信说明 <strong>{verdict.confidence}</strong>
            </span>
          </div>
        </div>
        {!verdict.reviewOnly && (
          <div className="risk-meter" aria-label={`${verdict.riskLabel} ${Math.round(verdict.risk * 100)}%`}>
            <div className="risk-meter-value">{Math.round(verdict.risk * 100)}<small>%</small></div>
            <span>{verdict.riskLabel}</span>
            <div className="risk-meter-track"><i style={{ width: `${Math.round(verdict.risk * 100)}%` }} /></div>
          </div>
        )}
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
          <section className="result-band result-priority-band">
            <div className="section-title"><Sparkles size={18} /><div><h3>关键依据</h3><p>优先展示对本次结论影响最大的证据。</p></div></div>
            <div className="result-explanation result-rationale is-priority" role="list">
              {keyExplanationPoints.map((point, index) => (
                <div className={point.decisive ? "is-decisive" : ""} role="listitem" key={`${point.label}-${index}`}>
                  <span className="rationale-rank" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                  <strong>{point.label}</strong>
                  <p>{publicCopy(point.text)}</p>
                </div>
              ))}
            </div>
            {additionalExplanationPoints.length > 0 && (
              <details className="rationale-disclosure">
                <summary>查看完整判断依据 <span>{additionalExplanationPoints.length} 项</span><ChevronDown size={15} /></summary>
                <div className="result-explanation result-rationale" role="list">
                  {additionalExplanationPoints.map((point, index) => (
                    <div className={point.decisive ? "is-decisive" : ""} role="listitem" key={`${point.label}-${index}`}>
                      <strong>{point.label}</strong>
                      <p>{publicCopy(point.text)}</p>
                    </div>
                  ))}
                </div>
              </details>
            )}
            {visualReview && (
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
            )}
          </section>
          <div className="result-actions">
            <button type="button" className="primary-button" onClick={props.onDownload} disabled={props.downloadBusy}>
              {props.downloadBusy ? <LoaderCircle size={17} className="spin" /> : <Download size={17} />}
              {props.downloadBusy ? "正在整理报告" : "下载鉴伪报告"}
            </button>
            <button type="button" className="secondary-button" onClick={props.onProvenance} disabled={!canDeepAnalyze || props.provenanceBusy} title={canDeepAnalyze ? "验证 C2PA 与文件元数据" : "历史任务需重新上传原文件后验证内容凭证"}>
              {props.provenanceBusy ? <LoaderCircle size={17} className="spin" /> : <Fingerprint size={17} />}
              {provenance ? "重新验证内容凭证" : "验证内容凭证"}
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
          <section className="result-band">
            <div className="section-title"><Layers3 size={18} /><div><h3>证据摘要</h3><p>证据条目用于解释模型判断，不应脱离原始文件单独使用。</p></div></div>
            <EvidenceList items={evidenceItems} />
          </section>
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
              {props.outcome.kind === "video" && <><div><dt>分辨率</dt><dd>{props.outcome.result.meta?.resolution || "未返回"}</dd></div><div><dt>时长</dt><dd>{props.outcome.result.meta?.duration || "未返回"}</dd></div><div><dt>抽帧数</dt><dd>{props.outcome.result.frame_count || "未返回"}</dd></div><div><dt>任务编号</dt><dd>{props.outcome.result.itemid}</dd></div></>}
              {props.outcome.kind === "evidence" && <><div><dt>文件大小</dt><dd>{props.outcome.result.fileMeta.size}</dd></div><div><dt>分辨率</dt><dd>{props.outcome.result.fileMeta.resolution || "不适用"}</dd></div><div><dt>文件指纹</dt><dd className="mono-value">{props.outcome.result.fileMeta.sha256 || "未返回"}</dd></div><div><dt>报告编号</dt><dd>{props.outcome.result.reportId}</dd></div></>}
            </dl>
          </section>
          <section className="result-band metadata-band">
            <div className="section-title"><Fingerprint size={18} /><div><h3>完整元数据</h3><p>展示服务器从原始文件中读取到的全部字段，不省略嵌套信息。</p></div></div>
            {completeMetadata.length > 0 ? (
              <dl className="metadata-list">
                {completeMetadata.map((row, index) => (
                  <div key={`${row.path}-${index}`}><dt>{row.path}</dt><dd>{row.value}</dd></div>
                ))}
              </dl>
            ) : (
              <div className="metadata-empty"><Info size={16} /> 当前文件未读取到可展示的元数据。</div>
            )}
          </section>
          <div className="result-disclaimer"><Info size={16} /><p>鉴伪结果是辅助判断，不等同于司法鉴定结论。高风险场景请结合原始文件、来源链路和人工复核。</p></div>
        </div>
      )}
      {lightboxOpen && preview && (
        <ImageLightbox src={preview} alt={fileName(props.outcome)} marks={previewWatermarkMarks} onClose={() => setLightboxOpen(false)} />
      )}
    </article>
  );
}
