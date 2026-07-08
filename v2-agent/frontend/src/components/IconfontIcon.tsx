import type { ReactNode } from "react";

export type IconfontName =
  | "archive"
  | "close"
  | "deep-analysis"
  | "history"
  | "image-forensics"
  | "plus"
  | "report"
  | "search"
  | "shield-check"
  | "video-forensics";

interface Props {
  name: IconfontName;
  size?: number;
  strokeWidth?: number;
  className?: string;
}

const glyphs: Record<IconfontName, ReactNode> = {
  archive: (
    <>
      <path d="M5 7.5h14" />
      <path d="M6.2 7.5v10.4c0 .9.7 1.6 1.6 1.6h8.4c.9 0 1.6-.7 1.6-1.6V7.5" />
      <path d="M8 4.5h8l1.4 3H6.6l1.4-3Z" />
      <path d="M9.2 11h5.6" />
      <path d="M9.2 14h3.5" />
    </>
  ),
  close: (
    <>
      <path d="M7 7l10 10" />
      <path d="M17 7 7 17" />
    </>
  ),
  "deep-analysis": (
    <>
      <path d="m4 7.5 8-4 8 4-8 4-8-4Z" />
      <path d="m4 12 8 4 8-4" />
      <path d="m4 16.5 8 4 8-4" />
      <path d="M12 8.7v3" />
      <path d="M16.2 17.2h2" />
    </>
  ),
  history: (
    <>
      <path d="M4.8 7.7A8 8 0 1 1 4 12" />
      <path d="M4 4.8v3.7h3.7" />
      <path d="M12 8v4.4l3 1.7" />
      <path d="M9.1 18.5h5.8" />
    </>
  ),
  "image-forensics": (
    <>
      <rect x="4.5" y="5" width="15" height="14" rx="2.2" />
      <path d="m7.4 15.8 3.4-3.6 2.5 2.5 1.5-1.5 2.2 2.6" />
      <circle cx="9.2" cy="8.8" r="1.1" />
      <path d="M15.8 7.8h1.8" />
      <path d="M16.7 6.9v1.8" />
    </>
  ),
  plus: (
    <>
      <circle cx="12" cy="12" r="7.2" />
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </>
  ),
  report: (
    <>
      <path d="M7 4.5h6l4 4v10c0 .8-.7 1.5-1.5 1.5H7c-.8 0-1.5-.7-1.5-1.5V6c0-.8.7-1.5 1.5-1.5Z" />
      <path d="M13 4.5v4h4" />
      <path d="M8.5 12h7" />
      <path d="M8.5 15h5" />
      <path d="M8.5 18h3" />
    </>
  ),
  search: (
    <>
      <circle cx="10.8" cy="10.8" r="5.8" />
      <path d="m15.1 15.1 4.4 4.4" />
      <path d="M8.2 10.8h5.2" />
      <path d="M10.8 8.2v5.2" />
    </>
  ),
  "shield-check": (
    <>
      <path d="M12 3.6 5.5 6.3v5.4c0 4.1 2.7 7.5 6.5 8.7 3.8-1.2 6.5-4.6 6.5-8.7V6.3L12 3.6Z" />
      <path d="m8.8 12.1 2.1 2.1 4.4-4.6" />
    </>
  ),
  "video-forensics": (
    <>
      <rect x="4.5" y="6" width="15" height="12" rx="2.1" />
      <path d="M8 6v12" />
      <path d="M16 6v12" />
      <path d="m11.2 9.5 3.4 2.5-3.4 2.5v-5Z" />
      <path d="M6.3 9h1.7" />
      <path d="M16 15h1.7" />
    </>
  ),
};

export default function IconfontIcon({ name, size = 20, strokeWidth = 1.9, className = "" }: Props) {
  return (
    <svg
      className={`iconfont-svg${className ? ` ${className}` : ""}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {glyphs[name]}
    </svg>
  );
}
