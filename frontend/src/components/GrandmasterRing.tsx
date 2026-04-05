import type { GrandmasterStatus } from "../api/types";

interface GrandmasterRingProps {
  status: GrandmasterStatus | null;
  children: React.ReactNode;
}

const TRACK_COLORS: Record<string, string> = {
  originator: "#f59e0b",
  architect: "#3b82f6",
  vanguard: "#10b981",
  evangelist: "#8b5cf6",
};

export default function GrandmasterRing({ status, children }: GrandmasterRingProps) {
  if (!status) return <>{children}</>;

  const qualifiedTracks = Object.entries(status.tracks)
    .filter(([, v]) => v)
    .map(([k]) => k);

  if (qualifiedTracks.length === 0) return <>{children}</>;

  const color = status.is_grandmaster
    ? "#f59e0b"
    : TRACK_COLORS[qualifiedTracks[0]] ?? "#38bdf8";

  return (
    <div
      className="relative inline-block rounded-full"
      style={{
        boxShadow: `0 0 ${status.is_grandmaster ? 12 : 6}px ${color}, 0 0 ${status.is_grandmaster ? 24 : 12}px ${color}40`,
        padding: 2,
        border: `2px solid ${color}`,
      }}
    >
      {children}
    </div>
  );
}
