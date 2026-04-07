const colors: Record<string, { bg: string; text: string; dot: string }> = {
  draft:        { bg: "bg-panel-bright/60", text: "text-muted", dot: "bg-muted/50" },
  open:         { bg: "bg-accent/10", text: "text-accent", dot: "bg-accent" },
  submitted:    { bg: "bg-accent-2/10", text: "text-accent-2", dot: "bg-accent-2" },
  active:       { bg: "bg-ok/10", text: "text-ok", dot: "bg-ok" },
  verified:     { bg: "bg-ok/10", text: "text-ok", dot: "bg-ok" },
  verification: { bg: "bg-warn/10", text: "text-warn", dot: "bg-warn" },
  pending:      { bg: "bg-warn/10", text: "text-warn", dot: "bg-warn animate-pulse" },
  running:      { bg: "bg-accent/10", text: "text-accent", dot: "bg-accent animate-pulse" },
  settled:      { bg: "bg-accent-2/10", text: "text-accent-2", dot: "bg-accent-2" },
  cancelled:    { bg: "bg-bad/10", text: "text-bad", dot: "bg-bad" },
  expired:      { bg: "bg-bad/8", text: "text-bad/60", dot: "bg-bad/50" },
  rejected:     { bg: "bg-bad/10", text: "text-bad", dot: "bg-bad" },
  approved:     { bg: "bg-ok/10", text: "text-ok", dot: "bg-ok" },
};

export default function StatusBadge({ status }: { status: string }) {
  const c = colors[status] ?? { bg: "bg-panel-bright/40", text: "text-muted", dot: "bg-muted/50" };
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium border border-current/10 ${c.bg} ${c.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
      {status}
    </span>
  );
}
