import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ReferralCode, Referral } from "../api/types";

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
    } catch {
      // ignore
    }
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
    <div className="bg-panel border border-border rounded-lg p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-muted uppercase tracking-wide">Referrals</h3>
        <button
          type="button"
          onClick={handleGenerate}
          disabled={generating}
          className="text-xs rounded border border-accent/40 bg-accent/10 px-3 py-1.5 text-accent hover:bg-accent/20 disabled:opacity-40"
        >
          {generating ? "Generating..." : "New Invite Code"}
        </button>
      </div>

      {codes.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-muted">Your codes</p>
          {codes.map((c) => (
            <div key={c.code} className="flex items-center gap-2 text-sm">
              <code className="font-mono text-gray-300 bg-panel-soft px-2 py-0.5 rounded">{c.code}</code>
              <span className="text-muted text-xs">{c.use_count}/{c.max_uses} used</span>
              <button
                type="button"
                onClick={() => copyLink(c.code)}
                className="text-xs text-accent hover:underline"
              >
                {copied === c.code ? "Copied!" : "Copy link"}
              </button>
            </div>
          ))}
        </div>
      )}

      {referrals.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-muted">Referred users ({referrals.length})</p>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted border-b border-border">
                <th className="pb-1 pr-3">User</th>
                <th className="pb-1 pr-3">Code</th>
                <th className="pb-1">Value Created</th>
              </tr>
            </thead>
            <tbody>
              {referrals.map((r) => (
                <tr key={r.id} className="border-b border-border/50">
                  <td className="py-1 pr-3 font-mono text-gray-300">{r.referee_id.slice(0, 8)}...</td>
                  <td className="py-1 pr-3 text-muted">{r.code}</td>
                  <td className="py-1">
                    {r.value_created_at ? (
                      <span className="text-ok">{r.first_value_event}</span>
                    ) : (
                      <span className="text-muted">Pending</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
