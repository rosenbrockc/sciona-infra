import type { BadgeDefinition, UserBadge, BadgeProgress } from "../api/types";
import HexBadge from "./HexBadge";
import BadgeTooltip from "./BadgeTooltip";

interface BadgeGridProps {
  badges: BadgeDefinition[];
  earned: UserBadge[];
  progress?: BadgeProgress[];
  userId?: string;
  onBadgeClick?: (badgeId: string) => void;
}

const TRACK_LABELS: Record<string, string> = {
  originator: "Originator",
  architect: "Architect",
  vanguard: "Vanguard",
  evangelist: "Evangelist",
};

const TRACK_ORDER = ["originator", "architect", "vanguard", "evangelist"];

function getEarnedTier(badgeId: string, earned: UserBadge[]): string | null {
  const tiers = ["lattice", "single", "edge", "node"];
  for (const tier of tiers) {
    if (earned.some((e) => e.milestone_id === `${badgeId}_${tier}`)) {
      return tier;
    }
  }
  return null;
}

export default function BadgeGrid({ badges, earned, progress, userId, onBadgeClick }: BadgeGridProps) {
  const byTrack = TRACK_ORDER.map((track) => ({
    track,
    label: TRACK_LABELS[track] ?? track,
    items: badges.filter((b) => b.track === track),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="space-y-6">
      {byTrack.map((group) => (
        <div key={group.track}>
          <h4 className="text-sm font-semibold text-muted uppercase tracking-wide mb-3">
            {group.label} Track
          </h4>
          <div className="flex flex-wrap gap-3">
            {group.items.map((badge) => {
              const tier = getEarnedTier(badge.badge_id, earned);
              const prog = progress?.find((p) => p.badge_id === badge.badge_id);
              return (
                <BadgeTooltip
                  key={badge.badge_id}
                  badgeId={badge.badge_id}
                  badgeName={badge.display_name}
                  tier={tier}
                  userId={userId}
                >
                  <div className="text-center">
                    <HexBadge
                      iconSlug={badge.icon_slug}
                      tier={tier}
                      size={56}
                      onClick={onBadgeClick ? () => onBadgeClick(badge.badge_id) : undefined}
                    />
                    <p className={`text-xs mt-1 ${tier ? "text-gray-300" : "text-muted"}`}>
                      {badge.display_name}
                    </p>
                    {prog && !tier && (
                      <p className="text-xs text-muted">{prog.current_value}</p>
                    )}
                  </div>
                </BadgeTooltip>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
