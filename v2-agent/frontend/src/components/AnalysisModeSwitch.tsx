import { Check, ChevronDown, ScanLine, Waypoints } from "lucide-react";
import { KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from "react";
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
  icon: typeof ScanLine;
}> = [
  { mode: "fast", label: "快速检测", detail: "即时鉴别", note: "真实性分析与 AI 水印同步核验", icon: ScanLine },
  { mode: "swarm", label: "Soar 模式", detail: "多源复核", note: "更多独立证据源参与交叉判断", icon: Waypoints },
];

export default function AnalysisModeSwitch({ mode, disabled = false, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const pendingFocusRef = useRef<number | null>(null);
  const selected = OPTIONS.find((option) => option.mode === mode) || OPTIONS[0];
  const Icon = selected.icon;

  useEffect(() => {
    if (!open) return;
    const focusIndex = pendingFocusRef.current ?? Math.max(0, OPTIONS.findIndex((option) => option.mode === mode));
    pendingFocusRef.current = null;
    window.requestAnimationFrame(() => optionRefs.current[focusIndex]?.focus());
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
  }, [mode, open]);

  function openWithFocus(index: number) {
    pendingFocusRef.current = index;
    setOpen(true);
  }

  function handleOptionKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>, index: number) {
    let nextIndex: number | null = null;
    if (event.key === "ArrowDown") nextIndex = (index + 1) % OPTIONS.length;
    if (event.key === "ArrowUp") nextIndex = (index - 1 + OPTIONS.length) % OPTIONS.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = OPTIONS.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    optionRefs.current[nextIndex]?.focus();
  }

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
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          openWithFocus(event.key === "ArrowDown" ? 0 : OPTIONS.length - 1);
        }}
      >
        <span className="analysis-model-trigger-icon" aria-hidden="true"><Icon size={16} /></span>
        <span className="analysis-model-trigger-copy">
          <small>检测模式</small>
          <strong>{selected.label}</strong>
        </span>
        <ChevronDown size={15} className="analysis-model-chevron" aria-hidden="true" />
      </button>
      {open && (
        <div className="analysis-model-menu" role="listbox" aria-label="图片检测模型">
          <div className="analysis-model-menu-heading"><strong>选择分析方式</strong><small>仅对图片任务生效</small></div>
          {OPTIONS.map((option) => {
            const OptionIcon = option.icon;
            const active = option.mode === mode;
            return (
              <button
                ref={(element) => { optionRefs.current[OPTIONS.indexOf(option)] = element; }}
                key={option.mode}
                type="button"
                role="option"
                aria-selected={active}
                tabIndex={-1}
                className={active ? "is-selected" : ""}
                onKeyDown={(event) => handleOptionKeyDown(event, OPTIONS.indexOf(option))}
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
