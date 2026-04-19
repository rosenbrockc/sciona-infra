import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api/client";
import type { AtomDetailResponse, AtomVersionResponse, AtomAuditEvidence, BenchmarkRecord } from "../api/types";
import BenchmarkTable from "../components/BenchmarkTable";
import { PageSkeleton } from "../components/LoadingSkeleton";
import { formatDate, formatDateTime } from "../utils/format";

function LicenseBadge({ expression, status, family }: { expression: string; status: string; family: string }) {
  const statusColors: Record<string, string> = {
    approved: "text-ok bg-ok/10 border-ok/20",
    restricted: "text-bad bg-bad/10 border-bad/20",
    needs_legal_review: "text-amber-400 bg-amber-400/10 border-amber-400/20",
    unknown: "text-muted bg-muted/10 border-muted/20",
  };
  const cls = statusColors[status] ?? statusColors.unknown;
  const familyLabel = family && family !== "unknown" ? ` (${family.replace(/_/g, " ")})` : "";
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${cls}`}>
      <svg className="w-3.5 h-3.5 opacity-70" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
      {expression}{familyLabel}
    </span>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${ok ? "bg-ok" : "bg-bad"}`} />
  );
}

function AuditStatusRow({ label, value }: { label: string; value: string }) {
  const colors: Record<string, string> = {
    trusted: "text-ok",
    acceptable_with_limits: "text-amber-400",
    acceptable_with_limits_candidate: "text-amber-400",
    limited_acceptability: "text-orange-400",
    misleading: "text-bad",
    broken: "text-bad",
    low: "text-ok",
    medium: "text-amber-400",
    high: "text-bad",
    ready: "text-ok",
    not_ready: "text-muted",
    missing: "text-muted",
    unknown: "text-muted",
    pass: "text-ok",
    fail: "text-bad",
  };
  const cls = colors[value] ?? "text-gray-400";
  return (
    <div className="flex items-center justify-between py-2 border-b border-border/30 last:border-0">
      <span className="text-sm text-muted">{label}</span>
      <span className={`text-sm font-medium ${cls}`}>
        {value.replace(/_/g, " ")}
      </span>
    </div>
  );
}

function SourceLink({ atom }: { atom: AtomDetailResponse }) {
  if (!atom.source_repo) return null;
  const { repo_url, default_branch } = atom.source_repo;
  const modulePath = atom.source_module_path;

  // Build a GitHub-style link to the source file
  let href = repo_url;
  if (modulePath && repo_url.includes("github.com")) {
    href = `${repo_url.replace(/\.git$/, "")}/blob/${default_branch}/${modulePath}`;
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-2 text-sm text-accent hover:text-accent-bright transition-colors"
    >
      <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 16 16">
        <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
      </svg>
      <span className="font-mono text-xs">
        {atom.source_repo.repo_name}
        {modulePath ? `/${modulePath}` : ""}
      </span>
      <svg className="w-3 h-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
      </svg>
    </a>
  );
}

