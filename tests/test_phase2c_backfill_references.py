"""Tests for Phase 2C reference backfill helpers."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.backfill_references import (
    build_atom_reference_row,
    build_ref_key,
    extract_fqdn,
    iter_reference_files,
    map_source,
)
from scripts.backfill_references_registry import build_registry_row, load_registry


def test_extract_fqdn_handles_manifest_and_legacy_keys() -> None:
    assert extract_fqdn("ageoa.algorithms.graph.bellman_ford@ageoa/algorithms/graph.py:174") == (
        "ageoa.algorithms.graph.bellman_ford"
    )
    assert extract_fqdn("ageoa.tempo.offset_tai2tdb") == "ageoa.tempo.offset_tai2tdb"


def test_build_ref_key_prefers_doi_then_ref_id_then_title() -> None:
    assert build_ref_key("almgren2000", {"doi": "10.1000/example"}) == "10.1000/example"
    assert build_ref_key("almgren2000", {"title": "Ignored"}) == "almgren2000"
    assert build_ref_key("", {"title": "X" * 100}) == "X" * 80


def test_map_source_canonicalizes_supported_match_types() -> None:
    assert map_source({"match_type": "manual"}) == "manual"
    assert map_source({"match_type": "ast_subgraph"}) == "llm_extracted"
    assert map_source({"match_type": "name_heuristic"}) == "llm_extracted"
    assert map_source({}) == "manual"


def test_build_registry_row_defaults_bibtex_key_to_ref_id() -> None:
    row = build_registry_row("clrs2009", {"type": "book", "title": "CLRS"})
    assert row["ref_id"] == "clrs2009"
    assert row["ref_type"] == "book"
    assert row["bibtex_key"] == "clrs2009"


def test_build_atom_reference_row_denormalizes_registry_fields() -> None:
    row = build_atom_reference_row(
        "atom-1",
        "clrs2009",
        {
            "doi": None,
            "title": "Introduction to Algorithms",
            "authors": ["Cormen"],
            "year": 2009,
            "url": "https://example.test/clrs",
        },
        {
            "notes": "core citation",
            "confidence": "high",
            "matched_nodes": ["shortest_path"],
            "match_type": "manual",
        },
    )
    assert row["ref_key"] == "clrs2009"
    assert row["title"] == "Introduction to Algorithms"
    assert row["matched_nodes"] == ["shortest_path"]
    assert row["source"] == "manual"


def test_load_registry_accepts_wrapped_and_plain_payloads(tmp_path: Path) -> None:
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"references": {"ref_a": {"title": "A"}}}))
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps({"ref_b": {"title": "B"}}))
    assert load_registry(wrapped) == {"ref_a": {"title": "A"}}
    assert load_registry(plain) == {"ref_b": {"title": "B"}}


def test_iter_reference_files_skips_pycache(tmp_path: Path) -> None:
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "references.json").write_text("{}")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "references.json").write_text("{}")
    files = iter_reference_files(tmp_path)
    assert files == [tmp_path / "alpha" / "references.json"]
