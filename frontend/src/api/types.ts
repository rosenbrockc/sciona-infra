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

export interface AtomDetailResponse {
  atom_id: string;
  fqdn: string;
  description: string;
  domain_tags: string[];
  status: string;
  owner_github_login: string;
  latest_version: AtomVersionResponse | null;
  created_at: string;
}

export interface AtomSummaryResponse {
  atom_id: string;
  fqdn: string;
  description: string;
  domain_tags: string[];
  status: string;
  latest_semver: string;
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
  h_index?: number;
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
