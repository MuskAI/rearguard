import { CapabilityIcon, CapabilityIconName } from "./BrandSystem";

export type BrandArtIconName = CapabilityIconName;

interface Props {
  name: BrandArtIconName;
  className?: string;
  label?: string;
}

export default function BrandArtIcon({ name, className = "", label }: Props) {
  return (
    <span
      className={`brand-art-icon brand-system-c-icon brand-art-icon-${name} ${className}`.trim()}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <CapabilityIcon name={name} />
    </span>
  );
}
