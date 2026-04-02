"""Tests for SQLite manifest snapshot generation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sciona_infra.api.snapshot import generate_manifest_sqlite


class TestSnapshotGeneration:
    def test_creates_tables(self):
        con = generate_manifest_sqlite([], [])
        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "atoms" in tables
        assert "hyperparams" in tables
        assert "benchmarks" in tables
        con.close()

    def test_inserts_atoms(self):
        atoms = [
            {"atom_id": "a1", "fqdn": "pkg.filter", "status": "approved"},
            {"atom_id": "a2", "fqdn": "pkg.sort", "status": "approved"},
        ]
        con = generate_manifest_sqlite(atoms, [])
        rows = con.execute("SELECT fqdn FROM atoms ORDER BY fqdn").fetchall()
        assert [r[0] for r in rows] == ["pkg.filter", "pkg.sort"]
        con.close()

    def test_inserts_hyperparams(self):
        atoms = [{"atom_id": "a1", "fqdn": "pkg.filter", "status": "approved"}]
        hps = [
            {
                "hp_id": "hp1",
                "atom_id": "a1",
                "name": "cutoff",
                "kind": "float",
                "default_value": "0.5",
            }
        ]
        con = generate_manifest_sqlite(atoms, hps)
        rows = con.execute("SELECT name, kind FROM hyperparams").fetchall()
        assert rows[0] == ("cutoff", "float")
        con.close()

    def test_inserts_benchmarks(self):
        atoms = [{"atom_id": "a1", "fqdn": "pkg.filter", "status": "approved"}]
        benchmarks = [
            {
                "atom_fqdn": "pkg.filter",
                "content_hash": "abc123",
                "benchmark_name": "signal_denoise",
                "metric_name": "loss",
                "metric_value": 0.42,
                "dataset_tag": "v1",
                "measured_at": "2025-01-01",
            }
        ]
        con = generate_manifest_sqlite(atoms, [], benchmarks=benchmarks)
        rows = con.execute(
            "SELECT atom_fqdn, metric_name, metric_value FROM benchmarks"
        ).fetchall()
        assert rows[0] == ("pkg.filter", "loss", 0.42)
        con.close()

    def test_write_to_file(self, tmp_path: Path):
        atoms = [{"atom_id": "a1", "fqdn": "pkg.filter", "status": "approved"}]
        output = tmp_path / "manifest.sqlite"
        con = generate_manifest_sqlite(atoms, [], output_path=output)
        con.close()

        assert output.exists()
        # Reopen and verify
        verify_con = sqlite3.connect(str(output))
        rows = verify_con.execute("SELECT fqdn FROM atoms").fetchall()
        assert rows[0][0] == "pkg.filter"
        verify_con.close()

    def test_domain_tags_serialization(self):
        atoms = [
            {
                "atom_id": "a1",
                "fqdn": "pkg.filter",
                "status": "approved",
                "domain_tags": ["signal", "audio"],
            }
        ]
        con = generate_manifest_sqlite(atoms, [])
        row = con.execute("SELECT domain_tags FROM atoms").fetchone()
        assert row[0] == "signal,audio"
        con.close()

    def test_compatible_with_existing_loader(self, tmp_path: Path):
        """Verify the snapshot is readable by the existing manifest loader."""
        from sciona.architect.hyperparams import load_hyperparams_manifest_sqlite

        atoms = [{"atom_id": "a1", "fqdn": "pkg.filter", "status": "approved"}]
        hps = [
            {
                "hp_id": "hp1",
                "atom_id": "a1",
                "name": "cutoff",
                "kind": "float",
                "default_value": "0.5",
                "min_value": "0.0",
                "max_value": "1.0",
                "semantic_role": "threshold",
                "status": "approved",
            }
        ]
        output = tmp_path / "manifest.sqlite"
        con = generate_manifest_sqlite(atoms, hps, output_path=output)
        con.close()

        result = load_hyperparams_manifest_sqlite(output)
        assert "pkg.filter" in result
        assert len(result["pkg.filter"]) == 1
        assert result["pkg.filter"][0].name == "cutoff"

    def test_upsert_idempotent(self):
        atoms = [{"atom_id": "a1", "fqdn": "pkg.filter", "status": "approved"}]
        con = generate_manifest_sqlite(atoms, [])
        # Insert same atoms again — should not raise
        for atom in atoms:
            tags_str = ""
            con.execute(
                "INSERT OR REPLACE INTO atoms (atom_id, fqdn, status, domain_tags, description) "
                "VALUES (?, ?, ?, ?, ?)",
                (atom["atom_id"], atom["fqdn"], atom.get("status", "approved"), tags_str, ""),
            )
        con.commit()
        rows = con.execute("SELECT COUNT(*) FROM atoms").fetchone()
        assert rows[0] == 1
        con.close()
