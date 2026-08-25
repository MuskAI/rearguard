import { useId, useMemo, type ReactNode } from "react";
import { createHuijianAvatarDataUri } from "../avatar/huijianAvatar";
export type CapabilityIconName =
  | "fast"
  | "swarm"
  | "image"
  | "video"
  | "document"
  | "report"
  | "developer"
  | "workflow"
  | "faq";

export type BrandStatusIconName =
  | "real"
  | "fake"
  | "processing"
  | "warning"
  | "offline"
  | "upload"
  | "extract"
  | "partial"
  | "error";

export type AgentAvatarState = "idle" | "receiving" | "processing" | "complete" | "error";
export type AnalysisModeMarkName = "fast" | "swarm";

interface SvgProps {
  size?: number;
  className?: string;
  label?: string;
}

interface GlyphFrameProps extends SvgProps {
  name: string;
  children: ReactNode;
}

function GlyphFrame({ size = 24, className = "", label, name, children }: GlyphFrameProps) {
  return (
    <svg
      className={`brand-glyph brand-glyph-${name} ${className}`.trim()}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {label && <title>{label}</title>}
      <g stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        {children}
      </g>
    </svg>
  );
}

export function BrandLogoMark({ size = 48, className = "", label }: SvgProps) {
  const paintKey = useId().replace(/:/g, "");
  const surfaceId = `huijian-surface-${paintKey}`;
  const lensId = `huijian-lens-${paintKey}`;

  return (
    <svg
      className={`brand-logo-mark ${className}`.trim()}
      viewBox="0 0 48 48"
      width={size}
      height={size}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {label && <title>{label}</title>}
      <defs>
        <linearGradient id={surfaceId} x1="7" y1="5" x2="42" y2="45" gradientUnits="userSpaceOnUse">
          <stop stopColor="#28333c" />
          <stop offset="1" stopColor="#101418" />
        </linearGradient>
        <radialGradient id={lensId} cx="0" cy="0" r="1" gradientTransform="translate(21.5 20.5) rotate(48) scale(13)">
          <stop stopColor="#f8ffff" />
          <stop offset=".32" stopColor="#73d9ff" />
          <stop offset=".7" stopColor="#3978e6" />
          <stop offset="1" stopColor="#142c60" />
        </radialGradient>
      </defs>
      <rect className="brand-logo-surface" x="2.5" y="2.5" width="43" height="43" rx="11.5" style={{ fill: `url(#${surfaceId})` }} />
      <rect className="brand-logo-edge" x="3.5" y="3.5" width="41" height="41" rx="10.5" />
      <path className="brand-logo-panel brand-logo-panel-left" d="M10 13.5A3.5 3.5 0 0 1 13.5 10H21v8h-4.5a3 3 0 0 0-3 3v13.5A3.5 3.5 0 0 1 10 38H9V14.5a1 1 0 0 1 1-1Z" />
      <path className="brand-logo-panel brand-logo-panel-right" d="M38 13.5A3.5 3.5 0 0 0 34.5 10H27v8h4.5a3 3 0 0 1 3 3v13.5A3.5 3.5 0 0 0 38 38h1V14.5a1 1 0 0 0-1-1Z" />
      <circle className="brand-logo-lens-shell" cx="24" cy="24" r="8.75" />
      <circle className="brand-logo-iris" cx="24" cy="24" r="5.75" style={{ fill: `url(#${lensId})` }} />
      <circle className="brand-logo-glint" cx="21.8" cy="21.6" r="1.55" />
      <path className="brand-logo-index" d="M13.5 40h21" />
    </svg>
  );
}

