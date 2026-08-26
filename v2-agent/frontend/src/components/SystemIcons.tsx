import { forwardRef, type CSSProperties, type HTMLAttributes } from "react";
import "@material-symbols/font-400/rounded.css";

type IconSize = number | string;

export interface SystemIconProps extends Omit<HTMLAttributes<HTMLSpanElement>, "color"> {
  size?: IconSize;
  color?: string;
  strokeWidth?: number;
  absoluteStrokeWidth?: boolean;
  fill?: string;
  focusable?: string | boolean;
}

export type LucideIcon = ReturnType<typeof createSystemIcon>;

function createSystemIcon(displayName: string, symbol: string) {
  const Icon = forwardRef<HTMLSpanElement, SystemIconProps>(function SystemIcon(
    {
      size = 24,
      color,
      strokeWidth,
      absoluteStrokeWidth,
      fill,
      focusable,
      className = "",
      style,
      "aria-label": ariaLabel,
      "aria-hidden": ariaHidden,
      ...props
    },
    ref,
  ) {
    const iconStyle = {
      ...style,
      width: size,
      height: size,
      fontSize: size,
      color: color || style?.color,
      "--hj-symbol-fill": fill && fill !== "none" ? 1 : 0,
    } as CSSProperties;

    return (
      <span
        {...props}
        ref={ref}
        className={`material-symbols-rounded hj-system-icon lucide lucide-${displayName}${className ? ` ${className}` : ""}`}
        style={iconStyle}
        role={ariaLabel ? "img" : props.role}
        aria-label={ariaLabel}
        aria-hidden={ariaHidden ?? (ariaLabel ? undefined : true)}
        data-symbol={symbol}
        data-stroke-width={strokeWidth}
        data-absolute-stroke-width={absoluteStrokeWidth || undefined}
        data-focusable={focusable === undefined ? undefined : String(focusable)}
      />
    );
  });
  Icon.displayName = displayName;
  return Icon;
}

export const Activity = createSystemIcon("activity", "monitoring");
export const AlertTriangle = createSystemIcon("alert-triangle", "warning");
export const Archive = createSystemIcon("archive", "inventory_2");
export const ArrowLeft = createSystemIcon("arrow-left", "arrow_back");
export const ArrowRight = createSystemIcon("arrow-right", "arrow_forward");
export const BadgeCheck = createSystemIcon("badge-check", "verified");
export const BookOpen = createSystemIcon("book-open", "menu_book");
export const Building2 = createSystemIcon("building-2", "apartment");
export const Camera = createSystemIcon("camera", "photo_camera");
export const Check = createSystemIcon("check", "check");
export const CheckCircle2 = createSystemIcon("check-circle-2", "check_circle");
export const ChevronDown = createSystemIcon("chevron-down", "keyboard_arrow_down");
export const ChevronRight = createSystemIcon("chevron-right", "keyboard_arrow_right");
export const CircleDollarSign = createSystemIcon("circle-dollar-sign", "paid");
export const Clapperboard = createSystemIcon("clapperboard", "movie");
export const Clipboard = createSystemIcon("clipboard", "content_paste");
export const Clock3 = createSystemIcon("clock-3", "schedule");
export const Code2 = createSystemIcon("code-2", "code");
export const Copy = createSystemIcon("copy", "content_copy");
export const Download = createSystemIcon("download", "download");
export const ExternalLink = createSystemIcon("external-link", "open_in_new");
export const Eye = createSystemIcon("eye", "visibility");
export const EyeOff = createSystemIcon("eye-off", "visibility_off");
export const FileImage = createSystemIcon("file-image", "image");
export const FileJson = createSystemIcon("file-json", "data_object");
export const FileSearch = createSystemIcon("file-search", "find_in_page");
export const FileText = createSystemIcon("file-text", "description");
export const Fingerprint = createSystemIcon("fingerprint", "fingerprint");
export const FlaskConical = createSystemIcon("flask-conical", "science");
export const Gauge = createSystemIcon("gauge", "speed");
export const Globe2 = createSystemIcon("globe-2", "travel_explore");
export const Handshake = createSystemIcon("handshake", "handshake");
export const Heart = createSystemIcon("heart", "favorite");
export const History = createSystemIcon("history", "history");
export const House = createSystemIcon("house", "home");
export const Image = createSystemIcon("image", "image");
export const Info = createSystemIcon("info", "info");
export const KeyRound = createSystemIcon("key-round", "key");
export const Landmark = createSystemIcon("landmark", "account_balance");
export const Layers3 = createSystemIcon("layers-3", "layers");
export const LayoutDashboard = createSystemIcon("layout-dashboard", "dashboard");
export const Link2 = createSystemIcon("link-2", "link");
export const LoaderCircle = createSystemIcon("loader-circle", "progress_activity");
export const LockKeyhole = createSystemIcon("lock-keyhole", "lock");
export const LogIn = createSystemIcon("log-in", "login");
export const LogOut = createSystemIcon("log-out", "logout");
export const Maximize2 = createSystemIcon("maximize-2", "fullscreen");
export const Menu = createSystemIcon("menu", "menu");
export const MessageCircleQuestion = createSystemIcon("message-circle-question", "contact_support");
export const MessageSquareText = createSystemIcon("message-square-text", "chat");
export const MousePointer2 = createSystemIcon("mouse-pointer-2", "arrow_selector_tool");
export const Network = createSystemIcon("network", "hub");
export const PanelLeftClose = createSystemIcon("panel-left-close", "left_panel_close");
export const PanelLeftOpen = createSystemIcon("panel-left-open", "left_panel_open");
export const Paperclip = createSystemIcon("paperclip", "attach_file");
export const Play = createSystemIcon("play", "play_arrow");
export const Plus = createSystemIcon("plus", "add");
export const RefreshCw = createSystemIcon("refresh-cw", "refresh");
export const RotateCcw = createSystemIcon("rotate-ccw", "rotate_left");
export const RotateCw = createSystemIcon("rotate-cw", "rotate_right");
export const ScanLine = createSystemIcon("scan-line", "document_scanner");
export const ScanSearch = createSystemIcon("scan-search", "center_focus_strong");
export const Search = createSystemIcon("search", "search");
export const Send = createSystemIcon("send", "send");
export const ShieldCheck = createSystemIcon("shield-check", "verified_user");
export const ShieldOff = createSystemIcon("shield-off", "gpp_bad");
export const Smartphone = createSystemIcon("smartphone", "smartphone");
export const Sparkles = createSystemIcon("sparkles", "auto_awesome");
export const SquareTerminal = createSystemIcon("square-terminal", "terminal");
export const Target = createSystemIcon("target", "track_changes");
export const ThumbsDown = createSystemIcon("thumbs-down", "thumb_down");
export const ThumbsUp = createSystemIcon("thumbs-up", "thumb_up");
export const Trash2 = createSystemIcon("trash-2", "delete");
export const Upload = createSystemIcon("upload", "upload");
export const UploadCloud = createSystemIcon("upload-cloud", "cloud_upload");
export const UserRound = createSystemIcon("user-round", "person");
export const Video = createSystemIcon("video", "videocam");
export const WalletCards = createSystemIcon("wallet-cards", "wallet");
export const X = createSystemIcon("x", "close");
export const ZoomIn = createSystemIcon("zoom-in", "zoom_in");
