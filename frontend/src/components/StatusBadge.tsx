const colors: Record<string, string> = {
  draft: "bg-border-bright/60 text-muted",
  open: "bg-accent/15 text-accent",
  submitted: "bg-accent-2/15 text-accent-2",
  active: "bg-ok/15 text-ok",
  verified: "bg-ok/15 text-ok",
  verification: "bg-warn/15 text-warn",
  pending: "bg-warn/15 text-warn",
  running: "bg-accent/15 text-accent",
  settled: "bg-accent-2/15 text-accent-2",
  cancelled: "bg-bad/15 text-bad",
  expired: "bg-bad/10 text-bad/70",
  rejected: "bg-bad/15 text-bad",
  approved: "bg-ok/15 text-ok",
};

export default function StatusBadge({ status }: { status: string }) {
  const cls = colors[status] ?? "bg-border-bright/40 text-muted";
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${cls}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current opacity-70" />
      {status}
    </span>
  );
}
