import { Check, ChevronDown, Gauge, Layers3 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
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
  note: string;
  icon: typeof Gauge;
}> = [
  { mode: "fast", label: "快速检测", detail: "主模型 + 水印", note: "速度优先，适合日常检测", icon: Gauge },
  { mode: "swarm", label: "Soar 模式", detail: "多源模型复核", note: "证据更充分，耗时更长", icon: Layers3 },
];

export default function AnalysisModeSwitch({ mode, disabled = false, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const selected = OPTIONS.find((option) => option.mode === mode) || OPTIONS[0];
  const Icon = selected.icon;

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div ref={rootRef} className={`analysis-model-picker ${open ? "is-open" : ""}`}>
      <button
        ref={triggerRef}
        type="button"
        className="analysis-model-trigger"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="选择图片检测模型"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="analysis-model-trigger-icon" aria-hidden="true"><Icon size={16} /></span>
        <span className="analysis-model-trigger-copy">
          <small>当前模型</small>
          <strong>{selected.label}</strong>
        </span>
        <ChevronDown size={15} className="analysis-model-chevron" aria-hidden="true" />
      </button>
      {open && (
        <div className="analysis-model-menu" role="listbox" aria-label="图片检测模型">
          <div className="analysis-model-menu-heading"><strong>选择检测模型</strong><small>仅对图片任务生效</small></div>
          {OPTIONS.map((option) => {
            const OptionIcon = option.icon;
            const active = option.mode === mode;
            return (
              <button
                key={option.mode}
                type="button"
                role="option"
                aria-selected={active}
                className={active ? "is-selected" : ""}
                onClick={() => {
                  onChange(option.mode);
                  setOpen(false);
                  triggerRef.current?.focus();
                }}
              >
                <span className="analysis-model-option-icon"><OptionIcon size={17} /></span>
                <span><strong>{option.label}</strong><small>{option.detail}</small><em>{option.note}</em></span>
                <span className="analysis-model-check">{active && <Check size={16} />}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
