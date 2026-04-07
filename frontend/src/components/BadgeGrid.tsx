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

const TRACK_LABELS: Record<string, { label: string; color: string; border: string }> = {
  originator: { label: "Originator", color: "text-track-originator", border: "border-l-track-originator" },
  architect: { label: "Architect", color: "text-track-architect", border: "border-l-track-architect" },
  vanguard: { label: "Vanguard", color: "text-track-vanguard", border: "border-l-track-vanguard" },
  evangelist: { label: "Evangelist", color: "text-track-evangelist", border: "border-l-track-evangelist" },
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
    ...(TRACK_LABELS[track] ?? { label: track, color: "text-muted", border: "border-l-muted" }),
    items: badges.filter((b) => b.track === track),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="space-y-6">
      {byTrack.map((group) => (
        <div key={group.track}>
          <p className={`text-xs font-semibold uppercase tracking-widest mb-3 pl-3 border-l-2 ${group.border} ${group.color}`}>
            {group.label}
          </p>
          <div className="flex flex-wrap gap-4">
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
                  <div className="text-center w-16">
                    <HexBadge
                      iconSlug={badge.icon_slug}
                      tier={tier}
                      size={52}
                      onClick={onBadgeClick ? () => onBadgeClick(badge.badge_id) : undefined}
                    />
                    <p className={`text-[10px] mt-1.5 font-medium leading-tight ${tier ? "text-gray-300" : "text-muted/50"}`}>
                      {badge.display_name}
                    </p>
                    {prog && !tier && (
                      <p className="text-[10px] text-muted/40 font-mono">{prog.current_value}</p>
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
