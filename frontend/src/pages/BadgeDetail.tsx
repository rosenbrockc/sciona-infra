import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api/client";
import type { BadgeDefinition, BadgeMilestone } from "../api/types";
import HexBadge from "../components/HexBadge";
import { PageSkeleton } from "../components/LoadingSkeleton";

const TIER_META: Record<string, { label: string; color: string }> = {
  node: { label: "Node", color: "text-amber-500" },
  edge: { label: "Edge", color: "text-gray-300" },
  lattice: { label: "Lattice", color: "text-yellow-400" },
  single: { label: "Achievement", color: "text-accent-2" },
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

  if (!badge) return <PageSkeleton />;

  return (
    <div className="space-y-6 animate-fade-in max-w-2xl">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-xs text-muted">
        <Link to="/badges" className="hover:text-accent transition-colors">Badges</Link>
        <span>/</span>
        <span className="text-gray-400">{badge.display_name}</span>
      </div>

      {/* Header */}
      <div className="card p-6">
        <div className="flex items-center gap-5">
          <HexBadge iconSlug={badge.icon_slug} tier="lattice" size={72} />
          <div>
            <h2 className="text-xl font-bold text-white">{badge.display_name}</h2>
            <p className="text-sm text-muted capitalize mt-0.5">{badge.track} track</p>
            <p className="text-sm text-gray-400 mt-2 leading-relaxed">{badge.description}</p>
          </div>
        </div>
      </div>

      {/* Milestones */}
      <div className="card p-6">
        <h3 className="section-heading mb-5">Tier Requirements</h3>
        <div className="space-y-3">
          {milestones.map((ms) => {
            const meta = TIER_META[ms.tier] ?? TIER_META.single;
            return (
              <div
                key={ms.milestone_id}
                className="flex items-center justify-between p-4 bg-bg-soft rounded-xl border border-border/50"
              >
                <div className="flex items-center gap-4">
                  <HexBadge iconSlug={badge.icon_slug} tier={ms.tier} size={36} />
                  <div>
                    <p className={`text-sm font-medium ${meta.color}`}>{meta.label}</p>
                    <p className="text-xs text-muted mt-0.5">
                      Reach <span className="text-white font-mono font-medium">{ms.threshold_value}</span> {ms.threshold_unit}
                    </p>
                  </div>
                </div>
                {ms.rarity_pct !== undefined && (
                  <div className="text-right">
                    <p className="text-sm font-mono text-white">{ms.rarity_pct.toFixed(1)}%</p>
                    <p className="text-xs text-muted">don't have it</p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
