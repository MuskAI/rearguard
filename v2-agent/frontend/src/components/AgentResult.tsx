import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BadgeCheck,
  Camera,
  CheckCircle2,
  CircleDashed,
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
  Microscope,
  ScanLine,
  ShieldCheck,
  ShieldOff,
  Sparkles,
  Video,
} from "lucide-react";
import type { AgentOutcome } from "../agentTypes";
import {
  createReportShareLink,
  listReportShares,
  revokeReportShare,
  type CaptureEvidence,
  type ForensicReport,
  type ProbabilityModel,
  type ProvenanceReport,
  type ReportShareItem,
  type SynthIDResult,
  type VisibleWatermarkResult,
} from "../api";
import { buildEvidenceExplanation, hasDecisiveAiWatermark } from "../evidenceExplanation";

type ResultTab = "summary" | "evidence" | "file";
type ForensicsPreviewState = "idle" | "running" | "complete" | "skipped";

interface Props {
  outcome: AgentOutcome;
  forensicsBusy: boolean;
  forensicsPreviewState: ForensicsPreviewState;
  provenanceBusy: boolean;
  downloadBusy: boolean;
  actionError?: string;
  onRetryAction?: () => void;
  onForensics: () => void;
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
    const reviewOnly = outcome.result.decisionStatus !== "verdict" || outcome.result.reviewRequired === true;
    if (reviewOnly) {
      return {
        label: "需人工复核",
        description: outcome.result.explanation || "自动分析已完成，但当前证据不足以发布真假结论。",
        risk: 0,
        riskLabel: "自动风险分数",
        tone: "warn",
        confidence: "不适用",
        reviewOnly: true,
      };
    }
    const raw = Number(outcome.result.probability || 0);
    const localizedWatermark = hasDecisiveAiWatermark(outcome.result.visibleWatermark);
    const risk = Math.max(clamp01(raw > 1 ? raw / 100 : raw), localizedWatermark ? 0.95 : 0);
    const tone = localizedWatermark ? "fake" : outcome.result.final_label.includes("真实") ? "real" : riskTone(risk);
    return {
      label: localizedWatermark ? "AI生成图像" : outcome.result.final_label || (tone === "real" ? "更倾向真实" : "存在生成风险"),
      description: tone === "real" ? "本次多源分析未发现足以支持 AI 生成的强证据。" : "检测到需要关注的生成或编辑线索，建议结合原始来源复核。",
      risk,
      riskLabel: outcome.result.swarm?.enabled ? "综合异常风险" : "AI 生成风险",
      tone,
      confidence: outcome.result.confidence || "未标注",
      reviewOnly: false,
    };
  }
  if (outcome.kind === "video") {
    if (outcome.result.decisionStatus !== "verdict" || outcome.result.reviewRequired === true) {
      return {
        label: "需人工复核",
        description: outcome.result.explanation || "视频分析已完成，但当前模型尚未获得自动判定授权。",
        risk: 0,
        riskLabel: "自动风险分数",
        tone: "warn",
        confidence: "不适用",
        reviewOnly: true,
      };
    }
    const risk = clamp01(Number(outcome.result.fake_percentage || 0) / 100);
    const tone = outcome.result.final_label.includes("真实") ? "real" : riskTone(risk);
    return {
      label: outcome.result.final_label || (tone === "real" ? "更倾向真实" : "存在合成风险"),
      description: tone === "real" ? "抽帧与时序分析未发现明确的合成证据。" : "视频中存在需要人工复核的合成线索。",
      risk,
      riskLabel: "合成风险",
      tone,
      confidence: outcome.result.confidence || "未标注",
      reviewOnly: false,
    };
  }
  if (outcome.result.decisionStatus !== "verdict" || outcome.result.reviewRequired === true) {
    return {
      label: "需人工复核",
      description: outcome.result.explanation || "自动分析已完成，但当前证据不足以发布真假结论。",
      risk: 0,
      riskLabel: "自动风险分数",
      tone: "warn",
      confidence: "不适用",
      reviewOnly: true,
    };
  }
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
  const specializedRisk = Math.max(tamperRisk, deepfakeRisk);
  const hasSpecializedRisk = specializedRisk >= Math.max(aiRisk, 0.62);
  const tone = localizedWatermark
    ? "fake"
    : hasSpecializedRisk
      ? riskTone(specializedRisk)
      : outcome.result.verdict === "real"
        ? "real"
        : outcome.result.verdict === "highly_suspected_fake"
          ? "fake"
          : "warn";
  const labels = { real: "更倾向真实", suspected_fake: "疑似 AI 生成", highly_suspected_fake: "高度疑似 AI 生成", unknown: "需要人工复核" };
  let label = labels[outcome.result.verdict];
  if (!localizedWatermark && tamperRisk >= Math.max(aiRisk, deepfakeRisk, 0.62)) label = "疑似篡改图像";
  else if (!localizedWatermark && deepfakeRisk >= Math.max(aiRisk, tamperRisk, 0.62)) label = "疑似人脸深伪";
  return {
    label: localizedWatermark ? labels.highly_suspected_fake : label,
    description: outcome.result.explanation || "请结合证据维度与原始来源进行判断。",
    risk,
    riskLabel: tamperRisk >= Math.max(aiRisk, deepfakeRisk, 0.62) || deepfakeRisk >= Math.max(aiRisk, tamperRisk, 0.62)
      ? "综合异常风险"
      : "AI 生成风险",
    tone,
    confidence: outcome.result.source === "vlm"
      ? "模型分析完成"
      : outcome.result.source === "provenance"
        ? "来源证据直接命中"
        : "证据有限",
    reviewOnly: false,
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
  const platformProviders = new Set(["gemini", "doubao", "jimeng", "jimeng_pill", "samsung"]);
  const hits = (report.hits || []).slice(0, 8);
  const platformHits = hits.filter((hit) => platformProviders.has(hit.provider));
  const genericHits = hits.filter((hit) => !platformProviders.has(hit.provider));
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
  const suppliedYolo = detector?.engines?.find((engine) => engine.id.includes("yolo"));
  const engines = [
    {
      ...(suppliedRegistry || {}),
      id: "known_ai_registry",
      label: "AI 平台标记匹配",
      available: Boolean(suppliedRegistry?.available ?? report.supported),
      detected: hasPlatformHit,
      count: platformHits.length,
      model: suppliedRegistry?.model || "wiltodelta/remove-ai-watermarks",
      version: suppliedRegistry?.version || platformHits[0]?.modelRevision,
      role: "attribution",
    },
    {
      ...(suppliedYolo || {}),
      id: "yolo_visible_watermark",
      label: "YOLO 可见水印检测",
      available: Boolean(suppliedYolo?.available),
      detected: Boolean(suppliedYolo?.detected ?? (genericHits.length > 0 || confirmedHits.length > 0)),
      count: suppliedYolo?.count ?? (genericHits.length + confirmedHits.length),
      model: suppliedYolo?.model || genericHits[0]?.model || platformHits[0]?.localizationModel || "corzent/yolo11x_watermark_detection",
      version: suppliedYolo?.version || genericHits[0]?.modelRevision || platformHits[0]?.localizationModelRevision,
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
        ? `匹配到 ${platformHits.length} 处 AI 平台标记${confirmedHits.length > 0 ? `，其中 ${confirmedHits.length} 处通过 YOLO 区域复核` : ""}${genericHits.length > 0 ? `；另有 ${genericHits.length} 处可见水印的平台归属待确认` : ""}。可见标记不单独决定真伪。`
        : detected
          ? "已定位到可见水印但尚不能确认平台归属；定位框仅作为上下文线索，不会单独改变鉴伪结论。"
          : "平台注册表与 YOLO 可见水印检测均未发现水印。";
  return (
    <section className="result-band watermark-band">
      <div className="section-title">
        <ScanLine size={18} />
        <div><h3>可见水印检测</h3><p>{statusText}</p></div>
      </div>
      <div className={`watermark-status ${hasPlatformHit ? "is-detected" : detected ? "is-possible" : report.supported ? "is-clear" : "is-unavailable"}`}>
        <span>{hasPlatformHit ? `已知平台 ${platformHits.length}` : detected ? `可见水印 ${genericHits.length}` : report.supported ? "未检出" : "暂不可用"}</span>
        <strong>{hasPlatformHit ? `${providerLabels} · 平台规则确认` : detected ? `${reusedFromSameFile ? "同一文件复核补充 · " : ""}通用水印线索，不单独判假` : "已完成平台规则与通用水印扫描"}</strong>
        {elapsed > 0 ? <time>{elapsed} ms</time> : null}
      </div>
      {report.supported && (
        <div className="watermark-layout">
          <div className="watermark-visual">
            <div className="watermark-canvas">
              <img src={preview} alt="带有可见水印定位框的原始图像" />
              {hits.map((hit, index) => {
                const x = clamp01(Number(hit.bbox?.x || 0));
                const y = clamp01(Number(hit.bbox?.y || 0));
                const width = Math.min(clamp01(Number(hit.bbox?.w || 0)), 1 - x);
                const height = Math.min(clamp01(Number(hit.bbox?.h || 0)), 1 - y);
                return (
                  <span
                    className={`watermark-box ${platformProviders.has(hit.provider) ? "is-platform" : ""}`}
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
                  <li className={platformProviders.has(hit.provider) ? "is-platform" : ""} key={`${hit.provider}-detail-${index}`}>
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <div>
                      <strong>{hit.label || (platformProviders.has(hit.provider) ? "已知 AI 平台水印" : "可见水印（平台待确认）")}</strong>
                      <small>
                        {platformProviders.has(hit.provider)
                          ? `remove-ai-watermarks 平台匹配${hit.localizationConfirmed ? " · YOLO 区域复核" : " · 视觉归属线索"}`
                          : "YOLO 可见水印定位 · 仅作上下文线索"}
                      </small>
                      <i><em style={{ width: `${clamp01(hit.confidence) * 100}%` }} /></i>
                    </div>
                    <b>{Math.round(hit.confidence * 100)}%</b>
                  </li>
                ))}
              </ol>
            ) : (
              <div className="watermark-clear-state"><CheckCircle2 size={18} /><span><strong>未发现可见水印</strong><small>已完成平台规则与 YOLO 扫描</small></span></div>
            )}
            <div className="watermark-model-meta">
              <span>检测引擎</span>
              <div className="watermark-engine-list">
                {engines.map((engine) => (
                  <div key={engine.id}>
                    <span>
                      <strong>{engine.label}</strong>
                      <small>{engine.model}{engine.version ? ` · ${engine.version}` : ""}</small>
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
    </section>
  );
}

function SynthIDSection({ report }: { report?: SynthIDResult }) {
  if (!report) return null;
  const modelResults = report.modelResults || [];
  const state = report.detected ? "detected" : report.possiblyDetected ? "possible" : report.supported ? "clear" : "unavailable";
  const stateLabel = state === "detected" ? "检出" : state === "possible" ? "疑似信号" : state === "clear" ? "未检出" : "暂不可用";
  const attributed = modelResults.find((item) => item.modelProfile === report.attributedModelProfile);
  const summary = attributed?.modelLabel
    ? `最接近 ${attributed.modelLabel} 档案`
    : report.candidateModelProfiles?.length
      ? `${report.candidateModelProfiles.length} 个模型档案存在匹配`
      : `已扫描 ${modelResults.length} 个模型档案`;
  return (
    <section className="result-band synthid-band">
      <div className="section-title"><Fingerprint size={18} /><div><h3>SynthID 多模型核验</h3><p>Google 图像模型频谱档案并行比对</p></div></div>
      <div className={`watermark-status is-${state}`}>
        <span>{stateLabel}</span>
        <strong>{summary}</strong>
        {report.elapsedMs ? <time>{report.elapsedMs} ms</time> : null}
      </div>
      {modelResults.length > 0 && (
        <div className="watermark-model-meta synthid-models">
          <span>模型档案</span>
          <div className="watermark-engine-list">
            {modelResults.map((model) => {
              const modelState = model.detected ? "检出" : model.possiblyDetected ? "疑似" : model.supported ? "未检出" : "不可用";
              const stateClass = model.detected ? "is-hit" : model.possiblyDetected ? "is-possible" : model.supported ? "is-ready" : "is-offline";
              return (
                <div key={model.modelProfile}>
                  <span>
                    <strong>{model.modelLabel || model.modelProfile}</strong>
                    <small>{model.exactResolutionMatch ? "原始分辨率档案" : "近邻分辨率档案"} · 相位 {Math.round(clamp01(model.phaseMatch) * 100)}%</small>
                  </span>
                  <b className={stateClass}>{modelState} · {Math.round(clamp01(model.confidence) * 100)}%</b>
                </div>
              );
            })}
          </div>
        </div>
      )}
      <div className="watermark-note"><Info size={15} /><p>{report.note} 实验引擎：<a href="https://github.com/aloshdenny/reverse-SynthID" target="_blank" rel="noreferrer">reverse-SynthID by Alosh Denny</a>。不等同于 Google 官方验证，也不会凭低强度信号单独定案。</p></div>
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
        <div><h3>综合风险依据</h3><p>像素模型形成风险基线，独立来源证据通过规则化证据权重更新结果。</p></div>
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
              <strong>{factor.label}</strong>
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

export default function AgentResult(props: Props) {
  const [tab, setTab] = useState<ResultTab>("summary");
  const [shareBusy, setShareBusy] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareMessage, setShareMessage] = useState("");
  const [createdShareUrl, setCreatedShareUrl] = useState("");
  const [shares, setShares] = useState<ReportShareItem[]>([]);
  useEffect(() => {
    setTab("summary");
    setShareOpen(false);
    setShareMessage("");
    setCreatedShareUrl("");
    setShares([]);
  }, [props.outcome.id]);
  const verdict = useMemo(() => verdictFor(props.outcome), [props.outcome]);
  const explanationPoints = useMemo(
    () => buildEvidenceExplanation(props.outcome, verdict.risk, verdict.label),
    [props.outcome, verdict.label, verdict.risk],
  );
  const preview = filePreview(props.outcome);
  const canDeepAnalyze = hasImageFile(props.outcome);
  const forensics = props.outcome.kind === "image" || props.outcome.kind === "evidence" ? props.outcome.forensics : undefined;
  const provenance = props.outcome.kind === "image" || props.outcome.kind === "evidence"
    ? props.outcome.provenance || (props.outcome.kind === "evidence" ? props.outcome.result.provenance || undefined : undefined)
    : undefined;
  const visibleWatermark = props.outcome.kind === "image" || props.outcome.kind === "evidence"
    ? props.outcome.result.visibleWatermark
    : undefined;
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
  const forensicsActionLabel = props.forensicsBusy
    ? props.forensicsPreviewState === "skipped" ? "服务端判读中" : forensics?.source === "browser-preview" ? "模型判读中" : forensics?.source === "vlm" ? "正在归档" : "本地图谱生成中"
    : forensics ? "重新生成取证图谱" : "生成取证图谱";

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
            <img src={preview} alt={fileName(props.outcome)} />
          ) : (
            <span>{props.outcome.kind === "video" ? <Video size={30} /> : props.outcome.kind === "image" ? <ImageIcon size={30} /> : <FileText size={30} />}</span>
          )}
        </div>
        <div className="result-verdict">
          <div className="verdict-kicker"><ShieldCheck size={16} /> 小鉴综合判断</div>
          <h2 id="detection-result-title">{verdict.label}</h2>
          <p>{verdict.description}</p>
          <div className="verdict-meta">
            {verdict.reviewOnly ? (
              <span><FileSearch size={15} /> 自动结论 <strong>未发布</strong></span>
            ) : (
              <>
                <span><Gauge size={15} /> {verdict.riskLabel} <strong>{Math.round(verdict.risk * 100)}%</strong></span>
                <span><BadgeCheck size={15} /> 置信说明 <strong>{verdict.confidence}</strong></span>
              </>
            )}
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
          <section className="result-band">
            <div className="section-title"><Sparkles size={18} /><div><h3>为什么这样判断</h3><p>已按水印、主模型、视觉复核与文件来源证据排序。</p></div></div>
            <div className="result-explanation result-rationale" role="list">
              {explanationPoints.map((point, index) => (
                <div className={point.decisive ? "is-decisive" : ""} role="listitem" key={`${point.label}-${index}`}>
                  <strong>{point.label}</strong>
                  <p>{point.text}</p>
                </div>
              ))}
            </div>
          </section>
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
          <CaptureEvidenceSection report={captureEvidence} />
          {!verdict.reviewOnly && <ProbabilitySection model={probabilityModel} />}
          <SynthIDSection report={synthid} />
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
          <ForensicsSection report={forensics} busy={props.forensicsBusy} previewState={props.forensicsPreviewState} />
          <ProvenanceSection report={provenance} />
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
          <div className="result-disclaimer"><Info size={16} /><p>鉴伪结果是辅助判断，不等同于司法鉴定结论。高风险场景请结合原始文件、来源链路和人工复核。</p></div>
        </div>
      )}
    </article>
  );
}
