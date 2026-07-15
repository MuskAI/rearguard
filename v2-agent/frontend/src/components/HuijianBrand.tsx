import { ScanEye } from "lucide-react";

export default function HuijianBrand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand-lockup" aria-label="慧鉴AI">
      <span className="brand-mark" aria-hidden="true">
        <ScanEye size={compact ? 19 : 22} strokeWidth={2.1} />
        <i />
      </span>
      <span className="brand-copy">
        <strong>慧鉴AI</strong>
        {!compact && <small>数字内容鉴伪智能体</small>}
      </span>
    </div>
  );
}
