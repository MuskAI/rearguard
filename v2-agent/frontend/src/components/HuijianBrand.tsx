import { ScanEye } from "lucide-react";

interface Props {
  compact?: boolean;
  onClick?: () => void;
}

export default function HuijianBrand({ compact = false, onClick }: Props) {
  const content = (
    <>
      <span className="brand-mark" aria-hidden="true">
        <ScanEye size={compact ? 19 : 22} strokeWidth={2.1} />
        <i />
      </span>
      <span className="brand-copy">
        <strong>慧鉴AI</strong>
        {!compact && <small>数字内容鉴伪智能体</small>}
      </span>
    </>
  );
  if (onClick) {
    return <button type="button" className="brand-lockup brand-home-button" onClick={onClick} aria-label="返回慧鉴AI官网首页" title="返回官网首页">{content}</button>;
  }
  return <div className="brand-lockup" aria-label="慧鉴AI">{content}</div>;
}
