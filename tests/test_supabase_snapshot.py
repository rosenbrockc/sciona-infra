from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sciona_infra.api.routers.catalog import get_atom_document
from sciona_infra.api.snapshot import fetch_manifest_data, generate_manifest_sqlite
from sciona.commands.catalog_cmds import _cmd_catalog_sync
from sciona.ecosystem.benchmarks import load_benchmarks_sqlite


@pytest.mark.asyncio
async def test_fetch_manifest_data_normalizes_benchmark_names(monkeypatch):
    atoms = [
        {
            "atom_id": "a1",
            "fqdn": "pkg.filter",
            "status": "approved",
            "domain_tags": ["signal"],
            "description": "Filter signal",
            "visibility_tier": "general",
            "source_kind": "hand_written",
            "stateful_kind": "none",
            "is_stochastic": False,
            "is_ffi": False,
            "namespace_root": "sciona.atoms",
            "namespace_path": "",
            "source_repo_id": None,
            "source_package": "",
            "source_module_path": "",
            "source_symbol": "",
            "is_publishable": True,
        }
    ]
    hyperparams = [{"hp_id": "hp1", "atom_id": "a1", "name": "cutoff", "kind": "float"}]
    rollups = [{"atom_id": "a1", "overall_verdict": "trusted"}]
    descriptions = [
        {
            "description_id": "d1",
            "atom_id": "a1",
            "kind": "dejargonized",
            "content": "Filter signal",
            "language": "en",
        }
    ]
    benchmarks = [
        {
            "atom_fqdn": "pkg.filter",
            "content_hash": "abc123",
            "benchmark_name": "signal_v1",
            "metric_name": "loss",
            "metric_value": 0.42,
            "dataset_tag": "v1",
            "measured_at": "2026-03-31T00:00:00Z",
        }
    ]

    async def fake_fetch_all_rows(base_url, token, table, **kwargs):
        if table == "atoms":
            return atoms
        if table == "hyperparams":
            return hyperparams
        if table == "atom_audit_rollups":
            return rollups
        if table == "atom_descriptions":
            return descriptions
        if table == "atom_benchmarks":
            return []
        raise AssertionError(f"unexpected table {table!r}")

    async def fake_call_rpc(base_url, token, rpc_name, payload=None, **kwargs):
        assert rpc_name == "get_manifest_benchmarks"
        return benchmarks

    monkeypatch.setattr("sciona_infra.api.snapshot._fetch_all_rows", fake_fetch_all_rows)
    monkeypatch.setattr("sciona_infra.api.snapshot._call_rpc", fake_call_rpc)

    data = await fetch_manifest_data("https://example.supabase.co", "token")

    assert set(data) == {"atoms", "hyperparams", "benchmarks", "rollups", "descriptions"}
    assert data["benchmarks"][0]["benchmark_id"] == "signal_v1"
    assert data["benchmarks"][0]["benchmark_name"] == "signal_v1"


def test_generate_manifest_sqlite_preserves_benchmark_id(tmp_path: Path):
    data = {
        "atoms": [
            {
                "atom_id": "a1",
                "fqdn": "pkg.filter",
                "status": "approved",
                "domain_tags": ["signal", "audio"],
                "description": "Filter signal",
                "is_publishable": True,
            }
        ],
        "hyperparams": [
            {
                "hp_id": "hp1",
                "atom_id": "a1",
                "name": "cutoff",
                "kind": "float",
                "default_value": "0.5",
                "status": "approved",
            }
        ],
        "benchmarks": [
            {
                "atom_fqdn": "pkg.filter",
                "content_hash": "abc123",
                "benchmark_name": "signal_v1",
                "metric_name": "loss",
                "metric_value": 0.42,
                "dataset_tag": "v1",
                "measured_at": "2026-03-31T00:00:00Z",
            }
        ],
        "rollups": [
            {
                "atom_id": "a1",
                "overall_verdict": "trusted",
                "risk_tier": "low",
            }
        ],
        "descriptions": [
            {
                "description_id": "d1",
                "atom_id": "a1",
                "kind": "dejargonized",
                "content": "Filter signal",
                "language": "en",
            }
        ],
    }
    output = tmp_path / "manifest.sqlite"

    con = generate_manifest_sqlite(data, output_path=output)
    con.close()

    rows = load_benchmarks_sqlite(output)["pkg.filter"]
    assert rows[0].benchmark_id == "signal_v1"
    assert rows[0].metric_name == "loss"

    verify = sqlite3.connect(str(output))
    try:
        tables = {
            row[0]
            for row in verify.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"atoms", "hyperparams", "benchmarks", "audit_rollups", "descriptions"}.issubset(
            tables
        )
        row = verify.execute(
            "SELECT benchmark_id, benchmark_name FROM benchmarks"
        ).fetchone()
        assert row == ("signal_v1", "signal_v1")
    finally:
        verify.close()


@pytest.mark.asyncio
async def test_catalog_sync_downloads_manifest_sqlite(monkeypatch, tmp_path: Path, capsys):
    output = tmp_path / "manifest.sqlite"
    source = tmp_path / "source.sqlite"
    con = generate_manifest_sqlite(
        {
            "atoms": [{"atom_id": "a1", "fqdn": "pkg.filter", "status": "approved"}],
            "hyperparams": [],
            "benchmarks": [
                {
                    "atom_fqdn": "pkg.filter",
                    "content_hash": "abc123",
                    "benchmark_name": "signal_v1",
                    "metric_name": "loss",
                    "metric_value": 0.42,
                    "dataset_tag": "v1",
                    "measured_at": "2026-03-31T00:00:00Z",
                }
            ],
            "rollups": [],
            "descriptions": [],
        },
        output_path=source,
    )
    con.close()

    captured_url: dict[str, str] = {}

    async def fake_download_manifest_bytes(manifest_url: str) -> bytes:
        captured_url["value"] = manifest_url
        return source.read_bytes()

    monkeypatch.setattr(
        "sciona.commands.catalog_cmds._download_manifest_bytes",
        fake_download_manifest_bytes,
    )

    await _cmd_catalog_sync(
        argparse.Namespace(output=str(output), api_url=None, manifest_url="https://bucket.example/manifests/manifest.sqlite")
    )

    assert captured_url["value"] == "https://bucket.example/manifests/manifest.sqlite"
    assert output.exists()
    assert load_benchmarks_sqlite(output)["pkg.filter"][0].benchmark_id == "signal_v1"

    captured = capsys.readouterr()
    assert "Manifest written to" in captured.out


class _FakeSupabase:
    def __init__(self, value):
        self.value = value

    def rpc(self, name: str, payload: dict[str, str]):
        assert name == "get_atom_document"
        return SimpleNamespace(execute=self._execute)

    async def _execute(self):
        return SimpleNamespace(data=self.value)


@pytest.mark.asyncio
async def test_get_atom_document_returns_rpc_payload():
    payload = {"atom": {"fqdn": "pkg.filter"}}
    result = await get_atom_document("pkg.filter", supabase=_FakeSupabase(payload))
    assert result == payload


@pytest.mark.asyncio
async def test_get_atom_document_raises_for_missing_atom():
    with pytest.raises(HTTPException) as excinfo:
        await get_atom_document("pkg.missing", supabase=_FakeSupabase(None))
    assert excinfo.value.status_code == 404
