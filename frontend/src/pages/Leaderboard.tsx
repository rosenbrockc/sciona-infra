import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { LeaderboardEntry, BadgeDefinition, UserBadge } from "../api/types";
import LeaderboardTable from "../components/LeaderboardTable";

export default function Leaderboard() {
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);
  const [badges, setBadges] = useState<BadgeDefinition[]>([]);
  const [badgesByUser, setBadgesByUser] = useState<Record<string, UserBadge[]>>({});

  useEffect(() => {
    api.getLeaderboard().then(setEntries);
    api.getBadgeCatalog().then(setBadges).catch(() => {});
  }, []);

  useEffect(() => {
    // Fetch badges for top leaderboard entries
    const ids = entries.slice(0, 20).map((e) => e.originator_id);
    for (const id of ids) {
      if (!badgesByUser[id]) {
        api.getUserBadges(id).then((earned) => {
          setBadgesByUser((prev) => ({ ...prev, [id]: earned }));
        }).catch(() => {});
      }
    }
  }, [entries]);

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-bold">Originator Leaderboard</h2>
      <div className="bg-panel border border-border rounded-lg p-5">
        <LeaderboardTable entries={entries} badges={badges} badgesByUser={badgesByUser} />
      </div>
    </div>
  );
}
