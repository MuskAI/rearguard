import { BrandLogoMark } from "./BrandSystem";

interface Props {
  size?: number;
  idSuffix?: string;
  className?: string;
}

export default function Logo({ size = 40, idSuffix = "0", className }: Props) {
  return (
    <BrandLogoMark
      size={size}
      className={`brand-system-c-logo ${className || ""}`.trim()}
      label={`慧鉴AI 标志 ${idSuffix}`}
    />
  );
}
