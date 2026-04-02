import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { AtomSummaryResponse } from "../api/types";
import Pagination from "../components/Pagination";

const LIMIT = 12;

export default function AtomList() {
  const [searchParams, setSearchParams] = useSearchParams();
  const search = searchParams.get("q") ?? "";
  const offset = Number(searchParams.get("offset") ?? 0);

  const [atoms, setAtoms] = useState<AtomSummaryResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState(search);

  useEffect(() => {
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
    <div className="space-y-6">
      <h2 className="text-xl font-bold">Atom Registry</h2>

      <form onSubmit={handleSearch} className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search atoms..."
          className="flex-1 bg-panel-soft border border-border rounded px-3 py-2 text-sm text-gray-200 placeholder:text-muted focus:outline-none focus:border-accent"
        />
        <button type="submit" className="px-4 py-2 bg-accent/20 text-accent rounded text-sm font-medium hover:bg-accent/30 border border-accent/40">
          Search
        </button>
      </form>

      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {atoms.map((a) => (
          <Link
            key={a.fqdn}
            to={`/atoms/${a.fqdn}`}
            className="bg-panel border border-border rounded-lg p-4 hover:border-accent/50 transition-colors"
          >
            <p className="font-mono text-accent text-sm mb-1">{a.fqdn}</p>
            <p className="text-sm text-gray-300 line-clamp-2 mb-3">{a.description}</p>
            <div className="flex items-center justify-between">
              <div className="flex gap-1 flex-wrap">
                {a.domain_tags.map((t) => (
                  <span key={t} className="px-2 py-0.5 bg-panel-soft rounded text-xs text-muted">
                    {t}
                  </span>
                ))}
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
    </div>
  );
}
