import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { AtomSummaryResponse } from "../api/types";
import Pagination from "../components/Pagination";
import EmptyState from "../components/EmptyState";
import { CardSkeleton } from "../components/LoadingSkeleton";

const LIMIT = 12;

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
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)}
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
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {atoms.map((a) => (
              <Link
                key={a.fqdn}
                to={`/atoms/${a.fqdn}`}
                className="card-hover p-5 group relative overflow-hidden"
              >
                <div className="absolute top-0 left-0 right-0 h-[1px] opacity-0 group-hover:opacity-100 transition-opacity bg-gradient-to-r from-accent/40 via-accent/20 to-transparent" />
                <p className="font-mono text-accent text-sm mb-1.5 group-hover:text-accent-bright transition-colors">
                  {a.fqdn}
                </p>
                <p className="text-sm text-gray-400 line-clamp-2 mb-4 leading-relaxed">
                  {a.description || "No description provided."}
                </p>
                <div className="flex items-center justify-between">
                  <div className="flex gap-1.5 flex-wrap">
                    {a.domain_tags.slice(0, 3).map((t) => (
                      <span key={t} className="tag">{t}</span>
                    ))}
                    {a.domain_tags.length > 3 && (
                      <span className="tag">+{a.domain_tags.length - 3}</span>
                    )}
                  </div>
                  <span className="text-xs font-mono text-muted">
                    {a.latest_semver ? `v${a.latest_semver}` : a.status}
                  </span>
                </div>
              </Link>
            ))}
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
