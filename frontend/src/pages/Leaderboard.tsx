import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { LeaderboardEntry, BadgeDefinition, UserBadge } from "../api/types";
import LeaderboardTable from "../components/LeaderboardTable";
import { PageSkeleton } from "../components/LoadingSkeleton";
import EmptyState from "../components/EmptyState";

export default function Leaderboard() {
  const [entries, setEntries] = useState<LeaderboardEntry[] | null>(null);
  const [badges, setBadges] = useState<BadgeDefinition[]>([]);
  const [badgesByUser, setBadgesByUser] = useState<Record<string, UserBadge[]>>({});

  useEffect(() => {
    api.getLeaderboard().then(setEntries);
    api.getBadgeCatalog().then(setBadges).catch(() => {});
  }, []);

  useEffect(() => {
    if (!entries) return;
    const ids = entries.slice(0, 20).map((e) => e.originator_id);
    for (const id of ids) {
      if (!badgesByUser[id]) {
        api.getUserBadges(id).then((earned) => {
          setBadgesByUser((prev) => ({ ...prev, [id]: earned }));
        }).catch(() => {});
      }
    }
  }, [entries]);

  if (entries === null) return <PageSkeleton />;

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h2 className="page-title">Originator Leaderboard</h2>
        <p className="page-subtitle">Top contributors ranked by algorithmic impact factor.</p>
      </div>
      <div className="card overflow-hidden">
        {entries.length > 0 ? (
          <div className="overflow-x-auto">
            <div className="p-6">
              <LeaderboardTable entries={entries} badges={badges} badgesByUser={badgesByUser} />
            </div>
          </div>
        ) : (
          <EmptyState title="No originators yet" description="Contributors will appear here as they publish atoms and win bounties." />
        )}
      </div>
    </div>
  );
}