export function AnalysisModeMark({ name, size = 56, className = "", label }: SvgProps & { name: AnalysisModeMarkName }) {
  return (
    <svg
      className={`analysis-mode-mark analysis-mode-mark-${name} ${className}`.trim()}
      viewBox="0 0 56 56"
      width={size}
      height={size}
      fill="none"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <rect className="analysis-mode-surface" x="6.5" y="8.5" width="42" height="42" rx="11" transform="rotate(-5 27.5 29.5)" />
      <rect className="analysis-mode-plate" x="9" y="5" width="42" height="42" rx="11" />
      {name === "fast" ? (
        <>
          <path className="analysis-mode-ink" d="M20 22v-4h4M36 18h4v4M20 34v4h4M40 34v4h-4" />
          <circle className="analysis-mode-lens" cx="30" cy="28" r="7" />
          <path className="analysis-mode-accent analysis-mode-scan" d="M18 28h24" />
          <circle className="analysis-mode-core" cx="30" cy="28" r="2.4" />
        </>
      ) : (
        <>
          <path className="analysis-mode-ink" d="m22.5 20.5 4.8 4.8m10.2-4.8-4.8 4.8m-10.2 10.2 4.8-4.8m10.2 4.8-4.8-4.8" />
          <circle className="analysis-mode-node" cx="21.5" cy="19.5" r="2.6" />
          <circle className="analysis-mode-node" cx="38.5" cy="19.5" r="2.6" />
          <circle className="analysis-mode-node" cx="21.5" cy="36.5" r="2.6" />
          <circle className="analysis-mode-node" cx="38.5" cy="36.5" r="2.6" />
          <circle className="analysis-mode-lens" cx="30" cy="28" r="6.5" />
          <circle className="analysis-mode-core" cx="30" cy="28" r="2.4" />
        </>
      )}
      <path className="analysis-mode-focus" d="M38 43h7" />
    </svg>
  );
}

export function CapabilityIcon({ name, size = 24, className = "", label }: SvgProps & { name: CapabilityIconName }) {
  const glyph = (() => {
    switch (name) {
      case "fast":
        return (
          <>
            <path d="M7.5 8V5.5H10M14 5.5h2.5V8M7.5 16v2.5H10M16.5 16v2.5H14" />
            <circle cx="12" cy="12" r="4.2" />
            <path className="brand-glyph-accent" d="M4.5 12h15" />
            <circle className="brand-glyph-fill" cx="12" cy="12" r="1.5" />
          </>
        );
      case "swarm":
        return (
          <>
            <circle cx="5" cy="6" r="2" />
            <circle cx="5" cy="18" r="2" />
            <circle cx="12" cy="12" r="2.35" />
            <circle className="brand-glyph-accent" cx="19" cy="12" r="2" />
            <path d="m6.7 7.1 3.3 3M6.7 16.9l3.3-3M14.35 12H17" />
          </>
        );
      case "image":
        return (
          <>
            <rect x="3" y="4" width="16" height="15" rx="2.5" />
            <circle className="brand-glyph-accent" cx="8" cy="9" r="1.5" />
            <path d="m5.5 16.5 4.2-4.3 2.8 2.7 2.35-2.2 2.15 2.2" />
            <circle cx="18.2" cy="17.2" r="2.8" />
            <path className="brand-glyph-accent" d="m20.2 19.2 1.5 1.5" />
          </>
        );
      case "video":
        return (
          <>
            <rect x="3" y="5" width="18" height="14" rx="2.5" />
            <path d="M7.5 5v14M16.5 5v14M3 9h4.5M16.5 9H21M3 15h4.5M16.5 15H21" />
            <path className="brand-glyph-accent" d="m10.5 9.2 4.2 2.8-4.2 2.8V9.2Z" />
          </>
        );
      case "document":
        return (
          <>
            <path d="M7 3.5h7l4 4V20a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z" />
            <path d="M14 3.5V8h4M8.7 11h6.6M8.7 14h3.6" />
            <rect className="brand-glyph-accent" x="13.5" y="13.5" width="5.5" height="4.5" rx="1" />
            <path d="m15 16 1-1 1.5 1.5" />
          </>
        );
      case "report":
        return (
          <>
            <path d="M6 3.5h9l3 3V19a2 2 0 0 1-2 2H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z" />
            <path d="M15 3.5V7h3M8 10h6M8 13h4" />
            <path className="brand-glyph-accent" d="m15.5 13.5 3 1.75V19l-3 1.75-3-1.75v-3.75l3-1.75Z" />
            <path d="m14.2 17.2.9.9 1.8-2" />
          </>
        );
      case "developer":
        return (
          <>
            <path d="m8 5-5 7 5 7M16 5l5 7-5 7" />
            <circle className="brand-glyph-accent" cx="12" cy="9.5" r="2" />
            <path className="brand-glyph-accent" d="M12 11.5v6M12 15.5h2.5" />
          </>
        );
      case "workflow":
        return (
          <>
            <rect x="3" y="4" width="5" height="5" rx="1.25" />
            <rect className="brand-glyph-accent" x="16" y="9.5" width="5" height="5" rx="1.25" />
            <rect x="3" y="15" width="5" height="5" rx="1.25" />
            <path d="M8 6.5h3a3 3 0 0 1 3 3V12h2M8 17.5h3a3 3 0 0 0 3-3V12" />
          </>
        );
      case "faq":
        return (
          <>
            <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v9a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4A2.5 2.5 0 0 1 3 14.5v-9" />
            <path className="brand-glyph-accent" d="M9.8 8.6a2.3 2.3 0 1 1 3.45 2c-.8.42-1.25.75-1.25 1.65M12 15h.01" />
          </>
        );
    }
  })();

  return <GlyphFrame name={name} size={size} className={className} label={label}>{glyph}</GlyphFrame>;
}

