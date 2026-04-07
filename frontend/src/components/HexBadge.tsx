/**
 * HexBadge — Inspired by the brushed-metal hexagonal badge concept art.
 * Node = copper frame + navy interior, Edge = silver frame + cyan glow,
 * Lattice = gold frame + ornate, single = dark steel, locked = dim.
 */

const TIER_STYLES: Record<string, {
  frameFill: string; frameStroke: string;
  innerFill: string; innerStroke: string;
  textFill: string; glowColor: string; glowOpacity: number;
}> = {
  node: {
    frameFill: "#7a4a2a", frameStroke: "#c87a50",
    innerFill: "#0c1428", innerStroke: "#1e3a5f",
    textFill: "#d4a07a", glowColor: "#c87a50", glowOpacity: 0.15,
  },
  edge: {
    frameFill: "#3a4560", frameStroke: "#94a3b8",
    innerFill: "#0c1428", innerStroke: "#2a4a6a",
    textFill: "#7dd3fc", glowColor: "#38bdf8", glowOpacity: 0.25,
  },
  lattice: {
    frameFill: "#5a4210", frameStroke: "#d4a843",
    innerFill: "#0a1230", innerStroke: "#2a3a68",
    textFill: "#d4a843", glowColor: "#d4a843", glowOpacity: 0.3,
  },
  single: {
    frameFill: "#2a2040", frameStroke: "#7c6aad",
    innerFill: "#0c0c20", innerStroke: "#3a2a5a",
    textFill: "#a78bfa", glowColor: "#a78bfa", glowOpacity: 0.2,
  },
  locked: {
    frameFill: "#141a36", frameStroke: "#253262",
    innerFill: "#0a0e20", innerStroke: "#1a2244",
    textFill: "#3a4570", glowColor: "#000000", glowOpacity: 0,
  },
};

// Simple geometric icon paths per badge (sacred geometry inspired)
const ICON_PATHS: Record<string, string> = {
  pro: "M0-6L5.2 3L-5.2 3Z M0 6L-5.2-3L5.2-3Z", // Star of David — prolific
  key: "M-4-4H4V4H-4Z M0-6V6 M-6 0H6", // Sacred cross — keystone
  sov: "M0-6L2-2 6 0 2 2 0 6-2 2-6 0-2-2Z", // 8-point star — sovereign
  lau: "M-3-5V5 M0-6V6 M3-5V5 M-5 3H5", // Column — laureate
  dea: "M-5 0H-1V-4H3V0H5V4H-5Z", // Maze arrow — deadend
  tit: "M0-6L6 4H-6Z", // Mountain — titan
  syn: "M0-5L4.8 1.5 2.9 6H-2.9L-4.8 1.5Z", // Pentagon — synthesizer
  pol: "M-4-4H4V4H-4Z M-2-2H2V2H-2Z", // Nested squares — polymath
  anv: "M-4 2H4 M-3-1H3V2H-3Z M-1-4H1V-1", // Anvil — anvil
  cha: "M-4 0A4 4 0 018 0 M0-4V4 M4-2L0 2L-4-2", // Chain — chain_reaction
  rai: "M0-5C2-3 5-1 5 2S0 6 0 6S-5 3-5 2C-5-1-2-3 0-5Z", // Drop — rainmaker
  lab: "M-5 3H5 M-4-3H4 M-3-1H3V3", // Building — lab_director
  gra: "M-2-4L0-6L2-4 M-3-1H3 M-4 2L0 5L4 2", // Shovel — graverobber
  fra: "M-4-3H4V3H-4Z M-3-4V4 M3-4V4 M-5 0H5", // Stitch grid — frankenstein
};

function getIconPath(slug: string): string {
  const key = slug.slice(0, 3);
  return ICON_PATHS[key] ?? "M-3-3H3V3H-3Z";
}

interface HexBadgeProps {
  iconSlug: string;
  tier: string | null;
  size?: number;
  onClick?: () => void;
  className?: string;
}

export default function HexBadge({ iconSlug, tier, size = 48, onClick, className = "" }: HexBadgeProps) {
  const style = TIER_STYLES[tier ?? "locked"] ?? TIER_STYLES.locked;
  const half = size / 2;
  const uid = `hex-${iconSlug}-${tier}-${size}`;

  // Outer frame hex (larger)
  const outerR = half * 0.92;
  const outerPts = hexPoints(half, half, outerR);
  // Inner face hex (smaller, the "brushed metal" interior)
  const innerR = half * 0.68;
  const innerPts = hexPoints(half, half, innerR);

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      onClick={onClick}
      className={`shrink-0 ${onClick ? "cursor-pointer hover:scale-110 transition-transform duration-200" : ""} ${className}`}
      role={onClick ? "button" : undefined}
    >
      <defs>
        {/* Glow filter for earned badges */}
        <filter id={`${uid}-glow`}>
          <feGaussianBlur stdDeviation="2.5" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
        {/* Brushed metal texture */}
        <linearGradient id={`${uid}-brushed`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor={style.innerFill} />
          <stop offset="40%" stopColor={style.innerStroke} stopOpacity="0.3" />
          <stop offset="60%" stopColor={style.innerFill} />
          <stop offset="100%" stopColor={style.innerStroke} stopOpacity="0.2" />
        </linearGradient>
        {/* Frame gradient */}
        <linearGradient id={`${uid}-frame`} x1="0" y1="0" x2="0.5" y2="1">
          <stop offset="0%" stopColor={style.frameStroke} />
          <stop offset="50%" stopColor={style.frameFill} />
          <stop offset="100%" stopColor={style.frameStroke} stopOpacity="0.7" />
        </linearGradient>
      </defs>

      {/* Glow behind (earned only) */}
      {tier && style.glowOpacity > 0 && (
        <polygon
          points={outerPts}
          fill={style.glowColor}
          opacity={style.glowOpacity}
          filter={`url(#${uid}-glow)`}
        />
      )}

      {/* Outer frame */}
      <polygon
        points={outerPts}
        fill={`url(#${uid}-frame)`}
        stroke={style.frameStroke}
        strokeWidth={1}
        opacity={tier ? 1 : 0.3}
      />

      {/* Inner face — brushed metal */}
      <polygon
        points={innerPts}
        fill={`url(#${uid}-brushed)`}
        stroke={style.innerStroke}
        strokeWidth={0.5}
        opacity={tier ? 1 : 0.25}
      />

      {/* Geometric icon */}
      <g
        transform={`translate(${half}, ${half}) scale(${size / 52})`}
        opacity={tier ? 0.85 : 0.2}
      >
        <path
          d={getIconPath(iconSlug)}
          fill="none"
          stroke={style.textFill}
          strokeWidth={1.2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </g>

      {/* Corner accents for Lattice tier */}
      {tier === "lattice" && (
        <>
          {[0, 1, 2, 3, 4, 5].map((i) => {
            const angle = (Math.PI / 3) * i - Math.PI / 2;
            const cx = half + outerR * 0.85 * Math.cos(angle);
            const cy = half + outerR * 0.85 * Math.sin(angle);
            return (
              <circle key={i} cx={cx} cy={cy} r={size * 0.025} fill="#d4a843" opacity={0.8} />
            );
          })}
        </>
      )}
    </svg>
  );
}

function hexPoints(cx: number, cy: number, r: number): string {
  return [0, 1, 2, 3, 4, 5]
    .map((i) => {
      const angle = (Math.PI / 3) * i - Math.PI / 2;
      return `${cx + r * Math.cos(angle)},${cy + r * Math.sin(angle)}`;
    })
    .join(" ");
}
