import { useEffect, useState } from "react";
import { useAuth } from "../auth/useAuth";
import { api } from "../api/client";
import type { ReputationBreakdown, ReputationCategory } from "../api/types";
import { PageSkeleton } from "../components/LoadingSkeleton";
import { formatNumber, formatUsd } from "../utils/format";

// ───── Category metadata ─────

const ORIGINATOR_CATEGORIES: Record<string, { label: string; weight: string; color: string; description: string }> = {
  publishing: {
    label: "Publishing",
    weight: "~30%",
    color: "#d4875c",
    description: "Base points per approved atom plus quality bonuses for passing smoke tests, fuzz tests, achieving trusted audit verdict, uncertainty estimates, and verified references.",
  },
  adoption: {
    label: "Adoption",
    weight: "~25%",
    color: "#38bdf8",
    description: "Points earned when your atoms are used in CDG submissions. Bonus for winning CDGs, unique architects using your atoms, and cross-domain reach.",
  },
  citation: {
    label: "Citation",
    weight: "~10%",
    color: "#818cf8",
    description: "Points from BibTeX exports of your atoms and version updates (rewarding active maintenance).",
  },
  bounty_earnings: {
    label: "Bounty Earnings",
    weight: "~25%",
    color: "#22c55e",
    description: "1 point per $10 earned in originator payouts from settled bounties.",
  },
  community: {
    label: "Community",
    weight: "~10%",
    color: "#a78bfa",
    description: "Points for co-authoring atoms with other researchers and for activated referrals.",
  },
};

const ARCHITECT_CATEGORIES: Record<string, { label: string; weight: string; color: string; description: string }> = {
  activity: {
    label: "Activity",
    weight: "~30%",
    color: "#5b8dd9",
    description: "Base points per CDG submission plus bonuses for passing public and blind verification, and for accurate metric claims.",
  },
  composition: {
    label: "Composition Quality",
    weight: "~20%",
    color: "#38bdf8",
    description: "Diversity bonus for winning CDGs: points per distinct atom, distinct author, and distinct domain tag represented.",
  },
  wins: {
    label: "Wins",
    weight: "~25%",
    color: "#d4a843",
    description: "150 base points per bounty win, plus flat bonuses for maintaining >50% win rate (min 3 subs) and >75% win rate (min 5 subs).",
  },
  bounty_earnings: {
    label: "Bounty Earnings",
    weight: "~25%",
    color: "#22c55e",
    description: "1 point per $10 earned in architect payouts from settled bounties.",
  },
  community: {
    label: "Community",
    weight: "~bonus",
    color: "#a78bfa",
    description: "Points for activated referrals where the referee created real value.",
  },
};


// ───── Formula table ─────

const ORIGINATOR_FORMULA = [
  { action: "Publish an approved atom", points: "100 base", notes: "Per atom, weighted by contribution share" },
  { action: "Atom passes smoke test", points: "+20", notes: "Per atom" },
  { action: "Atom passes fuzz test", points: "+30", notes: "Per atom" },
  { action: "Atom achieves trusted audit verdict", points: "+50", notes: "Highest quality tier" },
  { action: "Atom has uncertainty estimates", points: "+15", notes: "Per atom" },
  { action: "Verified references", points: "+10 each", notes: "Max 50 per atom" },
  { action: "Atom used in winning CDG", points: "+40/use", notes: "Per settled bounty" },
  { action: "Atom used in any submission", points: "+5/use", notes: "Non-winning submissions" },
  { action: "Distinct architects using atom", points: "+15/architect", notes: "Unique users" },
  { action: "Cross-domain reach", points: "+25/domain", notes: "Distinct domain tags" },
  { action: "BibTeX export", points: "+3/export", notes: "Uncapped" },
  { action: "Version update (after v1)", points: "+20/version", notes: "Rewards maintenance" },
  { action: "Originator payout", points: "1 pt / $10", notes: "From settlement payouts" },
  { action: "Co-authored atom", points: "+25/co-author", notes: "Per atom" },
  { action: "Activated referral", points: "+50/referral", notes: "Referee must create value" },
];

