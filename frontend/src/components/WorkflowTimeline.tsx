const STAGES = ["draft", "open", "active", "verification", "settled", "cancelled"] as const;

const stageColors: Record<string, { dot: string; line: string }> = {
  draft: { dot: "bg-border", line: "bg-border" },
  open: { dot: "bg-accent", line: "bg-accent" },
  active: { dot: "bg-ok", line: "bg-ok" },
  verification: { dot: "bg-warn", line: "bg-warn" },
  settled: { dot: "bg-purple-400", line: "bg-purple-400" },
  cancelled: { dot: "bg-bad", line: "bg-bad" },
};

function stageIndex(status: string): number {
  const idx = STAGES.indexOf(status as (typeof STAGES)[number]);
  return idx === -1 ? 0 : idx;
}

export default function WorkflowTimeline({ status }: { status: string }) {
  const current = stageIndex(status);
  const isCancelled = status === "cancelled";

  return (
    <div className="flex items-center gap-1">
      {STAGES.filter((s) => s !== "cancelled").map((stage, i) => {
        const reached = isCancelled ? false : i <= current;
        const colors = reached
          ? stageColors[stage]
          : { dot: "bg-border", line: "bg-border" };

        return (
          <div key={stage} className="flex items-center gap-1">
            <div className="flex flex-col items-center">
              <div
                className={`h-2.5 w-2.5 rounded-full ${colors.dot} ${
                  i === current && !isCancelled ? "ring-2 ring-accent/40" : ""
                }`}
                title={stage}
              />
              <span className="mt-1 text-[10px] text-muted">{stage}</span>
            </div>
            {i < STAGES.length - 2 && (
              <div className={`h-0.5 w-4 ${colors.line} -mt-3`} />
            )}
          </div>
        );
      })}
      {isCancelled && (
        <div className="flex flex-col items-center ml-2">
          <div className="h-2.5 w-2.5 rounded-full bg-bad ring-2 ring-bad/40" title="cancelled" />
          <span className="mt-1 text-[10px] text-bad">cancelled</span>
        </div>
      )}
    </div>
  );
}
