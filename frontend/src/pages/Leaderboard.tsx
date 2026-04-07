import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { LeaderboardEntry, ArchitectLeaderboardEntry, BadgeDefinition, UserBadge } from "../api/types";
import LeaderboardTable from "../components/LeaderboardTable";
import ArchitectTable from "../components/ArchitectTable";
import { PageSkeleton } from "../components/LoadingSkeleton";
import EmptyState from "../components/EmptyState";

type Tab = "originators" | "architects";

export default function Leaderboard() {
  const [tab, setTab] = useState<Tab>("originators");
  const [originators, setOriginators] = useState<LeaderboardEntry[] | null>(null);
  const [architects, setArchitects] = useState<ArchitectLeaderboardEntry[] | null>(null);
  const [badges, setBadges] = useState<BadgeDefinition[]>([]);
  const [badgesByUser, setBadgesByUser] = useState<Record<string, UserBadge[]>>({});

  useEffect(() => {
    api.getLeaderboard().then(setOriginators);
    api.getArchitectLeaderboard().then(setArchitects).catch(() => setArchitects([]));
    api.getBadgeCatalog().then(setBadges).catch(() => {});
  }, []);

  useEffect(() => {
    if (!originators) return;
    const ids = originators.slice(0, 20).map((e) => e.originator_id);
    for (const id of ids) {
      if (!badgesByUser[id]) {
        api.getUserBadges(id).then((earned) => {
          setBadgesByUser((prev) => ({ ...prev, [id]: earned }));
        }).catch(() => {});
      }
    }
  }, [originators]);

  if (originators === null) return <PageSkeleton />;

  const tabs: { key: Tab; label: string; description: string }[] = [
    { key: "originators", label: "Top Originators", description: "Researchers who publish and maintain algorithmic atoms." },
    { key: "architects", label: "Top Architects", description: "Practitioners who compose atoms into winning CDGs." },
  ];

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h2 className="page-title">Leaderboard</h2>
        <p className="page-subtitle">Top contributors ranked by impact and earnings.</p>
      </div>

      {/* Tab switcher */}
      <div className="flex gap-1">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 rounded-lg text-sm font-medium border transition-all duration-150 ${
              tab === t.key
                ? "text-white border-border-bright bg-panel-bright"
                : "text-muted border-transparent hover:text-gray-200 hover:bg-panel-soft"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      {tab === "originators" && (
        <div className="card overflow-hidden relative">
          <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-track-originator/30 via-track-originator/10 to-transparent" />
          {originators.length > 0 ? (
            <div className="overflow-x-auto">
              <div className="p-6">
                <LeaderboardTable entries={originators} badges={badges} badgesByUser={badgesByUser} />
              </div>
            </div>
          ) : (
            <EmptyState title="No originators yet" description="Contributors will appear here as they publish atoms and win bounties." />
          )}
        </div>
      )}

      {tab === "architects" && (
        <div className="card overflow-hidden relative">
          <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-track-architect/30 via-track-architect/10 to-transparent" />
          {architects === null ? (
            <div className="p-6"><PageSkeleton /></div>
          ) : architects.length > 0 ? (
            <div className="overflow-x-auto">
              <div className="p-6">
                <ArchitectTable entries={architects} />
              </div>
            </div>
          ) : (
            <EmptyState title="No architects yet" description="Architects will appear here as they submit and win bounties." />
          )}
        </div>
      )}
    </div>
  );
}
