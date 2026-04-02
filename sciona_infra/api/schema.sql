-- Algorithmic Commons Platform — PostgreSQL Schema
-- Apply with: psql -f ageom/api/schema.sql

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =================================================================
-- USERS & AUTH
-- =================================================================
CREATE TABLE IF NOT EXISTS users (
    user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    github_id     BIGINT UNIQUE NOT NULL,
    github_login  TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    avatar_url    TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    identity_tier TEXT NOT NULL DEFAULT 'contributor'
                  CHECK (identity_tier IN ('contributor', 'payee')),
    stripe_account_id TEXT,
    reputation_score  INTEGER NOT NULL DEFAULT 0,
    is_blacklisted    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_github_login ON users (github_login);

-- =================================================================
-- ATOMS & VERSIONS
-- =================================================================
CREATE TABLE IF NOT EXISTS atoms (
    atom_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fqdn          TEXT UNIQUE NOT NULL,
    owner_id      UUID NOT NULL REFERENCES users(user_id),
    domain_tags   TEXT[] NOT NULL DEFAULT '{}',
    description   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'approved'
                  CHECK (status IN ('approved', 'superseded', 'flagged', 'withdrawn')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS atom_versions (
    version_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id       UUID NOT NULL REFERENCES atoms(atom_id),
    content_hash  TEXT UNIQUE NOT NULL,
    semver        TEXT NOT NULL,
    is_latest     BOOLEAN NOT NULL DEFAULT FALSE,
    derives_from  UUID REFERENCES atom_versions(version_id),
    s3_key        TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (atom_id, semver)
);

CREATE INDEX IF NOT EXISTS idx_atom_versions_atom ON atom_versions (atom_id);
CREATE INDEX IF NOT EXISTS idx_atom_versions_hash ON atom_versions (content_hash);

CREATE TABLE IF NOT EXISTS atom_authors (
    atom_id       UUID NOT NULL REFERENCES atoms(atom_id),
    user_id       UUID NOT NULL REFERENCES users(user_id),
    contribution_share NUMERIC(5,4) NOT NULL DEFAULT 1.0
                  CHECK (contribution_share > 0 AND contribution_share <= 1),
    PRIMARY KEY (atom_id, user_id)
);

CREATE TABLE IF NOT EXISTS hyperparams (
    hp_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atom_id       UUID NOT NULL REFERENCES atoms(atom_id),
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('int', 'float', 'categorical', 'bool')),
    default_value JSONB,
    min_value     JSONB,
    max_value     JSONB,
    step_value    JSONB,
    log_scale     BOOLEAN NOT NULL DEFAULT FALSE,
    choices_json  JSONB,
    constraints_json JSONB,
    semantic_role TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'approved'
                  CHECK (status IN ('approved', 'blocked', 'deprecated')),
    UNIQUE (atom_id, name)
);

CREATE TABLE IF NOT EXISTS atom_benchmarks (
    benchmark_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id    UUID NOT NULL REFERENCES atom_versions(version_id),
    benchmark_name TEXT NOT NULL,
    metric_name   TEXT NOT NULL,
    metric_value  DOUBLE PRECISION NOT NULL,
    dataset_tag   TEXT NOT NULL DEFAULT '',
    measured_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =================================================================
-- BOUNTIES
-- =================================================================
CREATE TABLE IF NOT EXISTS bounties (
    bounty_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id  UUID NOT NULL REFERENCES users(user_id),
    title         TEXT NOT NULL,
    escrow_amount NUMERIC(12,2) NOT NULL CHECK (escrow_amount > 0),
    status        TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft', 'open', 'submitted', 'verified',
                                    'settled', 'expired', 'cancelled')),
    deadline      TIMESTAMPTZ,
    tier          TEXT NOT NULL DEFAULT 'standard'
                  CHECK (tier IN ('standard', 'heavy', 'gpu')),
    verification_budget INTEGER NOT NULL DEFAULT 5,
    verifications_used  INTEGER NOT NULL DEFAULT 0,
    config_yml    JSONB NOT NULL DEFAULT '{}',
    flare_payload JSONB,
    ageom_yml_s3  TEXT,
    dataset_s3    TEXT,
    public_split_hash TEXT,
    blind_split_hash  TEXT,
    cancellation_fee NUMERIC(12,2) DEFAULT 0,
    reposted_from UUID REFERENCES bounties(bounty_id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bounties_status ON bounties (status);
CREATE INDEX IF NOT EXISTS idx_bounties_principal ON bounties (principal_id);

-- =================================================================
-- SUBMISSIONS
-- =================================================================
CREATE TABLE IF NOT EXISTS submissions (
    submission_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id     UUID NOT NULL REFERENCES bounties(bounty_id),
    architect_id  UUID NOT NULL REFERENCES users(user_id),
    cdg_hash      TEXT NOT NULL,
    atom_versions JSONB NOT NULL,
    receipt_s3    TEXT NOT NULL DEFAULT '',
    receipt_json  JSONB NOT NULL,
    claimed_metric_name  TEXT NOT NULL,
    claimed_metric_value DOUBLE PRECISION NOT NULL,
    verified_metric_value DOUBLE PRECISION,
    verification_status TEXT NOT NULL DEFAULT 'pending'
                  CHECK (verification_status IN ('pending', 'receipt_valid',
                         'public_verified', 'blind_verified', 'rejected')),
    is_winner     BOOLEAN NOT NULL DEFAULT FALSE,
    submitted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_submissions_bounty ON submissions (bounty_id);

-- =================================================================
-- PAYOUTS
-- =================================================================
CREATE TABLE IF NOT EXISTS payouts (
    payout_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bounty_id     UUID NOT NULL REFERENCES bounties(bounty_id),
    user_id       UUID NOT NULL REFERENCES users(user_id),
    role          TEXT NOT NULL CHECK (role IN ('platform', 'architect', 'originator')),
    amount        NUMERIC(12,2) NOT NULL,
    shapley_value TEXT,
    stripe_transfer_id TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'kyc_hold', 'transferred', 'failed')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
