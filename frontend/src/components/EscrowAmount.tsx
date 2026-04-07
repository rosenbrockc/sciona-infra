import { formatUsd } from "../utils/format";

interface EscrowAmountProps {
  amount: number;
  className?: string;
}

/**
 * Tier-colored escrow display.
 * >$10,000 = gold (lattice), >$1,000 = silver (edge), rest = bronze (node)
 */
export default function EscrowAmount({ amount, className = "" }: EscrowAmountProps) {
  const tier =
    amount >= 10000 ? "gold" :
    amount >= 1000 ? "silver" :
    "bronze";

  const colorClass =
    tier === "gold" ? "text-tier-lattice" :
    tier === "silver" ? "text-tier-edge" :
    "text-tier-node";

  return (
    <span className={`font-mono font-semibold text-base tabular-nums ${colorClass} ${className}`}>
      {formatUsd(amount)}
    </span>
  );
}
