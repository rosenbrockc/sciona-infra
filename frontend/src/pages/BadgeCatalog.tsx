import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { BadgeDefinition } from "../api/types";
import HexBadge from "../components/HexBadge";

const TRACK_ORDER = ["originator", "architect", "vanguard", "evangelist"];
const TRACK_LABELS: Record<string, string> = {
  originator: "Originator",
  architect: "Architect",
  vanguard: "Vanguard",
  evangelist: "Evangelist",
};

export default function BadgeCatalog() {
  const [badges, setBadges] = useState<BadgeDefinition[]>([]);
  const navigate = useNavigate();

  useEffect(() => {
    api.getBadgeCatalog().then(setBadges).catch(() => {});
  }, []);

  const byTrack = TRACK_ORDER.map((track) => ({
    track,
    label: TRACK_LABELS[track] ?? track,
    items: badges.filter((b) => b.track === track),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="space-y-8">
      <h2 className="text-xl font-bold">Badge Catalog</h2>
      <p className="text-muted text-sm">
        Earn badges by contributing real value to the Algorithmic Commons. Badges come in three tiers:
        Node (bronze), Edge (silver), and Lattice (gold).
      </p>

      {byTrack.map((group) => (
        <div key={group.track} className="bg-panel border border-border rounded-lg p-5">
          <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">
            {group.label} Track
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
            {group.items.map((badge) => (
              <button
                key={badge.badge_id}
                type="button"
                onClick={() => navigate(`/badges/${badge.badge_id}`)}
                className="flex items-start gap-3 p-3 rounded-lg bg-panel-soft border border-border/50 hover:border-accent/40 transition-colors text-left"
              >
                <HexBadge iconSlug={badge.icon_slug} tier="node" size={40} />
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-200">{badge.display_name}</p>
                  <p className="text-xs text-muted mt-0.5 line-clamp-2">{badge.description}</p>
                </div>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
