const STAGES = ["draft", "open", "active", "verification", "settled"] as const;

const stageColors: Record<string, string> = {
  draft: "border-muted/40 bg-muted/10 text-muted",
  open: "border-accent/50 bg-accent/15 text-accent",
  active: "border-ok/50 bg-ok/15 text-ok",
  verification: "border-warn/50 bg-warn/15 text-warn",
  settled: "border-accent-2/50 bg-accent-2/15 text-accent-2",
  cancelled: "border-bad/50 bg-bad/15 text-bad",
};

function stageIndex(status: string): number {
  const idx = STAGES.indexOf(status as (typeof STAGES)[number]);
  return idx === -1 ? 0 : idx;
}

export default function WorkflowTimeline({ status }: { status: string }) {
  const current = stageIndex(status);
  const isCancelled = status === "cancelled";

  return (
    <div className="flex items-center gap-0.5">
      {STAGES.map((stage, i) => {
        const reached = isCancelled ? false : i <= current;
        const isCurrent = i === current && !isCancelled;

        return (
          <div key={stage} className="flex items-center">
            <div className="flex flex-col items-center gap-1.5">
              <div
                className={`h-3 w-3 rounded-full border-2 transition-all ${
                  reached
                    ? stageColors[stage]
                    : "border-border-bright bg-transparent"
                } ${isCurrent ? "ring-2 ring-accent/30 ring-offset-1 ring-offset-bg" : ""}`}
              />
              <span className={`text-[10px] font-medium ${reached ? "text-gray-300" : "text-muted/50"}`}>
                {stage}
              </span>
            </div>
            {i < STAGES.length - 1 && (
              <div
                className={`h-[2px] w-6 mx-0.5 -mt-4 rounded-full transition-colors ${
                  reached && i < current ? "bg-accent/40" : "bg-border"
                }`}
              />
            )}
          </div>
        );
      })}
      {isCancelled && (
        <div className="flex flex-col items-center gap-1.5 ml-2">
          <div className="h-3 w-3 rounded-full border-2 border-bad/50 bg-bad/15 ring-2 ring-bad/30 ring-offset-1 ring-offset-bg" />
          <span className="text-[10px] font-medium text-bad">cancelled</span>
        </div>
      )}
    </div>
  );
}
