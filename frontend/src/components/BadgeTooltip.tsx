import { useEffect, useState, useRef } from "react";
import { api } from "../api/client";
import type { BadgeTelemetry } from "../api/types";

interface BadgeTooltipProps {
  badgeId: string;
  badgeName: string;
  tier: string | null;
  userId?: string;
  children: React.ReactNode;
}

export default function BadgeTooltip({ badgeId, badgeName, tier, userId, children }: BadgeTooltipProps) {
  const [show, setShow] = useState(false);
  const [telemetry, setTelemetry] = useState<BadgeTelemetry | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>();

  function handleEnter() {
    debounceRef.current = setTimeout(() => {
      setShow(true);
      if (!telemetry) {
        api.getBadgeTelemetry(badgeId, userId).then(setTelemetry).catch(() => {});
      }
    }, 300);
  }

  function handleLeave() {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    setShow(false);
  }

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  return (
    <div className="relative inline-block" onMouseEnter={handleEnter} onMouseLeave={handleLeave}>
      {children}
      {show && (
        <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-48 bg-panel border border-border rounded-lg p-3 shadow-lg text-xs">
          <p className="font-semibold text-gray-200 mb-1">{badgeName}</p>
          {tier && <p className="text-muted mb-1">Tier: <span className="text-accent capitalize">{tier}</span></p>}
          {telemetry ? (
            <>
              <p className="text-muted">Progress: <span className="text-gray-300">{telemetry.current_value}</span></p>
              <p className="text-muted">Rarity: <span className="text-gray-300">{telemetry.rarity_pct.toFixed(1)}%</span> don't have it</p>
              <p className="text-muted">Holders: <span className="text-gray-300">{telemetry.holder_count}</span></p>
            </>
          ) : (
            <p className="text-muted">Loading...</p>
          )}
        </div>
      )}
    </div>
  );
}
