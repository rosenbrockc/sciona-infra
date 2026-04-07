import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api/client";
import type { AtomDetailResponse, AtomVersionResponse, BenchmarkRecord } from "../api/types";
import BenchmarkTable from "../components/BenchmarkTable";
import { PageSkeleton } from "../components/LoadingSkeleton";
import { formatDate } from "../utils/format";

export default function AtomDetail() {
  const { fqdn } = useParams<{ fqdn: string }>();
  const [atom, setAtom] = useState<AtomDetailResponse | null>(null);
  const [versions, setVersions] = useState<AtomVersionResponse[]>([]);
  const [benchmarks, setBenchmarks] = useState<BenchmarkRecord[]>([]);
  const [bibtex, setBibtex] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!fqdn) return;
    api.getAtom(fqdn).then(setAtom);
    api.getAtomVersions(fqdn).then(setVersions);
    api.getAtomBenchmarks(fqdn).then(setBenchmarks);
    api.getAtomBibtex(fqdn).then(setBibtex);
  }, [fqdn]);

  if (!atom) return <PageSkeleton />;

  function copyBibtex() {
    navigator.clipboard.writeText(bibtex);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-6 animate-fade-in max-w-4xl">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 text-xs text-muted mb-2">
          <Link to="/atoms" className="hover:text-accent transition-colors">Atoms</Link>
          <span>/</span>
          <span className="text-gray-400">{atom.fqdn}</span>
        </div>
        <h2 className="text-2xl font-bold font-mono text-accent tracking-tight">{atom.fqdn}</h2>
        <p className="text-gray-400 mt-2 leading-relaxed">{atom.description}</p>
        <div className="flex flex-wrap items-center gap-3 mt-4">
          <div className="flex gap-1.5 flex-wrap">
            {atom.domain_tags.map((t) => (
              <span key={t} className="tag">{t}</span>
            ))}
          </div>
          <span className="text-xs text-muted">
            by <Link to={`/originator/${atom.owner_github_login}`} className="text-accent hover:text-accent/80 transition-colors">{atom.owner_github_login || "unknown"}</Link>
          </span>
        </div>
      </div>

      {/* Versions */}
      <div className="card p-6">
        <h3 className="section-heading mb-4">Version History</h3>
        {versions.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-border">
                  <th className="pb-2.5 pr-4 section-heading">Version</th>
                  <th className="pb-2.5 pr-4 section-heading">Fingerprint</th>
                  <th className="pb-2.5 pr-4 section-heading">Published</th>
                  <th className="pb-2.5 section-heading">Status</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.version_id} className="border-b border-border/50 hover:bg-panel-soft/30 transition-colors">
                    <td className="py-3 pr-4 font-mono font-medium text-white">{v.semver}</td>
                    <td className="py-3 pr-4 font-mono text-xs text-muted">{v.fingerprint.slice(0, 16)}...</td>
                    <td className="py-3 pr-4 text-muted">{formatDate(v.created_at)}</td>
                    <td className="py-3">
                      {v.is_latest && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-ok/15 text-ok rounded-full text-xs font-medium">
                          <span className="w-1.5 h-1.5 rounded-full bg-ok" />
                          latest
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-muted text-sm">No versions published yet.</p>
        )}
      </div>

      {/* Benchmarks */}
      <div className="card p-6">
        <h3 className="section-heading mb-4">Benchmarks</h3>
        <BenchmarkTable records={benchmarks} />
      </div>

      {/* BibTeX */}
      {bibtex && (
        <div className="card p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="section-heading">Citation (BibTeX)</h3>
            <button
              onClick={copyBibtex}
              className="btn-secondary text-xs py-1.5 px-3"
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
          <pre className="text-xs font-mono text-gray-300 bg-bg-soft rounded-lg p-4 overflow-x-auto whitespace-pre-wrap border border-border/50">
            {bibtex}
          </pre>
        </div>
      )}
    </div>
  );
}
