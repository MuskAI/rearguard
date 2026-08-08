import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  LoaderCircle,
  LogIn,
  MessageCircleQuestion,
  Paperclip,
  Send,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import { createPortal } from "react-dom";
import type { AgentOutcome } from "../agentTypes";
import {
  ApiRequestError,
  askReportQuestion,
  type ImageAgentReview,
  type ProbabilityModel,
  type ProvenanceReport,
  type ReportQaMessage,
  type SynthIDResult,
  type VisibleWatermarkResult,
} from "../api";
import { binaryVerdictLabel, isFakeVerdict } from "../binaryVerdict";
import { AgentAvatar } from "./BrandSystem";

interface Props {
  outcome: AgentOutcome;
  requiresLogin: boolean;
  composerHost: HTMLDivElement | null;
  onAttach: () => void;
  onLogin: () => void;
}

interface ConversationMessage extends ReportQaMessage {
  id: string;
  evidenceRefs?: string[];
}

const MAX_QUESTION_LENGTH = 500;

function normalizeScore(value: unknown): number | null {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(0, Math.min(number > 1 ? number / 100 : number, 1));
}

function stripForensicImages(outcome: AgentOutcome) {
  if (outcome.kind === "video") return undefined;
  const report = outcome.forensics;
  if (!report) return undefined;
  return {
    summary: report.summary,
    items: report.items.map(({ key, title, explanation, status, finding }) => ({
      key,
      title,
      explanation,
      status,
      finding,
    })),
  };
}

function compactWatermark(report?: VisibleWatermarkResult) {
  if (!report) return undefined;
  return {
    detected: report.detected,
    provider: report.provider,
    confidence: report.confidence,
    evidenceLevel: report.evidenceLevel,
    note: report.note,
    explicitWatermark: report.explicitWatermark ? {
      type: report.explicitWatermark.type,
      sourcePlatform: report.explicitWatermark.sourcePlatform,
      provider: report.explicitWatermark.provider,
      confidence: report.explicitWatermark.confidence,
      aiWatermarkVerdict: report.explicitWatermark.aiWatermarkVerdict ? {
        verdict: report.explicitWatermark.aiWatermarkVerdict.verdict,
        isAiGeneratedWatermark: report.explicitWatermark.aiWatermarkVerdict.isAiGeneratedWatermark,
        confidence: report.explicitWatermark.aiWatermarkVerdict.confidence,
        reason: report.explicitWatermark.aiWatermarkVerdict.reason,
      } : undefined,
    } : undefined,
    hits: (report.hits || []).slice(0, 10).map((hit) => ({
      provider: hit.provider,
      label: hit.label,
      confidence: hit.confidence,
      bbox: hit.bbox,
      decisive: hit.decisive,
    })),
    pipelineTrace: report.pipelineTrace ? {
      stages: report.pipelineTrace.stages.slice(0, 10).map((stage) => ({
        id: stage.id,
        label: stage.label,
        status: stage.status,
        summary: stage.summary,
      })),
    } : undefined,
  };
}

function compactSynthid(report?: SynthIDResult) {
  if (!report) return undefined;
  return {
    detected: report.detected,
    detectionState: report.detectionState,
    confidence: report.confidence,
    evidenceLevel: report.evidenceLevel,
    note: report.note,
  };
}

function compactProvenance(report?: ProvenanceReport) {
  if (!report) return undefined;
  return {
    hasCredentials: report.hasCredentials,
    validationState: report.validationState,
    credentialTrusted: report.credentialTrusted,
    generator: report.generator,
    issuer: report.issuer,
    isAiGenerated: report.isAiGenerated,
    metadataAiGenerated: report.metadataAiGenerated,
    aiMetadata: report.aiMetadata ? {
      confidence: report.aiMetadata.confidence,
      confidenceText: report.aiMetadata.confidenceText,
      isAiLikely: report.aiMetadata.isAiLikely,
      signals: report.aiMetadata.signals.slice(0, 10).map((signal) => ({
        id: signal.id,
        label: signal.label,
        reason: signal.reason,
      })),
    } : undefined,
    actions: report.actions.slice(0, 10).map((action) => ({
      action: action.action,
      softwareAgent: action.softwareAgent,
      digitalSourceType: action.digitalSourceType,
    })),
    error: report.error,
  };
}

function compactProbability(report?: ProbabilityModel) {
  if (!report) return undefined;
  return {
    posterior: report.posterior,
    decisive: report.decisive,
    corroborated: report.corroborated,
    conflicting: report.conflicting,
    calibrationStatus: report.calibrationStatus,
    note: report.note,
    factors: report.factors.slice(0, 12).map((factor) => ({
      label: factor.label,
      direction: factor.direction,
      likelihoodRatio: factor.likelihoodRatio,
      effectiveLikelihoodRatio: factor.effectiveLikelihoodRatio,
    })),
  };
}

