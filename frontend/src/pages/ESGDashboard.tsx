import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ComputePreserved } from "../api/types";
import StatCard from "../components/StatCard";

export default function ESGDashboard() {
  const [stats, setStats] = useState<ComputePreserved | null>(null);

  useEffect(() => {
    api.getComputePreserved().then(setStats);
  }, []);

  if (!stats) return <p className="text-muted">Loading...</p>;

  return (
    <div className="space-y-8">
      <h2 className="text-xl font-bold">ESG Dashboard</h2>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <StatCard
          label="Tokens Saved"
          value={`${(stats.estimated_tokens_saved / 1e9).toFixed(1)}B`}
          sub="Estimated compute preserved"
        />
        <StatCard
          label="Cost Saved"
          value={`$${stats.estimated_cost_saved_usd.toLocaleString()}`}
          sub="Estimated USD savings"
        />
        <StatCard
          label="Bounties Settled"
          value={stats.total_bounties_settled}
          sub="Completed bounty cycles"
        />
        <StatCard
          label="Escrow Distributed"
          value={`$${stats.total_escrow_value.toLocaleString()}`}
          sub="Total payouts to contributors"
        />
      </div>

      <div className="bg-panel border border-border rounded-lg p-6">
        <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">
          Environmental Impact
        </h3>
        <p className="text-gray-300 text-sm leading-relaxed">
          By reusing verified algorithmic atoms instead of retraining from scratch,
          the Algorithmic Commons has preserved an estimated{" "}
          <span className="text-accent font-mono font-bold">
            {(stats.estimated_tokens_saved / 1e9).toFixed(1)}B tokens
          </span>{" "}
          of compute, saving approximately{" "}
          <span className="text-ok font-mono font-bold">
            ${stats.estimated_cost_saved_usd.toLocaleString()}
          </span>{" "}
          in cloud compute costs. This represents a significant reduction in carbon
          emissions from redundant model training.
        </p>
      </div>

      <div className="bg-panel border border-border rounded-lg p-6">
        <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">
          Social Impact
        </h3>
        <p className="text-gray-300 text-sm leading-relaxed">
          {stats.total_escrow_value.toLocaleString()} USD has been distributed
          across {stats.total_bounties_settled} settled bounty cycles through
          Shapley-value fair allocation. The platform continues to preserve
          compute by reusing vetted building blocks instead of duplicating
          training work.
        </p>
      </div>
    </div>
  );
}
