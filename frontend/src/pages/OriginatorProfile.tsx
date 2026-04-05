import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { OriginatorImpact, BadgeDefinition, UserBadge, BadgeProgress, GrandmasterStatus } from "../api/types";
import { useAuth } from "../auth/useAuth";
import StatCard from "../components/StatCard";
import BadgeGrid from "../components/BadgeGrid";
import GrandmasterRing from "../components/GrandmasterRing";
import ReferralPanel from "../components/ReferralPanel";

export default function OriginatorProfile() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [impact, setImpact] = useState<OriginatorImpact | null>(null);
  const [badges, setBadges] = useState<BadgeDefinition[]>([]);
  const [earned, setEarned] = useState<UserBadge[]>([]);
  const [progress, setProgress] = useState<BadgeProgress[]>([]);
  const [grandmaster, setGrandmaster] = useState<GrandmasterStatus | null>(null);

  const isOwnProfile = user && id && user.user_id === id;

  useEffect(() => {
    if (!id) return;
    api.getOriginatorImpact(id).then(setImpact);
    api.getBadgeCatalog().then(setBadges).catch(() => {});
    api.getUserBadges(id).then(setEarned).catch(() => {});
    api.getGrandmasterStatus(id).then(setGrandmaster).catch(() => {});
    if (isOwnProfile) {
      api.getUserBadgeProgress(id).then(setProgress).catch(() => {});
    }
  }, [id, isOwnProfile]);

  if (!impact) return <p className="text-muted">Loading...</p>;

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-4">
        <GrandmasterRing status={grandmaster}>
          <div className="h-14 w-14 rounded-full bg-panel-soft border border-border flex items-center justify-center text-xl font-bold text-accent">
            {(impact.github_username || "?")[0].toUpperCase()}
          </div>
        </GrandmasterRing>
        <h2 className="text-xl font-bold">{impact.github_username || impact.originator_id}</h2>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="h-index" value={impact.h_index} />
        <StatCard label="Bounties" value={impact.bounty_count} />
        <StatCard label="Total Value" value={`$${impact.total_bounty_value.toLocaleString()}`} />
        <StatCard label="Atoms" value={impact.atom_count} />
      </div>

      <div className="bg-panel border border-border rounded-lg p-5">
        <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">Profile</h3>
        <p className="text-sm text-gray-300">
          Affiliation: {impact.affiliation || "Not provided"}
        </p>
      </div>

      <div className="bg-panel border border-border rounded-lg p-5">
        <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">Badges</h3>
        <BadgeGrid
          badges={badges}
          earned={earned}
          progress={isOwnProfile ? progress : undefined}
          userId={id}
          onBadgeClick={(badgeId) => navigate(`/badges/${badgeId}`)}
        />
      </div>

      {isOwnProfile && <ReferralPanel />}
    </div>
  );
}
