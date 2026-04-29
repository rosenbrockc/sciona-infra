from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from fastapi import HTTPException

from sciona_infra.api.models import (
    AssetEntry,
    AtomPublishRequest,
    StateArtifactMetadata,
    StateArtifactPublishRequest,
    StatePortDeclaration,
    VerifyAssetsRequest,
)
from sciona_infra.api.routers import registry, state_artifacts


@dataclass
class FakeResult:
    data: Any = None
    count: int | None = None


class FakeQuery:
    def __init__(self, client: "FakeSupabaseClient", name: str) -> None:
        self.client = client
        self.name = name
        self.action = "select"
        self.filters: list[tuple[str, Any]] = []
        self.payload: Any = None
        self.select_fields = ""
        self.mode = ""

    def select(self, fields: str, count: str | None = None):
        self.action = "select"
        self.select_fields = fields
        return self

    def insert(self, payload: Any):
        self.action = "insert"
        self.payload = payload
        return self

    def update(self, payload: Any):
        self.action = "update"
        self.payload = payload
        return self

    def eq(self, field: str, value: Any):
        self.filters.append((field, value))
        return self

    def maybe_single(self):
        self.mode = "maybe_single"
        return self

    async def execute(self) -> FakeResult:
        return self.client.handler(self)


class FakeSupabaseClient:
    def __init__(self, handler: Callable[[FakeQuery], FakeResult]) -> None:
        self.handler = handler

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)


