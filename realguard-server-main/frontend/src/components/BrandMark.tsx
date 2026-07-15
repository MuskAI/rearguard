type Props = {
  size?: number;
  className?: string;
  label?: string;
};

export default function BrandMark({ size = 32, className = "", label = "慧鉴 AI" }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label={label}
    >
      <rect x="4" y="4" width="40" height="40" rx="11" fill="var(--bg-card, #fff)" stroke="#16324A" strokeWidth="2.5" />
      <path d="M14 19V14H19M29 14H34V19M34 29V34H29M19 34H14V29" stroke="#1F5F7A" strokeWidth="2.2" strokeLinecap="round" />
      <circle cx="24" cy="24" r="8.5" fill="#DDF2EC" stroke="#16324A" strokeWidth="2.2" />
      <circle cx="24" cy="24" r="4.2" fill="#1B8F7A" />
      <circle cx="22.7" cy="22.5" r="1.2" fill="#F7F7F2" />
      <path d="M32.5 34.5h6v6h-6z" fill="#D9573F" stroke="#16324A" strokeWidth="1.5" />
      <path d="m34.1 37.4 1.2 1.2 2-2.2" stroke="#fff" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