export function StatusIcon({ name, size = 24, className = "", label }: SvgProps & { name: BrandStatusIconName }) {
  const glyph = (() => {
    switch (name) {
      case "real":
        return <><circle cx="12" cy="12" r="8.5" /><path className="brand-glyph-accent" d="m8.3 12.2 2.3 2.3 5.2-5.2" /></>;
      case "fake":
        return <><path d="m12 3 9 9-9 9-9-9 9-9Z" /><path className="brand-glyph-accent" d="M8.8 15.2 15.2 8.8M8.8 8.8l6.4 6.4" /></>;
      case "processing":
        return <><circle className="brand-status-track" cx="12" cy="12" r="8.5" /><path className="brand-status-spinner" d="M12 3.5a8.5 8.5 0 0 1 8.5 8.5" /></>;
      case "warning":
        return <><path d="M10.25 4.4 2.9 17.1A2 2 0 0 0 4.65 20h14.7a2 2 0 0 0 1.75-2.9L13.75 4.4a2 2 0 0 0-3.5 0Z" /><path className="brand-glyph-accent" d="M12 8.5v4.5M12 16.5h.01" /></>;
      case "offline":
        return <><path d="M6.5 17.5h10a4 4 0 0 0 .65-7.95A5.5 5.5 0 0 0 6.8 8.2 4.7 4.7 0 0 0 6.5 17.5Z" /><path className="brand-glyph-accent" d="m4 4 16 16" /></>;
      case "upload":
        return <><path d="M5 15.5v3A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5v-3M12 16V4" /><path className="brand-glyph-accent" d="m7.5 8.5 4.5-4.5 4.5 4.5" /></>;
      case "extract":
        return <><path d="M5 3.5h9l4 4v5.5M14 3.5V8h4M7.5 12h4" /><rect x="5" y="15" width="5" height="5" rx="1" /><rect className="brand-glyph-accent" x="14" y="15" width="5" height="5" rx="1" /><path d="M12 13v7" /></>;
      case "partial":
        return <><circle cx="12" cy="12" r="8.5" /><path d="M12 3.5V12l6 6" /><path className="brand-glyph-accent" d="M12 12h8.5" /></>;
      case "error":
        return <><circle cx="12" cy="12" r="8.5" /><path className="brand-glyph-accent" d="m8.8 8.8 6.4 6.4M15.2 8.8l-6.4 6.4" /></>;
    }
  })();

  return <GlyphFrame name={`status-${name}`} size={size} className={`brand-status-icon ${className}`.trim()} label={label}>{glyph}</GlyphFrame>;
}

export interface UserAvatarProps {
  seed: string;
  displayName?: string;
  size?: number;
  className?: string;
  status?: "online" | "offline";
  label?: string;
}

export function UserAvatar({ seed, displayName, size = 40, className = "", status, label }: UserAvatarProps) {
  const name = displayName?.trim() || "用户";
  const avatarSource = useMemo(() => createHuijianAvatarDataUri(name, seed), [name, seed]);

  return (
    <span
      className={`brand-avatar brand-user-avatar ${className}`.trim()}
      style={{ width: size, height: size }}
      role="img"
      aria-label={label || `${name}的账户头像${status === "online" ? "，在线" : status === "offline" ? "，离线" : ""}`}
    >
      <img className="brand-user-avatar-art" src={avatarSource} alt="" aria-hidden="true" draggable={false} />
      {status && <i className={`brand-user-avatar-status is-${status}`} aria-hidden="true" />}
    </span>
  );
}

