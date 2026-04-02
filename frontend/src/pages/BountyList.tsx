import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { BountySummaryResponse } from "../api/types";
import StatusBadge from "../components/StatusBadge";
import Pagination from "../components/Pagination";

const STATUSES = ["all", "draft", "open", "submitted", "settled", "cancelled", "expired"];
const LIMIT = 10;

export default function BountyList() {
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = searchParams.get("status") ?? "all";
  const offset = Number(searchParams.get("offset") ?? 0);

  const [bounties, setBounties] = useState<BountySummaryResponse[]>([]);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    api
      .getBounties({
        status: statusFilter === "all" ? undefined : statusFilter,
        limit: LIMIT,
        offset,
      })
      .then((r) => {
        setBounties(r.items);
        setTotal(r.total);
      });
  }, [statusFilter, offset]);

  function setFilter(status: string) {
    const p = new URLSearchParams(searchParams);
    if (status === "all") p.delete("status");
    else p.set("status", status);
    p.delete("offset");
    setSearchParams(p);
  }

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-bold">Bounties</h2>

      {/* Status filter */}
      <div className="flex gap-2">
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`px-3 py-1 rounded text-xs font-medium border transition-colors ${
              statusFilter === s
                ? "bg-accent/20 text-accent border-accent/40"
                : "bg-panel-soft text-muted border-border hover:text-gray-200"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Table */}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-muted border-b border-border">
            <th className="pb-2 pr-4">Title</th>
            <th className="pb-2 pr-4">Escrow</th>
            <th className="pb-2 pr-4">Deadline</th>
            <th className="pb-2 pr-4">Tags</th>
            <th className="pb-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {bounties.map((b) => (
            <tr key={b.bounty_id} className="border-b border-border/50">
              <td className="py-3 pr-4">
                <Link to={`/bounties/${b.bounty_id}`} className="text-accent hover:underline">
                  {b.title}
                </Link>
              </td>
              <td className="py-3 pr-4 font-mono">${b.escrow_amount.toLocaleString()}</td>
              <td className="py-3 pr-4 text-muted">{b.deadline}</td>
              <td className="py-3 pr-4">
                <div className="flex gap-1 flex-wrap">
                  {b.domain_tags.map((t) => (
                    <span key={t} className="px-2 py-0.5 bg-panel-soft rounded text-xs text-muted">
                      {t}
                    </span>
                  ))}
                </div>
              </td>
              <td className="py-3">
                <StatusBadge status={b.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

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
