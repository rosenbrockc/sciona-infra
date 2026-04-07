const colors: Record<string, { bg: string; text: string; dot: string; border: string }> = {
  draft:        { bg: "rgba(26,34,68,0.6)", text: "#7b8ab5",  dot: "rgba(123,138,181,0.5)", border: "rgba(123,138,181,0.15)" },
  open:         { bg: "rgba(56,189,248,0.10)", text: "#38bdf8",  dot: "#38bdf8",  border: "rgba(56,189,248,0.20)" },
  submitted:    { bg: "rgba(168,85,247,0.10)", text: "#a855f7",  dot: "#a855f7",  border: "rgba(168,85,247,0.20)" },
  active:       { bg: "rgba(34,197,94,0.10)",  text: "#22c55e",  dot: "#22c55e",  border: "rgba(34,197,94,0.20)" },
  verified:     { bg: "rgba(34,197,94,0.10)",  text: "#22c55e",  dot: "#22c55e",  border: "rgba(34,197,94,0.20)" },
  verification: { bg: "rgba(245,158,11,0.10)", text: "#f59e0b",  dot: "#f59e0b",  border: "rgba(245,158,11,0.20)" },
  pending:      { bg: "rgba(245,158,11,0.10)", text: "#f59e0b",  dot: "#f59e0b",  border: "rgba(245,158,11,0.20)" },
  running:      { bg: "rgba(56,189,248,0.10)", text: "#38bdf8",  dot: "#38bdf8",  border: "rgba(56,189,248,0.20)" },
  settled:      { bg: "rgba(52,211,153,0.10)", text: "#34d399",  dot: "#34d399",  border: "rgba(52,211,153,0.20)" },
  cancelled:    { bg: "rgba(239,68,68,0.10)",  text: "#ef4444",  dot: "#ef4444",  border: "rgba(239,68,68,0.20)" },
  expired:      { bg: "rgba(239,68,68,0.06)",  text: "#f87171",  dot: "rgba(248,113,113,0.5)", border: "rgba(239,68,68,0.12)" },
  rejected:     { bg: "rgba(239,68,68,0.10)",  text: "#ef4444",  dot: "#ef4444",  border: "rgba(239,68,68,0.20)" },
  approved:     { bg: "rgba(34,197,94,0.10)",  text: "#22c55e",  dot: "#22c55e",  border: "rgba(34,197,94,0.20)" },
};

const pulsing = new Set(["pending", "running"]);

const fallback = { bg: "rgba(26,34,68,0.4)", text: "#7b8ab5", dot: "rgba(123,138,181,0.5)", border: "rgba(123,138,181,0.1)" };

export default function StatusBadge({ status }: { status: string }) {
  const c = colors[status] ?? fallback;
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium whitespace-nowrap"
      style={{ background: c.bg, color: c.text, border: `1px solid ${c.border}` }}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${pulsing.has(status) ? "animate-pulse" : ""}`}
        style={{ background: c.dot }}
      />
      {status}
    </span>
  );
}