@pytest.mark.asyncio
async def test_create_state_artifact_inserts_unified_rows() -> None:
    artifact_id = "11111111-1111-1111-1111-111111111111"
    version_id = "22222222-2222-2222-2222-222222222222"
    asset_id = "33333333-3333-3333-3333-333333333333"
    asset_sha = hashlib.sha256(b"{}").hexdigest()
    calls: list[tuple[str, str, Any]] = []

    def handler(query: FakeQuery) -> FakeResult:
        calls.append((query.name, query.action, query.payload))
        if query.name == "artifacts" and query.action == "select":
            return FakeResult(data=None)
        if query.name == "artifacts" and query.action == "insert":
            assert query.payload["artifact_kind"] == "state_artifact"
            assert query.payload["stateful_kind"] == "explicit_state_model"
            return FakeResult(data=[{"artifact_id": artifact_id}])
        if query.name == "artifact_versions" and query.action == "select":
            return FakeResult(data=None)
        if query.name == "artifact_versions" and query.action == "update":
            return FakeResult(data=[])
        if query.name == "artifact_versions" and query.action == "insert":
            assert query.payload["content_hash"] == asset_sha
            return FakeResult(data=[{"version_id": version_id}])
        if query.name == "artifact_assets" and query.action == "insert":
            assert query.payload[0]["version_id"] == version_id
            return FakeResult(data=[{**query.payload[0], "asset_id": asset_id}])
        if query.name == "state_artifact_metadata" and query.action == "insert":
            assert query.payload["version_id"] == version_id
            assert query.payload["resource_family"] == "taxonomy"
            return FakeResult(data=[query.payload])
        raise AssertionError(f"unexpected query: {query.name} {query.action}")

    client = FakeSupabaseClient(handler)
    body = StateArtifactPublishRequest(
        fqdn="resources.taxonomy.en",
        semver="1.0.0",
        description="English taxonomy",
        assets=[
            AssetEntry(
                asset_path="taxonomy.json",
                byte_size=2,
                sha256=asset_sha,
                format="json",
                storage_uri="file:///tmp/taxonomy.json",
            )
        ],
        metadata=StateArtifactMetadata(
            resource_family="taxonomy",
            language_tags=["en"],
        ),
    )

    result = await state_artifacts.create_state_artifact(
        body,
        user=SimpleNamespace(user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        supabase=client,
    )

    assert str(result.artifact_id) == artifact_id
    assert str(result.version_id) == version_id
    assert result.content_hash == asset_sha
    assert result.presigned_uploads["taxonomy.json"].startswith("placeholder://")
    assert [call[0:2] for call in calls] == [
        ("artifacts", "select"),
        ("artifacts", "insert"),
        ("artifact_versions", "select"),
        ("artifact_versions", "update"),
        ("artifact_versions", "insert"),
        ("artifact_assets", "insert"),
        ("state_artifact_metadata", "insert"),
    ]


@pytest.mark.asyncio
async def test_verify_assets_recomputes_hash_and_writes_audit(tmp_path) -> None:
    artifact_id = "11111111-1111-1111-1111-111111111111"
    version_id = "22222222-2222-2222-2222-222222222222"
    asset_id = "33333333-3333-3333-3333-333333333333"
    asset_path = tmp_path / "taxonomy.json"
    asset_path.write_text('{"Organization": ["OpenAI"]}', encoding="utf-8")
    asset_sha = hashlib.sha256(asset_path.read_bytes()).hexdigest()
    audit_payloads: list[dict[str, Any]] = []

    def handler(query: FakeQuery) -> FakeResult:
        if query.name == "artifacts" and query.action == "select":
            return FakeResult(
                data={
                    "artifact_id": artifact_id,
                    "artifact_kind": "state_artifact",
                }
            )
        if query.name == "artifact_versions" and query.action == "select":
            return FakeResult(data={"version_id": version_id})
        if query.name == "artifact_assets" and query.action == "select":
            return FakeResult(
                data=[
                    {
                        "asset_id": asset_id,
                        "version_id": version_id,
                        "asset_path": "taxonomy.json",
                        "byte_size": asset_path.stat().st_size,
                        "sha256": asset_sha,
                        "format": "json",
                        "storage_uri": asset_path.as_posix(),
                    }
                ]
            )
        if query.name == "artifact_audit_evidence" and query.action == "insert":
            audit_payloads.extend(query.payload)
            return FakeResult(data=query.payload)
        raise AssertionError(f"unexpected query: {query.name} {query.action}")

    result = await state_artifacts.verify_state_artifact_assets(
        "resources.taxonomy.en",
        version_id,
        VerifyAssetsRequest(),
        supabase=FakeSupabaseClient(handler),
    )

    assert result.passed is True
    assert result.results[0].actual_sha256 == asset_sha
    assert [row["audit_type"] for row in audit_payloads] == [
        "asset_integrity_check",
        "format_security_scan",
    ]
    assert all(row["passed"] for row in audit_payloads)


@pytest.mark.asyncio
async def test_create_state_artifact_version_requires_existing_artifact() -> None:
    asset_sha = hashlib.sha256(b"{}").hexdigest()
    calls: list[tuple[str, str]] = []

    def handler(query: FakeQuery) -> FakeResult:
        calls.append((query.name, query.action))
        if query.name == "artifacts" and query.action == "select":
            return FakeResult(data=None)
        raise AssertionError(f"unexpected query: {query.name} {query.action}")

    body = StateArtifactPublishRequest(
        fqdn="resources.missing",
        semver="1.0.1",
        assets=[
            AssetEntry(
                asset_path="taxonomy.json",
                byte_size=2,
                sha256=asset_sha,
                format="json",
            )
        ],
    )

    with pytest.raises(HTTPException) as exc:
        await state_artifacts.create_state_artifact_version(
            "resources.missing",
            body,
            user=SimpleNamespace(user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            supabase=FakeSupabaseClient(handler),
        )

    assert exc.value.status_code == 404
    assert calls == [("artifacts", "select")]


@pytest.mark.asyncio
async def test_verify_assets_rejects_renamed_pickle(tmp_path) -> None:
    artifact_id = "11111111-1111-1111-1111-111111111111"
    version_id = "22222222-2222-2222-2222-222222222222"
    asset_path = tmp_path / "renamed_pickle.json"
    asset_path.write_bytes(b"\x80\x04pickle-bytes")
    asset_sha = hashlib.sha256(asset_path.read_bytes()).hexdigest()

    def handler(query: FakeQuery) -> FakeResult:
        if query.name == "artifacts" and query.action == "select":
            return FakeResult(
                data={
                    "artifact_id": artifact_id,
                    "artifact_kind": "state_artifact",
                }
            )
        if query.name == "artifact_versions" and query.action == "select":
            return FakeResult(data={"version_id": version_id})
        if query.name == "artifact_assets" and query.action == "select":
            return FakeResult(
                data=[
                    {
                        "asset_id": "33333333-3333-3333-3333-333333333333",
                        "asset_path": "renamed_pickle.json",
                        "byte_size": asset_path.stat().st_size,
                        "sha256": asset_sha,
                        "format": "json",
                        "storage_uri": f"file://{asset_path}",
                    }
                ]
            )
        raise AssertionError(f"unexpected query: {query.name} {query.action}")

    result = await state_artifacts.verify_state_artifact_assets(
        "resources.bad",
        version_id,
        VerifyAssetsRequest(write_audit_evidence=False),
        supabase=FakeSupabaseClient(handler),
    )

    assert result.passed is False
    assert "blocked_magic:pickle_v4" in result.results[0].errors


@pytest.mark.asyncio
async def test_publish_atom_inserts_state_ports_when_unified_artifact_exists() -> None:
    artifact_id = "44444444-4444-4444-4444-444444444444"
    unified_version_id = "55555555-5555-5555-5555-555555555555"
    atom_id = "66666666-6666-6666-6666-666666666666"
    atom_version_id = "77777777-7777-7777-7777-777777777777"
    user_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    content_hash = hashlib.sha256(b"a").hexdigest()
    inserted_ports: list[dict[str, Any]] = []

    def handler(query: FakeQuery) -> FakeResult:
        if query.name == "atom_versions" and query.action == "select":
            return FakeResult(data=None)
        if query.name == "atoms" and query.action == "select":
            return FakeResult(data=None)
        if query.name == "atoms" and query.action == "insert":
            return FakeResult(data=[{"atom_id": atom_id}])
        if query.name == "atom_versions" and query.action == "update":
            return FakeResult(data=[])
        if query.name == "atom_versions" and query.action == "insert":
            return FakeResult(data=[{"version_id": atom_version_id}])
        if query.name == "artifacts" and query.action == "select":
            return FakeResult(data={"artifact_id": artifact_id})
        if query.name == "artifact_versions" and query.action == "select":
            assert ("content_hash", content_hash) in query.filters
            return FakeResult(data={"version_id": unified_version_id})
        if query.name == "artifact_state_ports" and query.action == "insert":
            inserted_ports.extend(query.payload)
            return FakeResult(data=query.payload)
        raise AssertionError(f"unexpected query: {query.name} {query.action}")

    body = AtomPublishRequest(
        fqdn="pkg.ner",
        semver="1.0.0",
        source_tar_b64="YQ==",
        fingerprint="f" * 64,
        state_ports=[
            StatePortDeclaration(
                port_name="model_resource",
                type_desc="ONNX NER model",
                accepted_formats=["onnx"],
                required_metadata={"label_schema": {"kind": "BIO_NER"}},
            )
        ],
    )

    result = await registry.publish_atom(
        body,
        user=SimpleNamespace(user_id=user_id),
        supabase=FakeSupabaseClient(handler),
    )

    assert result.content_hash == content_hash
    assert inserted_ports == [
        {
            "artifact_id": artifact_id,
            "version_id": unified_version_id,
            "port_name": "model_resource",
            "type_desc": "ONNX NER model",
            "accepted_formats": ["onnx"],
            "required_metadata": {"label_schema": {"kind": "BIO_NER"}},
            "required": True,
            "ordinal": 0,
        }
    ]


def test_app_mounts_state_artifact_router() -> None:
    app_source = Path(__file__).parents[1] / "sciona_infra/api/app.py"
    text = app_source.read_text(encoding="utf-8")
    assert "state_artifacts_router" in text
    assert 'prefix="/artifacts"' in text


def test_resolve_local_path_rejects_dotdot_traversal(tmp_path: Path) -> None:
    from sciona_infra.api.routers.state_artifacts import _resolve_local_path

    asset = {"asset_path": "../../../etc/passwd"}
    body = VerifyAssetsRequest(local_base_path=str(tmp_path))
    with pytest.raises(ValueError, match="escapes base directory"):
        _resolve_local_path(asset, body)


def test_resolve_local_path_file_uri(tmp_path: Path) -> None:
    from sciona_infra.api.routers.state_artifacts import _resolve_local_path

    target = tmp_path / "data.json"
    target.write_text("{}", encoding="utf-8")
    asset = {"asset_path": "data.json", "storage_uri": target.as_uri()}
    body = VerifyAssetsRequest()
    result = _resolve_local_path(asset, body)
    assert result == target


def test_resolve_local_path_missing_uri_raises() -> None:
    from sciona_infra.api.routers.state_artifacts import _resolve_local_path

    asset = {"asset_path": "data.json", "storage_uri": ""}
    body = VerifyAssetsRequest()
    with pytest.raises(ValueError, match="No storage_uri"):
        _resolve_local_path(asset, body)
