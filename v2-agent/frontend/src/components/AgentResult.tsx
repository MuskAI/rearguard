import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BadgeCheck,
  CheckCircle2,
  CircleDashed,
  Download,
  FileSearch,
  FileText,
  Fingerprint,
  Gauge,
  Image as ImageIcon,
  Info,
  Layers3,
  LoaderCircle,
  Microscope,
  ScanLine,
  ShieldCheck,
  Sparkles,
  Video,
} from "lucide-react";
import type { AgentOutcome } from "../agentTypes";
import type { ForensicReport, ProvenanceReport, VisibleWatermarkResult } from "../api";

type ResultTab = "summary" | "evidence" | "file";
type ForensicsPreviewState = "idle" | "running" | "complete" | "skipped";

interface Props {
  outcome: AgentOutcome;
  forensicsBusy: boolean;
  forensicsPreviewState: ForensicsPreviewState;
  provenanceBusy: boolean;
  downloadBusy: boolean;
  onForensics: () => void;
  onProvenance: () => void;
  onDownload: () => void;
}

interface VerdictView {
  label: string;
  description: string;
  risk: number;
  tone: "real" | "warn" | "fake";
  confidence: string;
}

function clamp01(value: number) {
  return Math.max(0, Math.min(value, 1));
}

function riskTone(risk: number): VerdictView["tone"] {
  if (risk < 0.4) return "real";
  if (risk < 0.72) return "warn";
  return "fake";
}

