import { ArrowRight, Check, Layers3, LogIn, ThumbsDown, ThumbsUp } from "lucide-react";
import type { AgentOutcome } from "../agentTypes";

interface Props {
  outcome: AgentOutcome;
  submitting: boolean;
  upgradeBusy: boolean;
  requiresLogin: boolean;
  error: string;
  onFeedback: (feedback: 1 | -1) => void;
  onUpgrade: () => void;
}

export default function ResultFeedback({ outcome, submitting, upgradeBusy, requiresLogin, error, onFeedback, onUpgrade }: Props) {
  const isFallback = outcome.kind === "evidence" && outcome.fallbackFromImage === true;
  if (outcome.kind !== "image" && !isFallback) return null;

  if (isFallback) {
    const requestedSwarm = outcome.analysisMode === "swarm";
    return (
      <section className="result-feedback fallback-result-notice" aria-label="备用检测链路说明">
        <div className="swarm-upgrade">
          <Layers3 size={20} aria-hidden="true" />
          <div>
            <span className="result-mode-label">备用链路结果</span>
            <strong>{requestedSwarm ? "Swarm 未完成，当前展示备用模型结果" : "主模型未完成，当前展示备用模型结果"}</strong>
            <p>当前报告来自备用视觉链路，已与原请求模式明确区分。</p>
          </div>
          {!requestedSwarm && (
            <button type="button" className="swarm-upgrade-button" disabled={upgradeBusy} onClick={onUpgrade}>
              {requiresLogin ? <LogIn size={16} /> : null}{requiresLogin ? "登录后使用 Swarm" : "使用 Swarm 重新复核"}{requiresLogin ? null : <ArrowRight size={16} />}
            </button>
          )}
        </div>
      </section>
    );
  }
  if (outcome.kind !== "image") return null;

  const feedback = outcome.result.feedback ?? null;
  const isSwarm = outcome.analysisMode === "swarm" || outcome.result.swarm?.enabled === true;
  const canReuseFile = Boolean(outcome.file);

  return (
    <section className={`result-feedback ${feedback === -1 ? "has-concern" : ""}`} aria-labelledby="result-feedback-title">
      <div className="result-feedback-main">
        <div className="result-feedback-copy">
          <span className={`result-mode-label ${isSwarm ? "is-swarm" : ""}`}>
            {isSwarm ? <Layers3 size={13} /> : null}{isSwarm ? "Swarm 复核" : "快速检测"}
          </span>
          <div>
            <strong id="result-feedback-title">这个结果对你有帮助吗？</strong>
            <small>反馈不会改变当前报告</small>
          </div>
        </div>
        <div className="result-feedback-actions" aria-label="评价检测结果">
          <button
            type="button"
            className={feedback === 1 ? "is-selected is-positive" : ""}
            aria-label="结果有帮助"
            title="结果有帮助"
            aria-pressed={feedback === 1}
            disabled={submitting}
            onClick={() => onFeedback(1)}
          >
            <ThumbsUp size={18} />
          </button>
          <button
            type="button"
            className={feedback === -1 ? "is-selected is-negative" : ""}
            aria-label="结果没有帮助"
            title="结果没有帮助"
            aria-pressed={feedback === -1}
            disabled={submitting}
            onClick={() => onFeedback(-1)}
          >
            <ThumbsDown size={18} />
          </button>
        </div>
      </div>

      <div className="feedback-status" aria-live="polite">
        {error ? <span className="is-error">{error}</span> : feedback === 1 ? <span><Check size={14} /> 谢谢，你的反馈已记录</span> : null}
      </div>

      {feedback === -1 && (
        <div className="swarm-upgrade" role="status">
          <Layers3 size={20} aria-hidden="true" />
          <div>
            <strong>{isSwarm ? "你的复核反馈已记录" : "需要更充分的交叉核验？"}</strong>
            <p>{isSwarm ? "当前已经是 Swarm 结果，高风险场景建议结合原始来源与人工复核。" : "Swarm 会调度更多证据源，耗时更长，适合对当前结果存疑时使用。"}</p>
          </div>
          {!isSwarm && (
            <button type="button" className="swarm-upgrade-button" disabled={upgradeBusy} onClick={onUpgrade}>
              {requiresLogin ? <LogIn size={16} /> : null}
              {requiresLogin ? "登录后使用 Swarm" : canReuseFile ? "使用 Swarm 重新复核" : "重新上传并使用 Swarm"}
              {requiresLogin ? null : <ArrowRight size={16} />}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
