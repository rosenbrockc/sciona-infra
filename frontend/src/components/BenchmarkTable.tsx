import type { BenchmarkRecord } from "../api/types";
import { formatDate } from "../utils/format";

export default function BenchmarkTable({ records }: { records: BenchmarkRecord[] }) {
  if (!records.length) {
    return (
      <div className="text-center py-8">
        <p className="text-muted text-sm">No benchmarks recorded yet.</p>
      </div>
    );
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left border-b border-border">
          <th className="pb-2.5 pr-4 section-heading">Metric</th>
          <th className="pb-2.5 pr-4 section-heading">Value</th>
          <th className="pb-2.5 pr-4 section-heading">Dataset</th>
          <th className="pb-2.5 section-heading">Recorded</th>
        </tr>
      </thead>
      <tbody>
        {records.map((r, i) => (
          <tr key={i} className="border-b border-border/50 hover:bg-panel-soft/30 transition-colors">
            <td className="py-2.5 pr-4 font-mono text-accent">{r.metric_name}</td>
            <td className="py-2.5 pr-4 font-mono font-medium text-white">{r.metric_value.toFixed(4)}</td>
            <td className="py-2.5 pr-4"><span className="tag">{r.dataset_tag}</span></td>
            <td className="py-2.5 text-muted">{formatDate(r.measured_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
