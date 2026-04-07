import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { LeaderboardEntry, ArchitectLeaderboardEntry, BadgeDefinition, UserBadge } from "../api/types";
import LeaderboardTable from "../components/LeaderboardTable";
import ArchitectTable from "../components/ArchitectTable";
import { PageSkeleton } from "../components/LoadingSkeleton";
import EmptyState from "../components/EmptyState";

export default function Leaderboard() {
  const [originators, setOriginators] = useState<LeaderboardEntry[] | null>(null);
  const [architects, setArchitects] = useState<ArchitectLeaderboardEntry[] | null>(null);
  const [badges, setBadges] = useState<BadgeDefinition[]>([]);
  const [badgesByUser, setBadgesByUser] = useState<Record<string, UserBadge[]>>({});

  useEffect(() => {
    api.getLeaderboard().then(setOriginators);
    api.getArchitectLeaderboard().then(setArchitects).catch(() => setArchitects([]));
    api.getBadgeCatalog().then(setBadges).catch(() => {});
  }, []);

  useEffect(() => {
    if (!originators) return;
    const ids = originators.slice(0, 20).map((e) => e.originator_id);
    for (const id of ids) {
      if (!badgesByUser[id]) {
        api.getUserBadges(id).then((earned) => {
          setBadgesByUser((prev) => ({ ...prev, [id]: earned }));
        }).catch(() => {});
      }
    }
  }, [originators]);

  if (originators === null) return <PageSkeleton />;

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h2 className="page-title">Leaderboard</h2>
        <p className="page-subtitle">Top contributors ranked by impact and earnings.</p>
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        {/* Originators */}
        <div className="card overflow-hidden relative">
          <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-track-originator/30 via-track-originator/10 to-transparent" />
          <div className="px-6 pt-5 pb-3">
            <h3 className="text-sm font-semibold text-track-originator">Top Originators</h3>
            <p className="text-xs text-muted mt-0.5">Researchers who publish and maintain algorithmic atoms.</p>
          </div>
          {originators.length > 0 ? (
            <div className="overflow-x-auto px-6 pb-5">
              <LeaderboardTable entries={originators} badges={badges} badgesByUser={badgesByUser} compact />
            </div>
          ) : (
            <EmptyState title="No originators yet" description="Contributors will appear here as they publish atoms and win bounties." />
          )}
        </div>

        {/* Architects */}
        <div className="card overflow-hidden relative">
          <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-track-architect/30 via-track-architect/10 to-transparent" />
          <div className="px-6 pt-5 pb-3">
            <h3 className="text-sm font-semibold text-track-architect">Top Architects</h3>
            <p className="text-xs text-muted mt-0.5">Practitioners who compose atoms into winning CDGs.</p>
          </div>
          {architects === null ? (
            <div className="p-6"><PageSkeleton /></div>
          ) : architects.length > 0 ? (
            <div className="overflow-x-auto px-6 pb-5">
              <ArchitectTable entries={architects} />
            </div>
          ) : (
            <EmptyState title="No architects yet" description="Architects will appear here as they submit and win bounties." />
          )}
        </div>
      </div>
    </div>
  );
}
