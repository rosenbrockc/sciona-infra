const TIER_COLORS: Record<string, { fill: string; stroke: string; glow: string }> = {
  node: { fill: "#78350f", stroke: "#d97706", glow: "rgba(217, 119, 6, 0.2)" },
  edge: { fill: "#27303f", stroke: "#94a3b8", glow: "rgba(148, 163, 184, 0.15)" },
  lattice: { fill: "#713f12", stroke: "#eab308", glow: "rgba(234, 179, 8, 0.25)" },
  single: { fill: "#3b1d6e", stroke: "#a78bfa", glow: "rgba(167, 139, 250, 0.2)" },
  locked: { fill: "#111827", stroke: "#334155", glow: "none" },
};

interface HexBadgeProps {
  iconSlug: string;
  tier: string | null;
  size?: number;
  onClick?: () => void;
  className?: string;
}

export default function HexBadge({ iconSlug, tier, size = 48, onClick, className = "" }: HexBadgeProps) {
  const colors = TIER_COLORS[tier ?? "locked"] ?? TIER_COLORS.locked;
  const half = size / 2;
  const r = half * 0.88;
  const points = [0, 1, 2, 3, 4, 5]
    .map((i) => {
      const angle = (Math.PI / 3) * i - Math.PI / 2;
      return `${half + r * Math.cos(angle)},${half + r * Math.sin(angle)}`;
    })
    .join(" ");

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      onClick={onClick}
      className={`shrink-0 ${onClick ? "cursor-pointer hover:scale-110 transition-transform duration-150" : ""} ${className}`}
      role={onClick ? "button" : undefined}
    >
      {tier && colors.glow !== "none" && (
        <polygon points={points} fill={colors.glow} filter="url(#blur)" />
      )}
      <defs>
        <filter id="blur"><feGaussianBlur stdDeviation="2" /></filter>
      </defs>
      <polygon
        points={points}
        fill={colors.fill}
        stroke={colors.stroke}
        strokeWidth={1.5}
        opacity={tier ? 1 : 0.35}
      />
      <text
        x={half}
        y={half + 1}
        textAnchor="middle"
        dominantBaseline="central"
        fill={tier ? "#e2e8f0" : "#4b5563"}
        fontSize={size * 0.2}
        fontWeight="600"
        fontFamily="Inter, system-ui, sans-serif"
      >
        {iconSlug.slice(0, 3).toUpperCase()}
      </text>
    </svg>
  );
}
