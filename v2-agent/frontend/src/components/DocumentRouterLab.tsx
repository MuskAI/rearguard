import {
  ArrowLeft,
  Check,
  FileImage,
  RefreshCw,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import { ChangeEvent, DragEvent, useMemo, useRef, useState } from "react";
import {
  DocumentRouterAsset,
  DocumentRouterPreview,
  DocumentRouterRoute,
  previewDocumentRouter,
} from "../api";
import HuijianBrand from "./HuijianBrand";


interface Props {
  onHome: () => void;
  onWorkspace: () => void;
}

type Filter = "all" | DocumentRouterRoute;

const MAX_BYTES = 25 * 1024 * 1024;

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function routeLabel(route: DocumentRouterRoute) {
  return route === "detect" ? "建议送检" : route === "skip" ? "无需检测" : "边界项 · 默认送检";
}

function locationLabel(asset: DocumentRouterAsset) {
  if (asset.pageNumber) return `第 ${asset.pageNumber} 页 · 图片 ${asset.ordinal}`;
  if (asset.partPath?.includes("header")) return `页眉 · 图片 ${asset.ordinal}`;
  if (asset.partPath?.includes("footer")) return `页脚 · 图片 ${asset.ordinal}`;
  return `图片 ${asset.ordinal}`;
}

function featureLabel(key: string) {
  const labels: Record<string, string> = {
    entropy: "信息熵",
    luminanceStd: "明暗变化",
    edgeDensity: "边缘密度",
    colorfulness: "色彩丰富度",
    dominantColorRatio: "主色占比",
    transparentRatio: "透明像素",
    semitransparentRatio: "半透明像素",
    aspectRatio: "长宽比",
    pixelCount: "像素数量",
    pdfObjectId: "PDF 对象",
    pdfSoftMaskObjectId: "透明蒙版对象",
    pdfColorSpace: "颜色空间",
    pdfBitsPerComponent: "通道位深",
    sourceKind: "文档来源",
  };
  return labels[key] || key;
}

function featureValue(key: string, value: string | number | boolean | null) {
  if (value == null || value === "") return "-";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number" && ["dominantColorRatio", "transparentRatio", "semitransparentRatio", "edgeDensity"].includes(key)) {
    return `${(value * 100).toFixed(1)}%`;
  }
  return String(value);
}

