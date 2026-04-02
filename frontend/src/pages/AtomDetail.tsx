import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { AtomDetailResponse, AtomVersionResponse, BenchmarkRecord } from "../api/types";
import BenchmarkTable from "../components/BenchmarkTable";

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

  if (!atom) return <p className="text-muted">Loading...</p>;

  function copyBibtex() {
    navigator.clipboard.writeText(bibtex);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-xl font-bold font-mono text-accent">{atom.fqdn}</h2>
        <p className="text-gray-300 mt-2">{atom.description}</p>
        <div className="flex gap-2 mt-3">
          {atom.domain_tags.map((t) => (
            <span key={t} className="px-3 py-1 bg-panel-soft rounded-full text-xs text-muted border border-border">
              {t}
            </span>
          ))}
        </div>
        <p className="text-muted text-sm mt-2">
          Owner: {atom.owner_github_login || "unknown"}
        </p>
      </div>

      {/* Versions */}
      <div className="bg-panel border border-border rounded-lg p-5">
        <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">Versions</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted border-b border-border">
              <th className="pb-2 pr-4">Version</th>
              <th className="pb-2 pr-4">Fingerprint</th>
              <th className="pb-2 pr-4">Published</th>
              <th className="pb-2">Latest</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.version_id} className="border-b border-border/50">
                <td className="py-2 pr-4 font-mono">{v.semver}</td>
                <td className="py-2 pr-4 font-mono text-xs text-muted">{v.fingerprint}</td>
                <td className="py-2 pr-4 text-muted">{v.created_at}</td>
                <td className="py-2">
                  {v.is_latest && (
                    <span className="px-2 py-0.5 bg-ok/20 text-ok rounded text-xs">latest</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Benchmarks */}
      <div className="bg-panel border border-border rounded-lg p-5">
        <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">Benchmarks</h3>
        <BenchmarkTable records={benchmarks} />
      </div>

      {/* BibTeX */}
      <div className="bg-panel border border-border rounded-lg p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-muted uppercase tracking-wide">BibTeX</h3>
          <button
            onClick={copyBibtex}
            className="px-3 py-1 text-xs rounded bg-panel-soft border border-border text-muted hover:text-gray-200"
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>
        <pre className="text-xs font-mono text-gray-300 bg-bg rounded p-3 overflow-x-auto whitespace-pre-wrap">
          {bibtex}
        </pre>
      </div>
    </div>
  );
}
