interface StatCardProps {
  label: string;
  value: string | number;
  sub?: string;
  trend?: "up" | "down" | "neutral";
}

export default function StatCard({ label, value, sub, trend }: StatCardProps) {
  return (
    <div className="card p-5 group">
      <p className="section-heading mb-2">{label}</p>
      <div className="flex items-baseline gap-2">
        <p className="text-2xl font-bold text-white tracking-tight">{value}</p>
        {trend === "up" && <span className="text-ok text-xs font-medium">+</span>}
        {trend === "down" && <span className="text-bad text-xs font-medium">-</span>}
      </div>
      {sub && <p className="text-muted text-xs mt-1.5">{sub}</p>}
    </div>
  );
}
