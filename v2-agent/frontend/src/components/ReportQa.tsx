import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  streamReportQuestion,
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

interface StreamRevealState {
  id: string;
  target: string;
  displayed: string;
  frame: number | null;
  lastFrame: number;
  budget: number;
  pauseUntil: number;
  finishing: boolean;
  resolve?: () => void;
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
    return {
      reportId: outcome.result.reportId,
      media: {
        type: outcome.result.fileMeta.type,
        fileName: outcome.result.fileMeta.name,
      },
    };
  }
  return {
    report: outcome.kind === "image" ? imageReportContext(outcome) : videoReportContext(outcome),
    media: {
      type: outcome.kind,
      fileName: outcome.result.filename,
      legacyDetectionId: outcome.result.itemid,
    },
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

function clientUuid() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") crypto.getRandomValues(bytes);
  else bytes.forEach((_value, index) => { bytes[index] = Math.floor(Math.random() * 256); });
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const value = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
}

function reducedMotionRequested() {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export default function ReportQa({ outcome, requiresLogin, composerHost, onAttach, onLogin }: Props) {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [streamingMessageId, setStreamingMessageId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>(() => initialQuestions(outcome));
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const scrollFrameRef = useRef<number | null>(null);
  const revealRef = useRef<StreamRevealState | null>(null);
  const revealTickRef = useRef<(timestamp: number) => void>(() => undefined);
  const conversationIdRef = useRef(clientUuid());
  const outcomeRef = useRef(outcome);
  outcomeRef.current = outcome;
  const request = useMemo(() => reportRequest(outcome), [outcome]);

  const paintReveal = useCallback((id: string, content: string) => {
    setMessages((current) => current.map((message) => (
      message.id === id ? { ...message, content } : message
    )));
  }, []);

  const scheduleReveal = useCallback(() => {
    const state = revealRef.current;
    if (!state || state.frame !== null) return;
    state.frame = window.requestAnimationFrame((timestamp) => revealTickRef.current(timestamp));
  }, []);

  const stopReveal = useCallback(() => {
    const state = revealRef.current;
    if (!state) return;
    if (state.frame !== null) window.cancelAnimationFrame(state.frame);
    state.resolve?.();
    revealRef.current = null;
  }, []);

  revealTickRef.current = (timestamp: number) => {
    const state = revealRef.current;
    if (!state) return;
    state.frame = null;

    if (reducedMotionRequested()) {
      state.displayed = state.target;
      paintReveal(state.id, state.displayed);
    } else if (timestamp < state.pauseUntil) {
      scheduleReveal();
      return;
    } else {
      if (state.lastFrame === 0) state.lastFrame = timestamp;
      const elapsed = Math.min(64, Math.max(0, timestamp - state.lastFrame));
      state.lastFrame = timestamp;
      const remaining = Array.from(state.target.slice(state.displayed.length));
      const backlog = remaining.length;
      const charactersPerSecond = state.finishing
        ? Math.min(160, 58 + backlog * 2.2)
        : Math.min(92, 30 + backlog * 1.25);
      state.budget += elapsed * charactersPerSecond / 1_000;
      let amount = Math.min(backlog, Math.floor(state.budget));
      if (amount > 0) {
        const punctuation = remaining.slice(0, amount).findIndex((character) => /[，。！？；]/.test(character));
        if (punctuation >= 0) amount = punctuation + 1;
        const addition = remaining.slice(0, amount).join("");
        state.displayed += addition;
        state.budget -= amount;
        if (/[。！？]$/.test(addition)) state.pauseUntil = timestamp + (state.finishing ? 55 : 85);
        else if (/[，；]$/.test(addition)) state.pauseUntil = timestamp + (state.finishing ? 35 : 55);
        paintReveal(state.id, state.displayed);
      }
    }

    if (state.displayed.length < state.target.length) {
      scheduleReveal();
      return;
    }
    state.lastFrame = 0;
    state.budget = 0;
    if (state.finishing) {
      const resolve = state.resolve;
      state.resolve = undefined;
      resolve?.();
    }
  };

  const appendReveal = useCallback((id: string, delta: string) => {
    let state = revealRef.current;
    if (!state || state.id !== id) {
      stopReveal();
      state = {
        id,
        target: "",
        displayed: "",
        frame: null,
        lastFrame: 0,
        budget: 0,
        pauseUntil: 0,
        finishing: false,
      };
      revealRef.current = state;
    }
    state.target += delta;
    if (reducedMotionRequested()) {
      state.displayed = state.target;
      paintReveal(id, state.displayed);
      return;
    }
    scheduleReveal();
  }, [paintReveal, scheduleReveal, stopReveal]);

  const finishReveal = useCallback(async (id: string, finalAnswer: string) => {
    let state = revealRef.current;
    if (!state || state.id !== id) {
      state = {
        id,
        target: finalAnswer,
        displayed: "",
        frame: null,
        lastFrame: 0,
        budget: 0,
        pauseUntil: 0,
        finishing: true,
      };
      revealRef.current = state;
    }
    if (!finalAnswer.startsWith(state.displayed)) {
      state.target = finalAnswer;
      state.displayed = finalAnswer;
      paintReveal(id, finalAnswer);
      return;
    }
    state.target = finalAnswer;
    state.finishing = true;
    if (state.displayed === state.target || reducedMotionRequested()) {
      state.displayed = state.target;
      paintReveal(id, state.displayed);
      return;
    }
    await new Promise<void>((resolve) => {
      state!.resolve = resolve;
      scheduleReveal();
    });
  }, [paintReveal, scheduleReveal]);

  useEffect(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    stopReveal();
    setMessages([]);
    setQuestion("");
    setBusy(false);
    setStreamingMessageId(null);
    setError("");
    setSuggestions(initialQuestions(outcomeRef.current));
    conversationIdRef.current = clientUuid();
  }, [outcome.id, stopReveal]);

  useEffect(() => {
    if (messages.length === 0 && !busy) return;
    if (scrollFrameRef.current !== null) window.cancelAnimationFrame(scrollFrameRef.current);
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      endRef.current?.scrollIntoView({
        behavior: streamingMessageId || reducedMotionRequested() ? "auto" : "smooth",
        block: "nearest",
      });
      scrollFrameRef.current = null;
    });
    return () => {
      if (scrollFrameRef.current !== null) window.cancelAnimationFrame(scrollFrameRef.current);
      scrollFrameRef.current = null;
    };
  }, [busy, messages, streamingMessageId]);

  useEffect(() => () => {
    abortRef.current?.abort();
    stopReveal();
    if (scrollFrameRef.current !== null) window.cancelAnimationFrame(scrollFrameRef.current);
  }, [stopReveal]);

  async function submit(rawQuestion: string) {
    const value = rawQuestion.trim();
    if (!value || busy || requiresLogin) return;
    const history = messages.slice(-8).map(({ role, content }) => ({ role, content }));
    const userMessage: ConversationMessage = { id: messageId("user"), role: "user", content: value };
    const assistantId = messageId("assistant");
    let assistantAdded = false;
    setMessages((current) => [...current, userMessage]);
    setQuestion("");
    setError("");
    setBusy(true);
    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;
    try {
      const response = await streamReportQuestion(
        {
          ...request,
          question: value,
          history,
          conversationId: conversationIdRef.current,
          turnId: clientUuid(),
        },
        {
          onDelta: (delta) => {
            if (!assistantAdded) {
              assistantAdded = true;
              setStreamingMessageId(assistantId);
              setMessages((current) => [...current, {
                id: assistantId,
                role: "assistant",
                content: "",
              }]);
            }
            appendReveal(assistantId, delta);
          },
        },
        controller.signal,
      );
      if (!assistantAdded) {
        assistantAdded = true;
        setStreamingMessageId(assistantId);
        setMessages((current) => [...current, { id: assistantId, role: "assistant", content: "" }]);
      }
      await finishReveal(assistantId, response.answer);
      if (controller.signal.aborted) return;
      setMessages((current) => {
        const finalMessage: ConversationMessage = {
          id: assistantId,
          role: "assistant",
          content: response.answer,
          evidenceRefs: response.evidenceRefs,
        };
        return assistantAdded
          ? current.map((message) => message.id === assistantId ? finalMessage : message)
          : [...current, finalMessage];
      });
      if (response.suggestedQuestions.length > 0) setSuggestions(response.suggestedQuestions);
    } catch (requestError) {
      if (controller.signal.aborted) return;
      stopReveal();
      setMessages((current) => current.filter((message) => ![userMessage.id, assistantId].includes(message.id)));
      setQuestion(value);
      const message = requestError instanceof ApiRequestError && requestError.status === 401
        ? "登录状态已失效，请重新登录后继续提问。"
        : requestError instanceof Error
          ? requestError.message
          : "报告解释暂不可用，请稍后重试。";
      setError(message);
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      if (!controller.signal.aborted) {
        stopReveal();
        setStreamingMessageId(null);
        setBusy(false);
      }
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
          <div className="report-qa-messages" aria-live="polite" aria-busy={busy} aria-label="报告问答记录">
            {messages.map((message) => (
              <div
                className={`report-qa-message is-${message.role}${streamingMessageId === message.id ? " is-streaming" : ""}`}
                key={message.id}
              >
                <span className="report-qa-speaker" aria-hidden="true">
                  {message.role === "assistant" ? <AgentAvatar size={30} state="complete" /> : <UserRound size={16} />}
                </span>
                <div>
                  <p>
                    {message.content}
                    {streamingMessageId === message.id && <span className="report-qa-stream-cursor" aria-hidden="true" />}
                  </p>
                  {message.evidenceRefs && message.evidenceRefs.length > 0 && (
                    <ul className="report-qa-references" aria-label="引用的报告证据">
                      {message.evidenceRefs.map((reference) => <li key={reference}>{reference}</li>)}
                    </ul>
                  )}
                </div>
              </div>
            ))}
            {busy && !streamingMessageId && (
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
