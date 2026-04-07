import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { BountySummaryResponse } from "../api/types";
import StatusBadge from "../components/StatusBadge";
import EscrowAmount from "../components/EscrowAmount";
import Pagination from "../components/Pagination";
import EmptyState from "../components/EmptyState";
import { TableSkeleton } from "../components/LoadingSkeleton";
import { formatDate } from "../utils/format";

const STATUSES = ["all", "open", "submitted", "verification", "settled", "cancelled", "expired"];
const LIMIT = 12;

export default function BountyList() {
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = searchParams.get("status") ?? "all";
  const offset = Number(searchParams.get("offset") ?? 0);
  const [bounties, setBounties] = useState<BountySummaryResponse[] | null>(null);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    setBounties(null);
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
    <div className="space-y-6 animate-fade-in">
      <div>
        <h2 className="page-title">Bounties</h2>
        <p className="page-subtitle">Open challenges seeking verified algorithmic solutions.</p>
      </div>

      {/* Status filter */}
      <div className="flex gap-1.5 flex-wrap">
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-all duration-150 capitalize ${
              statusFilter === s
                ? "bg-accent/15 text-accent border-accent/30"
                : "bg-transparent text-muted border-border hover:text-gray-200 hover:border-border-bright"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        {bounties === null ? (
          <div className="p-6"><TableSkeleton rows={6} cols={5} /></div>
        ) : bounties.length === 0 ? (
          <EmptyState
            title="No bounties found"
            description={statusFilter !== "all" ? `No bounties with status "${statusFilter}".` : undefined}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-border">
                  <th className="px-6 py-3 section-heading">Title</th>
                  <th className="px-6 py-3 section-heading">Escrow</th>
                  <th className="px-6 py-3 section-heading">Deadline</th>
                  <th className="px-6 py-3 section-heading">Tags</th>
                  <th className="px-6 py-3 section-heading">Status</th>
                </tr>
              </thead>
              <tbody>
                {bounties.map((b) => (
                  <tr key={b.bounty_id} className="border-b border-border/50 hover:bg-panel-soft/30 transition-colors">
                    <td className="px-6 py-4">
                      <Link to={`/bounties/${b.bounty_id}`} className="text-gray-200 hover:text-white font-medium transition-colors">
                        {b.title}
                      </Link>
                    </td>
                    <td className="px-6 py-4"><EscrowAmount amount={b.escrow_amount} /></td>
                    <td className="px-6 py-4 text-muted">{formatDate(b.deadline)}</td>
                    <td className="px-6 py-4">
                      <div className="flex gap-1 flex-wrap">
                        {b.domain_tags.map((t) => (
                          <span key={t} className="tag">{t}</span>
                        ))}
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <StatusBadge status={b.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {bounties && bounties.length > 0 && (
          <div className="px-6 pb-4">
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
        )}
      </div>
    </div>
  );
}