export default function DocumentRouterLab({ onHome, onWorkspace }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<DocumentRouterPreview | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");

  const filtered = useMemo(() => {
    if (!result) return [];
    return filter === "all" ? result.assets : result.assets.filter((asset) => asset.router.route === filter);
  }, [filter, result]);

  function selectFile(next: File | null) {
    setError("");
    setResult(null);
    setFilter("all");
    if (!next) {
      setFile(null);
      return;
    }
    if (!/\.(pdf|docx)$/i.test(next.name)) {
      setFile(null);
      setError("请选择 PDF 或 DOCX 文档");
      return;
    }
    if (next.size > MAX_BYTES) {
      setFile(null);
      setError("测试文档不能超过 25 MB");
      return;
    }
    setFile(next);
  }

  function onInput(event: ChangeEvent<HTMLInputElement>) {
    selectFile(event.target.files?.[0] || null);
    event.target.value = "";
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    selectFile(event.dataTransfer.files?.[0] || null);
  }

  async function run() {
    if (!file || busy) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setError("");
    try {
      setResult(await previewDocumentRouter(file, controller.signal));
    } catch (reason) {
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError(reason instanceof Error ? reason.message : "Router 分析失败");
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      setBusy(false);
    }
  }

  return (
    <div className="router-lab-page">
      <header className="router-lab-header">
        <button type="button" className="router-lab-brand" onClick={onHome} aria-label="返回慧鉴AI首页">
          <HuijianBrand compact />
        </button>
        <div className="router-lab-header-actions">
          <button type="button" onClick={onHome}><ArrowLeft size={17} /> 官网首页</button>
          <button type="button" className="is-primary" onClick={onWorkspace}>鉴伪工作台</button>
        </div>
      </header>

      <main className="router-lab-main">
        <section className="router-lab-intro" aria-labelledby="router-lab-title">
          <div>
            <p><ShieldCheck size={16} /> DOCUMENT ROUTER / BASELINE</p>
            <h1 id="router-lab-title" tabIndex={-1}><span>哪些图片值得</span><br className="router-lab-mobile-break" /><span>送去鉴伪？</span></h1>
            <span>本页面只执行文档提图与可解释分流，不调用鉴伪模型，也不会产生真假结论。</span>
          </div>
          <aside>
            <small>当前策略</small>
            <strong>高置信度才跳过</strong>
            <span>边界图片默认送检，优先避免漏检。</span>
          </aside>
        </section>

        <section className="router-lab-upload" aria-label="上传测试文档">
          <div
            className={`router-lab-dropzone ${dragging ? "is-dragging" : ""}`}
            onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false); }}
            onDrop={onDrop}
          >
            <span className="router-lab-upload-icon"><Upload size={25} /></span>
            <div>
              <strong>{file ? file.name : "选择一份 PDF 或 DOCX"}</strong>
              <span>{file ? `${formatBytes(file.size)} · 准备执行 Router` : "支持拖放上传，文件上限 25 MB"}</span>
            </div>
            <input ref={inputRef} type="file" accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={onInput} hidden />
            {file ? (
              <button type="button" className="router-lab-clear" onClick={() => selectFile(null)} aria-label="移除已选文档"><X size={18} /></button>
            ) : (
              <button type="button" onClick={() => inputRef.current?.click()}>选择文档</button>
            )}
          </div>
          <button type="button" className="router-lab-run" disabled={!file || busy} onClick={() => void run()}>
            {busy ? <RefreshCw className="spin" size={18} /> : <Check size={18} />}
            {busy ? "正在提取并分流" : result ? "重新运行 Router" : "开始 Router 测试"}
          </button>
        </section>

        {error && <div className="router-lab-error" role="alert">{error}</div>}

        {result && (
          <section className="router-lab-results" aria-label="Router 测试结果">
            <header className="router-lab-result-heading">
              <div>
                <small>{result.routerVersion}</small>
                <h2>{result.filename}</h2>
                <p>{result.pageCount ? `${result.pageCount} 页 · ` : ""}{formatBytes(result.size)} · Router 用时 {result.elapsedMs} ms</p>
              </div>
              <strong>减少 {result.summary.modelCallsAvoided} 次模型调用</strong>
            </header>

            <div className="router-lab-metrics">
              <article><span>提取对象</span><strong>{result.summary.extracted}</strong></article>
              <article className="is-detect"><span>建议送检</span><strong>{result.summary.detect}</strong></article>
              <article className="is-uncertain"><span>边界项</span><strong>{result.summary.uncertain}</strong></article>
              <article className="is-skip"><span>无需检测</span><strong>{result.summary.skip}</strong></article>
              <article><span>实际模型调用</span><strong>{result.summary.recommendedModelCalls}</strong></article>
            </div>

            {result.warnings.length > 0 && <p className="router-lab-warning">{result.warnings.join("；")}</p>}

            <div className="router-lab-toolbar">
              <div>
                {(["all", "detect", "uncertain", "skip"] as const).map((value) => (
                  <button key={value} type="button" className={filter === value ? "is-active" : ""} onClick={() => setFilter(value)}>
                    {{ all: "全部", detect: "建议送检", uncertain: "边界项", skip: "无需检测" }[value]}
                  </button>
                ))}
              </div>
              <span>{filtered.length} 项</span>
            </div>

            {filtered.length ? (
              <div className="router-lab-grid">
                {filtered.map((asset) => (
                  <article key={`${asset.ordinal}-${asset.occurrenceIndex}`} className={`router-lab-asset is-${asset.router.route}`}>
                    <div className="router-lab-preview">
                      {asset.preview ? <img src={asset.preview} alt={`${locationLabel(asset)}缩略图`} /> : <FileImage size={30} />}
                      <span>{routeLabel(asset.router.route)}</span>
                    </div>
                    <div className="router-lab-asset-copy">
                      <small>{locationLabel(asset)}</small>
                      <h3>{asset.router.categoryLabel}</h3>
                      <p>{asset.width}×{asset.height} · 决策置信度 {Math.round(asset.router.confidence * 100)}%</p>
                      <ul>{asset.router.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                      <details>
                        <summary>查看结构与统计特征</summary>
                        <dl>
                          {Object.entries(asset.router.features).filter(([key]) => [
                            "entropy", "luminanceStd", "edgeDensity", "colorfulness", "dominantColorRatio",
                            "transparentRatio", "aspectRatio", "pixelCount", "pdfObjectId", "pdfSoftMaskObjectId",
                            "pdfColorSpace", "pdfBitsPerComponent", "sourceKind",
                          ].includes(key)).map(([key, value]) => (
                            <div key={key}><dt>{featureLabel(key)}</dt><dd>{featureValue(key, value)}</dd></div>
                          ))}
                        </dl>
                      </details>
                    </div>
                  </article>
                ))}
              </div>
            ) : <div className="router-lab-empty">当前筛选下没有图片</div>}
          </section>
        )}
      </main>
    </div>
  );
}
