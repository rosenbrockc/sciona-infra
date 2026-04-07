import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ComputePreserved } from "../api/types";
import StatCard from "../components/StatCard";
import { PageSkeleton } from "../components/LoadingSkeleton";
import { formatUsd, formatNumber } from "../utils/format";

export default function ESGDashboard() {
  const [stats, setStats] = useState<ComputePreserved | null>(null);

  useEffect(() => {
    api.getComputePreserved().then(setStats);
  }, []);

  if (!stats) return <PageSkeleton />;

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h2 className="page-title">Impact Dashboard</h2>
        <p className="page-subtitle">
          Environmental and social impact of the Algorithmic Commons ecosystem.
        </p>
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Compute Preserved"
          value={`${formatNumber(stats.estimated_tokens_saved)} tokens`}
          sub="Estimated inference tokens saved"
          accentColor="#22c55e"
        />
        <StatCard
          label="Cost Avoided"
          value={formatUsd(stats.estimated_cost_saved_usd)}
          sub="Cloud compute savings"
          accentColor="#34d399"
        />
        <StatCard
          label="Bounties Settled"
          value={stats.total_bounties_settled}
          sub="Completed verification cycles"
          accentColor="#38bdf8"
        />
        <StatCard
          label="Distributed to Contributors"
          value={formatUsd(stats.total_escrow_value)}
          sub="Total Shapley-value payouts"
          accentColor="#818cf8"
        />
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        <div className="card p-6 relative overflow-hidden">
          <div className="absolute top-0 left-0 w-[2px] h-full bg-gradient-to-b from-ok/50 via-ok/20 to-transparent" />
          <div className="flex items-center gap-3 mb-4">
            <div className="w-9 h-9 rounded-lg bg-ok/10 border border-ok/20 flex items-center justify-center">
              <svg className="w-4.5 h-4.5 text-ok" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h3 className="text-sm font-semibold text-white">Environmental Impact</h3>
          </div>
          <p className="text-gray-400 text-sm leading-relaxed">
            By reusing verified algorithmic atoms instead of retraining from scratch,
            the Algorithmic Commons has preserved an estimated{" "}
            <span className="text-white font-mono font-medium">
              {formatNumber(stats.estimated_tokens_saved)} tokens
            </span>{" "}
            of compute, saving approximately{" "}
            <span className="text-ok font-mono font-medium">
              {formatUsd(stats.estimated_cost_saved_usd)}
            </span>{" "}
            in cloud compute costs. This represents a measurable reduction in
            carbon emissions from redundant model training and evaluation.
          </p>
        </div>

        <div className="card p-6 relative overflow-hidden">
          <div className="absolute top-0 left-0 w-[2px] h-full bg-gradient-to-b from-accent-2/50 via-accent-2/20 to-transparent" />
          <div className="flex items-center gap-3 mb-4">
            <div className="w-9 h-9 rounded-lg bg-accent-2/10 border border-accent-2/20 flex items-center justify-center">
              <svg className="w-4.5 h-4.5 text-accent-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
              </svg>
            </div>
            <h3 className="text-sm font-semibold text-white">Social Impact</h3>
          </div>
          <p className="text-gray-400 text-sm leading-relaxed">
            <span className="text-white font-mono font-medium">{formatUsd(stats.total_escrow_value)}</span>{" "}
            has been distributed across{" "}
            <span className="text-white font-medium">{stats.total_bounties_settled}</span>{" "}
            settled bounty cycles through Shapley-value fair allocation.
            Contributors are rewarded proportionally to the verified value they
            create, ensuring equitable compensation for algorithmic innovation.
          </p>
        </div>
      </div>
    </div>
  );
}
