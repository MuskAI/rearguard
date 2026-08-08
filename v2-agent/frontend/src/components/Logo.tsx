interface Props {
  size?: number;
  idSuffix?: string;
  className?: string;
}

export default function Logo({ size = 40, idSuffix = "0", className }: Props) {
  return (
    <img
      src="/brand/huijian-mark-v3.webp"
      width={size}
      height={size}
      className={className}
      alt="慧鉴AI"
      title={`慧鉴AI ${idSuffix}`}
    />
  );
}
