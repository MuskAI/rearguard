import {
  Archive,
  Clapperboard,
  FileText,
  History,
  House,
  Layers3,
  Plus,
  ScanSearch,
  Search,
  ShieldCheck,
  Upload,
  X,
  type LucideIcon,
} from "lucide-react";

export type IconfontName =
  | "archive"
  | "close"
  | "deep-analysis"
  | "history"
  | "home"
  | "image-forensics"
  | "plus"
  | "report"
  | "search"
  | "shield-check"
  | "upload"
  | "video-forensics";

interface Props {
  name: IconfontName;
  size?: number;
  strokeWidth?: number;
  className?: string;
}

const glyphs: Record<IconfontName, LucideIcon> = {
  archive: Archive,
  close: X,
  "deep-analysis": Layers3,
  history: History,
  home: House,
  "image-forensics": ScanSearch,
  plus: Plus,
  report: FileText,
  search: Search,
  "shield-check": ShieldCheck,
  upload: Upload,
  "video-forensics": Clapperboard,
};

export default function IconfontIcon({ name, size = 20, strokeWidth = 1.9, className = "" }: Props) {
  const Icon = glyphs[name];
  return (
    <Icon
      className={`iconfont-svg${className ? ` ${className}` : ""}`}
      size={size}
      strokeWidth={strokeWidth}
      aria-hidden="true"
      focusable="false"
    />
  );
}
