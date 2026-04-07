import { Link } from "react-router-dom";
import type { ArchitectLeaderboardEntry } from "../api/types";
import EscrowAmount from "./EscrowAmount";
import { formatNumber } from "../utils/format";

interface ArchitectTableProps {
  entries: ArchitectLeaderboardEntry[];
  compact?: boolean;
}

function RankCell({ rank }: { rank: number }) {
  if (rank === 1) return <span className="text-tier-lattice font-bold">{rank}</span>;
  if (rank === 2) return <span className="text-tier-edge font-bold">{rank}</span>;
  if (rank === 3) return <span className="text-tier-node font-bold">{rank}</span>;
  return <span className="text-muted">{rank}</span>;
}

export default function ArchitectTable({ entries, compact }: ArchitectTableProps) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left border-b border-border">
          <th className="pb-2.5 pr-4 section-heading w-10">#</th>
          <th className="pb-2.5 pr-4 section-heading">Architect</th>
          <th className="pb-2.5 pr-4 section-heading">Rep</th>
          <th className="pb-2.5 pr-4 section-heading">Wins</th>
          {!compact && <th className="pb-2.5 pr-4 section-heading">Submissions</th>}
          <th className="pb-2.5 pr-4 section-heading">Earned</th>
          {!compact && <th className="pb-2.5 pr-4 section-heading">Bounties</th>}
          {!compact && <th className="pb-2.5 section-heading">Atoms Used</th>}
        </tr>
      </thead>
      <tbody>
        {entries.map((e, index) => {
          const winRate = e.submission_count > 0
            ? Math.round((e.win_count / e.submission_count) * 100)
            : 0;
          return (
            <tr key={e.architect_id} className="border-b border-border/50 hover:bg-panel-soft/30 transition-colors group">
              <td className="py-3 pr-4 tabular-nums font-medium">
                <RankCell rank={index + 1} />
              </td>
              <td className="py-3 pr-4">
                <Link to={`/originator/${e.architect_id}`} className="text-gray-200 group-hover:text-accent transition-colors font-medium">
                  {e.github_login || e.architect_id.slice(0, 8)}
                </Link>
              </td>
              <td className="py-3 pr-4 font-mono text-accent font-medium tabular-nums">
                {formatNumber(e.reputation)}
              </td>
              <td className="py-3 pr-4">
                <span className="font-mono text-white font-medium tabular-nums">{e.win_count}</span>
                {!compact && e.submission_count > 0 && (
                  <span className="text-muted text-xs ml-1.5">({winRate}%)</span>
                )}
              </td>
              {!compact && <td className="py-3 pr-4 text-muted tabular-nums">{e.submission_count}</td>}
              <td className="py-3 pr-4">
                <EscrowAmount amount={e.total_earned} className="text-sm" />
              </td>
              {!compact && <td className="py-3 pr-4 text-muted tabular-nums">{e.bounties_won}</td>}
              {!compact && <td className="py-3 text-muted tabular-nums">{e.distinct_atoms_used}</td>}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
