"""Pydantic request/response models for the platform API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class DeviceFlowResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str = ""
    expires_in: int


class PendingResponse(BaseModel):
    status: str = "authorization_pending"
    interval: int = 5


class UserResponse(BaseModel):
    user_id: UUID
    github_login: str
    display_name: str
    avatar_url: str
    identity_tier: str
    effective_tier: str = "general"
    reputation_score: int
    created_at: datetime


class AuthorShare(BaseModel):
    github_login: str
    contribution_share: float = 1.0


class AssetEntry(BaseModel):
    asset_id: UUID | None = None
    asset_path: str
    byte_size: int = Field(..., ge=0)
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    format: str
    media_type: str = "application/octet-stream"
    storage_uri: str = ""
    compression: str = ""
    mmap_safe: bool = False
    loader_name: str = ""


class StateArtifactMetadata(BaseModel):
    resource_family: str = ""
    language_tags: list[str] = Field(default_factory=list)
    vocabulary_size: int | None = None
    embedding_dim: int | None = None
    max_sequence_length: int | None = None
    label_schema: dict = Field(default_factory=dict)
    training_data_summary: str = ""
    provenance_summary: str = ""
    intended_use: str = ""
    limitations: list[str] = Field(default_factory=list)
    legal_basis: dict = Field(default_factory=dict)
    deterministic_output_precision: int = 6


class StatePortDeclaration(BaseModel):
    port_name: str
    type_desc: str
    accepted_formats: list[str] = Field(default_factory=list)
    required_metadata: dict = Field(default_factory=dict)
    required: bool = True
    ordinal: int = 0


class DependencyPin(BaseModel):
    dependency_artifact_fqdn: str
    dependency_content_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    dependency_role: str
    port_name: str = ""
    optional: bool = False
    binding_metadata: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Atoms / Registry
# ---------------------------------------------------------------------------


class AtomPublishRequest(BaseModel):
    fqdn: str
    semver: str
    description: str = ""
    domain_tags: list[str] = Field(default_factory=list)
    source_tar_b64: str = Field(
        ..., description="Base64-encoded tar.gz of the atom source."
    )
    fingerprint: str = Field(
        ..., description="Full SHA-256 AST fingerprint (64 hex chars)."
    )
    authors: list[AuthorShare] | None = None
    state_ports: list[StatePortDeclaration] = Field(default_factory=list)


class AtomPublishResponse(BaseModel):
    atom_id: UUID
    version_id: UUID
    fqdn: str
    content_hash: str
    semver: str
    is_new_atom: bool


class AtomAuditRollupResponse(BaseModel):
    overall_verdict: str = "unknown"
    structural_status: str = "unknown"
    runtime_status: str = "unknown"
    semantic_status: str = "unknown"
    developer_semantics_status: str = "unknown"
    risk_tier: str = "medium"
    risk_score: int = 0
    risk_dimensions: dict = Field(default_factory=dict)
    risk_reasons: list[str] = Field(default_factory=list)
    acceptability_score: int = 0
    acceptability_band: str = "unknown"
    parity_coverage_level: str = "unknown"
    parity_test_status: str = "unknown"
    parity_fixture_count: int = 0
    parity_case_count: int = 0
    review_status: str = "missing"
    review_semantic_verdict: str = "unknown"
    review_developer_semantics_verdict: str = "unknown"
    review_limitations: list[str] = Field(default_factory=list)
    review_required_actions: list[str] = Field(default_factory=list)
    trust_readiness: str = "not_ready"
    trust_blockers: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class AtomAuditEvidenceResponse(BaseModel):
    evidence_id: UUID
    audit_type: str
    passed: bool
    status: str = "completed"
    details: dict = Field(default_factory=dict)
    source_kind: str = "automated"
    runner_version: str = ""
    run_duration_ms: int | None = None
    source_revision: str = ""
    upstream_version: str = ""
    created_at: datetime


class AtomSourceRepoResponse(BaseModel):
    repo_url: str
    repo_name: str
    vcs_provider: str = "github"
    default_branch: str = "main"


class AtomDetailResponse(BaseModel):
    atom_id: UUID
    fqdn: str
    description: str
    domain_tags: list[str]
    status: str
    owner_github_login: str
    latest_version: AtomVersionResponse | None = None
    license_expression: str = ""
    license_status: str = ""
    license_family: str = ""
    source_module_path: str = ""
    source_symbol: str = ""
    source_repo: AtomSourceRepoResponse | None = None
    audit_rollup: AtomAuditRollupResponse | None = None
    audit_evidence: list[AtomAuditEvidenceResponse] = Field(default_factory=list)
    created_at: datetime


class AtomVersionResponse(BaseModel):
    version_id: UUID
    content_hash: str
    semver: str
    is_latest: bool
    fingerprint: str
    created_at: datetime


class AtomSummaryResponse(BaseModel):
    atom_id: UUID
    fqdn: str
    description: str
    domain_tags: list[str]
    status: str
    latest_semver: str = ""
    overall_verdict: str = ""
    risk_tier: str = ""
    acceptability_band: str = ""
    license_expression: str = ""
    license_status: str = ""


class StateArtifactPublishRequest(BaseModel):
    fqdn: str
    semver: str
    description: str = ""
    assets: list[AssetEntry] = Field(default_factory=list, min_length=1)
    metadata: StateArtifactMetadata = Field(default_factory=StateArtifactMetadata)
    dependencies: list[DependencyPin] = Field(default_factory=list)
    declared_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class StateArtifactPublishResponse(BaseModel):
    artifact_id: UUID
    version_id: UUID
    fqdn: str
    content_hash: str
    semver: str
    is_new_artifact: bool
    assets: list[AssetEntry] = Field(default_factory=list)
    presigned_uploads: dict[str, str] = Field(default_factory=dict)


class PresignAssetsRequest(BaseModel):
    assets: list[AssetEntry] = Field(default_factory=list)


class VerifyAssetsRequest(BaseModel):
    storage_uris: dict[str, str] = Field(default_factory=dict)
    local_base_path: str = ""
    write_audit_evidence: bool = True


class VerifyAssetResult(BaseModel):
    asset_id: UUID | None = None
    asset_path: str
    passed: bool
    expected_sha256: str
    actual_sha256: str = ""
    scan_passed: bool = False
    errors: list[str] = Field(default_factory=list)


class VerifyAssetsResponse(BaseModel):
    artifact_id: UUID
    version_id: UUID
    passed: bool
    results: list[VerifyAssetResult] = Field(default_factory=list)


class ArtifactDocumentResponse(BaseModel):
    artifact: dict | None = None
    source_repository: dict | None = None
    descriptions: list[dict] | None = None
    io_specs: list[dict] | None = None
    parameters: list[dict] | None = None
    references: list[dict] | None = None
    audit_rollup: dict | None = None
    audit_latest: list[dict] | None = None
    assets: list[dict] | None = None
    state_metadata: list[dict] | None = None
    state_ports: list[dict] | None = None
    dependencies: list[dict] | None = None


# ---------------------------------------------------------------------------
# Bounties
# ---------------------------------------------------------------------------


class BountyCreateRequest(BaseModel):
    title: str
    escrow_amount: float = Field(..., gt=0)
    deadline: datetime | None = None
    tier: str = "standard"
    domain_tags: list[str] = Field(default_factory=list)
    flare_payload: dict | None = None
    config_yml: dict = Field(default_factory=dict)


class BountyResponse(BaseModel):
    bounty_id: UUID
    principal_id: UUID
    title: str
    escrow_amount: float
    status: str
    deadline: datetime | None
    tier: str
    verification_budget: int
    verifications_used: int
    submission_count: int = 0
    created_at: datetime
    updated_at: datetime


class BountyFundResponse(BaseModel):
    bounty_id: UUID
    status: str
    checkout_url: str = ""


class BountyCancelResponse(BaseModel):
    bounty_id: UUID
    status: str
    cancellation_fee: float


class BountySummaryResponse(BaseModel):
    bounty_id: UUID
    title: str
    escrow_amount: float
    status: str
    deadline: datetime | None
    tier: str
    submission_count: int = 0
    domain_tags: list[str] = Field(default_factory=list)


class SubmissionRequest(BaseModel):
    cdg_hash: str
    atom_versions: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of atom FQDN to content hash.",
    )
    receipt_json: dict
    claimed_metric_name: str
    claimed_metric_value: float


class SubmissionResponse(BaseModel):
    submission_id: UUID
    bounty_id: UUID
    verification_status: str
    submitted_at: datetime


class UpdateTargetRequest(BaseModel):
    min_metric_value: float


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class CatalogEntry(BaseModel):
    fqdn: str
    description: str
    domain_tags: list[str] = Field(default_factory=list)
    latest_semver: str = ""
    status: str = "approved"
    overall_verdict: str = ""
    risk_tier: str = ""
    trust_readiness: str = ""


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""

    items: list = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------


class HeuristicRegistryEntry(BaseModel):
    heuristic_id: str
    display_name: str
    dejargonized_meaning: str = ""
    evidence_type: str
    value_kind: str = ""
    value_shape: str = ""
    confidence: float = 1.0
    producer_kind: str = "atom_output"
    applicability_scope: str = "cross_family"
    supported_action_classes: list[str] = Field(default_factory=list)
    provenance_requirements: list[str] = Field(default_factory=list)
    domain: str = ""
    family: str = ""
    source_atom_fqdn: str = ""
    uncertainty_notes: list[str] = Field(default_factory=list)
    references: list = Field(default_factory=list)


class BindingEvidenceItem(BaseModel):
    evidence_id: str = ""
    heuristic_id: str = ""
    metric_name: str = ""
    metric_value: float | None = None
    threshold: float | None = None
    confidence: float = 0.5
    action_class: str = "precondition"
    reasoning: str = ""
    alternatives: list = Field(default_factory=list)
    thresholds_applied: dict = Field(default_factory=dict)
    provenance: str = ""


class BindingEvidenceResponse(BaseModel):
    binding_id: str
    node_id: str = ""
    bound_artifact_fqdn: str = ""
    binding_confidence: float = 0.0
    binding_source: str = ""
    action_class: str | None = None
    status: str = "active"
    alternatives: list = Field(default_factory=list)
    evidence_summary: dict = Field(default_factory=dict)
    evidence: list[BindingEvidenceItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------


class BadgeDefinitionResponse(BaseModel):
    badge_id: str
    display_name: str
    description: str = ""
    track: str
    icon_slug: str = ""
    is_hidden: bool = False
    sort_order: int = 0


class BadgeMilestoneResponse(BaseModel):
    milestone_id: str
    badge_id: str
    tier: str
    threshold_value: float
    threshold_unit: str = "count"


class UserBadgeResponse(BaseModel):
    id: UUID
    user_id: UUID
    milestone_id: str
    awarded_at: datetime
    progress_value: float = 0


class BadgeProgressResponse(BaseModel):
    user_id: UUID
    badge_id: str
    current_value: float = 0
    highest_awarded_tier: str | None = None
    updated_at: datetime


class BadgeTelemetryResponse(BaseModel):
    badge_id: str
    current_value: float = 0
    rarity_pct: float = 100.0
    holder_count: int = 0


class GrandmasterResponse(BaseModel):
    user_id: str
    tracks: dict[str, bool] = Field(default_factory=dict)
    is_grandmaster: bool = False


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------


class ReferralCodeResponse(BaseModel):
    code: str
    referrer_id: UUID
    created_at: datetime
    expires_at: datetime | None = None
    max_uses: int = 50
    use_count: int = 0


class ReferralResponse(BaseModel):
    id: UUID
    referrer_id: UUID
    referee_id: UUID
    code: str
    created_at: datetime
    first_value_event: str | None = None
    value_created_at: datetime | None = None
