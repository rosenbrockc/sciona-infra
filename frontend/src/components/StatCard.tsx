interface StatCardProps {
  label: string;
  value: string | number;
  sub?: string;
  accentColor?: string;
}

export default function StatCard({ label, value, sub, accentColor }: StatCardProps) {
  return (
    <div className="card p-5 relative overflow-hidden group">
      {/* Accent top stripe */}
      <div
        className="absolute top-0 left-0 right-0 h-[2px] opacity-40 group-hover:opacity-70 transition-opacity"
        style={{ background: accentColor ?? "linear-gradient(90deg, #38bdf8, #818cf8)" }}
      />
      <p className="section-heading mb-2">{label}</p>
      <p className="text-2xl font-bold text-white tracking-tight tabular-nums">{value}</p>
      {sub && <p className="text-muted/70 text-xs mt-1.5">{sub}</p>}
    </div>
  );
}
