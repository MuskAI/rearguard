import {
  Activity,
  AlertTriangle,
  Archive,
  BarChart3,
  Camera,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  Download,
  Eye,
  EyeOff,
  FileText,
  Film,
  History,
  House,
  Image,
  Info,
  Layers3,
  LayoutGrid,
  Lightbulb,
  Link2,
  ListFilter,
  Loader2,
  Lock,
  LogOut,
  Menu,
  Moon,
  Phone,
  PlayCircle,
  RefreshCw,
  ScanEye,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Sun,
  UploadCloud,
  UserRound,
  UserRoundCog,
  UsersRound,
  X,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type IconfontName =
  | "activity"
  | "alert-triangle"
  | "archive"
  | "bar-chart"
  | "bolt"
  | "brand"
  | "camera"
  | "check"
  | "check-circle"
  | "chevron-down"
  | "chevron-right"
  | "clock"
  | "close"
  | "copy"
  | "deep-analysis"
  | "download"
  | "expert-review"
  | "eye"
  | "eye-off"
  | "filter"
  | "grid"
  | "history"
  | "home"
  | "image-forensics"
  | "info"
  | "lightbulb"
  | "link"
  | "loader"
  | "lock"
  | "logout"
  | "menu"
  | "moon"
  | "phone"
  | "play"
  | "refresh"
  | "report"
  | "search"
  | "settings"
  | "shield-check"
  | "sparkles"
  | "sun"
  | "upload"
  | "user"
  | "user-secret"
  | "video-forensics"
  | "x";

interface Props {
  name: IconfontName;
  size?: number;
  strokeWidth?: number;
  className?: string;
}

const glyphs: Record<IconfontName, LucideIcon> = {
  activity: Activity,
  "alert-triangle": AlertTriangle,
  archive: Archive,
  "bar-chart": BarChart3,
  bolt: Zap,
  brand: ScanEye,
  camera: Camera,
  check: Check,
  "check-circle": CheckCircle2,
  "chevron-down": ChevronDown,
  "chevron-right": ChevronRight,
  clock: Clock,
  close: X,
  copy: Copy,
  "deep-analysis": Layers3,
  download: Download,
  "expert-review": UsersRound,
  eye: Eye,
  "eye-off": EyeOff,
  filter: ListFilter,
  grid: LayoutGrid,
  history: History,
  home: House,
  "image-forensics": Image,
  info: Info,
  lightbulb: Lightbulb,
  link: Link2,
  loader: Loader2,
  lock: Lock,
  logout: LogOut,
  menu: Menu,
  moon: Moon,
  phone: Phone,
  play: PlayCircle,
  refresh: RefreshCw,
  report: FileText,
  search: Search,
  settings: SlidersHorizontal,
  "shield-check": ShieldCheck,
  sparkles: Sparkles,
  sun: Sun,
  upload: UploadCloud,
  user: UserRound,
  "user-secret": UserRoundCog,
  "video-forensics": Film,
  x: X,
};

export default function IconfontIcon({ name, size = 20, strokeWidth = 1.8, className = "" }: Props) {
  const Glyph = glyphs[name];
  return (
    <Glyph
      className={`iconfont-svg${className ? ` ${className}` : ""}`}
      width={size}
      height={size}
      strokeWidth={strokeWidth}
      aria-hidden="true"
      focusable="false"
    />
  );
}
