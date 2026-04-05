import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { BadgeDefinition, BadgeMilestone } from "../api/types";
import HexBadge from "../components/HexBadge";

const TIER_LABELS: Record<string, string> = {
  node: "Node (Bronze)",
  edge: "Edge (Silver)",
  lattice: "Lattice (Gold)",
  single: "Single",
};

export default function BadgeDetail() {
  const { badgeId } = useParams<{ badgeId: string }>();
  const [badge, setBadge] = useState<BadgeDefinition | null>(null);
  const [milestones, setMilestones] = useState<(BadgeMilestone & { rarity_pct?: number })[]>([]);

  useEffect(() => {
    if (!badgeId) return;
    api.getBadgeDetail(badgeId).then((data) => {
      setBadge(data.badge);
      setMilestones(data.milestones);
    }).catch(() => {});
  }, [badgeId]);

  if (!badge) return <p className="text-muted">Loading...</p>;

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="flex items-center gap-4">
        <HexBadge iconSlug={badge.icon_slug} tier="lattice" size={72} />
        <div>
          <h2 className="text-xl font-bold">{badge.display_name}</h2>
          <p className="text-muted text-sm capitalize">{badge.track} track</p>
        </div>
      </div>

      <p className="text-gray-300">{badge.description}</p>

      <div className="bg-panel border border-border rounded-lg p-5">
        <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">Milestones</h3>
        <div className="space-y-3">
          {milestones.map((ms) => (
            <div
              key={ms.milestone_id}
              className="flex items-center justify-between p-3 bg-panel-soft rounded-lg border border-border/50"
            >
              <div className="flex items-center gap-3">
                <HexBadge iconSlug={badge.icon_slug} tier={ms.tier} size={32} />
                <div>
                  <p className="text-sm font-medium text-gray-200">
                    {TIER_LABELS[ms.tier] ?? ms.tier}
                  </p>
                  <p className="text-xs text-muted">
                    Threshold: {ms.threshold_value} {ms.threshold_unit}
                  </p>
                </div>
              </div>
              {ms.rarity_pct !== undefined && (
                <span className="text-xs text-muted">
                  {ms.rarity_pct.toFixed(1)}% don't have it
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