function AuditEvidenceTable({ evidence }: { evidence: AtomAuditEvidence[] }) {
  if (evidence.length === 0) {
    return <p className="text-muted text-sm">No audit evidence recorded yet.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left border-b border-border">
            <th className="pb-2.5 pr-4 section-heading">Type</th>
            <th className="pb-2.5 pr-4 section-heading">Result</th>
            <th className="pb-2.5 pr-4 section-heading">Source</th>
            <th className="pb-2.5 pr-4 section-heading">Runner</th>
            <th className="pb-2.5 pr-4 section-heading">Duration</th>
            <th className="pb-2.5 pr-4 section-heading">Revision</th>
            <th className="pb-2.5 section-heading">Date</th>
          </tr>
        </thead>
        <tbody>
          {evidence.map((e) => (
            <tr key={e.evidence_id} className="border-b border-border/50 hover:bg-panel-soft/30 transition-colors">
              <td className="py-2.5 pr-4 font-mono text-xs text-white">
                {e.audit_type.replace(/_/g, " ")}
              </td>
              <td className="py-2.5 pr-4">
                <span className="inline-flex items-center gap-1.5">
                  <StatusDot ok={e.passed} />
                  <span className={`text-xs ${e.passed ? "text-ok" : "text-bad"}`}>
                    {e.passed ? "passed" : "failed"}
                  </span>
                </span>
              </td>
              <td className="py-2.5 pr-4 text-xs text-muted">
                {e.source_kind.replace(/_/g, " ")}
              </td>
              <td className="py-2.5 pr-4 text-xs font-mono text-muted">
                {e.runner_version || "—"}
              </td>
              <td className="py-2.5 pr-4 text-xs text-muted">
                {e.run_duration_ms != null ? `${(e.run_duration_ms / 1000).toFixed(1)}s` : "—"}
              </td>
              <td className="py-2.5 pr-4 font-mono text-xs text-muted">
                {e.source_revision ? e.source_revision.slice(0, 8) : "—"}
              </td>
              <td className="py-2.5 text-xs text-muted">
                {formatDate(e.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

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

  const rollup = atom.audit_rollup;

  return (
    <div className="space-y-6 animate-fade-in max-w-4xl">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 text-xs text-muted mb-2">
          <Link to="/atoms" className="hover:text-accent transition-colors">Atoms</Link>
          <span className="text-border-bright">/</span>
          <span className="text-gray-400">{atom.fqdn}</span>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="text-2xl font-bold font-mono text-accent tracking-tight">{atom.fqdn}</h2>
          {atom.license_expression && (
            <LicenseBadge
              expression={atom.license_expression}
              status={atom.license_status}
              family={atom.license_family}
            />
          )}
        </div>
        <p className="text-gray-400 mt-2 leading-relaxed">{atom.description}</p>
        <div className="flex flex-wrap items-center gap-3 mt-4">
          <div className="flex gap-1.5 flex-wrap">
            {atom.domain_tags.map((t) => (
              <span key={t} className="tag">{t}</span>
            ))}
          </div>
          <span className="text-xs text-muted">
            by <Link to={`/originator/${atom.owner_github_login}`} className="text-accent hover:text-accent-bright transition-colors">{atom.owner_github_login || "unknown"}</Link>
          </span>
        </div>
        {atom.source_repo && (
          <div className="mt-3">
            <SourceLink atom={atom} />
          </div>
        )}
      </div>

      {/* Audit Summary */}
      {rollup && (
        <div className="card p-6 relative overflow-hidden">
          <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-accent/20 via-transparent to-transparent" />
          <h3 className="section-heading mb-4">Audit Summary</h3>
          <div className="grid sm:grid-cols-2 gap-x-8 gap-y-0">
            <div>
              <AuditStatusRow label="Overall Verdict" value={rollup.overall_verdict} />
              <AuditStatusRow label="Trust Readiness" value={rollup.trust_readiness} />
              <AuditStatusRow label="Risk Tier" value={rollup.risk_tier} />
              <AuditStatusRow label="Risk Score" value={String(rollup.risk_score)} />
              <AuditStatusRow label="Acceptability Band" value={rollup.acceptability_band} />
              <AuditStatusRow label="Acceptability Score" value={String(rollup.acceptability_score)} />
            </div>
            <div>
              <AuditStatusRow label="Structural" value={rollup.structural_status} />
              <AuditStatusRow label="Runtime" value={rollup.runtime_status} />
              <AuditStatusRow label="Semantic" value={rollup.semantic_status} />
              <AuditStatusRow label="Developer Semantics" value={rollup.developer_semantics_status} />
              <AuditStatusRow label="Review Status" value={rollup.review_status} />
              <AuditStatusRow label="Parity Coverage" value={rollup.parity_coverage_level} />
            </div>
          </div>
          {rollup.risk_reasons.length > 0 && (
            <div className="mt-4">
              <p className="text-xs text-muted uppercase tracking-wider mb-1.5">Risk Reasons</p>
              <ul className="space-y-1">
                {rollup.risk_reasons.map((r, i) => (
                  <li key={i} className="text-sm text-gray-400 flex items-start gap-2">
                    <span className="text-amber-400 mt-0.5">*</span>
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {rollup.trust_blockers.length > 0 && (
            <div className="mt-4">
              <p className="text-xs text-muted uppercase tracking-wider mb-1.5">Trust Blockers</p>
              <ul className="space-y-1">
                {rollup.trust_blockers.map((b, i) => (
                  <li key={i} className="text-sm text-gray-400 flex items-start gap-2">
                    <span className="text-bad mt-0.5">*</span>
                    {b}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {rollup.review_limitations.length > 0 && (
            <div className="mt-4">
              <p className="text-xs text-muted uppercase tracking-wider mb-1.5">Review Limitations</p>
              <ul className="space-y-1">
                {rollup.review_limitations.map((l, i) => (
                  <li key={i} className="text-sm text-gray-400 flex items-start gap-2">
                    <span className="text-muted mt-0.5">*</span>
                    {l}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {rollup.updated_at && (
            <p className="text-xs text-muted mt-4">Last updated {formatDateTime(rollup.updated_at)}</p>
          )}
        </div>
      )}

      {/* Audit Evidence */}
      <div className="card p-6 relative overflow-hidden">
        <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-accent/20 via-transparent to-transparent" />
        <h3 className="section-heading mb-4">Audit Evidence</h3>
        <AuditEvidenceTable evidence={atom.audit_evidence} />
      </div>

      {/* Versions */}
      <div className="card p-6 relative overflow-hidden">
        <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-accent/20 via-transparent to-transparent" />
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
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-ok/10 text-ok rounded-full text-xs font-medium border border-ok/20">
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
          <pre className="text-xs font-mono text-gray-300 bg-bg rounded-lg p-4 overflow-x-auto whitespace-pre-wrap border border-border/50">
            {bibtex}
          </pre>
        </div>
      )}
    </div>
  );
}
