import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { BountySummaryResponse, LeaderboardEntry, ComputePreserved } from "../api/types";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import LeaderboardTable from "../components/LeaderboardTable";

export default function Home() {
  const [stats, setStats] = useState<ComputePreserved | null>(null);
  const [bounties, setBounties] = useState<BountySummaryResponse[]>([]);
  const [leaders, setLeaders] = useState<LeaderboardEntry[]>([]);

  useEffect(() => {
    api.getComputePreserved().then(setStats);
    api.getBounties({ limit: 5 }).then((r) => setBounties(r.items));
    api.getLeaderboard(5).then((l) => setLeaders(l.slice(0, 5)));
  }, []);

  if (!stats) return <p className="text-muted">Loading...</p>;

  return (
    <div className="space-y-8">
      <h2 className="text-xl font-bold">Overview</h2>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Bounties Settled" value={stats.total_bounties_settled} />
        <StatCard label="Escrow Value" value={`$${stats.total_escrow_value.toLocaleString()}`} />
        <StatCard label="Compute Preserved" value={`${(stats.estimated_tokens_saved / 1e9).toFixed(1)}B tokens`} sub={`$${stats.estimated_cost_saved_usd.toLocaleString()} saved`} />
        <StatCard label="Active Leaders" value={leaders.length} />
      </div>

      <div className="grid lg:grid-cols-2 gap-8">
        {/* Recent bounties */}
        <div className="bg-panel border border-border rounded-lg p-5">
          <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">Recent Bounties</h3>
          <ul className="space-y-3">
            {bounties.map((b) => (
              <li key={b.bounty_id} className="flex items-center justify-between">
                <Link to={`/bounties/${b.bounty_id}`} className="text-sm text-accent hover:underline truncate mr-3">
                  {b.title}
                </Link>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-xs font-mono text-muted">${b.escrow_amount.toLocaleString()}</span>
                  <StatusBadge status={b.status} />
                </div>
              </li>
            ))}
          </ul>
        </div>

        {/* Top originators */}
        <div className="bg-panel border border-border rounded-lg p-5">
          <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">Top Originators</h3>
          <LeaderboardTable entries={leaders} compact />
        </div>
      </div>
    </div>
  );
}
