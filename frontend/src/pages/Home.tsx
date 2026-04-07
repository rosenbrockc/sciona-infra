import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  BountySummaryResponse,
  BountyResponse,
  LeaderboardEntry,
  ArchitectLeaderboardEntry,
  ComputePreserved,
} from "../api/types";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import LeaderboardTable from "../components/LeaderboardTable";
import ArchitectTable from "../components/ArchitectTable";
import { PageSkeleton } from "../components/LoadingSkeleton";
import EmptyState from "../components/EmptyState";
import EscrowAmount from "../components/EscrowAmount";
import { formatUsd, formatNumber, formatDateTime, truncateId } from "../utils/format";

export default function Home() {
  const [stats, setStats] = useState<ComputePreserved | null>(null);
  const [bounties, setBounties] = useState<BountySummaryResponse[]>([]);
  const [leaders, setLeaders] = useState<LeaderboardEntry[]>([]);
  const [architects, setArchitects] = useState<ArchitectLeaderboardEntry[]>([]);

  // Bounty detail panel state
  const [selectedBountyId, setSelectedBountyId] = useState<string | null>(null);
  const [selectedBounty, setSelectedBounty] = useState<BountyResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    api.getComputePreserved().then(setStats).catch(() => {});
    api.getBounties({ limit: 8 }).then((r) => setBounties(r.items)).catch(() => {});
    api.getLeaderboard(10).then((l) => setLeaders(l.slice(0, 10))).catch(() => {});
    api.getArchitectLeaderboard(10).then((a) => setArchitects(a.slice(0, 10))).catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedBountyId) {
      setSelectedBounty(null);
      return;
    }
    setDetailLoading(true);
    api.getBounty(selectedBountyId).then((b) => {
      setSelectedBounty(b);
      setDetailLoading(false);
    }).catch(() => setDetailLoading(false));
  }, [selectedBountyId]);

  if (!stats) return <PageSkeleton />;

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Hero */}
      <div className="relative overflow-hidden rounded-2xl border border-border">
        <div className="absolute inset-0 bg-hero-gradient" />
        <div className="absolute inset-0 opacity-[0.03]"
          style={{ backgroundImage: "radial-gradient(circle, #38bdf8 1px, transparent 1px)", backgroundSize: "32px 32px" }} />
        <div className="absolute -top-20 -right-20 w-64 h-64 rounded-full opacity-[0.04]"
          style={{ background: "radial-gradient(circle, #38bdf8, transparent 70%)" }} />
        <div className="absolute -bottom-16 -left-16 w-48 h-48 rounded-full opacity-[0.03]"
          style={{ background: "radial-gradient(circle, #818cf8, transparent 70%)" }} />
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
        <StatCard label="Bounties Settled" value={stats.total_bounties_settled} accentColor="#38bdf8" />
        <StatCard label="Escrow Distributed" value={formatUsd(stats.total_escrow_value)} accentColor="#818cf8" />
        <StatCard label="Compute Preserved" value={`${formatNumber(stats.estimated_tokens_saved)} tokens`} sub={`${formatUsd(stats.estimated_cost_saved_usd)} saved`} accentColor="#22c55e" />
        <StatCard label="Top Contributors" value={leaders.length + architects.length} accentColor="#d4a843" />
      </div>

      {/* Bounties panel — list left, detail right */}
      <div className="card overflow-hidden relative">
        <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-accent/30 via-accent/10 to-transparent" />
        <div className="flex items-center justify-between px-6 pt-5 pb-0">
          <h3 className="section-heading">Recent Bounties</h3>
          <Link to="/bounties" className="text-xs text-accent hover:text-accent-bright transition-colors font-medium">
            View all
          </Link>
        </div>
        <div className="flex min-h-[320px]">
          {/* Left: bounty list */}
          <div className={`${selectedBounty ? "w-1/2 border-r border-border" : "w-full"} transition-all duration-200`}>
            {bounties.length > 0 ? (
              <ul className="divide-y divide-border/40">
                {bounties.map((b) => (
                  <li key={b.bounty_id}>
                    <button
                      type="button"
                      onClick={() => setSelectedBountyId(selectedBountyId === b.bounty_id ? null : b.bounty_id)}
                      className={`w-full flex items-center justify-between px-6 py-3.5 text-left transition-colors ${
                        selectedBountyId === b.bounty_id
                          ? "bg-panel-soft/60"
                          : "hover:bg-panel-soft/30"
                      }`}
                    >
                      <span className={`text-sm truncate mr-3 transition-colors ${
                        selectedBountyId === b.bounty_id ? "text-white font-medium" : "text-gray-300"
                      }`}>
                        {b.title}
                      </span>
                      <div className="flex items-center gap-3 shrink-0">
                        <EscrowAmount amount={b.escrow_amount} className="text-sm" />
                        <StatusBadge status={b.status} />
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="p-6">
                <EmptyState title="No bounties yet" description="Bounties will appear here once created." />
              </div>
            )}
          </div>

          {/* Right: bounty detail */}
          {selectedBountyId && (
            <div className="w-1/2 p-6 overflow-y-auto animate-fade-in">
              {detailLoading || !selectedBounty ? (
                <div className="space-y-3">
                  <div className="skeleton h-5 w-3/4 rounded" />
                  <div className="skeleton h-3 w-1/2 rounded" />
                  <div className="skeleton h-3 w-2/3 rounded" />
                  <div className="grid grid-cols-2 gap-3 mt-4">
                    <div className="skeleton h-16 rounded-lg" />
                    <div className="skeleton h-16 rounded-lg" />
                    <div className="skeleton h-16 rounded-lg" />
                    <div className="skeleton h-16 rounded-lg" />
                  </div>
                </div>
              ) : (
                <div className="space-y-4">
                  <div>
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <h4 className="text-base font-semibold text-white leading-tight">{selectedBounty.title}</h4>
                      <StatusBadge status={selectedBounty.status} />
                    </div>
                    <p className="text-xs text-muted font-mono">{selectedBounty.bounty_id}</p>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div className="bg-bg-soft rounded-lg p-3 border border-border/40">
                      <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Escrow</p>
                      <EscrowAmount amount={selectedBounty.escrow_amount} className="text-lg" />
                    </div>
                    <div className="bg-bg-soft rounded-lg p-3 border border-border/40">
                      <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Tier</p>
                      <p className="text-sm font-medium text-white capitalize">{selectedBounty.tier}</p>
                    </div>
                    <div className="bg-bg-soft rounded-lg p-3 border border-border/40">
                      <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Deadline</p>
                      <p className="text-sm text-gray-300">{selectedBounty.deadline ? formatDateTime(selectedBounty.deadline) : "None"}</p>
                    </div>
                    <div className="bg-bg-soft rounded-lg p-3 border border-border/40">
                      <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Submissions</p>
                      <p className="text-sm font-medium text-white tabular-nums">{selectedBounty.submission_count}</p>
                    </div>
                  </div>

                  <div className="bg-bg-soft rounded-lg p-3 border border-border/40">
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-[10px] text-muted uppercase tracking-wider">Verification Budget</p>
                      <span className="text-xs font-mono text-muted tabular-nums">
                        {selectedBounty.verifications_used}/{selectedBounty.verification_budget}
                      </span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-panel-soft border border-border/30">
                      <div
                        className="h-full rounded-full bg-accent-gradient transition-all duration-500"
                        style={{ width: `${Math.min(selectedBounty.verification_budget ? Math.round((selectedBounty.verifications_used / selectedBounty.verification_budget) * 100) : 0, 100)}%` }}
                      />
                    </div>
                  </div>

                  <div className="text-xs text-muted space-y-1.5">
                    <div className="flex justify-between">
                      <span>Principal</span>
                      <span className="font-mono text-gray-300">{truncateId(selectedBounty.principal_id)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Created</span>
                      <span className="text-gray-300">{formatDateTime(selectedBounty.created_at)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Updated</span>
                      <span className="text-gray-300">{formatDateTime(selectedBounty.updated_at)}</span>
                    </div>
                  </div>

                  <Link
                    to={`/bounties/${selectedBounty.bounty_id}`}
                    className="btn-primary w-full justify-center text-xs mt-2"
                  >
                    View Full Details
                  </Link>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Side-by-side leaderboards */}
      <div className="grid lg:grid-cols-2 gap-6">
        {/* Top Originators */}
        <div className="card p-6 relative overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-track-originator/30 via-track-originator/10 to-transparent" />
          <div className="flex items-center justify-between mb-5">
            <h3 className="section-heading">Top Originators</h3>
            <Link to="/leaderboard" className="text-xs text-accent hover:text-accent-bright transition-colors font-medium">
              Full leaderboard
            </Link>
          </div>
          {leaders.length > 0 ? (
            <div className="overflow-x-auto">
              <LeaderboardTable entries={leaders} compact />
            </div>
          ) : (
            <EmptyState title="No originators yet" />
          )}
        </div>

        {/* Top Architects */}
        <div className="card p-6 relative overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-track-architect/30 via-track-architect/10 to-transparent" />
          <div className="flex items-center justify-between mb-5">
            <h3 className="section-heading">Top Architects</h3>
            <Link to="/leaderboard" className="text-xs text-accent hover:text-accent-bright transition-colors font-medium">
              Full leaderboard
            </Link>
          </div>
          {architects.length > 0 ? (
            <div className="overflow-x-auto">
              <ArchitectTable entries={architects} compact />
            </div>
          ) : (
            <EmptyState title="No architects yet" description="Architects will appear as they submit winning CDGs." />
          )}
        </div>
      </div>
    </div>
  );
}