function compactSwarm(report?: ImageAgentReview) {
  if (!report) return undefined;
  return {
    enabled: report.enabled,
    consensusLevel: report.consensusLevel,
    consensusScore: report.consensusScore,
    disagreement: report.disagreement,
    effectiveExperts: report.effectiveExperts,
    totalExperts: report.totalExperts,
    evidence: (report.evidence || []).slice(0, 16),
  };
}

function imageReportContext(outcome: Extract<AgentOutcome, { kind: "image" }>): Record<string, unknown> {
  const result = outcome.result;
  const verdictLabel = binaryVerdictLabel(result.final_label, result.probability);
  const keyEvidence = [
    ...(result.swarm?.evidence || []).map((detail, index) => ({ label: `Swarm 证据 ${index + 1}`, detail })),
    ...(result.visual_issues || []).map((detail, index) => ({ label: `视觉线索 ${index + 1}`, detail })),
    ...(result.visualReview?.evidence || []).map((detail, index) => ({ label: `视觉复核 ${index + 1}`, detail })),
  ];
  return {
    kind: "image",
    mediaType: "image",
    analysisMode: outcome.analysisMode || (result.swarm?.enabled ? "swarm" : "fast"),
    verdict: result.final_label,
    verdictLabel,
    confidence: result.confidence,
    riskScore: normalizeScore(result.probability),
    aiProbability: normalizeScore(result.detector_probability ?? result.p_visual ?? result.probability),
    decisionStatus: result.decisionStatus,
    decisionAuthority: result.decisionAuthority,
    explanation: result.explanation,
    keyEvidence,
    visibleWatermark: compactWatermark(result.visibleWatermark),
    synthid: compactSynthid(result.synthid),
    captureEvidence: result.capture_evidence || outcome.provenance?.captureEvidence,
    provenance: compactProvenance(outcome.provenance),
    probabilityModel: compactProbability(result.probabilityModel || result.swarm?.probabilityModel),
    swarm: compactSwarm(result.swarm),
    visualReview: result.visualReview ? {
      nonAuthoritative: result.visualReview.nonAuthoritative,
      evidence: (result.visualReview.evidence || []).slice(0, 12),
      note: result.visualReview.note,
    } : undefined,
    forensics: stripForensicImages(outcome),
    evidenceWarnings: result.evidenceWarnings,
    limitations: result.visualReview?.nonAuthoritative
      ? [result.visualReview.note || "视觉复核是补充解释，不改变已发布主结论。"]
      : [],
    disclaimer: "自动化检测结论仅供专业复核参考，不构成司法鉴定结论。",
  };
}

function videoReportContext(outcome: Extract<AgentOutcome, { kind: "video" }>): Record<string, unknown> {
  const result = outcome.result;
  const riskScore = normalizeScore(result.fake_percentage ?? result.confidence_score);
  return {
    kind: "video",
    mediaType: "video",
    verdict: result.final_label,
    verdictLabel: binaryVerdictLabel(result.final_label, riskScore),
    confidence: result.confidence,
    riskScore,
    aiProbability: riskScore,
    decisionStatus: result.decisionStatus,
    decisionAuthority: result.decisionAuthority,
    explanation: result.explanation,
    keyEvidence: [
      {
        label: "抽帧检测",
        detail: result.frame_count ? `本次报告分析了 ${result.frame_count} 个采样帧。` : "报告未返回可用的抽帧数量。",
      },
    ],
    limitations: ["视频报告未给出帧级定位时，不能据此指出具体画面区域。"],
    disclaimer: "自动化检测结论仅供专业复核参考，不构成司法鉴定结论。",
  };
}

function reportRequest(outcome: AgentOutcome) {
  if (outcome.kind === "evidence") {
    return { reportId: outcome.result.reportId };
  }
  return {
    report: outcome.kind === "image" ? imageReportContext(outcome) : videoReportContext(outcome),
  };
}

function initialQuestions(outcome: AgentOutcome): string[] {
  const label = outcome.kind === "evidence"
    ? binaryVerdictLabel(outcome.result.verdict, outcome.result.riskScore ?? outcome.result.confidence)
    : outcome.kind === "image"
      ? binaryVerdictLabel(outcome.result.final_label, outcome.result.probability)
      : binaryVerdictLabel(outcome.result.final_label, outcome.result.fake_percentage);
  if (isFakeVerdict(label)) {
    return ["为什么判断为 AI 生成？", "哪些位置或证据最可疑？", "水印和元数据分别说明了什么？"];
  }
  return ["为什么判断为真实图像？", "有哪些实拍来源证据？", "当前报告还有哪些局限？"];
}