const ARCHITECT_FORMULA = [
  { action: "Submit a CDG", points: "20 base", notes: "Per submission" },
  { action: "CDG passes public verification", points: "+15", notes: "Per verified submission" },
  { action: "CDG passes blind verification", points: "+25", notes: "Higher trust tier" },
  { action: "Metric accuracy ≤5% deviation", points: "+10", notes: "Claimed vs verified" },
  { action: "Distinct atoms in winning CDG", points: "+20/atom", notes: "Per winning CDG" },
  { action: "Distinct authors in winning CDG", points: "+15/author", notes: "Ecosystem breadth" },
  { action: "Distinct domain tags in winning CDG", points: "+30/domain", notes: "Cross-domain synthesis" },
  { action: "Win a bounty", points: "150 base", notes: "Per bounty won" },
  { action: "Win rate >50% (min 3 subs)", points: "+100 flat", notes: "Consistency bonus" },
  { action: "Win rate >75% (min 5 subs)", points: "+250 flat", notes: "Stacks with above" },
  { action: "Architect payout", points: "1 pt / $10", notes: "From settlement payouts" },
  { action: "Activated referral", points: "+50/referral", notes: "Referee must create value" },
];


// ───── Detail renderers ─────

function OriginatorDetail({ category, detail }: { category: string; detail: Record<string, any> }) {
  if (category === "publishing" && Array.isArray(detail)) {
    return (
      <table className="w-full text-xs mt-2">
        <thead>
          <tr className="text-left border-b border-border/50">
            <th className="pb-1.5 pr-3 text-muted font-medium">Atom</th>
            <th className="pb-1.5 pr-3 text-muted font-medium">Base</th>
            <th className="pb-1.5 pr-3 text-muted font-medium">Smoke</th>
            <th className="pb-1.5 pr-3 text-muted font-medium">Fuzz</th>
            <th className="pb-1.5 pr-3 text-muted font-medium">Trusted</th>
            <th className="pb-1.5 pr-3 text-muted font-medium">Refs</th>
            <th className="pb-1.5 text-muted font-medium">Subtotal</th>
          </tr>
        </thead>
        <tbody>
          {detail.map((item: any, i: number) => (
            <tr key={i} className="border-b border-border/30">
              <td className="py-1.5 pr-3 font-mono text-gray-300 truncate max-w-[180px]">{item.fqdn}</td>
              <td className="py-1.5 pr-3 tabular-nums text-gray-400">{item.base}</td>
              <td className="py-1.5 pr-3 tabular-nums">{item.smoke > 0 ? <span className="text-green-400">+{item.smoke}</span> : <span className="text-muted">0</span>}</td>
              <td className="py-1.5 pr-3 tabular-nums">{item.fuzz > 0 ? <span className="text-green-400">+{item.fuzz}</span> : <span className="text-muted">0</span>}</td>
              <td className="py-1.5 pr-3 tabular-nums">{item.trusted > 0 ? <span className="text-tier-lattice">+{item.trusted}</span> : <span className="text-muted">0</span>}</td>
              <td className="py-1.5 pr-3 tabular-nums">{item.references > 0 ? <span className="text-accent">+{item.references}</span> : <span className="text-muted">0</span>}</td>
              <td className="py-1.5 tabular-nums font-medium text-white">{item.subtotal}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  if (category === "adoption") {
    return (
      <div className="grid grid-cols-2 gap-3 mt-2 text-xs">
        <DetailStat label="Winning CDG uses" value={detail.winning_cdg_uses} pts={detail.winning_cdg_pts} />
        <DetailStat label="Other submission uses" value={detail.any_submission_uses} pts={detail.any_submission_pts} />
        <DetailStat label="Unique architects" value={detail.unique_architects} pts={detail.architect_pts} />
        <DetailStat label="Domain reach" value={detail.domain_reach} pts={detail.domain_pts} />
      </div>
    );
  }

  if (category === "citation") {
    return (
      <div className="grid grid-cols-2 gap-3 mt-2 text-xs">
        <DetailStat label="BibTeX exports" value={detail.bibtex_exports} pts={detail.bibtex_pts} />
        <DetailStat label="Version updates" value={detail.version_updates} pts={detail.version_pts} />
      </div>
    );
  }

  if (category === "bounty_earnings") {
    return (
      <div className="mt-2 text-xs">
        <DetailStat label="Total earned" value={formatUsd(detail.total_earned_usd || 0)} pts={detail.points} />
      </div>
    );
  }

  if (category === "community") {
    return (
      <div className="grid grid-cols-2 gap-3 mt-2 text-xs">
        <DetailStat label="Co-authors" value={detail.coauthor_count} pts={detail.coauthor_pts} />
        <DetailStat label="Activated referrals" value={detail.activated_referrals} pts={detail.referral_pts} />
      </div>
    );
  }

  return null;
}

function ArchitectDetail({ category, detail }: { category: string; detail: Record<string, any> }) {
  if (category === "activity") {
    return (
      <div className="grid grid-cols-2 gap-3 mt-2 text-xs">
        <DetailStat label="Submissions" value={detail.submissions} pts={detail.submission_pts} />
        <DetailStat label="Public verified" value={detail.public_verified} pts={detail.public_pts} />
        <DetailStat label="Blind verified" value={detail.blind_verified} pts={detail.blind_pts} />
        <DetailStat label="Accurate claims" value={detail.accurate_claims} pts={detail.accuracy_pts} />
      </div>
    );
  }

  if (category === "composition") {
    return (
      <div className="grid grid-cols-2 gap-3 mt-2 text-xs">
        <DetailStat label="Winning CDGs" value={detail.winning_cdgs} pts={null} />
        <DetailStat label="Atom diversity pts" value={null} pts={detail.total_atoms_pts} />
        <DetailStat label="Author breadth pts" value={null} pts={detail.total_authors_pts} />
        <DetailStat label="Domain synthesis pts" value={null} pts={detail.total_domains_pts} />
      </div>
    );
  }

  if (category === "wins") {
    return (
      <div className="grid grid-cols-2 gap-3 mt-2 text-xs">
        <DetailStat label="Bounties won" value={detail.wins} pts={detail.win_pts} />
        <DetailStat label="Win rate" value={`${Math.round((detail.win_rate || 0) * 100)}%`} pts={detail.win_rate_bonus} />
      </div>
    );
  }

  if (category === "bounty_earnings") {
    return (
      <div className="mt-2 text-xs">
        <DetailStat label="Total earned" value={formatUsd(detail.total_earned_usd || 0)} pts={detail.points} />
      </div>
    );
  }

  if (category === "community") {
    return (
      <div className="mt-2 text-xs">
        <DetailStat label="Activated referrals" value={detail.activated_referrals} pts={detail.referral_pts} />
      </div>
    );
  }

  return null;
}

function DetailStat({ label, value, pts }: { label: string; value: any; pts: number | null }) {
  return (
    <div className="bg-bg-soft rounded-lg p-2.5 border border-border/40">
      <p className="text-[10px] text-muted uppercase tracking-wider mb-1">{label}</p>
      <div className="flex items-baseline justify-between">
        {value !== null && value !== undefined && <span className="text-sm text-gray-200 font-medium tabular-nums">{value}</span>}
        {pts !== null && pts !== undefined && <span className="text-xs font-mono text-accent tabular-nums">+{pts} pts</span>}
      </div>
    </div>
  );
}


// ───── Category card with expandable detail ─────

function CategoryCard({
  category,
  score,
  detail,
  meta,
  totalScore,
  renderDetail,
}: {
  category: string;
  score: number;
  detail: Record<string, any>;
  meta: { label: string; weight: string; color: string; description: string };
  totalScore: number;
  renderDetail: (category: string, detail: Record<string, any>) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const pct = totalScore > 0 ? Math.round((score / totalScore) * 100) : 0;

  return (
    <div className="card overflow-hidden relative">
      <div className="absolute top-0 left-0 right-0 h-[1px]" style={{ background: `linear-gradient(to right, ${meta.color}44, ${meta.color}11, transparent)` }} />
      <button type="button" className="w-full text-left px-5 py-4" onClick={() => setOpen(!open)}>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full" style={{ backgroundColor: meta.color }} />
            <h4 className="text-sm font-semibold text-gray-100">{meta.label}</h4>
            <span className="text-[10px] text-muted uppercase tracking-wider">{meta.weight}</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="font-mono text-lg font-bold tabular-nums" style={{ color: meta.color }}>
              {formatNumber(score)}
            </span>
            <svg className={`w-4 h-4 text-muted transition-transform ${open ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-panel-soft border border-border/30">
          <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, backgroundColor: meta.color }} />
        </div>
        <p className="text-[11px] text-muted mt-2 leading-relaxed">{meta.description}</p>
      </button>
      {open && (
        <div className="px-5 pb-4 border-t border-border/40 pt-3 animate-fade-in">
          {renderDetail(category, detail)}
        </div>
      )}
    </div>
  );
}


// ───── Main page ─────

export default function ReputationPage() {
  const { user } = useAuth();
  const [breakdown, setBreakdown] = useState<ReputationBreakdown | null>(null);
  const [originatorDetail, setOriginatorDetail] = useState<ReputationCategory[]>([]);
  const [architectDetail, setArchitectDetail] = useState<ReputationCategory[]>([]);
  const [activeTab, setActiveTab] = useState<"overview" | "originator" | "architect">("overview");

  useEffect(() => {
    if (!user) return;
    api.getReputationBreakdown(user.user_id).then(setBreakdown).catch(() => {});
    api.getOriginatorReputationDetail(user.user_id).then(setOriginatorDetail).catch(() => {});
    api.getArchitectReputationDetail(user.user_id).then(setArchitectDetail).catch(() => {});
  }, [user]);

  const originatorTotal = originatorDetail.reduce((s, c) => s + c.score, 0);
  const architectTotal = architectDetail.reduce((s, c) => s + c.score, 0);

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h2 className="page-title">Reputation</h2>
        <p className="page-subtitle">Your contribution score across the Algorithmic Commons ecosystem.</p>
      </div>

      {!user ? (
        <div className="card p-8 text-center">
          <p className="text-muted text-sm">Sign in to view your reputation breakdown.</p>
        </div>
      ) : !breakdown ? (
        <PageSkeleton />
      ) : (
        <>
          {/* Score summary */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="card p-5 relative overflow-hidden">
              <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-accent/30 via-accent/10 to-transparent" />
              <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Total Reputation</p>
              <p className="text-3xl font-bold font-mono text-accent tabular-nums">{formatNumber(breakdown.total_reputation)}</p>
            </div>
            <div className="card p-5 relative overflow-hidden">
              <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-track-originator/30 via-track-originator/10 to-transparent" />
              <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Originator Score</p>
              <p className="text-2xl font-bold font-mono text-track-originator tabular-nums">{formatNumber(breakdown.originator_reputation)}</p>
            </div>
            <div className="card p-5 relative overflow-hidden">
              <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-track-architect/30 via-track-architect/10 to-transparent" />
              <p className="text-[10px] text-muted uppercase tracking-wider mb-1">Architect Score</p>
              <p className="text-2xl font-bold font-mono text-track-architect tabular-nums">{formatNumber(breakdown.architect_reputation)}</p>
            </div>
          </div>

          {/* Tab switcher */}
          <div className="flex gap-1 p-1 rounded-lg bg-panel-soft border border-border w-fit">
            {(["overview", "originator", "architect"] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-2 rounded-md text-xs font-medium transition-all ${
                  activeTab === tab
                    ? "bg-accent/12 text-accent border border-accent/20"
                    : "text-muted hover:text-gray-200"
                }`}
              >
                {tab === "overview" ? "Formula" : tab === "originator" ? "Originator Detail" : "Architect Detail"}
              </button>
            ))}
          </div>

          {/* Tab content */}
          {activeTab === "overview" && (
            <div className="grid lg:grid-cols-2 gap-6">
              {/* Originator formula */}
              <div className="card overflow-hidden relative">
                <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-track-originator/30 via-track-originator/10 to-transparent" />
                <div className="px-6 pt-5 pb-3">
                  <h3 className="text-sm font-semibold text-track-originator">Originator Formula</h3>
                  <p className="text-xs text-muted mt-0.5">Points for publishing, quality, adoption, and citations.</p>
                </div>
                <div className="overflow-x-auto px-6 pb-5">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left border-b border-border">
                        <th className="pb-2 pr-3 text-muted font-medium">Action</th>
                        <th className="pb-2 pr-3 text-muted font-medium">Points</th>
                        <th className="pb-2 text-muted font-medium">Notes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ORIGINATOR_FORMULA.map((row, i) => (
                        <tr key={i} className="border-b border-border/30">
                          <td className="py-2 pr-3 text-gray-300">{row.action}</td>
                          <td className="py-2 pr-3 font-mono text-accent font-medium tabular-nums whitespace-nowrap">{row.points}</td>
                          <td className="py-2 text-muted">{row.notes}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Architect formula */}
              <div className="card overflow-hidden relative">
                <div className="absolute top-0 left-0 right-0 h-[1px] bg-gradient-to-r from-track-architect/30 via-track-architect/10 to-transparent" />
                <div className="px-6 pt-5 pb-3">
                  <h3 className="text-sm font-semibold text-track-architect">Architect Formula</h3>
                  <p className="text-xs text-muted mt-0.5">Points for submissions, wins, composition quality, and earnings.</p>
                </div>
                <div className="overflow-x-auto px-6 pb-5">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left border-b border-border">
                        <th className="pb-2 pr-3 text-muted font-medium">Action</th>
                        <th className="pb-2 pr-3 text-muted font-medium">Points</th>
                        <th className="pb-2 text-muted font-medium">Notes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ARCHITECT_FORMULA.map((row, i) => (
                        <tr key={i} className="border-b border-border/30">
                          <td className="py-2 pr-3 text-gray-300">{row.action}</td>
                          <td className="py-2 pr-3 font-mono text-accent font-medium tabular-nums whitespace-nowrap">{row.points}</td>
                          <td className="py-2 text-muted">{row.notes}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {activeTab === "originator" && (
            <div className="space-y-4">
              {originatorDetail.length === 0 ? (
                <div className="card p-8 text-center">
                  <p className="text-muted text-sm">No originator reputation data yet. Publish an atom to get started.</p>
                </div>
              ) : (
                originatorDetail.map((cat) => (
                  <CategoryCard
                    key={cat.category}
                    category={cat.category}
                    score={cat.score}
                    detail={cat.detail}
                    meta={ORIGINATOR_CATEGORIES[cat.category] || { label: cat.category, weight: "", color: "#888", description: "" }}
                    totalScore={originatorTotal}
                    renderDetail={(c, d) => <OriginatorDetail category={c} detail={d} />}
                  />
                ))
              )}
            </div>
          )}

          {activeTab === "architect" && (
            <div className="space-y-4">
              {architectDetail.length === 0 ? (
                <div className="card p-8 text-center">
                  <p className="text-muted text-sm">No architect reputation data yet. Submit a CDG to get started.</p>
                </div>
              ) : (
                architectDetail.map((cat) => (
                  <CategoryCard
                    key={cat.category}
                    category={cat.category}
                    score={cat.score}
                    detail={cat.detail}
                    meta={ARCHITECT_CATEGORIES[cat.category] || { label: cat.category, weight: "", color: "#888", description: "" }}
                    totalScore={architectTotal}
                    renderDetail={(c, d) => <ArchitectDetail category={c} detail={d} />}
                  />
                ))
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
