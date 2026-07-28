import { ChevronDown, Layers3, Zap } from "lucide-react";
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
}> = [
  { mode: "fast", label: "快速检测", detail: "主模型 + 水印", icon: Zap },
  { mode: "swarm", label: "Soar 模式", detail: "多源模型复核", icon: Layers3 },
];

export default function AnalysisModeSwitch({ mode, disabled = false, onChange }: Props) {
  const selected = OPTIONS.find((option) => option.mode === mode) || OPTIONS[0];
  const Icon = selected.icon;
  return (
    <label className={`analysis-mode-select ${disabled ? "is-disabled" : ""}`}>
      <span className="analysis-mode-select-icon" aria-hidden="true"><Icon size={16} /></span>
      <span className="analysis-mode-select-copy">
        <small>检测模型</small>
        <strong>{selected.label}</strong>
      </span>
      <select
        value={mode}
        disabled={disabled}
        aria-label="选择图片检测模型"
        onChange={(event) => onChange(event.target.value as ImageAnalysisMode)}
      >
        {OPTIONS.map((option) => (
          <option key={option.mode} value={option.mode}>{option.label} · {option.detail}</option>
        ))}
      </select>
      <ChevronDown size={15} className="analysis-mode-select-chevron" aria-hidden="true" />
    </label>
  );
}
