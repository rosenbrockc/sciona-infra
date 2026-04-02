import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { OriginatorImpact } from "../api/types";
import StatCard from "../components/StatCard";

export default function OriginatorProfile() {
  const { id } = useParams<{ id: string }>();
  const [impact, setImpact] = useState<OriginatorImpact | null>(null);

  useEffect(() => {
    if (!id) return;
    api.getOriginatorImpact(id).then(setImpact);
  }, [id]);

  if (!impact) return <p className="text-muted">Loading...</p>;

  return (
    <div className="space-y-8">
      <h2 className="text-xl font-bold">{impact.github_username || impact.originator_id}</h2>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="h-index" value={impact.h_index} />
        <StatCard label="Bounties" value={impact.bounty_count} />
        <StatCard label="Total Value" value={`$${impact.total_bounty_value.toLocaleString()}`} />
        <StatCard label="Atoms" value={impact.atom_count} />
      </div>

      <div className="bg-panel border border-border rounded-lg p-5">
        <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">Profile</h3>
        <p className="text-sm text-gray-300">
          Affiliation: {impact.affiliation || "Not provided"}
        </p>
      </div>
    </div>
  );
}
