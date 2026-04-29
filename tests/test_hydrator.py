from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest_state_artifacts import fixture_path
from sciona_infra.assets.hydrator import hydrate_cdg


class _RpcResult:
    def __init__(self, data: dict) -> None:
        self.data = data

    def execute(self) -> "_RpcResult":
        return self


class _FakeSupabase:
    def __init__(self, documents: dict[str, dict]) -> None:
        self.documents = documents

    def rpc(self, name: str, params: dict) -> _RpcResult:
        assert name == "get_artifact_document"
        return _RpcResult(self.documents[params["request_fqdn"]])


@pytest.mark.asyncio
async def test_hydrate_cdg_writes_offline_lockfile(tmp_path: Path) -> None:
    taxonomy = fixture_path("org_taxonomy_en.json")
    state_fqdn = "sciona.resources.nlp.org_taxonomy.en.v1"
    cdg_fqdn = "sciona.cdgs.nlp.org_ner.en.v1"
    state_doc = {
        "fqdn": state_fqdn,
        "content_hash": "558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
        "assets": [
            {
                "asset_path": "taxonomy.json",
                "sha256": "558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
                "byte_size": taxonomy.stat().st_size,
                "storage_uri": taxonomy.as_uri(),
                "format": "json",
                "loader_name": "json.load",
            }
        ],
    }
    cdg_doc = {
        "fqdn": cdg_fqdn,
        "content_hash": "a" * 64,
        "dependencies": [
            {
                "dependency_artifact_fqdn": state_fqdn,
                "dependency_content_hash": state_doc["content_hash"],
                "dependency_role": "state_artifact",
                "port_name": "taxonomy",
            }
        ],
    }
    supabase = _FakeSupabase({cdg_fqdn: cdg_doc, state_fqdn: state_doc})

    receipt = await hydrate_cdg(cdg_fqdn, supabase=supabase, cache_dir=tmp_path)

    assert receipt.all_verified
    assert receipt.lockfile_path.exists()
    payload = json.loads(receipt.lockfile_path.read_text(encoding="utf-8"))
    assert payload["cdg_fqdn"] == cdg_fqdn
    assert payload["dependencies"][0]["fqdn"] == state_fqdn
    assert payload["dependencies"][0]["assets"][0]["path"] == "taxonomy.json"


@pytest.mark.asyncio
async def test_hydrate_cdg_raises_on_missing_document() -> None:
    class _MissingSupabase:
        def rpc(self, name: str, params: dict) -> _RpcResult:
            raise LookupError(f"artifact document not found: {params['request_fqdn']}")

    with pytest.raises(LookupError, match="artifact document not found"):
        await hydrate_cdg("sciona.cdgs.missing.v1", supabase=_MissingSupabase(), cache_dir=Path("/tmp"))


@pytest.mark.asyncio
async def test_lockfile_has_required_keys(tmp_path: Path) -> None:
    taxonomy = fixture_path("org_taxonomy_en.json")
    state_fqdn = "sciona.resources.nlp.org_taxonomy.en.v1"
    cdg_fqdn = "sciona.cdgs.nlp.lockfile_test.v1"
    state_doc = {
        "fqdn": state_fqdn,
        "content_hash": "558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
        "assets": [
            {
                "asset_path": "taxonomy.json",
                "sha256": "558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
                "byte_size": taxonomy.stat().st_size,
                "storage_uri": taxonomy.as_uri(),
                "format": "json",
                "loader_name": "json.load",
            }
        ],
    }
    cdg_doc = {
        "fqdn": cdg_fqdn,
        "content_hash": "b" * 64,
        "dependencies": [
            {
                "dependency_artifact_fqdn": state_fqdn,
                "dependency_content_hash": state_doc["content_hash"],
                "dependency_role": "state_artifact",
                "port_name": "taxonomy",
            }
        ],
    }
    supabase = _FakeSupabase({cdg_fqdn: cdg_doc, state_fqdn: state_doc})

    receipt = await hydrate_cdg(cdg_fqdn, supabase=supabase, cache_dir=tmp_path)

    payload = json.loads(receipt.lockfile_path.read_text(encoding="utf-8"))
    assert "cdg_fqdn" in payload
    assert "hydrated_at" in payload
    assert "dependencies" in payload
    assert payload["hydrated_at"].endswith("Z")
