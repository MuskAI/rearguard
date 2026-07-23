import { Activity, ChevronDown, Clock3 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { WatermarkPipelineStage, WatermarkPipelineTrace } from "../api";

interface Props {
  trace?: WatermarkPipelineTrace | null;
}

type Data = Record<string, unknown>;

const STATUS_LABEL: Record<string, string> = {
  success: "完成",
  hit: "命中",
  clean: "未命中",
  warning: "需复核",
  error: "失败",
  skipped: "已跳过",
};

function data(value: unknown): Data {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Data : {};
}

function list(value: unknown): Data[] {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Data[] : [];
}

function value(value: unknown, fallback = "-") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function duration(value: unknown) {
  const ms = Math.max(0, Number(value) || 0);
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`;
}

function score(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "-";
}

function Fact({ label, children, tone = "" }: { label: string; children: unknown; tone?: string }) {
  return <div className={`pipeline-fact ${tone}`}><dt>{label}</dt><dd>{value(children)}</dd></div>;
}

function Empty({ children }: { children: string }) {
  return <p className="pipeline-empty">{children}</p>;
}

function CandidateList({ items }: { items: Data[] }) {
  if (!items.length) return <Empty>本阶段没有候选区域。</Empty>;
  return <div className="pipeline-candidates">{items.map((item, index) => (
    <div key={`${index}-${item.label || item.provider || "candidate"}`}>
      <b>{String(index + 1).padStart(2, "0")}</b>
      <span><strong>{value(item.label || item.provider, "未知候选")}</strong><small>{value(item.location || item.position, "localized")}</small></span>
      <em>{score(item.confidence)}</em>
    </div>
  ))}</div>;
}

function RetrievalStage({ details }: { details: Data }) {
  const results = list(details.results);
  if (!results.length) return <Empty>没有候选区域，向量检索未运行。</Empty>;
  return <div className="pipeline-retrievals">{results.map((item, index) => {
    const similarity = Math.max(0, Math.min(1, Number(item.similarity) || 0));
    const threshold = Math.max(0, Math.min(1, Number(item.threshold) || 0));
    const matches = list(item.topMatches).slice(0, 5);
    return <article className={item.accepted ? "accepted" : "rejected"} key={`${index}-${item.referenceId || "retrieval"}`}>
      <header><strong>候选 {value(item.candidate, String(index + 1))} · {value(item.sourcePlatform || item.candidatePlatform, "平台未确认")}</strong><span>{item.accepted ? "通过" : "拒绝"}</span></header>
      <div className="pipeline-threshold" aria-label={`相似度 ${similarity.toFixed(4)}，阈值 ${threshold.toFixed(4)}`}><i style={{ width: `${similarity * 100}%` }} /><b style={{ left: `${threshold * 100}%` }} /></div>
      <div className="pipeline-threshold-label"><span>相似度 {similarity.toFixed(4)}</span><span>阈值 {threshold.toFixed(4)}</span></div>
      <dl className="pipeline-facts compact">
        <Fact label="平台间距">{Number(item.margin || 0).toFixed(4)}</Fact>
        <Fact label="最小间距">{Number(item.minimumMargin || 0).toFixed(4)}</Fact>
        <Fact label="决策原因">{item.reason}</Fact>
        <Fact label="参考样本">{item.referenceId}</Fact>
      </dl>
      {matches.length > 0 && <div className="pipeline-ranking">{matches.map((match, matchIndex) => (
        <div key={`${matchIndex}-${match.referenceId || match.platform}`}><span>{matchIndex + 1}</span><strong>{value(match.platform)}</strong><i><b style={{ width: `${Math.max(0, Math.min(1, Number(match.similarity) || 0)) * 100}%` }} /></i><em>{Number(match.similarity || 0).toFixed(4)}</em></div>
      ))}</div>}
    </article>;
  })}</div>;
}

function StageDetails({ stage }: { stage: WatermarkPipelineStage }) {
  const details = data(stage.details);
  if (stage.id === "decode") {
    const input = data(details.input);
    const encoded = data(details.encodedSize);
    const display = data(details.displaySize);
    return <dl className="pipeline-facts"><Fact label="输入文件">{input.filename}</Fact><Fact label="文件大小">{input.bytes ? `${(Number(input.bytes) / 1024 / 1024).toFixed(2)} MB` : "-"}</Fact><Fact label="编码尺寸">{`${encoded.width || 0} x ${encoded.height || 0}`}</Fact><Fact label="分析尺寸">{`${display.width || 0} x ${display.height || 0}`}</Fact></dl>;
  }
  if (stage.id === "metadata") {
    const report = data(details.report);
    const signals = list(report.signals);
    return <><dl className="pipeline-facts"><Fact label="AI 元数据信号" tone={report.isAiGenerated ? "hit" : ""}>{report.isAiGenerated ? "发现" : "未发现"}</Fact><Fact label="可能来源">{report.platform}</Fact></dl>{signals.length ? <div className="pipeline-signals">{signals.map((item, index) => <div key={`${index}-${item.name}`}><strong>{value(item.name)}</strong><span>{value(item.detail)}</span><b>{value(item.confidence)}</b></div>)}</div> : <Empty>未读取到可用 AI 来源元数据。</Empty>}</>;
  }
  if (stage.id === "registry") return <CandidateList items={list(details.hits)} />;
  if (stage.id === "yolo") {
    const runtime = data(details.runtime);
    return <><dl className="pipeline-facts"><Fact label="定位模型">{runtime.model}</Fact><Fact label="运行设备">{runtime.gpu || runtime.device}</Fact><Fact label="模型耗时">{duration(runtime.elapsedMs)}</Fact><Fact label="往返耗时">{duration(runtime.roundTripMs)}</Fact></dl><CandidateList items={list(details.candidates)} /></>;
  }
  if (stage.id === "ocr") {
    const results = list(details.results);
    return results.length ? <div className="pipeline-ocr">{results.map((item, index) => {
      const analysis = data(item.analysis);
      return <div key={`${index}-${item.candidate}`}><header><strong>候选 {value(item.candidate, String(index + 1))} · {value(item.text, "未识别文字")}</strong><span>{duration(item.elapsedMs)}</span></header><p>OCR {score(item.confidence)} · {value(analysis.interpretation || analysis.verdict)}</p></div>;
    })}</div> : <Empty>没有候选区域，OCR 未运行。</Empty>;
  }
  if (stage.id === "retrieval") return <RetrievalStage details={details} />;
  if (stage.id === "fusion") {
    const timings = data(details.timings);
    return <><p className="pipeline-rule">{value(details.rule, "未提供融合规则。")}</p><dl className="pipeline-facts"><Fact label="候选数量">{details.candidateCount ?? 0}</Fact><Fact label="注册表命中">{details.registryCount ?? 0}</Fact><Fact label="OCR 最长耗时">{duration(timings.ocrMaxMs)}</Fact><Fact label="检索最长耗时">{duration(timings.retrievalMaxMs)}</Fact></dl></>;
  }
  if (stage.id === "verdict") {
    const verdict = data(details.verdict);
    const label = verdict.verdict === "yes" ? "存在 AI 水印" : verdict.verdict === "no" ? "未发现 AI 水印" : "需要复核";
    return <><dl className="pipeline-facts"><Fact label="判断" tone={verdict.verdict === "yes" ? "hit" : ""}>{label}</Fact><Fact label="置信度">{score(verdict.confidence)}</Fact><Fact label="来源平台">{details.sourcePlatform}</Fact><Fact label="相关证据">{verdict.relevantHitCount ?? 0}</Fact></dl><p className="pipeline-rationale">{value(verdict.reason, stage.summary)}</p></>;
  }
  return <Empty>{stage.summary || "本阶段没有可展示的数据。"}</Empty>;
}

export default function WatermarkPipeline({ trace }: Props) {
  const stages = useMemo(() => trace?.stages?.filter((stage) => stage && stage.id) || [], [trace]);
  const preferred = stages.find((stage) => stage.status === "error" || stage.status === "warning") || stages.find((stage) => stage.id === "verdict") || stages[0];
  const [selectedId, setSelectedId] = useState(preferred?.id || "");

  useEffect(() => setSelectedId(preferred?.id || ""), [trace?.schemaVersion, trace?.totalElapsedMs, preferred?.id]);

  if (!trace || trace.schemaVersion !== "watermark_pipeline_trace_v1" || !stages.length) return null;
  const selected = stages.find((stage) => stage.id === selectedId) || preferred || stages[0];
  const maxElapsed = Math.max(1, ...stages.map((stage) => Number(stage.elapsedMs) || 0));

  return <details className="watermark-pipeline">
    <summary className="pipeline-heading">
      <div><Activity size={17} /><span><strong>检测流水线</strong><small>逐阶段查看真实输入、输出与阈值</small></span></div>
      <p><Clock3 size={14} /> 总耗时 <strong>{duration(trace.totalElapsedMs)}</strong></p>
      <ChevronDown className="pipeline-toggle" size={16} />
    </summary>
    <div className="pipeline-body">
      <div className="pipeline-stage-tabs" role="tablist" aria-label="检测阶段">
        {stages.map((stage, index) => <button key={stage.id} type="button" role="tab" aria-selected={stage.id === selected.id} className={`${stage.status} ${stage.id === selected.id ? "active" : ""}`} onClick={() => setSelectedId(stage.id)}><i>{String(index + 1).padStart(2, "0")}</i><strong>{stage.label}</strong><small>{duration(stage.elapsedMs)}</small><b aria-hidden="true" /></button>)}
      </div>
      <div className="pipeline-workspace">
        <aside className="pipeline-waterfall" aria-label="阶段耗时">
          <header><strong>阶段耗时</strong><span>并行分支不累加</span></header>
          {stages.map((stage) => <button key={stage.id} type="button" className={stage.id === selected.id ? "active" : ""} onClick={() => setSelectedId(stage.id)}><span>{stage.label}</span><i><b className={stage.status} style={{ width: `${Math.max(2, (Number(stage.elapsedMs) || 0) / maxElapsed * 100)}%` }} /></i><em>{duration(stage.elapsedMs)}</em></button>)}
        </aside>
        <article className="pipeline-stage-detail" role="tabpanel">
          <header><div><span>STAGE {String(stages.indexOf(selected) + 1).padStart(2, "0")}</span><h4>{selected.label}</h4></div><b className={selected.status}>{STATUS_LABEL[selected.status] || selected.status}</b></header>
          <p className="pipeline-summary">{selected.summary}</p>
          <StageDetails stage={selected} />
        </article>
      </div>
    </div>
  </details>;
}
