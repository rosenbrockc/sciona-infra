import { Link } from "react-router-dom";
import type { LeaderboardEntry, BadgeDefinition, UserBadge } from "../api/types";
import BadgeShowcase from "./BadgeShowcase";
import { formatUsd } from "../utils/format";

interface LeaderboardTableProps {
  entries: LeaderboardEntry[];
  compact?: boolean;
  badges?: BadgeDefinition[];
  badgesByUser?: Record<string, UserBadge[]>;
}

export default function LeaderboardTable({ entries, compact, badges, badgesByUser }: LeaderboardTableProps) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left border-b border-border">
          <th className="pb-2.5 pr-4 section-heading w-10">#</th>
          <th className="pb-2.5 pr-4 section-heading">Originator</th>
          {badges && badges.length > 0 && <th className="pb-2.5 pr-4 section-heading">Badges</th>}
          <th className="pb-2.5 pr-4 section-heading">Impact</th>
          {!compact && <th className="pb-2.5 pr-4 section-heading">Bounties</th>}
          <th className="pb-2.5 pr-4 section-heading">Value</th>
          {!compact && <th className="pb-2.5 section-heading">Atoms</th>}
        </tr>
      </thead>
      <tbody>
        {entries.map((e, index) => (
          <tr key={e.originator_id} className="border-b border-border/50 hover:bg-panel-soft/30 transition-colors group">
            <td className="py-3 pr-4 text-muted tabular-nums font-medium">{index + 1}</td>
            <td className="py-3 pr-4">
              <Link to={`/originator/${e.originator_id}`} className="text-gray-200 group-hover:text-accent transition-colors font-medium">
                {e.github_login || e.originator_id.slice(0, 8)}
              </Link>
            </td>
            {badges && badges.length > 0 && (
              <td className="py-3 pr-4">
                <BadgeShowcase
                  earned={badgesByUser?.[e.originator_id] ?? []}
                  badges={badges}
                  max={3}
                />
              </td>
            )}
            <td className="py-3 pr-4 font-mono text-accent font-medium tabular-nums">{e.h_index ?? "-"}</td>
            {!compact && <td className="py-3 pr-4 text-muted tabular-nums">{e.bounty_count}</td>}
            <td className="py-3 pr-4 font-mono text-white tabular-nums">{formatUsd(e.total_bounty_value)}</td>
            {!compact && <td className="py-3 text-muted tabular-nums">{e.atom_count}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
