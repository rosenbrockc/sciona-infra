import type { UserBadge, BadgeDefinition } from "../api/types";
import HexBadge from "./HexBadge";

interface BadgeShowcaseProps {
  earned: UserBadge[];
  badges: BadgeDefinition[];
  max?: number;
}

function tierFromMilestone(milestoneId: string): string {
  const parts = milestoneId.split("_");
  return parts[parts.length - 1] ?? "node";
}

function badgeIdFromMilestone(milestoneId: string): string {
  const parts = milestoneId.split("_");
  return parts.slice(0, -1).join("_");
}

export default function BadgeShowcase({ earned, badges, max = 5 }: BadgeShowcaseProps) {
  // Pick the highest-tier badges, deduped by badge_id
  const seen = new Set<string>();
  const top: { badge: BadgeDefinition; tier: string }[] = [];
  const tierRank: Record<string, number> = { lattice: 3, single: 3, edge: 2, node: 1 };

  const sorted = [...earned].sort(
    (a, b) => (tierRank[tierFromMilestone(b.milestone_id)] ?? 0) - (tierRank[tierFromMilestone(a.milestone_id)] ?? 0),
  );

  for (const ub of sorted) {
    const bid = badgeIdFromMilestone(ub.milestone_id);
    if (seen.has(bid)) continue;
    seen.add(bid);
    const def = badges.find((b) => b.badge_id === bid);
    if (def) {
      top.push({ badge: def, tier: tierFromMilestone(ub.milestone_id) });
    }
    if (top.length >= max) break;
  }

  if (top.length === 0) return null;

  return (
    <div className="flex gap-1">
      {top.map(({ badge, tier }) => (
        <HexBadge
          key={badge.badge_id}
          iconSlug={badge.icon_slug}
          tier={tier}
          size={24}
        />
      ))}
    </div>
  );
}
