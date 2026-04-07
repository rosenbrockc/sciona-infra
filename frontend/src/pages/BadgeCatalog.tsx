import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { BadgeDefinition } from "../api/types";
import HexBadge from "../components/HexBadge";
import { PageSkeleton } from "../components/LoadingSkeleton";

const TRACK_ORDER = ["originator", "architect", "vanguard", "evangelist"];
const TRACK_META: Record<string, { label: string; description: string; color: string }> = {
  originator: { label: "Originator", description: "Publish and maintain algorithmic building blocks", color: "text-warn" },
  architect: { label: "Architect", description: "Compose and verify solutions to open bounties", color: "text-accent" },
  vanguard: { label: "Vanguard", description: "Stress-test and validate the ecosystem", color: "text-ok" },
  evangelist: { label: "Evangelist", description: "Grow the community through referrals", color: "text-accent-2" },
};

export default function BadgeCatalog() {
  const [badges, setBadges] = useState<BadgeDefinition[] | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api.getBadgeCatalog().then(setBadges).catch(() => setBadges([]));
  }, []);

  if (badges === null) return <PageSkeleton />;

  const byTrack = TRACK_ORDER.map((track) => ({
    track,
    ...TRACK_META[track],
    items: badges.filter((b) => b.track === track),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="space-y-8 animate-fade-in">
      <div>
        <h2 className="page-title">Badge Catalog</h2>
        <p className="page-subtitle">
          Earn badges by contributing real value to the Algorithmic Commons.
          Each badge has three tiers: Node (bronze), Edge (silver), and Lattice (gold).
        </p>
      </div>

      {byTrack.map((group) => (
        <div key={group.track}>
          <div className="mb-4">
            <h3 className={`text-sm font-semibold ${group.color}`}>{group.label} Track</h3>
            <p className="text-xs text-muted mt-0.5">{group.description}</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {group.items.map((badge) => (
              <button
                key={badge.badge_id}
                type="button"
                onClick={() => navigate(`/badges/${badge.badge_id}`)}
                className="card-hover p-4 flex items-start gap-4 text-left"
              >
                <HexBadge iconSlug={badge.icon_slug} tier="node" size={44} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-200">{badge.display_name}</p>
                  <p className="text-xs text-muted mt-1 leading-relaxed line-clamp-2">{badge.description}</p>
                </div>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
