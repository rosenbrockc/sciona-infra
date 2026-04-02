const colors: Record<string, string> = {
  open: "bg-accent/20 text-accent",
  active: "bg-ok/20 text-ok",
  verification: "bg-warn/20 text-warn",
  settled: "bg-purple-500/20 text-purple-400",
  cancelled: "bg-bad/20 text-bad",
};

export default function StatusBadge({ status }: { status: string }) {
  const cls = colors[status] ?? "bg-border text-muted";
  return (
    <span className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-medium ${cls}`}>
      {status}
    </span>
  );
}
