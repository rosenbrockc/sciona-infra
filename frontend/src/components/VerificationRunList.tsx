import type { VerificationRun } from "../api/types";

const statusColors: Record<string, string> = {
  pending: "text-warn",
  running: "text-accent",
  completed: "text-ok",
  passed: "text-ok",
  failed: "text-bad",
};

export default function VerificationRunList({ runs }: { runs: VerificationRun[] }) {
  if (!runs.length) {
    return <p className="text-sm text-muted italic">No verification runs yet.</p>;
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-border text-left">
          <th className="pb-2.5 pr-4 section-heading">#</th>
          <th className="pb-2.5 pr-4 section-heading">Status</th>
          <th className="pb-2.5 pr-4 section-heading">Deterministic</th>
          <th className="pb-2.5 pr-4 section-heading">Metrics</th>
          <th className="pb-2.5 section-heading">Output Hash</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run, i) => (
          <tr key={i} className="border-b border-border/50 hover:bg-panel-soft/30 transition-colors">
            <td className="py-2.5 pr-4 text-muted tabular-nums">{i + 1}</td>
            <td className={`py-2.5 pr-4 font-medium ${statusColors[run.status] ?? "text-muted"}`}>
              {run.status}
            </td>
            <td className="py-2.5 pr-4 text-muted">
              {run.is_deterministic === null ? "-" : run.is_deterministic ? "yes" : "no"}
            </td>
            <td className="py-2.5 pr-4 font-mono text-xs">
              {run.metric_values
                ? Object.entries(run.metric_values).map(([k, v]) => `${k}: ${v}`).join(", ")
                : "-"}
            </td>
            <td className="py-2.5 font-mono text-xs text-muted">
              {run.output_hash ? `${run.output_hash.slice(0, 12)}...` : "-"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
