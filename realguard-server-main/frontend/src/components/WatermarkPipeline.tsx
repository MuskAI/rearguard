import { useEffect, useMemo, useState } from "react";
import type { WatermarkPipelineStage, WatermarkPipelineTrace } from "../api";
import IconfontIcon from "./IconfontIcon";

type Lang = "zh" | "en";

type Props = {
  trace?: WatermarkPipelineTrace | null;
  lang: Lang;
};

const STATUS_LABELS: Record<string, [string, string]> = {
  success: ["完成", "Complete"],
  hit: ["命中", "Hit"],
  clean: ["未命中", "Clear"],
  warning: ["需复核", "Review"],
  error: ["失败", "Failed"],
  skipped: ["已跳过", "Skipped"],
};

function duration(value: unknown, lang: Lang) {
  const milliseconds = Math.max(0, Number(value) || 0);
  if (milliseconds >= 1000) return `${(milliseconds / 1000).toFixed(2)} s`;
  return `${Math.round(milliseconds)} ms`;
}

function score(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "-";
}

function text(value: unknown, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function Fact({ label, value, tone = "" }: { label: string; value: unknown; tone?: string }) {
  return <div className={`pipeline-fact ${tone}`}><span>{label}</span><strong>{text(value)}</strong></div>;
}

function Empty({ children }: { children: string }) {
  return <p className="pipeline-empty">{children}</p>;
}

function CandidateList({ values, lang }: { values: any[]; lang: Lang }) {
  if (!values.length) return <Empty>{lang === "zh" ? "本阶段没有候选区域。" : "No candidate region in this stage."}</Empty>;
  return <div className="pipeline-candidate-list">{values.map((item, index) => (
    <div className="pipeline-candidate" key={`${index}-${item.label || item.provider || "candidate"}`}>
      <b>{String(index + 1).padStart(2, "0")}</b>
      <div><strong>{text(item.label || item.provider, lang === "zh" ? "未知候选" : "Unknown candidate")}</strong><small>{text(item.location || item.position || "localized")}</small></div>
      <em>{score(item.confidence)}</em>
    </div>
  ))}</div>;
}

function RetrievalDetails({ details, lang }: { details: any; lang: Lang }) {
  const results = Array.isArray(details.results) ? details.results : [];
  if (!results.length) return <Empty>{lang === "zh" ? "没有候选区域，向量检索未运行。" : "No candidates; vector retrieval did not run."}</Empty>;
  return <div className="pipeline-retrieval-list">{results.map((item: any, index: number) => {
    const similarity = Math.max(0, Math.min(1, Number(item.similarity) || 0));
    const threshold = Math.max(0, Math.min(1, Number(item.threshold) || 0));
    const matches = Array.isArray(item.topMatches) ? item.topMatches.slice(0, 5) : [];
    return <article className={item.accepted ? "accepted" : "rejected"} key={`${index}-${item.referenceId || "retrieval"}`}>
      <header><strong>{lang === "zh" ? `候选 ${item.candidate || index + 1}` : `Candidate ${item.candidate || index + 1}`} · {text(item.sourcePlatform || item.candidatePlatform, lang === "zh" ? "平台未确认" : "Unattributed")}</strong><span>{item.accepted ? (lang === "zh" ? "通过" : "Accepted") : (lang === "zh" ? "拒绝" : "Rejected")}</span></header>
      <div className="pipeline-threshold" aria-label={lang === "zh" ? "相似度与阈值" : "Similarity and threshold"}>
        <i style={{ width: `${similarity * 100}%` }} /><b style={{ left: `${threshold * 100}%` }} />
      </div>
      <div className="pipeline-threshold-labels"><span>{lang === "zh" ? "相似度" : "Similarity"} {similarity.toFixed(4)}</span><span>{lang === "zh" ? "阈值" : "Threshold"} {threshold.toFixed(4)}</span></div>
      <div className="pipeline-facts compact">
        <Fact label={lang === "zh" ? "平台间距" : "Platform margin"} value={Number(item.margin || 0).toFixed(4)} />
        <Fact label={lang === "zh" ? "最小间距" : "Required margin"} value={Number(item.minimumMargin || 0).toFixed(4)} />
        <Fact label={lang === "zh" ? "决策原因" : "Decision reason"} value={item.reason} />
        <Fact label={lang === "zh" ? "参考样本" : "Reference"} value={item.referenceId} />
      </div>
      {matches.length > 0 && <div className="pipeline-ranking">{matches.map((match: any, matchIndex: number) => (
        <div key={`${matchIndex}-${match.referenceId || match.platform}`}><span>{matchIndex + 1}</span><strong>{text(match.platform)}</strong><i><b style={{ width: `${Math.max(0, Math.min(1, Number(match.similarity) || 0)) * 100}%` }} /></i><em>{Number(match.similarity || 0).toFixed(4)}</em></div>
      ))}</div>}
    </article>;
  })}</div>;
}

function StageBody({ stage, lang }: { stage: WatermarkPipelineStage; lang: Lang }) {
  const details: any = stage.details || {};
  if (stage.id === "decode") {
    const input = details.input || {};
    const encoded = details.encodedSize || {};
    const display = details.displaySize || {};
    return <div className="pipeline-facts"><Fact label={lang === "zh" ? "输入文件" : "Input file"} value={input.filename} /><Fact label={lang === "zh" ? "文件大小" : "File size"} value={input.bytes ? `${(Number(input.bytes) / 1024 / 1024).toFixed(2)} MB` : "-"} /><Fact label={lang === "zh" ? "编码尺寸" : "Encoded size"} value={`${encoded.width || 0} x ${encoded.height || 0}`} /><Fact label={lang === "zh" ? "分析尺寸" : "Analysis size"} value={`${display.width || 0} x ${display.height || 0}`} /></div>;
  }
  if (stage.id === "metadata") {
    const report = details.report || {};
    const signals = Array.isArray(report.signals) ? report.signals : [];
    return <><div className="pipeline-facts"><Fact label={lang === "zh" ? "AI 元数据信号" : "AI metadata"} value={report.isAiGenerated ? (lang === "zh" ? "发现" : "Found") : (lang === "zh" ? "未发现" : "Not found")} tone={report.isAiGenerated ? "hit" : ""} /><Fact label={lang === "zh" ? "可能来源" : "Possible source"} value={report.platform} /></div>{signals.length ? <div className="pipeline-signal-list">{signals.map((item: any, index: number) => <div key={`${index}-${item.name}`}><strong>{text(item.name)}</strong><span>{text(item.detail)}</span><b>{text(item.confidence)}</b></div>)}</div> : <Empty>{lang === "zh" ? "未读取到可用 AI 来源元数据。" : "No usable AI provenance metadata was found."}</Empty>}</>;
  }
  if (stage.id === "registry") return <CandidateList values={Array.isArray(details.hits) ? details.hits : []} lang={lang} />;
  if (stage.id === "yolo") {
    const runtime = details.runtime || {};
    return <><div className="pipeline-facts"><Fact label={lang === "zh" ? "定位模型" : "Localization model"} value={runtime.model} /><Fact label={lang === "zh" ? "运行设备" : "Runtime device"} value={runtime.gpu || runtime.device} /><Fact label={lang === "zh" ? "模型耗时" : "Model latency"} value={duration(runtime.elapsedMs, lang)} /><Fact label={lang === "zh" ? "往返耗时" : "Round trip"} value={duration(runtime.roundTripMs, lang)} /></div><CandidateList values={Array.isArray(details.candidates) ? details.candidates : []} lang={lang} /></>;
  }
  if (stage.id === "ocr") {
    const results = Array.isArray(details.results) ? details.results : [];
    return results.length ? <div className="pipeline-ocr-list">{results.map((item: any, index: number) => <div key={`${index}-${item.candidate}`}><header><strong>{lang === "zh" ? `候选 ${item.candidate || index + 1}` : `Candidate ${item.candidate || index + 1}`} · {text(item.text, lang === "zh" ? "未识别文字" : "No text")}</strong><span>{duration(item.elapsedMs, lang)}</span></header><p>OCR {score(item.confidence)} · {text(item.analysis?.interpretation || item.analysis?.verdict)}</p></div>)}</div> : <Empty>{lang === "zh" ? "没有候选区域，OCR 未运行。" : "No candidates; OCR did not run."}</Empty>;
  }
  if (stage.id === "retrieval") return <RetrievalDetails details={details} lang={lang} />;
  if (stage.id === "fusion") {
    const timings = details.timings || {};
    return <><p className="pipeline-rule">{text(details.rule, lang === "zh" ? "未提供融合规则。" : "No fusion rule supplied.")}</p><div className="pipeline-facts"><Fact label={lang === "zh" ? "候选数量" : "Candidates"} value={details.candidateCount ?? 0} /><Fact label={lang === "zh" ? "注册表命中" : "Registry hits"} value={details.registryCount ?? 0} /><Fact label={lang === "zh" ? "OCR 最长耗时" : "Max OCR latency"} value={duration(timings.ocrMaxMs, lang)} /><Fact label={lang === "zh" ? "检索最长耗时" : "Max retrieval latency"} value={duration(timings.retrievalMaxMs, lang)} /></div></>;
  }
  if (stage.id === "verdict") {
    const verdict = details.verdict || {};
    const verdictText = verdict.verdict === "yes" ? (lang === "zh" ? "存在 AI 水印" : "AI watermark found") : verdict.verdict === "no" ? (lang === "zh" ? "未发现 AI 水印" : "No AI watermark") : (lang === "zh" ? "需要复核" : "Review required");
    return <><div className="pipeline-facts"><Fact label={lang === "zh" ? "判断" : "Verdict"} value={verdictText} tone={verdict.verdict === "yes" ? "hit" : ""} /><Fact label={lang === "zh" ? "置信度" : "Confidence"} value={score(verdict.confidence)} /><Fact label={lang === "zh" ? "来源平台" : "Source platform"} value={details.sourcePlatform} /><Fact label={lang === "zh" ? "相关证据" : "Relevant evidence"} value={verdict.relevantHitCount ?? 0} /></div><p className="pipeline-rationale">{text(verdict.reason, stage.summary)}</p></>;
  }
  return <Empty>{stage.summary || (lang === "zh" ? "本阶段没有可展示的数据。" : "No displayable data for this stage.")}</Empty>;
}

export default function WatermarkPipeline({ trace, lang }: Props) {
  const stages = useMemo(() => trace?.stages?.filter((stage) => stage && stage.id) || [], [trace]);
  const preferred = stages.find((stage) => stage.status === "error" || stage.status === "warning") || stages.find((stage) => stage.id === "verdict") || stages[0];
  const [selectedId, setSelectedId] = useState(preferred?.id || "");

  useEffect(() => setSelectedId(preferred?.id || ""), [trace?.schemaVersion, trace?.totalElapsedMs, preferred?.id]);

  if (!trace || trace.schemaVersion !== "watermark_pipeline_trace_v1" || !stages.length) return null;
  const selected = stages.find((stage) => stage.id === selectedId) || preferred || stages[0];
  const maxElapsed = Math.max(1, ...stages.map((stage) => Number(stage.elapsedMs) || 0));
  const tr = (zh: string, en: string) => lang === "zh" ? zh : en;

  return <section className="watermark-pipeline" aria-label={tr("水印检测流水线", "Watermark detection pipeline")}>
    <header className="pipeline-headline">
      <div><span><IconfontIcon name="activity" size={16} /> {tr("取证过程", "Forensic process")}</span><h4>{tr("检测流水线", "Detection pipeline")}</h4></div>
      <p><IconfontIcon name="clock" size={14} /> {tr("总耗时", "Total")} <strong>{duration(trace.totalElapsedMs, lang)}</strong></p>
    </header>
    <div className="pipeline-stage-tabs" role="tablist" aria-label={tr("检测阶段", "Detection stages")}>
      {stages.map((stage, index) => <button key={stage.id} type="button" role="tab" aria-selected={stage.id === selected.id} className={`${stage.status} ${stage.id === selected.id ? "active" : ""}`} onClick={() => setSelectedId(stage.id)}><i>{String(index + 1).padStart(2, "0")}</i><strong>{stage.label}</strong><small>{duration(stage.elapsedMs, lang)}</small><b aria-hidden="true" /></button>)}
    </div>
    <div className="pipeline-workspace">
      <aside className="pipeline-waterfall" aria-label={tr("阶段耗时", "Stage latency")}>
        <div className="pipeline-side-title"><strong>{tr("阶段耗时", "Stage latency")}</strong><span>{tr("并行分支不累加", "Parallel branches overlap")}</span></div>
        {stages.map((stage) => <button key={stage.id} type="button" className={stage.id === selected.id ? "active" : ""} onClick={() => setSelectedId(stage.id)}><span>{stage.label}</span><i><b className={stage.status} style={{ width: `${Math.max(2, (Number(stage.elapsedMs) || 0) / maxElapsed * 100)}%` }} /></i><em>{duration(stage.elapsedMs, lang)}</em></button>)}
      </aside>
      <article className="pipeline-stage-detail" role="tabpanel">
        <header><div><span>STAGE {String(stages.indexOf(selected) + 1).padStart(2, "0")}</span><h5>{selected.label}</h5></div><b className={selected.status}>{STATUS_LABELS[selected.status]?.[lang === "zh" ? 0 : 1] || selected.status}</b></header>
        <p className="pipeline-summary">{selected.summary}</p>
        <StageBody stage={selected} lang={lang} />
      </article>
    </div>
  </section>;
}
