"""Tests for platform API Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from sciona_infra.api.models import (
    AtomPublishRequest,
    AtomPublishResponse,
    AssetEntry,
    BountyCreateRequest,
    BountyResponse,
    CatalogEntry,
    DeviceFlowResponse,
    PaginatedResponse,
    StateArtifactMetadata,
    StateArtifactPublishRequest,
    StatePortDeclaration,
    SubmissionRequest,
    TokenResponse,
    UserResponse,
    VerifyAssetsRequest,
)


class TestAuthModels:
    def test_device_flow_response(self):
        r = DeviceFlowResponse(
            device_code="dc123",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            expires_in=900,
            interval=5,
        )
        assert r.user_code == "ABCD-1234"

    def test_token_response(self):
        r = TokenResponse(access_token="jwt.token.here", expires_in=2592000)
        assert r.token_type == "bearer"

    def test_user_response(self):
        r = UserResponse(
            user_id=uuid4(),
            github_login="alice",
            display_name="Alice",
            avatar_url="https://example.com/avatar.png",
            identity_tier="contributor",
            reputation_score=42,
            created_at=datetime.now(timezone.utc),
        )
        data = r.model_dump()
        restored = UserResponse.model_validate(data)
        assert restored.github_login == "alice"


class TestRegistryModels:
    def test_publish_request(self):
        req = AtomPublishRequest(
            fqdn="pkg.mod.filter",
            semver="1.0.0",
            source_tar_b64="dGVzdA==",
            fingerprint="a" * 64,
        )
        assert req.fqdn == "pkg.mod.filter"
        assert len(req.fingerprint) == 64

    def test_publish_response(self):
        resp = AtomPublishResponse(
            atom_id=uuid4(),
            version_id=uuid4(),
            fqdn="pkg.mod.filter",
            content_hash="b" * 64,
            semver="1.0.0",
            is_new_atom=True,
        )
        assert resp.is_new_atom

    def test_publish_request_accepts_state_ports(self):
        req = AtomPublishRequest(
            fqdn="pkg.mod.ner",
            semver="1.0.0",
            source_tar_b64="dGVzdA==",
            fingerprint="a" * 64,
            state_ports=[
                StatePortDeclaration(
                    port_name="taxonomy",
                    type_desc="JSON entity taxonomy",
                    accepted_formats=["json"],
                    required_metadata={"label_schema": {"kind": "entity_taxonomy"}},
                )
            ],
        )
        assert req.state_ports[0].port_name == "taxonomy"

    def test_state_artifact_publish_request(self):
        req = StateArtifactPublishRequest(
            fqdn="resources.taxonomy.en",
            semver="1.0.0",
            description="English taxonomy",
            assets=[
                AssetEntry(
                    asset_path="taxonomy.json",
                    byte_size=2,
                    sha256="b" * 64,
                    format="json",
                )
            ],
            metadata=StateArtifactMetadata(
                resource_family="taxonomy",
                language_tags=["en"],
            ),
        )
        assert req.assets[0].format == "json"
        assert req.metadata.resource_family == "taxonomy"

    def test_verify_assets_request_defaults(self):
        req = VerifyAssetsRequest()
        assert req.storage_uris == {}
        assert req.write_audit_evidence is True


class TestBountyModels:
    def test_create_request_validates_positive_escrow(self):
        req = BountyCreateRequest(title="Test", escrow_amount=100.0)
        assert req.escrow_amount > 0

    def test_create_request_rejects_zero_escrow(self):
        with pytest.raises(Exception):
            BountyCreateRequest(title="Test", escrow_amount=0)

    def test_bounty_response_round_trip(self):
        resp = BountyResponse(
            bounty_id=uuid4(),
            principal_id=uuid4(),
            title="Test bounty",
            escrow_amount=500.0,
            status="draft",
            deadline=None,
            tier="standard",
            verification_budget=5,
            verifications_used=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        data = resp.model_dump()
        restored = BountyResponse.model_validate(data)
        assert restored.title == "Test bounty"

    def test_submission_request(self):
        req = SubmissionRequest(
            cdg_hash="abc123",
            atom_versions={"pkg.mod.filter": "hash1"},
            receipt_json={"bounty_id": "b1"},
            claimed_metric_name="loss",
            claimed_metric_value=0.42,
        )
        assert req.claimed_metric_value == 0.42


class TestCatalogModels:
    def test_catalog_entry(self):
        e = CatalogEntry(fqdn="pkg.mod.filter", description="A filter")
        assert e.status == "approved"
        assert e.domain_tags == []


class TestPagination:
    def test_paginated_response(self):
        resp = PaginatedResponse(items=[1, 2, 3], total=100, limit=50, offset=0)
        assert len(resp.items) == 3
        assert resp.total == 100
