import type { ReactNode } from "react";

export type IconfontName =
  | "archive"
  | "bolt"
  | "brand"
  | "chevron-down"
  | "close"
  | "deep-analysis"
  | "history"
  | "home"
  | "image-forensics"
  | "logout"
  | "menu"
  | "moon"
  | "report"
  | "shield-check"
  | "sun"
  | "user"
  | "video-forensics"
  | "expert-review";

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
  bolt: (
    <>
      <path d="M13 3.6 6.5 13h4.2L9.9 20.4l7-10.3h-4.3L13 3.6Z" />
      <path d="M17.6 4.8h1.7" />
      <path d="M18.4 4v1.7" />
      <path d="M5.3 18.2h1.6" />
      <path d="M6.1 17.4V19" />
    </>
  ),
  brand: (
    <>
      <path d="M4.3 12s2.8-5.2 7.7-5.2S19.7 12 19.7 12 16.9 17.2 12 17.2 4.3 12 4.3 12Z" />
      <circle cx="12" cy="12" r="2.6" />
      <path d="M6.6 5.2V3.9h2" />
      <path d="M17.4 5.2V3.9h-2" />
      <path d="M6.6 18.8v1.3h2" />
      <path d="M17.4 18.8v1.3h-2" />
    </>
  ),
  "chevron-down": <path d="m7 9.5 5 5 5-5" />,
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
  home: (
    <>
      <path d="m4.5 11.2 7.5-6.5 7.5 6.5" />
      <path d="M6.4 10v8.4c0 .7.5 1.2 1.2 1.2h8.8c.7 0 1.2-.5 1.2-1.2V10" />
      <path d="M10 19.6v-5.2h4v5.2" />
    </>
  ),
  "image-forensics": (
    <>
      <rect x="4.5" y="5" width="15" height="14" rx="2.2" />
      <path d="m7.4 15.8 3.4-3.6 2.5 2.5 1.5-1.5 2.2 2.6" />
      <circle cx="9.2" cy="8.8" r="1.1" />
      <path d="M15.8 7.8h1.8" />
      <path d="M16.7 6.9v1.8" />
      <path d="M7 4.1V3h2.2" />
      <path d="M17 20.9V22h-2.2" />
    </>
  ),
  logout: (
    <>
      <path d="M10.2 6.2H6.8c-.8 0-1.4.6-1.4 1.4v8.8c0 .8.6 1.4 1.4 1.4h3.4" />
      <path d="M13.2 8.2 17 12l-3.8 3.8" />
      <path d="M9.4 12H17" />
    </>
  ),
  menu: (
    <>
      <path d="M5 7h14" />
      <path d="M5 12h14" />
      <path d="M5 17h14" />
    </>
  ),
  moon: (
    <>
      <path d="M18.6 14.7A7 7 0 0 1 9.3 5.4 7.8 7.8 0 1 0 18.6 14.7Z" />
      <path d="M16.8 5.2h1.6" />
      <path d="M17.6 4.4V6" />
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
  "shield-check": (
    <>
      <path d="M12 3.6 5.5 6.3v5.4c0 4.1 2.7 7.5 6.5 8.7 3.8-1.2 6.5-4.6 6.5-8.7V6.3L12 3.6Z" />
      <path d="m8.8 12.1 2.1 2.1 4.4-4.6" />
      <path d="M9 6.9h6" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 3.5v2" />
      <path d="M12 18.5v2" />
      <path d="M3.5 12h2" />
      <path d="M18.5 12h2" />
      <path d="m5.8 5.8 1.4 1.4" />
      <path d="m16.8 16.8 1.4 1.4" />
      <path d="m18.2 5.8-1.4 1.4" />
      <path d="m7.2 16.8-1.4 1.4" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8.4" r="3.1" />
      <path d="M5.5 19.5c.8-3.2 3.1-5 6.5-5s5.7 1.8 6.5 5" />
      <path d="M8 19.5h8" />
    </>
  ),
  "video-forensics": (
    <>
      <rect x="4.5" y="6" width="15" height="12" rx="2.1" />
      <path d="M8 6v12" />
      <path d="M16 6v12" />
      <path d="m11.2 9.5 3.4 2.5-3.4 2.5v-5Z" />
      <path d="M6.3 9h1.7" />
      <path d="M6.3 15h1.7" />
      <path d="M16 9h1.7" />
      <path d="M16 15h1.7" />
    </>
  ),
  "expert-review": (
    <>
      <circle cx="12" cy="12" r="3.4" />
      <path d="M12 4v2.2" />
      <path d="M12 17.8V20" />
      <path d="M4 12h2.2" />
      <path d="M17.8 12H20" />
      <path d="m6.3 6.3 1.6 1.6" />
      <path d="m16.1 16.1 1.6 1.6" />
      <path d="m17.7 6.3-1.6 1.6" />
      <path d="m7.9 16.1-1.6 1.6" />
      <path d="M10.4 12.2 11.5 13.3 14 10.7" />
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
