import { CheckCircle2, Circle, Layers3, Zap } from "lucide-react";
import type { ImageAnalysisMode } from "../agentTypes";

interface Props {
  mode: ImageAnalysisMode;
  disabled?: boolean;
  onChange: (mode: ImageAnalysisMode) => void;
}

const OPTIONS: Array<{
  mode: ImageAnalysisMode;
  label: string;
  detail: string;
  icon: typeof Zap;
  marker?: string;
}> = [
  { mode: "fast", label: "快速检测", detail: "主模型优先 · 更快", icon: Zap, marker: "默认" },
  { mode: "swarm", label: "Swarm 复核", detail: "多源证据 · 更充分", icon: Layers3 },
];

export default function AnalysisModeSwitch({ mode, disabled = false, onChange }: Props) {
  const descriptionId = "image-analysis-mode-description";
  return (
    <div className="analysis-mode-control">
      <div className="analysis-mode-heading">
        <strong>图片检测模式</strong>
        <small id={descriptionId}>仅对图片生效</small>
      </div>
      <div className="analysis-mode-options" role="radiogroup" aria-label="图片检测模式">
        {OPTIONS.map((option) => {
          const Icon = option.icon;
          const selected = mode === option.mode;
          return (
            <label key={option.mode} className={`analysis-mode-option ${selected ? "is-selected" : ""} ${disabled ? "is-disabled" : ""}`}>
              <input
                type="radio"
                name="image-analysis-mode"
                value={option.mode}
                checked={selected}
                disabled={disabled}
                aria-describedby={descriptionId}
                onChange={() => onChange(option.mode)}
              />
              <span className="analysis-mode-frame">
                <Icon size={18} aria-hidden="true" />
                <span className="analysis-mode-copy"><strong>{option.label}</strong><small>{option.detail}</small></span>
                <span className="analysis-mode-marker" aria-hidden="true">
                  {selected ? <CheckCircle2 size={17} /> : <Circle size={17} />}
                  {option.marker ? <em>{option.marker}</em> : null}
                </span>
              </span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
