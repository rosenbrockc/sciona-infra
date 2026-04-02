import type { BenchmarkRecord } from "../api/types";

export default function BenchmarkTable({ records }: { records: BenchmarkRecord[] }) {
  if (!records.length) return <p className="text-muted text-sm">No benchmarks recorded.</p>;

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-muted border-b border-border">
          <th className="pb-2 pr-4">Metric</th>
          <th className="pb-2 pr-4">Value</th>
          <th className="pb-2 pr-4">Dataset</th>
          <th className="pb-2">Recorded</th>
        </tr>
      </thead>
      <tbody>
        {records.map((r, i) => (
          <tr key={i} className="border-b border-border/50">
            <td className="py-2 pr-4 font-mono">{r.metric_name}</td>
            <td className="py-2 pr-4 font-mono">{r.metric_value.toFixed(3)}</td>
            <td className="py-2 pr-4">{r.dataset_tag}</td>
            <td className="py-2 text-muted">{r.measured_at}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
