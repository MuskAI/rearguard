interface Props {
  size?: number;
  idSuffix?: string;
  className?: string;
}

/** 慧鉴品牌标识：取证焦点、校验镜头与朱砂确认章。 */
export default function Logo({ size = 40, idSuffix = "0", className }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label="慧鉴 AI"
    >
      <title>{`慧鉴 AI ${idSuffix}`}</title>
      <rect x="5" y="5" width="54" height="54" rx="15" fill="#f7f7f2" stroke="#16324a" strokeWidth="3" />
      <path d="M18 25v-7h7m14 0h7v7m0 14v7h-7m-14 0h-7v-7" stroke="#1f5f7a" strokeWidth="3" strokeLinecap="round" />
      <circle cx="32" cy="32" r="11.5" fill="#ddf2ec" stroke="#16324a" strokeWidth="3" />
      <circle cx="32" cy="32" r="5.6" fill="#1b8f7a" />
      <circle cx="30" cy="30" r="1.6" fill="#ffffff" />
      <path d="M43 45h9v9h-9z" fill="#d9573f" stroke="#16324a" strokeWidth="2" />
      <path d="m45.2 49 1.7 1.7 3-3.2" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
