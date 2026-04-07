import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { BountySummaryResponse, LeaderboardEntry, ComputePreserved } from "../api/types";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import LeaderboardTable from "../components/LeaderboardTable";
import { PageSkeleton } from "../components/LoadingSkeleton";
import EmptyState from "../components/EmptyState";
import EscrowAmount from "../components/EscrowAmount";
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
      <div className="relative overflow-hidden rounded-2xl border border-border">
        {/* Background layers */}
        <div className="absolute inset-0 bg-hero-gradient" />
        <div className="absolute inset-0 opacity-[0.03]"
          style={{ backgroundImage: "radial-gradient(circle, #38bdf8 1px, transparent 1px)", backgroundSize: "32px 32px" }} />

        {/* Geometric accent shapes */}
        <div className="absolute -top-20 -right-20 w-64 h-64 rounded-full opacity-[0.04]"
          style={{ background: "radial-gradient(circle, #38bdf8, transparent 70%)" }} />
        <div className="absolute -bottom-16 -left-16 w-48 h-48 rounded-full opacity-[0.03]"
          style={{ background: "radial-gradient(circle, #818cf8, transparent 70%)" }} />

        {/* Animated glow line at top */}
        <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-transparent via-accent/40 to-transparent" />

        <div className="relative px-8 py-10 lg:px-12 lg:py-14">
          <div className="flex items-center gap-2 mb-4">
            <div className="h-[2px] w-8 bg-accent-gradient rounded-full" />
            <span className="text-[10px] font-semibold text-accent/80 uppercase tracking-[0.2em]">Research Platform</span>
          </div>
          <h2 className="text-3xl lg:text-4xl font-bold text-white tracking-tight mb-3">
            Algorithmic Commons
          </h2>
          <p className="text-muted text-sm max-w-lg leading-relaxed">
            The open marketplace for verified algorithmic building blocks.
            Publish atoms, solve bounties, earn royalties through Shapley-value fair allocation.
          </p>
          <div className="flex gap-3 mt-6">
            <Link to="/bounties" className="btn-primary text-xs">
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              Browse Bounties
            </Link>
            <Link to="/atoms" className="btn-secondary text-xs">
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
              </svg>
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
          accentColor="#38bdf8"
        />
        <StatCard
          label="Escrow Distributed"
          value={formatUsd(stats.total_escrow_value)}
          accentColor="#818cf8"
        />
        <StatCard
          label="Compute Preserved"
          value={`${formatNumber(stats.estimated_tokens_saved)} tokens`}
          sub={`${formatUsd(stats.estimated_cost_saved_usd)} saved`}
          accentColor="#22c55e"
        />
        <StatCard
          label="Top Contributors"
          value={leaders.length}
          accentColor="#d4a843"
        />
      </div>

      {/* Two-column */}
      <div className="grid lg:grid-cols-2 gap-6">
        {/* Recent bounties */}
        <div className="card p-6 relative overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-accent/30 via-accent/10 to-transparent" />
          <div className="flex items-center justify-between mb-5">
            <h3 className="section-heading">Recent Bounties</h3>
            <Link to="/bounties" className="text-xs text-accent hover:text-accent-bright transition-colors font-medium">
              View all
            </Link>
          </div>
          {bounties.length > 0 ? (
            <ul className="space-y-1">
              {bounties.map((b) => (
                <li key={b.bounty_id}>
                  <Link
                    to={`/bounties/${b.bounty_id}`}
                    className="flex items-center justify-between group py-2.5 px-3 -mx-3 rounded-lg hover:bg-panel-soft/40 transition-colors"
                  >
                    <span className="text-sm text-gray-300 group-hover:text-white truncate mr-3 transition-colors">
                      {b.title}
                    </span>
                    <div className="flex items-center gap-3 shrink-0">
                      <EscrowAmount amount={b.escrow_amount} className="text-sm" />
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
        <div className="card p-6 relative overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-accent-2/30 via-accent-2/10 to-transparent" />
          <div className="flex items-center justify-between mb-5">
            <h3 className="section-heading">Top Originators</h3>
            <Link to="/leaderboard" className="text-xs text-accent hover:text-accent-bright transition-colors font-medium">
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
