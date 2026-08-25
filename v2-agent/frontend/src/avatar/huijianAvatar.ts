import { createAvatar, type Style } from "@dicebear/core";

type HuijianAvatarOptions = Record<never, never>;

interface AvatarPalette {
  paper: string;
  paperEdge: string;
  rear: string;
  rearEdge: string;
  ink: string;
  primary: string;
  primaryDeep: string;
  secondary: string;
  secondarySoft: string;
}

const PALETTES: AvatarPalette[] = [
  {
    paper: "#f8fafb",
    paperEdge: "#cad5dc",
    rear: "#dfe8f2",
    rearEdge: "#b9c9dc",
    ink: "#172129",
    primary: "#2869df",
    primaryDeep: "#174aaf",
    secondary: "#078f80",
    secondarySoft: "#ccebe5",
  },
  {
    paper: "#f9fbfa",
    paperEdge: "#c7d8d4",
    rear: "#dcece8",
    rearEdge: "#accfc7",
    ink: "#18242a",
    primary: "#087f76",
    primaryDeep: "#075f59",
    secondary: "#376fd2",
    secondarySoft: "#d9e5fa",
  },
  {
    paper: "#fafafa",
    paperEdge: "#d1d7dc",
    rear: "#e5e9ef",
    rearEdge: "#c2cbd4",
    ink: "#171d22",
    primary: "#3566bd",
    primaryDeep: "#244a8e",
    secondary: "#c85747",
    secondarySoft: "#f2dcd7",
  },
  {
    paper: "#f8fbfc",
    paperEdge: "#c7d9df",
    rear: "#dbeaf0",
    rearEdge: "#aecbd5",
    ink: "#19232a",
    primary: "#157b91",
    primaryDeep: "#0d596b",
    secondary: "#4468c2",
    secondarySoft: "#dce4f7",
  },
  {
    paper: "#fbfaf7",
    paperEdge: "#d7d2c7",
    rear: "#ebe7de",
    rearEdge: "#cec4b0",
    ink: "#202326",
    primary: "#48669f",
    primaryDeep: "#334a77",
    secondary: "#aa7132",
    secondarySoft: "#eee2cf",
  },
] as const;

const FRAME_ANGLES = [-8, -5, -2, 3, 6, 9] as const;
const LENS_Y = [43, 46, 49, 52] as const;
const ARC_ROTATIONS = [-24, 18, 62, 108, 154] as const;

function signatureMarkup(index: number, x: number, color: string) {
  const origin = `translate(${x} 31)`;
  const common = `fill="none" stroke="${color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"`;
  const marks = [
    `<g transform="${origin}" ${common}><path d="M0 1h11M0 7h8M0 13h11" /></g>`,
    `<g transform="${origin}" fill="${color}"><circle cx="2" cy="2" r="2"/><circle cx="9" cy="7" r="2"/><circle cx="2" cy="12" r="2"/></g>`,
    `<g transform="${origin}" ${common}><path d="m0 12 4-5 3 3 5-8" /></g>`,
    `<g transform="${origin}" ${common}><path d="M1 1v12M6 4v9M11 0v13" /></g>`,
    `<g transform="${origin}" ${common}><path d="M1 12A11 11 0 0 1 12 1M2 7h5V2" /></g>`,
    `<g transform="${origin}" ${common}><path d="M0 3h5v5h6M0 12h11" /></g>`,
  ];
  return marks[index % marks.length];
}

