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
    }, 250);
  }

  function handleLeave() {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    setShow(false);
  }

  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, []);

  return (
    <div className="relative inline-block" onMouseEnter={handleEnter} onMouseLeave={handleLeave}>
      {children}
      {show && (
        <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-44 bg-panel border border-border-bright rounded-xl p-3 shadow-card-hover animate-fade-in">
          <p className="font-medium text-white text-xs mb-1.5">{badgeName}</p>
          {tier && (
            <p className="text-[10px] text-muted mb-1.5">
              Tier: <span className="text-accent capitalize font-medium">{tier}</span>
            </p>
          )}
          {telemetry ? (
            <div className="space-y-1">
              <div className="flex justify-between text-[10px]">
                <span className="text-muted">Progress</span>
                <span className="text-gray-300 font-mono">{telemetry.current_value}</span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-muted">Rarity</span>
                <span className="text-gray-300 font-mono">{telemetry.rarity_pct.toFixed(1)}%</span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-muted">Holders</span>
                <span className="text-gray-300 font-mono">{telemetry.holder_count}</span>
              </div>
            </div>
          ) : (
            <div className="space-y-1.5">
              <div className="skeleton h-2.5 w-full rounded" />
              <div className="skeleton h-2.5 w-3/4 rounded" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
