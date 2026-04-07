import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  BountyResponse,
  SettlementInfo,
  SubmissionLeaderboardEntry,
  WorkflowStatus,
} from "../api/types";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import WorkflowTimeline from "../components/WorkflowTimeline";
import VerificationRunList from "../components/VerificationRunList";
import EscrowAmount from "../components/EscrowAmount";
import { PageSkeleton } from "../components/LoadingSkeleton";
import { formatDateTime, truncateId, formatUsd } from "../utils/format";

export default function BountyDetail() {
  const { id } = useParams<{ id: string }>();
  const [bounty, setBounty] = useState<BountyResponse | null>(null);
  const [leaderboard, setLeaderboard] = useState<SubmissionLeaderboardEntry[]>([]);
  const [settlement, setSettlement] = useState<SettlementInfo | null>(null);
  const [workflowStatuses, setWorkflowStatuses] = useState<Record<string, WorkflowStatus>>({});

  useEffect(() => {
    if (!id) return;
    api.getBounty(id).then(setBounty);
    api.getBountyLeaderboard(id).then((r) => setLeaderboard(r.items));
    api.getBountySettlement(id).then(setSettlement).catch(() => {});
  }, [id]);

  useEffect(() => {
    if (!leaderboard.length) return;
    let cancelled = false;
    async function loadStatuses() {
      const entries = await Promise.all(
        leaderboard.map(async (entry) => {
          try {
            const status = await api.getSubmissionStatus(entry.submission_id);
            return [entry.submission_id, status] as const;
          } catch { return null; }
        }),
      );
      if (cancelled) return;
      setWorkflowStatuses(
        Object.fromEntries(entries.filter((e): e is readonly [string, WorkflowStatus] => e !== null)),
      );
    }
    void loadStatuses();
    const timer = window.setInterval(() => void loadStatuses(), 10000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [leaderboard]);

  if (!bounty) return <PageSkeleton />;

  const budgetPct = bounty.verification_budget
    ? Math.round((bounty.verifications_used / bounty.verification_budget) * 100)
    : 0;

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 text-xs text-muted mb-3">
          <Link to="/bounties" className="hover:text-accent transition-colors">Bounties</Link>
          <span className="text-border-bright">/</span>
          <span className="text-gray-400 truncate">{bounty.title}</span>
        </div>
        <div className="flex items-start gap-3 mb-3">
          <h2 className="page-title flex-1">{bounty.title}</h2>
          <StatusBadge status={bounty.status} />
        </div>
        <WorkflowTimeline status={bounty.status} />
        <p className="text-muted text-xs font-mono mt-3">{bounty.bounty_id}</p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="card p-5 relative overflow-hidden group">
          <div
            className="absolute top-0 left-0 right-0 h-[2px] opacity-40 group-hover:opacity-70 transition-opacity"
            style={{ background: bounty.escrow_amount >= 10000 ? "#d4a843" : bounty.escrow_amount >= 1000 ? "#94a3b8" : "#c87a50" }}
          />
          <p className="section-heading mb-2">Escrow</p>
          <EscrowAmount amount={bounty.escrow_amount} className="text-2xl" />
        </div>
        <StatCard label="Tier" value={bounty.tier} accentColor="#818cf8" />
        <StatCard label="Deadline" value={bounty.deadline ? formatDateTime(bounty.deadline) : "None"} />
        <StatCard label="Submissions" value={bounty.submission_count} accentColor="#22c55e" />
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Principal" value={truncateId(bounty.principal_id)} />
        <StatCard label="Verifications" value={`${bounty.verifications_used} / ${bounty.verification_budget}`} />
        <StatCard label="Created" value={formatDateTime(bounty.created_at)} />
        <StatCard label="Updated" value={formatDateTime(bounty.updated_at)} />
      </div>

      {/* Verification Budget */}
      <div className="card p-6">
        <h3 className="section-heading mb-4">Verification Budget</h3>
        <div className="flex items-center gap-4">
          <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-panel-soft border border-border/50">
            <div
              className="h-full rounded-full bg-accent-gradient transition-all duration-500"
              style={{ width: `${Math.min(budgetPct, 100)}%` }}
            />
          </div>
          <span className="text-sm font-mono text-muted tabular-nums">
            {bounty.verifications_used}/{bounty.verification_budget}
          </span>
        </div>
      </div>

      {/* Submission Leaderboard */}
      {leaderboard.length > 0 && (
        <div className="card p-6 relative overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-accent/30 via-transparent to-transparent" />
          <h3 className="section-heading mb-5">Submission Leaderboard</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left">
                  <th className="pb-2.5 pr-4 section-heading">#</th>
                  <th className="pb-2.5 pr-4 section-heading">Architect</th>
                  <th className="pb-2.5 pr-4 section-heading">Metrics</th>
                  <th className="pb-2.5 pr-4 section-heading">Verified</th>
                  <th className="pb-2.5 pr-4 section-heading">Workflow</th>
                  <th className="pb-2.5 section-heading">Runs</th>
                </tr>
              </thead>
              <tbody>
                {leaderboard.map((sub, idx) => (
                  <tr key={sub.submission_id} className="border-b border-border/50 hover:bg-panel-soft/30 transition-colors">
                    <td className="py-3 pr-4 tabular-nums font-medium">
                      {idx === 0 ? (
                        <span className="text-tier-lattice">{sub.rank}</span>
                      ) : idx === 1 ? (
                        <span className="text-tier-edge">{sub.rank}</span>
                      ) : idx === 2 ? (
                        <span className="text-tier-node">{sub.rank}</span>
                      ) : (
                        <span className="text-muted">{sub.rank}</span>
                      )}
                    </td>
                    <td className="py-3 pr-4">
                      <Link to={`/originator/${sub.architect_id}`} className="text-accent hover:text-accent-bright transition-colors font-mono text-xs">
                        {truncateId(sub.architect_id)}
                      </Link>
                    </td>
                    <td className="py-3 pr-4 font-mono text-xs">
                      {Object.entries(sub.metric_values).map(([k, v]) => (
                        <span key={k} className="inline-flex gap-1 mr-3">
                          <span className="text-muted">{k}:</span>
                          <span className="text-white font-medium">{v}</span>
                        </span>
                      ))}
                    </td>
                    <td className="py-3 pr-4 text-muted text-xs">{formatDateTime(sub.verified_at)}</td>
                    <td className="py-3 pr-4">
                      <StatusBadge status={workflowStatuses[sub.submission_id]?.verification_status ?? "verified"} />
                    </td>
                    <td className="py-3">
                      {workflowStatuses[sub.submission_id]?.runs?.length ? (
                        <VerificationRunList runs={workflowStatuses[sub.submission_id].runs} />
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Settlement */}
      {settlement && settlement.status === "settled" && (
        <div className="card p-6 relative overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-ok/30 via-transparent to-transparent" />
          <h3 className="section-heading mb-5">Settlement Breakdown</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left">
                  <th className="pb-2.5 pr-4 section-heading">Recipient</th>
                  <th className="pb-2.5 pr-4 section-heading">Role</th>
                  <th className="pb-2.5 pr-4 section-heading">Amount</th>
                  <th className="pb-2.5 section-heading">Trace</th>
                </tr>
              </thead>
              <tbody>
                {settlement.payouts.map((p) => (
                  <tr key={`${p.recipient_id}-${p.role}`} className="border-b border-border/50 hover:bg-panel-soft/30 transition-colors">
                    <td className="py-3 pr-4 font-mono text-xs text-accent">{truncateId(p.recipient_id)}</td>
                    <td className="py-3 pr-4"><span className="tag capitalize">{p.role}</span></td>
                    <td className="py-3 pr-4 font-mono font-medium text-white">{formatUsd(p.amount)}</td>
                    <td className="py-3 text-muted font-mono text-xs">{p.atom_fqdn || truncateId(p.cdg_hash ?? "") || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
