import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { AtomSummaryResponse } from "../api/types";
import Pagination from "../components/Pagination";
import EmptyState from "../components/EmptyState";
import { TableSkeleton } from "../components/LoadingSkeleton";

const LIMIT = 20;

function VerdictBadge({ value }: { value: string }) {
  if (!value) return <span className="text-muted text-xs">—</span>;
  const colors: Record<string, string> = {
    trusted: "text-ok bg-ok/10 border-ok/20",
    acceptable_with_limits: "text-amber-400 bg-amber-400/10 border-amber-400/20",
    limited_acceptability: "text-orange-400 bg-orange-400/10 border-orange-400/20",
    misleading: "text-bad bg-bad/10 border-bad/20",
    broken: "text-bad bg-bad/10 border-bad/20",
    unknown: "text-muted bg-muted/10 border-muted/20",
  };
  const cls = colors[value] ?? colors.unknown;
  const label = value.replace(/_/g, " ");
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}>
      {label}
    </span>
  );
}

function RiskBadge({ value }: { value: string }) {
  if (!value) return <span className="text-muted text-xs">—</span>;
  const colors: Record<string, string> = {
    low: "text-ok bg-ok/10 border-ok/20",
    medium: "text-amber-400 bg-amber-400/10 border-amber-400/20",
    high: "text-bad bg-bad/10 border-bad/20",
  };
  const cls = colors[value] ?? "text-muted bg-muted/10 border-muted/20";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}>
      {value}
    </span>
  );
}

function LicenseBadge({ expression, status }: { expression: string; status: string }) {
  if (!expression) return <span className="text-muted text-xs">—</span>;
  const colors: Record<string, string> = {
    approved: "text-ok bg-ok/10 border-ok/20",
    restricted: "text-bad bg-bad/10 border-bad/20",
    needs_legal_review: "text-amber-400 bg-amber-400/10 border-amber-400/20",
    unknown: "text-muted bg-muted/10 border-muted/20",
  };
  const cls = colors[status] ?? colors.unknown;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border ${cls}`}>
      <svg className="w-3 h-3 opacity-70" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
      {expression}
    </span>
  );
}

export default function AtomList() {
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get("q") ?? "";
  const offset = Number(searchParams.get("offset") ?? 0);
  const [atoms, setAtoms] = useState<AtomSummaryResponse[] | null>(null);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState(search);

  useEffect(() => {
    setAtoms(null);
    api
      .getAtoms({ search: search || undefined, limit: LIMIT, offset })
      .then((r) => {
        setAtoms(r.items);
        setTotal(r.total);
      });
  }, [search, offset]);

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    const p = new URLSearchParams();
    if (query) p.set("q", query);
    setSearchParams(p);
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h2 className="page-title">Atom Registry</h2>
        <p className="page-subtitle">Verified algorithmic building blocks ready for composition.</p>
      </div>

      <form onSubmit={handleSearch} className="flex gap-2">
        <div className="relative flex-1">
          <svg className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search atoms by name or description..."
            className="input-field pl-10"
          />
        </div>
        <button type="submit" className="btn-primary">
          Search
        </button>
      </form>

      {atoms === null ? (
        <div className="card p-5">
          <TableSkeleton rows={8} cols={5} />
        </div>
      ) : atoms.length === 0 ? (
        <div className="card">
          <EmptyState
            title="No atoms found"
            description={search ? `No results for "${search}". Try a different query.` : "Atoms will appear here once published."}
          />
        </div>
      ) : (
        <>
          <div className="card overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left border-b border-border">
                    <th className="px-4 py-3 section-heading">Atom</th>
                    <th className="px-4 py-3 section-heading">License</th>
                    <th className="px-4 py-3 section-heading">Verdict</th>
                    <th className="px-4 py-3 section-heading">Risk</th>
                    <th className="px-4 py-3 section-heading">Acceptability</th>
                    <th className="px-4 py-3 section-heading text-right">Version</th>
                  </tr>
                </thead>
                <tbody>
                  {atoms.map((a) => (
                    <tr key={a.fqdn} className="border-b border-border/50 hover:bg-panel-soft/30 transition-colors group">
                      <td className="px-4 py-3">
                        <Link to={`/atoms/${a.fqdn}`} className="block">
                          <span className="font-mono text-accent text-sm group-hover:text-accent-bright transition-colors">
                            {a.fqdn.replace(/^sciona\.atoms\./, "")}
                          </span>
                          <p className="text-xs text-muted mt-0.5 line-clamp-1 max-w-md">
                            {a.description || "No description"}
                          </p>
                        </Link>
                      </td>
                      <td className="px-4 py-3">
                        <LicenseBadge expression={a.license_expression} status={a.license_status} />
                      </td>
                      <td className="px-4 py-3">
                        <VerdictBadge value={a.overall_verdict} />
                      </td>
                      <td className="px-4 py-3">
                        <RiskBadge value={a.risk_tier} />
                      </td>
                      <td className="px-4 py-3">
                        <VerdictBadge value={a.acceptability_band} />
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className="text-xs font-mono text-muted">
                          {a.latest_semver ? `v${a.latest_semver}` : a.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <Pagination
            total={total}
            limit={LIMIT}
            offset={offset}
            onChange={(o) => {
              const p = new URLSearchParams(searchParams);
              p.set("offset", String(o));
              setSearchParams(p);
            }}
          />
        </>
      )}
    </div>
  );
}