export const huijianAvatarStyle: Style<HuijianAvatarOptions> = {
  meta: {
    title: "Huijian Evidence ID",
    creator: "Huijian AI",
    source: "Huijian Focus Layers design system",
    license: {
      name: "Proprietary artwork, DiceBear core under MIT",
      url: "https://github.com/dicebear/dicebear",
    },
  },
  create({ prng }) {
    const palette = prng.pick(PALETTES, PALETTES[0]);
    const frameAngle = prng.pick([...FRAME_ANGLES], 0);
    const lensOnLeft = prng.bool(50);
    const lensX = lensOnLeft ? 40 : 60;
    const lensY = prng.pick([...LENS_Y], 47);
    const signatureX = lensOnLeft ? 61 : 28;
    const signatureIndex = prng.integer(0, 5);
    const arcRotation = prng.pick([...ARC_ROTATIONS], 18);
    const frameRadius = prng.pick([12, 15, 18, 21], 15);
    const glassId = `hj-glass-${prng.string(8, "abcdef0123456789")}`;
    const surfaceId = `hj-surface-${prng.string(8, "abcdef0123456789")}`;
    const clipId = `hj-clip-${prng.string(8, "abcdef0123456789")}`;

    return {
      attributes: {
        viewBox: "0 0 100 100",
        width: "100",
        height: "100",
        fill: "none",
        xmlns: "http://www.w3.org/2000/svg",
      },
      body: `
        <defs>
          <linearGradient id="${surfaceId}" x1="20" y1="16" x2="79" y2="84" gradientUnits="userSpaceOnUse">
            <stop stop-color="#ffffff"/>
            <stop offset="1" stop-color="${palette.paper}"/>
          </linearGradient>
          <radialGradient id="${glassId}" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(${lensX - 5} ${lensY - 7}) rotate(49) scale(30)">
            <stop stop-color="#ffffff" stop-opacity=".96"/>
            <stop offset=".22" stop-color="${palette.primary}" stop-opacity=".78"/>
            <stop offset=".68" stop-color="${palette.primaryDeep}"/>
            <stop offset="1" stop-color="${palette.ink}"/>
          </radialGradient>
          <clipPath id="${clipId}"><circle cx="50" cy="50" r="47"/></clipPath>
        </defs>
        <g clip-path="url(#${clipId})">
          <circle cx="50" cy="50" r="47" fill="#f4f7f8"/>
          <path d="M13 63 56 7h34v56L46 96H13Z" fill="${palette.secondarySoft}" opacity=".72"/>
          <g transform="rotate(${frameAngle} 50 50)">
            <rect x="25" y="20" width="54" height="64" rx="${frameRadius}" fill="${palette.rear}" stroke="${palette.rearEdge}" stroke-width="2"/>
            <rect x="19" y="16" width="58" height="66" rx="${frameRadius}" fill="url(#${surfaceId})" stroke="${palette.paperEdge}" stroke-width="2"/>
            <path d="M30 23h36" stroke="#ffffff" stroke-width="2" stroke-linecap="round" opacity=".9"/>
            <path d="M27 72h42" stroke="${palette.paperEdge}" stroke-width="1.5" stroke-linecap="round" opacity=".7"/>
          </g>
          <g transform="rotate(${arcRotation} 50 50)">
            <path d="M19 39A34 34 0 0 1 39 19" stroke="${palette.primary}" stroke-width="4.5" stroke-linecap="round"/>
            <path d="M81 61A34 34 0 0 1 61 81" stroke="${palette.secondary}" stroke-width="4.5" stroke-linecap="round"/>
          </g>
          ${signatureMarkup(signatureIndex, signatureX, palette.ink)}
          <circle cx="${lensX}" cy="${lensY}" r="21" fill="${palette.paper}" stroke="${palette.ink}" stroke-width="2.6"/>
          <circle cx="${lensX}" cy="${lensY}" r="16.5" fill="url(#${glassId})" stroke="${palette.primary}" stroke-width="2"/>
          <circle cx="${lensX}" cy="${lensY}" r="8.5" fill="${palette.primaryDeep}" stroke="#ffffff" stroke-opacity=".72" stroke-width="1.4"/>
          <circle cx="${lensX}" cy="${lensY}" r="3.8" fill="${palette.ink}"/>
          <ellipse cx="${lensX - 5}" cy="${lensY - 6}" rx="4.2" ry="2.6" fill="#ffffff" opacity=".82" transform="rotate(-28 ${lensX - 5} ${lensY - 6})"/>
          <path d="M${lensX - 21} ${lensY - 12}v-7h7M${lensX + 21} ${lensY + 12}v7h-7" stroke="${palette.secondary}" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/>
        </g>
        <circle cx="50" cy="50" r="47" stroke="${palette.ink}" stroke-opacity=".16" stroke-width="1.5"/>
      `,
      extra: () => ({
        palette: palette.primary,
        lensPosition: lensOnLeft ? "left" : "right",
        signature: signatureIndex,
      }),
    };
  },
};

const avatarCache = new Map<string, string>();

export function createHuijianAvatarDataUri(displayName: string, identitySeed: string) {
  const normalizedName = displayName.normalize("NFKC").trim().toLocaleLowerCase("zh-CN") || "慧鉴用户";
  const cacheKey = `${normalizedName}:${identitySeed}`;
  const cached = avatarCache.get(cacheKey);
  if (cached) return cached;

  const dataUri = createAvatar(huijianAvatarStyle, {
    seed: cacheKey,
    size: 128,
  }).toDataUri();

  if (avatarCache.size >= 128) {
    const oldestKey = avatarCache.keys().next().value;
    if (oldestKey) avatarCache.delete(oldestKey);
  }
  avatarCache.set(cacheKey, dataUri);
  return dataUri;
}
