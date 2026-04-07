import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { BountySummaryResponse, LeaderboardEntry, ComputePreserved } from "../api/types";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import LeaderboardTable from "../components/LeaderboardTable";
import { PageSkeleton } from "../components/LoadingSkeleton";
import EmptyState from "../components/EmptyState";
import { formatUsd, formatNumber } from "../utils/format";

export default function Home() {
  const [stats, setStats] = useState<ComputePreserved | null>(null);
  const [bounties, setBounties] = useState<BountySummaryResponse[]>([]);
  const [leaders, setLeaders] = useState<LeaderboardEntry[]>([]);

  useEffect(() => {
    api.getComputePreserved().then(setStats).catch(() => {});
    api.getBounties({ limit: 5 }).then((r) => setBounties(r.items)).catch(() => {});
    api.getLeaderboard(5).then((l) => setLeaders(l.slice(0, 5))).catch(() => {});
  }, []);

  if (!stats) return <PageSkeleton />;

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Hero */}
      <div className="relative overflow-hidden card p-8 lg:p-10">
        <div className="absolute inset-0 bg-gradient-to-br from-accent/5 via-transparent to-accent-2/5" />
        <div className="relative">
          <h2 className="text-3xl font-bold text-white tracking-tight mb-2">
            Algorithmic Commons
          </h2>
          <p className="text-muted text-sm max-w-lg leading-relaxed">
            The open marketplace for verified algorithmic building blocks.
            Publish atoms, solve bounties, earn royalties through Shapley-value fair allocation.
          </p>
          <div className="flex gap-3 mt-5">
            <Link to="/bounties" className="btn-primary text-xs">
              Browse Bounties
            </Link>
            <Link to="/atoms" className="btn-secondary text-xs">
              Explore Atoms
            </Link>
          </div>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Bounties Settled"
          value={stats.total_bounties_settled}
        />
        <StatCard
          label="Escrow Distributed"
          value={formatUsd(stats.total_escrow_value)}
        />
        <StatCard
          label="Compute Preserved"
          value={`${formatNumber(stats.estimated_tokens_saved)} tokens`}
          sub={`${formatUsd(stats.estimated_cost_saved_usd)} saved`}
        />
        <StatCard
          label="Top Contributors"
          value={leaders.length}
        />
      </div>

      {/* Two-column */}
      <div className="grid lg:grid-cols-2 gap-6">
        {/* Recent bounties */}
        <div className="card p-6">
          <div className="flex items-center justify-between mb-5">
            <h3 className="section-heading">Recent Bounties</h3>
            <Link to="/bounties" className="text-xs text-accent hover:text-accent/80 transition-colors font-medium">
              View all
            </Link>
          </div>
          {bounties.length > 0 ? (
            <ul className="space-y-3">
              {bounties.map((b) => (
                <li key={b.bounty_id}>
                  <Link
                    to={`/bounties/${b.bounty_id}`}
                    className="flex items-center justify-between group py-1.5"
                  >
                    <span className="text-sm text-gray-300 group-hover:text-white truncate mr-3 transition-colors">
                      {b.title}
                    </span>
                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-xs font-mono text-muted">{formatUsd(b.escrow_amount)}</span>
                      <StatusBadge status={b.status} />
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No bounties yet" description="Bounties will appear here once created." />
          )}
        </div>

        {/* Top originators */}
        <div className="card p-6">
          <div className="flex items-center justify-between mb-5">
            <h3 className="section-heading">Top Originators</h3>
            <Link to="/leaderboard" className="text-xs text-accent hover:text-accent/80 transition-colors font-medium">
              Full leaderboard
            </Link>
          </div>
          {leaders.length > 0 ? (
            <LeaderboardTable entries={leaders} compact />
          ) : (
            <EmptyState title="No originators yet" />
          )}
        </div>
      </div>
    </div>
  );
}
