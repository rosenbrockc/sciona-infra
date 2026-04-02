import type { VerificationRun } from "../api/types";

const statusColors: Record<string, string> = {
  pending: "text-warn",
  running: "text-accent",
  passed: "text-ok",
  failed: "text-bad",
};

export default function VerificationRunList({
  runs,
}: {
  runs: VerificationRun[];
}) {
  if (!runs.length) {
    return <p className="text-sm text-muted">No verification runs yet.</p>;
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-border text-left text-muted">
          <th className="pb-2 pr-4">#</th>
          <th className="pb-2 pr-4">Status</th>
          <th className="pb-2 pr-4">Deterministic</th>
          <th className="pb-2 pr-4">Metrics</th>
          <th className="pb-2">Output Hash</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run, i) => (
          <tr key={i} className="border-b border-border/50">
            <td className="py-2 pr-4 text-muted">{i + 1}</td>
            <td
              className={`py-2 pr-4 font-medium ${statusColors[run.status] ?? "text-muted"}`}
            >
              {run.status}
            </td>
            <td className="py-2 pr-4 text-muted">
              {run.is_deterministic === null
                ? "-"
                : run.is_deterministic
                  ? "yes"
                  : "no"}
            </td>
            <td className="py-2 pr-4 font-mono text-xs">
              {run.metric_values
                ? Object.entries(run.metric_values)
                    .map(([k, v]) => `${k}: ${v}`)
                    .join(", ")
                : "-"}
            </td>
            <td className="py-2 font-mono text-xs text-muted">
              {run.output_hash
                ? `${run.output_hash.slice(0, 12)}...`
                : "-"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
