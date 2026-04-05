const TIER_COLORS: Record<string, { fill: string; stroke: string }> = {
  node: { fill: "#92400e", stroke: "#d97706" },
  edge: { fill: "#374151", stroke: "#9ca3af" },
  lattice: { fill: "#78350f", stroke: "#f59e0b" },
  single: { fill: "#4c1d95", stroke: "#8b5cf6" },
  locked: { fill: "#1f2937", stroke: "#4b5563" },
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
  // Pointy-top hexagon
  const points = [0, 1, 2, 3, 4, 5]
    .map((i) => {
      const angle = (Math.PI / 3) * i - Math.PI / 2;
      return `${half + half * 0.9 * Math.cos(angle)},${half + half * 0.9 * Math.sin(angle)}`;
    })
    .join(" ");

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      onClick={onClick}
      className={`${onClick ? "cursor-pointer" : ""} ${className}`}
      role={onClick ? "button" : undefined}
    >
      <polygon
        points={points}
        fill={colors.fill}
        stroke={colors.stroke}
        strokeWidth={2}
        opacity={tier ? 1 : 0.4}
      />
      <text
        x={half}
        y={half + 1}
        textAnchor="middle"
        dominantBaseline="central"
        fill={tier ? "#e5e7eb" : "#6b7280"}
        fontSize={size * 0.22}
        fontWeight="bold"
      >
        {iconSlug.slice(0, 3).toUpperCase()}
      </text>
    </svg>
  );
}
