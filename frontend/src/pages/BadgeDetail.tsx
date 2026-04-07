import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api/client";
import type { BadgeDefinition, BadgeMilestone } from "../api/types";
import HexBadge from "../components/HexBadge";
import { PageSkeleton } from "../components/LoadingSkeleton";

const TIER_META: Record<string, { label: string; color: string; bg: string }> = {
  node: { label: "Node", color: "text-tier-node", bg: "bg-tier-node/10" },
  edge: { label: "Edge", color: "text-tier-edge", bg: "bg-tier-edge/10" },
  lattice: { label: "Lattice", color: "text-tier-lattice", bg: "bg-tier-lattice/10" },
  single: { label: "Achievement", color: "text-accent-2", bg: "bg-accent-2/10" },
};

const TRACK_COLORS: Record<string, string> = {
  originator: "text-track-originator",
  architect: "text-track-architect",
  vanguard: "text-track-vanguard",
  evangelist: "text-track-evangelist",
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
        <span className="text-border-bright">/</span>
        <span className="text-gray-400">{badge.display_name}</span>
      </div>

      {/* Header */}
      <div className="card p-6 relative overflow-hidden">
        <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-tier-lattice/40 via-tier-edge/20 to-transparent" />
        <div className="flex items-center gap-5">
          <div className="shrink-0">
            <HexBadge iconSlug={badge.icon_slug} tier="lattice" size={72} />
          </div>
          <div>
            <h2 className="text-xl font-bold text-white">{badge.display_name}</h2>
            <p className={`text-sm capitalize mt-0.5 font-medium ${TRACK_COLORS[badge.track] ?? "text-muted"}`}>{badge.track} track</p>
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
                className={`flex items-center justify-between p-4 rounded-xl border border-border/50 ${meta.bg}`}
              >
                <div className="flex items-center gap-4">
                  <HexBadge iconSlug={badge.icon_slug} tier={ms.tier} size={36} />
                  <div>
                    <p className={`text-sm font-semibold ${meta.color}`}>{meta.label}</p>
                    <p className="text-xs text-muted mt-0.5">
                      Reach <span className="text-white font-mono font-medium">{ms.threshold_value}</span> {ms.threshold_unit}
                    </p>
                  </div>
                </div>
                {ms.rarity_pct !== undefined && (
                  <div className="text-right">
                    <p className="text-sm font-mono text-white">{ms.rarity_pct.toFixed(1)}%</p>
                    <p className="text-[10px] text-muted">don't have it</p>
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
