export type BrandArtIconName =
  | "fast"
  | "swarm"
  | "image"
  | "video"
  | "document"
  | "report"
  | "developer"
  | "workflow"
  | "faq";

interface Props {
  name: BrandArtIconName;
  className?: string;
  label?: string;
}

export default function BrandArtIcon({ name, className = "", label }: Props) {
  return (
    <span
      className={`brand-art-icon brand-art-icon-${name} ${className}`.trim()}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}
