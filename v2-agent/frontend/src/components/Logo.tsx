interface Props {
  size?: number;
  idSuffix?: string;
  className?: string;
}

/** 鉴真品牌标识：印章方圆 + 取证焦点角 + 「真」字。 */
export default function Logo({ size = 40, idSuffix = "0", className }: Props) {
  const g = `jz-grad-${idSuffix}`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label="鉴真"
    >
      <defs>
        <linearGradient id={g} x1="6" y1="6" x2="58" y2="58" gradientUnits="userSpaceOnUse">
          <stop stopColor="#e8654f" />
          <stop offset="1" stopColor="#a82d20" />
        </linearGradient>
      </defs>

      {/* 印章外框（方圆结合） */}
      <rect x="4.5" y="4.5" width="55" height="55" rx="15" stroke={`url(#${g})`} strokeWidth="3.5" />

      {/* 取证焦点角（扫描取景框） */}
      <g stroke={`url(#${g})`} strokeWidth="2.4" strokeLinecap="round">
        <path d="M16 22 L16 16 L22 16" />
        <path d="M48 22 L48 16 L42 16" />
        <path d="M16 42 L16 48 L22 48" />
        <path d="M48 42 L48 48 L42 48" />
      </g>

      {/* 真 */}
      <text
        x="32"
        y="34.5"
        textAnchor="middle"
        dominantBaseline="central"
        fontSize="25"
        fontWeight={800}
        fill={`url(#${g})`}
        fontFamily="'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif"
      >
        真
      </text>
    </svg>
  );
}
