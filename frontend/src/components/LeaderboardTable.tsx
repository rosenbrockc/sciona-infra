import { Link } from "react-router-dom";
import type { LeaderboardEntry, BadgeDefinition, UserBadge } from "../api/types";
import BadgeShowcase from "./BadgeShowcase";

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
        <tr className="text-left text-muted border-b border-border">
          <th className="pb-2 pr-4">#</th>
          <th className="pb-2 pr-4">Originator</th>
          {badges && <th className="pb-2 pr-4">Badges</th>}
          <th className="pb-2 pr-4">Impact</th>
          {!compact && <th className="pb-2 pr-4">Bounties</th>}
          <th className="pb-2 pr-4">Total Value</th>
          {!compact && <th className="pb-2">Atoms</th>}
        </tr>
      </thead>
      <tbody>
        {entries.map((e, index) => (
          <tr key={e.originator_id} className="border-b border-border/50">
            <td className="py-2 pr-4 text-muted">{index + 1}</td>
            <td className="py-2 pr-4">
              <Link to={`/originator/${e.originator_id}`} className="text-accent hover:underline">
                {e.github_login || e.originator_id}
              </Link>
            </td>
            {badges && (
              <td className="py-2 pr-4">
                <BadgeShowcase
                  earned={badgesByUser?.[e.originator_id] ?? []}
                  badges={badges}
                  max={3}
                />
              </td>
            )}
            <td className="py-2 pr-4 font-mono">{e.h_index ?? "n/a"}</td>
            {!compact && <td className="py-2 pr-4">{e.bounty_count}</td>}
            <td className="py-2 pr-4 font-mono">${e.total_bounty_value.toLocaleString()}</td>
            {!compact && <td className="py-2">{e.atom_count}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
