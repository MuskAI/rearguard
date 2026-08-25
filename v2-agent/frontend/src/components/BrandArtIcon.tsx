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
      <i className="brand-art-icon-rear" aria-hidden="true" />
      <i className="brand-art-icon-front" aria-hidden="true" />
      <CapabilityIcon name={name} />
      <i className="brand-art-icon-focus" aria-hidden="true" />
    </span>
  );
}
