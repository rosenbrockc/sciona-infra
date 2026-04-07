import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { api } from "../api/client";
import type { OriginatorImpact, BadgeDefinition, UserBadge, BadgeProgress, GrandmasterStatus } from "../api/types";
import { useAuth } from "../auth/useAuth";
import StatCard from "../components/StatCard";
import BadgeGrid from "../components/BadgeGrid";
import GrandmasterRing from "../components/GrandmasterRing";
import ReferralPanel from "../components/ReferralPanel";
import { PageSkeleton } from "../components/LoadingSkeleton";
import { formatUsd } from "../utils/format";

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

  if (!impact) return <PageSkeleton />;

  return (
    <div className="space-y-6 animate-fade-in max-w-4xl">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-xs text-muted">
        <Link to="/leaderboard" className="hover:text-accent transition-colors">Leaderboard</Link>
        <span>/</span>
        <span className="text-gray-400">{impact.github_username || "Profile"}</span>
      </div>

      {/* Profile Header */}
      <div className="card p-6">
        <div className="flex items-center gap-5">
          <GrandmasterRing status={grandmaster}>
            <div className="h-16 w-16 rounded-full bg-panel-soft border border-border-bright flex items-center justify-center text-2xl font-bold text-accent">
              {(impact.github_username || "?")[0].toUpperCase()}
            </div>
          </GrandmasterRing>
          <div>
            <h2 className="text-xl font-bold text-white">{impact.github_username || impact.originator_id}</h2>
            <p className="text-sm text-muted mt-0.5">
              {impact.affiliation || "Independent researcher"}
            </p>
          </div>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Impact (h-index)" value={impact.h_index} />
        <StatCard label="Bounties" value={impact.bounty_count} />
        <StatCard label="Total Value" value={formatUsd(impact.total_bounty_value)} />
        <StatCard label="Atoms Published" value={impact.atom_count} />
      </div>

      {/* Badges */}
      <div className="card p-6">
        <div className="flex items-center justify-between mb-5">
          <h3 className="section-heading">Badges</h3>
          <Link to="/badges" className="text-xs text-accent hover:text-accent/80 transition-colors font-medium">
            View catalog
          </Link>
        </div>
        <BadgeGrid
          badges={badges}
          earned={earned}
          progress={isOwnProfile ? progress : undefined}
          userId={id}
          onBadgeClick={(badgeId) => navigate(`/badges/${badgeId}`)}
        />
      </div>

      {/* Referrals (own profile only) */}
      {isOwnProfile && <ReferralPanel />}
    </div>
  );
}