export interface AgentAvatarProps extends Omit<SvgProps, "label"> {
  state?: AgentAvatarState;
  label?: string;
}

export function AgentAvatar({ state = "idle", size = 40, className = "", label }: AgentAvatarProps) {
  const stateLabels: Record<AgentAvatarState, string> = {
    idle: "待命",
    receiving: "正在接收文件",
    processing: "正在分析",
    complete: "分析完成",
    error: "需要处理异常",
  };

  return (
    <span
      className={`brand-avatar brand-agent-avatar brand-agent-avatar-${state} ${className}`.trim()}
      style={{ width: size, height: size }}
      role="img"
      aria-label={label || `小鉴，${stateLabels[state]}`}
    >
      <svg className="brand-agent-portrait" viewBox="0 0 48 48" aria-hidden="true" focusable="false">
        <rect className="brand-agent-shell" x="4" y="4" width="40" height="40" rx="12" />
        <path className="brand-agent-panel brand-agent-panel-left" d="M11 15a3 3 0 0 1 3-3h7v8h-3a3 3 0 0 0-3 3v12a3 3 0 0 1-3 3h-1V15Z" />
        <path className="brand-agent-panel brand-agent-panel-right" d="M37 15a3 3 0 0 0-3-3h-7v8h3a3 3 0 0 1 3 3v12a3 3 0 0 0 3 3h1V15Z" />
        <circle className="brand-agent-lens" cx="24" cy="24" r="8" />
        <circle className="brand-agent-lens-core" cx="24" cy="24" r="4.8" />
        <circle className="brand-agent-glint" cx="22.2" cy="22" r="1.25" />
        <path className="brand-agent-focus" d="M15 40h18" />
      </svg>
      <i className="brand-agent-state-emblem" aria-hidden="true" />
    </span>
  );
}

interface IdentityProps extends SvgProps {
  tone?: "blue" | "green" | "violet";
}

function IdentityFrame({ size = 40, className = "", label, tone = "blue", children, name }: IdentityProps & { children: ReactNode; name: string }) {
  return (
    <svg
      className={`brand-avatar brand-identity-avatar brand-identity-${name} brand-identity-${tone} ${className}`.trim()}
      viewBox="0 0 40 40"
      width={size}
      height={size}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <rect className="brand-identity-surface" x="3" y="3" width="34" height="34" rx="9" />
      <g className="brand-identity-glyph" fill="none" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        {children}
      </g>
    </svg>
  );
}

export function AdminIdentity({ label = "管理员身份", ...props }: IdentityProps) {
  return (
    <IdentityFrame {...props} name="admin" label={label} tone="violet">
      <path d="M12 14h16M12 20h16M12 26h16" />
      <circle cx="17" cy="14" r="2" />
      <circle cx="24" cy="20" r="2" />
      <circle cx="15" cy="26" r="2" />
    </IdentityFrame>
  );
}

export function OrganizationIdentity({ label = "组织身份", ...props }: IdentityProps) {
  return (
    <IdentityFrame {...props} name="organization" label={label} tone="green">
      <rect x="17" y="10" width="6" height="6" rx="1.5" />
      <rect x="10" y="24" width="6" height="6" rx="1.5" />
      <rect x="24" y="24" width="6" height="6" rx="1.5" />
      <path d="M20 16v4M13 24v-4h14v4" />
    </IdentityFrame>
  );
}

export function ApiIdentity({ label = "API 应用身份", ...props }: IdentityProps) {
  return (
    <IdentityFrame {...props} name="api" label={label} tone="blue">
      <path d="m15 12-5 8 5 8M25 12l5 8-5 8" />
      <circle cx="20" cy="16" r="1.75" />
      <circle cx="20" cy="24" r="1.75" />
      <path d="M20 17.75v4.5" />
    </IdentityFrame>
  );
}
