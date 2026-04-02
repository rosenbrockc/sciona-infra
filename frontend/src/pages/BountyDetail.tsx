import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
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

export default function BountyDetail() {
  const { id } = useParams<{ id: string }>();
  const [bounty, setBounty] = useState<BountyResponse | null>(null);
  const [leaderboard, setLeaderboard] = useState<SubmissionLeaderboardEntry[]>(
    [],
  );
  const [settlement, setSettlement] = useState<SettlementInfo | null>(null);
  const [workflowStatuses, setWorkflowStatuses] = useState<
    Record<string, WorkflowStatus>
  >({});

  useEffect(() => {
    if (!id) {
      return;
    }
    api.getBounty(id).then(setBounty);
    api.getBountyLeaderboard(id).then((response) => {
      setLeaderboard(response.items);
    });
    api.getBountySettlement(id).then(setSettlement).catch(() => {});
  }, [id]);

  useEffect(() => {
    if (!leaderboard.length) {
      return;
    }

    let cancelled = false;

    async function loadStatuses() {
      const entries = await Promise.all(
        leaderboard.map(async (entry) => {
          try {
            const status = await api.getSubmissionStatus(entry.submission_id);
            return [entry.submission_id, status] as const;
          } catch {
            return null;
          }
        }),
      );
      if (cancelled) {
        return;
      }
      setWorkflowStatuses(
        Object.fromEntries(
          entries.filter(
            (entry): entry is readonly [string, WorkflowStatus] =>
              entry !== null,
          ),
        ),
      );
    }

    void loadStatuses();
    const timer = window.setInterval(() => {
      void loadStatuses();
    }, 10000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [leaderboard]);

  if (!bounty) {
    return <p className="text-muted">Loading...</p>;
  }

  const budgetPct = bounty.verification_budget
    ? Math.round(
        (bounty.verifications_used / bounty.verification_budget) * 100,
      )
    : 0;

  return (
    <div className="space-y-8">
      <div>
        <div className="mb-2 flex items-center gap-3">
          <h2 className="text-xl font-bold">{bounty.title}</h2>
          <StatusBadge status={bounty.status} />
        </div>
        <div className="mt-3">
          <WorkflowTimeline status={bounty.status} />
        </div>
        <p className="text-muted text-sm font-mono">{bounty.bounty_id}</p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Escrow"
          value={`$${bounty.escrow_amount.toLocaleString()}`}
        />
        <StatCard label="Tier" value={bounty.tier} />
        <StatCard label="Deadline" value={bounty.deadline ?? "none"} />
        <StatCard label="Created" value={bounty.created_at} />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Principal" value={bounty.principal_id} />
        <StatCard label="Submissions" value={bounty.submission_count} />
        <StatCard
          label="Verifications"
          value={`${bounty.verifications_used}/${bounty.verification_budget}`}
        />
        <StatCard label="Updated" value={bounty.updated_at} />
      </div>

      <div className="rounded-lg border border-border bg-panel p-5">
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
          Verification Budget
        </h3>
        <div className="flex items-center gap-4">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-panel-soft">
            <div
              className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${budgetPct}%` }}
            />
          </div>
          <span className="text-sm font-mono text-muted">
            {bounty.verifications_used}/{bounty.verification_budget}
          </span>
        </div>
      </div>

      {leaderboard.length > 0 ? (
        <div className="rounded-lg border border-border bg-panel p-5">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-muted">
            Submission Leaderboard
          </h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted">
                <th className="pb-2 pr-4">#</th>
                <th className="pb-2 pr-4">Architect</th>
                <th className="pb-2 pr-4">Metrics</th>
                <th className="pb-2 pr-4">Verified</th>
                <th className="pb-2 pr-4">Workflow</th>
                <th className="pb-2">Runs</th>
              </tr>
            </thead>
            <tbody>
              {leaderboard.map((submission) => (
                <tr
                  key={submission.submission_id}
                  className="border-b border-border/50"
                >
                  <td className="py-2 pr-4 text-muted">{submission.rank}</td>
                  <td className="py-2 pr-4 text-accent">
                    {submission.architect_id}
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs">
                    {Object.entries(submission.metric_values)
                      .map(([key, value]) => `${key}: ${value}`)
                      .join(", ")}
                  </td>
                  <td className="py-2 pr-4 text-muted">
                    {submission.verified_at}
                  </td>
                  <td className="py-2 text-muted">
                    {workflowStatuses[submission.submission_id]
                      ?.verification_status ?? "verified"}
                  </td>
                  <td className="py-2">
                    {workflowStatuses[submission.submission_id]?.runs
                      ?.length ? (
                      <VerificationRunList
                        runs={
                          workflowStatuses[submission.submission_id].runs
                        }
                      />
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {settlement && settlement.status === "settled" ? (
        <div className="rounded-lg border border-border bg-panel p-5">
          <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-muted">
            Settlement Breakdown
          </h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted">
                <th className="pb-2 pr-4">Recipient</th>
                <th className="pb-2 pr-4">Role</th>
                <th className="pb-2 pr-4">Amount</th>
                <th className="pb-2">Trace</th>
              </tr>
            </thead>
            <tbody>
              {settlement.payouts.map((payout) => (
                <tr
                  key={`${payout.recipient_id}-${payout.role}`}
                  className="border-b border-border/50"
                >
                  <td className="py-2 pr-4 font-mono">{payout.recipient_id}</td>
                  <td className="py-2 pr-4">{payout.role}</td>
                  <td className="py-2 pr-4 font-mono">
                    ${payout.amount.toLocaleString()}
                  </td>
                  <td className="py-2 text-muted">
                    {payout.atom_fqdn || payout.cdg_hash || "n/a"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
