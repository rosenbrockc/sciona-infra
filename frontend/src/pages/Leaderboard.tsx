import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { LeaderboardEntry } from "../api/types";
import LeaderboardTable from "../components/LeaderboardTable";

export default function Leaderboard() {
  const [entries, setEntries] = useState<LeaderboardEntry[]>([]);

  useEffect(() => {
    api.getLeaderboard().then(setEntries);
  }, []);

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-bold">Originator Leaderboard</h2>
      <div className="bg-panel border border-border rounded-lg p-5">
        <LeaderboardTable entries={entries} />
      </div>
    </div>
  );
}