function verdictFor(outcome: AgentOutcome): VerdictView {
  if (outcome.kind === "image") {
    const raw = Number(outcome.result.probability || 0);
    const risk = clamp01(raw > 1 ? raw / 100 : raw);
    const tone = outcome.result.final_label.includes("真实") ? "real" : riskTone(risk);
    return {
      label: outcome.result.final_label || (tone === "real" ? "更倾向真实" : "存在生成风险"),
      description: tone === "real" ? "本次多源分析未发现足以支持 AI 生成的强证据。" : "检测到需要关注的生成或编辑线索，建议结合原始来源复核。",
      risk,
      tone,
      confidence: outcome.result.confidence || "未标注",
    };
  }
  if (outcome.kind === "video") {
    const risk = clamp01(Number(outcome.result.fake_percentage || 0) / 100);
    const tone = outcome.result.final_label.includes("真实") ? "real" : riskTone(risk);
    return {
      label: outcome.result.final_label || (tone === "real" ? "更倾向真实" : "存在合成风险"),
      description: tone === "real" ? "抽帧与时序分析未发现明确的合成证据。" : "视频中存在需要人工复核的合成线索。",
      risk,
      tone,
      confidence: outcome.result.confidence || "未标注",
    };
  }
  const risk = clamp01(Number(outcome.result.confidence || 0));
  const tone = outcome.result.verdict === "real" ? "real" : outcome.result.verdict === "highly_suspected_fake" ? "fake" : "warn";
  const labels = { real: "更倾向真实", suspected_fake: "疑似 AI 生成", highly_suspected_fake: "高度疑似 AI 生成", unknown: "需要人工复核" };
  return {
    label: labels[outcome.result.verdict],
    description: outcome.result.explanation || "请结合证据维度与原始来源进行判断。",
    risk,
    tone,
    confidence: outcome.result.source === "vlm" ? "模型分析完成" : "证据有限",
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
  if (status === "success") return <CheckCircle2 size={15} className="status-success" />;
  if (status === "failed") return <AlertTriangle size={15} className="status-danger" />;
  if (status === "running") return <LoaderCircle size={15} className="spin status-running" />;
  return <CircleDashed size={15} className="status-muted" />;
}

function EvidenceList({ items }: { items: string[] }) {
  if (items.length === 0) {
    return <div className="evidence-empty"><Info size={17} /> 暂无更多可展示的证据条目。</div>;
  }
  return (
    <ul className="evidence-list">
      {items.map((item, index) => (
        <li key={`${index}-${item}`}><span>{index + 1}</span><p>{item}</p></li>
      ))}
    </ul>
  );
}

function ForensicsSection({ report, busy, previewState }: { report?: ForensicReport; busy: boolean; previewState: ForensicsPreviewState }) {
  if (!report && !busy) return null;
  const isBrowserPreview = report?.source === "browser-preview";
  const completed = report?.items.length || 0;
  const pending = busy && previewState === "running" && (!report || isBrowserPreview) ? Math.max(0, 7 - completed) : 0;
  const status = !report
    ? previewState === "skipped" ? "本地预览已跳过，服务端无损图谱判读中" : "浏览器正在生成第 1 组本地预览"
    : isBrowserPreview && busy
      ? previewState === "skipped"
        ? `本地预览停在 ${completed}/7，服务端无损图谱判读中`
        : `本地预览 ${completed}/7，服务端无损图谱判读中`
      : isBrowserPreview
        ? "本地预览已完成，服务端判读暂时不可用"
        : report.source === "vlm"
          ? "服务端模型判读完成"
          : "服务端取证分析完成";
  return (
    <section className="result-band forensic-band">
      <div className="section-title"><Microscope size={18} /><div><h3>{isBrowserPreview || !report ? "取证图谱预览" : "取证图谱"}</h3><p>{report?.summary || "低分辨率预览在本机逐项生成，服务端同时判读无损图谱。"}</p></div></div>
      <div className={`forensic-progress ${busy ? "is-running" : "is-complete"}`} role="status" aria-live="polite" aria-atomic="true">
        {busy ? <LoaderCircle size={14} className="spin" /> : <CheckCircle2 size={14} />}
        <span>{status}</span>
        {report?.elapsedMs ? <time>{(report.elapsedMs / 1000).toFixed(1)}s</time> : null}
      </div>
      <div className="forensic-grid">
        {report?.items.map((item) => (
          <figure key={item.key}>
            <img src={item.image} alt={item.title} />
            <figcaption><strong>{item.title}</strong><span>{item.finding}</span></figcaption>
          </figure>
        ))}
        {Array.from({ length: pending }, (_, index) => (
          <figure className="forensic-pending" key={`forensic-pending-${completed + index}`} aria-hidden="true">
            <div className="forensic-placeholder" />
            <figcaption><strong>正在生成图谱</strong><span>本地信号计算进行中</span></figcaption>
          </figure>
        ))}
      </div>
    </section>
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

const WATERMARK_PROVIDER_LABELS: Record<string, string> = {
  gemini: "Google Gemini",
  doubao: "豆包",
  jimeng: "即梦",
  jimeng_pill: "即梦",
  samsung: "Samsung",
  yolo11x_watermark: "通用可见水印",
};

function watermarkBox(hit: VisibleWatermarkResult["hits"][number]) {
  const x = clamp01(Number(hit.bbox?.x || 0));
  const y = clamp01(Number(hit.bbox?.y || 0));
  const w = Math.min(1 - x, clamp01(Number(hit.bbox?.w || 0)));
  const h = Math.min(1 - y, clamp01(Number(hit.bbox?.h || 0)));
  return { x, y, w, h };
}

function WatermarkSection({ report, preview }: { report?: VisibleWatermarkResult; preview?: string }) {
  const [selectedHit, setSelectedHit] = useState(0);
  if (!report) return null;
  const hits = report.hits || [];
  const activeHit = Math.min(selectedHit, Math.max(0, hits.length - 1));
  const status = !report.supported
    ? "暂不可用"
    : report.detected
      ? `检出 ${Math.max(hits.length, 1)} 处`
      : "未检出";
  const source = report.provider
    ? WATERMARK_PROVIDER_LABELS[report.provider] || "可见水印"
    : report.supported
      ? "平台规则与通用定位"
      : "无可用引擎";

  return (
    <section className="result-band watermark-section">
      <div className="section-title">
        <ScanLine size={18} />
        <div><h3>可见水印定位</h3><p>检测框按原始图像坐标绘制，可逐项查看定位证据。</p></div>
      </div>
      <div className={`watermark-status ${report.detected ? "is-detected" : ""}`} role="status">
        <strong>{status}</strong>
        <span>{source}</span>
        <span>{report.elapsedMs ? `${Math.round(report.elapsedMs)} ms` : "扫描完成"}</span>
      </div>
      {preview && hits.length > 0 && (
        <div className="watermark-layout">
          <figure className="watermark-visual">
            <div className="watermark-canvas">
              <img src={preview} alt="带有可见水印定位框的原图" />
              {hits.map((hit, index) => {
                const box = watermarkBox(hit);
                const provider = WATERMARK_PROVIDER_LABELS[hit.provider] || hit.label || "可见水印";
                const generic = hit.provider === "yolo11x_watermark";
                return (
                  <button
                    type="button"
                    key={`${hit.provider}-${index}`}
                    className={`watermark-box ${generic ? "is-generic" : "is-platform"} ${box.y < 0.08 ? "is-label-inside" : ""} ${index === activeHit ? "is-active" : ""}`}
                    style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }}
                    aria-label={`定位 ${index + 1}：${provider}`}
                    onMouseEnter={() => setSelectedHit(index)}
                    onFocus={() => setSelectedHit(index)}
                    onClick={() => setSelectedHit(index)}
                  >
                    <span>{index + 1}</span>
                  </button>
                );
              })}
            </div>
            <figcaption>原图坐标映射，点击框或右侧条目可交叉定位</figcaption>
          </figure>
          <ol className="watermark-hit-list" aria-label="水印定位结果">
            {hits.map((hit, index) => {
              const box = watermarkBox(hit);
              const provider = WATERMARK_PROVIDER_LABELS[hit.provider] || hit.label || "可见水印";
              const confidence = Math.round(clamp01(Number(hit.confidence || 0)) * 100);
              return (
                <li key={`${hit.provider}-detail-${index}`}>
                  <button
                    type="button"
                    className={index === activeHit ? "is-active" : ""}
                    onMouseEnter={() => setSelectedHit(index)}
                    onFocus={() => setSelectedHit(index)}
                    onClick={() => setSelectedHit(index)}
                  >
                    <span className="watermark-hit-index">{index + 1}</span>
                    <span className="watermark-hit-copy">
                      <strong>{provider}</strong>
                      <small>置信度 {confidence}% · x {Math.round(box.x * 100)}% · y {Math.round(box.y * 100)}%</small>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </div>
      )}
      <p className="result-explanation">{report.note || "水印结果仅用于辅助复核，不会单独改写主鉴伪结论。"}</p>
    </section>
  );
}

export default function AgentResult(props: Props) {
  const [tab, setTab] = useState<ResultTab>("summary");
  useEffect(() => setTab("summary"), [props.outcome.id]);
  const verdict = useMemo(() => verdictFor(props.outcome), [props.outcome]);
  const preview = filePreview(props.outcome);
  const canDeepAnalyze = hasImageFile(props.outcome);
  const forensics = props.outcome.kind === "image" || props.outcome.kind === "evidence" ? props.outcome.forensics : undefined;
  const provenance = props.outcome.kind === "image" || props.outcome.kind === "evidence"
    ? props.outcome.provenance || (props.outcome.kind === "evidence" ? props.outcome.result.provenance || undefined : undefined)
    : undefined;
  const visibleWatermark = props.outcome.kind === "image" || props.outcome.kind === "evidence"
    ? props.outcome.result.visibleWatermark
    : undefined;
  const forensicsActionLabel = props.forensicsBusy
    ? props.forensicsPreviewState === "skipped" ? "服务端判读中" : forensics?.source === "browser-preview" ? "模型判读中" : forensics?.source === "vlm" ? "正在归档" : "本地图谱生成中"
    : forensics ? "重新生成取证图谱" : "生成取证图谱";

  const evidenceItems = props.outcome.kind === "image"
    ? [...(props.outcome.result.swarm?.evidence || []), ...(props.outcome.result.visual_issues || [])]
    : props.outcome.kind === "video"
      ? [props.outcome.result.explanation].filter(Boolean)
      : props.outcome.result.dimensions.map((item) => `${item.label}：${item.result}`);

  return (
    <article className={`agent-result tone-${verdict.tone}`}>
      <header className="result-hero">
        <div className="result-preview">
          {props.outcome.kind === "video" && preview ? (
            <video src={preview} controls preload="metadata" />
          ) : preview ? (
            <img src={preview} alt={fileName(props.outcome)} />
          ) : (
            <span>{props.outcome.kind === "video" ? <Video size={30} /> : props.outcome.kind === "image" ? <ImageIcon size={30} /> : <FileText size={30} />}</span>
          )}
        </div>
        <div className="result-verdict">
          <div className="verdict-kicker"><ShieldCheck size={16} /> 小鉴综合判断</div>
          <h2>{verdict.label}</h2>
          <p>{verdict.description}</p>
          <div className="verdict-meta">
            <span><Gauge size={15} /> AI 风险评分 <strong>{Math.round(verdict.risk * 100)}%</strong></span>
            <span><BadgeCheck size={15} /> 置信说明 <strong>{verdict.confidence}</strong></span>
          </div>
        </div>
        <div className="risk-meter" aria-label={`AI 风险评分 ${Math.round(verdict.risk * 100)}%`}>
          <div className="risk-meter-value">{Math.round(verdict.risk * 100)}<small>%</small></div>
          <span>AI 风险</span>
          <div className="risk-meter-track"><i style={{ width: `${Math.round(verdict.risk * 100)}%` }} /></div>
        </div>
      </header>

      <nav className="result-tabs" aria-label="检测结果视图">
        <button type="button" className={tab === "summary" ? "active" : ""} onClick={() => setTab("summary")}><ShieldCheck size={16} /> 结论</button>
        <button type="button" className={tab === "evidence" ? "active" : ""} onClick={() => setTab("evidence")}><Layers3 size={16} /> 证据</button>
        <button type="button" className={tab === "file" ? "active" : ""} onClick={() => setTab("file")}><FileSearch size={16} /> 文件信息</button>
      </nav>

      {tab === "summary" && (
        <div className="result-tab-panel">
          <section className="result-band">
            <div className="section-title"><Sparkles size={18} /><div><h3>为什么这样判断</h3><p>结论来自已完成的模型分析与可用证据，不使用随机结果。</p></div></div>
            <p className="result-explanation">{props.outcome.kind === "evidence" ? props.outcome.result.explanation : props.outcome.result.explanation || verdict.description}</p>
          </section>
          {props.outcome.kind === "image" && props.outcome.result.swarm?.enabled && (
            <section className="result-band consensus-band">
              <div className="section-title"><ScanLine size={18} /><div><h3>多源复核共识</h3><p>{props.outcome.result.swarm.disagreement ? "不同证据源存在分歧，建议人工复核原始文件。" : "有效证据源的判断方向较一致。"}</p></div></div>
              <div className="consensus-line">
                <span>有效复核 {props.outcome.result.swarm.effectiveExperts || 0}/{props.outcome.result.swarm.totalExperts || props.outcome.result.swarm.experts?.length || 0}</span>
                <strong>{Math.round(Number(props.outcome.result.swarm.consensusScore || 0) * 100)}% 共识</strong>
              </div>
              <div className="consensus-track"><i style={{ width: `${Math.round(Number(props.outcome.result.swarm.consensusScore || 0) * 100)}%` }} /></div>
            </section>
          )}
          <WatermarkSection report={visibleWatermark} preview={preview} />
          <div className="result-actions">
            <button type="button" className="primary-button" onClick={props.onDownload} disabled={props.downloadBusy}>
              {props.downloadBusy ? <LoaderCircle size={17} className="spin" /> : <Download size={17} />}
              {props.downloadBusy ? "正在整理报告" : "下载鉴伪报告"}
            </button>
            <button type="button" className="secondary-button" onClick={props.onForensics} disabled={!canDeepAnalyze || props.forensicsBusy} title={canDeepAnalyze ? "生成像素级取证图谱" : "历史任务需重新上传原文件后生成取证图谱"}>
              {props.forensicsBusy ? <LoaderCircle size={17} className="spin" /> : <Microscope size={17} />}
              {forensicsActionLabel}
            </button>
            <button type="button" className="secondary-button" onClick={props.onProvenance} disabled={!canDeepAnalyze || props.provenanceBusy} title={canDeepAnalyze ? "验证 C2PA 与文件元数据" : "历史任务需重新上传原文件后验证内容凭证"}>
              {props.provenanceBusy ? <LoaderCircle size={17} className="spin" /> : <Fingerprint size={17} />}
              {provenance ? "重新验证内容凭证" : "验证内容凭证"}
            </button>
          </div>
          <ForensicsSection report={forensics} busy={props.forensicsBusy} previewState={props.forensicsPreviewState} />
          <ProvenanceSection report={provenance} />
        </div>
      )}

      {tab === "evidence" && (
        <div className="result-tab-panel">
          <section className="result-band">
            <div className="section-title"><Layers3 size={18} /><div><h3>证据摘要</h3><p>证据条目用于解释模型判断，不应脱离原始文件单独使用。</p></div></div>
            <EvidenceList items={evidenceItems} />
          </section>
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
                    <span><strong>{dimension.label}</strong><small>{dimension.result}</small></span>
                    <b>{Math.round(clamp01(Number(dimension.score || 0)) * 100)}%</b>
                    <i><em style={{ width: `${Math.round(clamp01(Number(dimension.score || 0)) * 100)}%` }} /></i>
                  </div>
                ))}
              </div>
            </section>
          )}
          <ForensicsSection report={forensics} busy={props.forensicsBusy} previewState={props.forensicsPreviewState} />
          <ProvenanceSection report={provenance} />
        </div>
      )}

      {tab === "file" && (
        <div className="result-tab-panel">
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
          <div className="result-disclaimer"><Info size={16} /><p>鉴伪结果是辅助判断，不等同于司法鉴定结论。高风险场景请结合原始文件、来源链路和人工复核。</p></div>
        </div>
      )}
    </article>
  );
}