function messageId(role: "user" | "assistant") {
  return `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function ReportQa({ outcome, requiresLogin, composerHost, onAttach, onLogin }: Props) {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>(() => initialQuestions(outcome));
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const outcomeRef = useRef(outcome);
  outcomeRef.current = outcome;
  const request = useMemo(() => reportRequest(outcome), [outcome]);

  useEffect(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages([]);
    setQuestion("");
    setBusy(false);
    setError("");
    setSuggestions(initialQuestions(outcomeRef.current));
  }, [outcome.id]);

  useEffect(() => {
    if (messages.length > 0 || busy) endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [busy, messages]);

  useEffect(() => () => abortRef.current?.abort(), []);

  async function submit(rawQuestion: string) {
    const value = rawQuestion.trim();
    if (!value || busy || requiresLogin) return;
    const history = messages.slice(-8).map(({ role, content }) => ({ role, content }));
    const userMessage: ConversationMessage = { id: messageId("user"), role: "user", content: value };
    setMessages((current) => [...current, userMessage]);
    setQuestion("");
    setError("");
    setBusy(true);
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    try {
      const response = await askReportQuestion({ ...request, question: value, history }, controller.signal);
      setMessages((current) => [...current, {
        id: messageId("assistant"),
        role: "assistant",
        content: response.answer,
        evidenceRefs: response.evidenceRefs,
      }]);
      if (response.suggestedQuestions.length > 0) setSuggestions(response.suggestedQuestions);
    } catch (requestError) {
      if (controller.signal.aborted) return;
      setMessages((current) => current.filter((message) => message.id !== userMessage.id));
      setQuestion(value);
      const message = requestError instanceof ApiRequestError && requestError.status === 401
        ? "登录状态已失效，请重新登录后继续提问。"
        : requestError instanceof Error
          ? requestError.message
          : "报告解释暂不可用，请稍后重试。";
      setError(message);
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submit(question);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void submit(question);
  }

  const dock = composerHost ? createPortal(
    <div className="report-qa-dock-content">
      {!requiresLogin && (
        <div className="report-qa-suggestions" aria-label="常见问题">
          {suggestions.slice(0, 3).map((item) => (
            <button type="button" key={item} disabled={busy} onClick={() => void submit(item)}>
              {item}<ArrowRight size={14} />
            </button>
          ))}
        </div>
      )}
      <form className={`report-qa-composer${requiresLogin ? " requires-login" : ""}`} onSubmit={onSubmit}>
        <button type="button" className="report-qa-attach" onClick={onAttach} disabled={busy} aria-label="上传新的内容" title="上传新的内容">
          <Paperclip size={19} />
        </button>
        {requiresLogin ? (
          <button type="button" className="report-qa-login-field" onClick={onLogin}>
            <span><strong>登录后询问当前报告</strong><small>对话只使用本次检测证据</small></span>
          </button>
        ) : (
          <textarea
            value={question}
            rows={1}
            maxLength={MAX_QUESTION_LENGTH}
            placeholder="询问当前检测报告…"
            aria-label="向小鉴询问本次检测报告"
            disabled={busy}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={onKeyDown}
          />
        )}
        <button
          type={requiresLogin ? "button" : "submit"}
          className="report-qa-send"
          disabled={!requiresLogin && (busy || !question.trim())}
          onClick={requiresLogin ? onLogin : undefined}
          aria-label={requiresLogin ? "登录后提问" : "发送问题"}
          title={requiresLogin ? "登录后提问" : "发送问题"}
        >
          {requiresLogin ? <LogIn size={18} /> : busy ? <LoaderCircle size={18} className="spin" /> : <Send size={18} />}
        </button>
      </form>
      {error && <p className="report-qa-error" role="alert">{error}</p>}
    </div>,
    composerHost,
  ) : null;

  return (
    <>
      {(messages.length > 0 || busy) && (
        <section className="report-qa" aria-labelledby="report-qa-title">
          <header className="report-qa-header">
            <span className="report-qa-mark"><MessageCircleQuestion size={18} /></span>
            <div>
              <h3 id="report-qa-title">报告问答</h3>
              <p><ShieldCheck size={13} /> 只依据当前报告</p>
            </div>
          </header>
          <div className="report-qa-messages" aria-live="polite" aria-label="报告问答记录">
            {messages.map((message) => (
              <div className={`report-qa-message is-${message.role}`} key={message.id}>
                <span className="report-qa-speaker" aria-hidden="true">
                  {message.role === "assistant" ? <AgentAvatar size={30} state="complete" /> : <UserRound size={16} />}
                </span>
                <div>
                  <p>{message.content}</p>
                  {message.evidenceRefs && message.evidenceRefs.length > 0 && (
                    <ul className="report-qa-references" aria-label="引用的报告证据">
                      {message.evidenceRefs.map((reference) => <li key={reference}>{reference}</li>)}
                    </ul>
                  )}
                </div>
              </div>
            ))}
            {busy && (
              <div className="report-qa-message is-assistant is-loading" role="status">
                <span className="report-qa-speaker" aria-hidden="true"><AgentAvatar size={30} state="processing" /></span>
                <div><p><LoaderCircle size={15} className="spin" /> 正在核对报告证据</p></div>
              </div>
            )}
            <div ref={endRef} />
          </div>
        </section>
      )}
      {dock}
    </>
  );
}
