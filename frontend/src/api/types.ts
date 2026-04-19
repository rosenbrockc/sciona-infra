// Mirrors the live FastAPI response models under sciona/api.

export interface BountyResponse {
  bounty_id: string;
  principal_id: string;
  title: string;
  escrow_amount: number;
  status: string;
  deadline: string | null;
  tier: string;
  verification_budget: number;
  verifications_used: number;
  submission_count: number;
  created_at: string;
  updated_at: string;
}

export interface BountySummaryResponse {
  bounty_id: string;
  title: string;
  escrow_amount: number;
  status: string;
  deadline: string | null;
  tier: string;
  submission_count: number;
  domain_tags: string[];
}

export interface AtomAuditRollup {
  overall_verdict: string;
  structural_status: string;
  runtime_status: string;
  semantic_status: string;
  developer_semantics_status: string;
  risk_tier: string;
  risk_score: number;
  risk_dimensions: Record<string, any>;
  risk_reasons: string[];
  acceptability_score: number;
  acceptability_band: string;
  parity_coverage_level: string;
  parity_test_status: string;
  parity_fixture_count: number;
  parity_case_count: number;
  review_status: string;
  review_semantic_verdict: string;
  review_developer_semantics_verdict: string;
  review_limitations: string[];
  review_required_actions: string[];
  trust_readiness: string;
  trust_blockers: string[];
  updated_at: string | null;
}

export interface AtomAuditEvidence {
  evidence_id: string;
  audit_type: string;
  passed: boolean;
  status: string;
  details: Record<string, any>;
  source_kind: string;
  runner_version: string;
  run_duration_ms: number | null;
  source_revision: string;
  upstream_version: string;
  created_at: string;
}

export interface AtomSourceRepo {
  repo_url: string;
  repo_name: string;
  vcs_provider: string;
  default_branch: string;
}

export interface AtomDetailResponse {
  atom_id: string;
  fqdn: string;
  description: string;
  domain_tags: string[];
  status: string;
  owner_github_login: string;
  latest_version: AtomVersionResponse | null;
  license_expression: string;
  license_status: string;
  license_family: string;
  source_module_path: string;
  source_symbol: string;
  source_repo: AtomSourceRepo | null;
  audit_rollup: AtomAuditRollup | null;
  audit_evidence: AtomAuditEvidence[];
  created_at: string;
}

export interface AtomSummaryResponse {
  atom_id: string;
  fqdn: string;
  description: string;
  domain_tags: string[];
  status: string;
  latest_semver: string;
  overall_verdict: string;
  risk_tier: string;
  acceptability_band: string;
  license_expression: string;
  license_status: string;
}

export interface AtomVersionResponse {
  version_id: string;
  content_hash: string;
  semver: string;
  is_latest: boolean;
  fingerprint: string;
  created_at: string;
}

export interface LeaderboardEntry {
  originator_id: string;
  github_login: string;
  bounty_count: number;
  total_bounty_value: number;
  atom_count: number;
  reputation: number;
  h_index?: number;
}

export interface ArchitectLeaderboardEntry {
  architect_id: string;
  github_login: string;
  submission_count: number;
  win_count: number;
  total_earned: number;
  bounties_won: number;
  distinct_atoms_used: number;
  reputation: number;
}

export interface OriginatorImpact {
  originator_id: string;
  github_username: string;
  affiliation: string;
  bounty_count: number;
  total_bounty_value: number;
  atom_count: number;
  h_index: number;
}

export interface ComputePreserved {
  estimated_tokens_saved: number;
  estimated_cost_saved_usd: number;
  total_bounties_settled: number;
  total_escrow_value: number;
}

export interface BenchmarkRecord {
  atom_fqdn: string;
  content_hash: string;
  benchmark_id: string;
  metric_name: string;
  metric_value: number;
  dataset_tag: string;
  measured_at: string;
}

export interface SubmissionLeaderboardEntry {
  rank: number;
  submission_id: string;
  architect_id: string;
  metric_values: Record<string, number>;
  verified_at: string;
}

export interface SettlementInfo {
  bounty_id: string;
  status: string;
  escrow_amount: number;
  payouts: PayoutRecipient[];
}

export interface PayoutRecipient {
  recipient_id: string;
  role: string;
  amount: number;
  atom_fqdn?: string | null;
  cdg_hash?: string | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface UserProfile {
  user_id: string;
  github_login: string;
  display_name: string;
  avatar_url: string;
  identity_tier: string;
  effective_tier: string;
  reputation_score: number;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  refresh_token: string;
  expires_in: number;
}

export interface WorkflowStatus {
  submission_id: string;
  verification_status: string;
  runs: VerificationRun[];
}

export interface VerificationRun {
  status: string;
  metric_values: Record<string, number> | null;
  output_hash: string | null;
  is_deterministic: boolean | null;
}

// Reputation

export interface ReputationBreakdown {
  user_id: string;
  originator_reputation: number;
  architect_reputation: number;
  total_reputation: number;
}

export interface ReputationCategory {
  category: string;
  score: number;
  detail: Record<string, any>;
}

// Badges

export interface BadgeDefinition {
  badge_id: string;
  display_name: string;
  description: string;
  track: string;
  icon_slug: string;
  is_hidden: boolean;
  sort_order: number;
}

export interface BadgeMilestone {
  milestone_id: string;
  badge_id: string;
  tier: string;
  threshold_value: number;
  threshold_unit: string;
  rarity_pct?: number;
}

export interface UserBadge {
  id: string;
  user_id: string;
  milestone_id: string;
  awarded_at: string;
  progress_value: number;
}

export interface BadgeProgress {
  user_id: string;
  badge_id: string;
  current_value: number;
  highest_awarded_tier: string | null;
  updated_at: string;
}

export interface BadgeTelemetry {
  badge_id: string;
  current_value: number;
  rarity_pct: number;
  holder_count: number;
}

export interface GrandmasterStatus {
  user_id: string;
  tracks: Record<string, boolean>;
  is_grandmaster: boolean;
}

// Referrals

export interface ReferralCode {
  code: string;
  referrer_id: string;
  created_at: string;
  expires_at: string | null;
  max_uses: number;
  use_count: number;
}

export interface Referral {
  id: string;
  referrer_id: string;
  referee_id: string;
  code: string;
  created_at: string;
  first_value_event: string | null;
  value_created_at: string | null;
}
