import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ReferralCode, Referral } from "../api/types";
import { truncateId, formatRelative } from "../utils/format";

export default function ReferralPanel() {
  const [codes, setCodes] = useState<ReferralCode[]>([]);
  const [referrals, setReferrals] = useState<Referral[]>([]);
  const [copied, setCopied] = useState("");
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    api.getMyReferralCodes().then(setCodes).catch(() => {});
    api.getMyReferrals().then(setReferrals).catch(() => {});
  }, []);

  async function handleGenerate() {
    setGenerating(true);
    try {
      const code = await api.generateReferralCode();
      setCodes((prev) => [code, ...prev]);
    } catch { /* ignore */ }
    setGenerating(false);
  }

  function copyLink(code: string) {
    const url = `${window.location.origin}/invite/${code}`;
    navigator.clipboard.writeText(url).then(() => {
      setCopied(code);
      setTimeout(() => setCopied(""), 2000);
    });
  }

  return (
    <div className="card p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="section-heading">Referrals</h3>
        <button
          type="button"
          onClick={handleGenerate}
          disabled={generating}
          className="btn-primary text-xs py-1.5 disabled:opacity-40"
        >
          {generating ? "Generating..." : "New Invite Code"}
        </button>
      </div>

      {codes.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-muted font-medium">Your invite codes</p>
          <div className="space-y-2">
            {codes.map((c) => (
              <div key={c.code} className="flex items-center gap-3 p-2.5 bg-bg-soft rounded-lg border border-border/50">
                <code className="font-mono text-sm text-white flex-1">{c.code}</code>
                <span className="text-xs text-muted tabular-nums">{c.use_count}/{c.max_uses}</span>
                <button
                  type="button"
                  onClick={() => copyLink(c.code)}
                  className="text-xs text-accent hover:text-accent/80 transition-colors font-medium"
                >
                  {copied === c.code ? "Copied!" : "Copy link"}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {referrals.length > 0 && (
        <div className="space-y-3">
          <p className="text-xs text-muted font-medium">Referred users ({referrals.length})</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left border-b border-border">
                  <th className="pb-2 pr-3 section-heading">User</th>
                  <th className="pb-2 pr-3 section-heading">Code</th>
                  <th className="pb-2 pr-3 section-heading">Joined</th>
                  <th className="pb-2 section-heading">Value</th>
                </tr>
              </thead>
              <tbody>
                {referrals.map((r) => (
                  <tr key={r.id} className="border-b border-border/50">
                    <td className="py-2 pr-3 font-mono text-gray-300">{truncateId(r.referee_id)}</td>
                    <td className="py-2 pr-3 text-muted font-mono">{r.code}</td>
                    <td className="py-2 pr-3 text-muted">{formatRelative(r.created_at)}</td>
                    <td className="py-2">
                      {r.value_created_at ? (
                        <span className="inline-flex items-center gap-1 text-ok font-medium">
                          <span className="w-1.5 h-1.5 rounded-full bg-ok" />
                          {r.first_value_event}
                        </span>
                      ) : (
                        <span className="text-muted">Pending</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {codes.length === 0 && referrals.length === 0 && (
        <p className="text-sm text-muted text-center py-4">
          Generate an invite code to start referring researchers to the platform.
        </p>
      )}
    </div>
  );
}
