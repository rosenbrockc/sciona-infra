"""Tests for Phase 2E diagnostic and git-history helpers."""

from __future__ import annotations

from scripts.backfill_contribution_events_git import (
    build_user_family_events,
    derive_atom_family_from_path,
    matching_atoms_for_family,
    parse_git_log,
)
from scripts.diagnose_publication_completeness import (
    compute_missing_pillars,
    is_publishable_from_sets,
    summarize_publication_completeness,
)


def test_compute_missing_pillars_and_publishable_from_sets() -> None:
    pillar_sets = {
        "io_specs": {"a1"},
        "parameters": {"a1"},
        "dejargonized_description": set(),
        "audit_rollups": {"a1"},
        "references": {"a1"},
    }
    assert compute_missing_pillars("a1", pillar_sets) == ["dejargonized_description"]
    assert not is_publishable_from_sets("a1", pillar_sets)


def test_summarize_publication_completeness_counts_mismatches() -> None:
    atoms = {
        "a1": {"fqdn": "ageoa.one", "is_publishable": True},
        "a2": {"fqdn": "ageoa.two", "is_publishable": False},
    }
    pillar_sets = {
        "io_specs": {"a1", "a2"},
        "parameters": {"a1", "a2"},
        "dejargonized_description": {"a1"},
        "audit_rollups": {"a1", "a2"},
        "references": {"a1", "a2"},
    }
    publishable_count, missing_report, mismatch_count = summarize_publication_completeness(atoms, pillar_sets)
    assert publishable_count == 1
    assert missing_report == [("ageoa.two", ["dejargonized_description"])]
    assert mismatch_count == 0


def test_parse_git_log_groups_commits_and_files() -> None:
    raw = "sha1|a@example.com|2024-01-01T00:00:00Z\nageoa/foo/atoms.py\nsha2|b@example.com|2024-02-01T00:00:00Z\nageoa/bar/atoms.py\n"
    parsed = parse_git_log(raw)
    assert parsed[0]["sha"] == "sha1"
    assert parsed[0]["files"] == ["ageoa/foo/atoms.py"]
    assert parsed[1]["email"] == "b@example.com"


def test_derive_atom_family_from_path_and_matching_atoms() -> None:
    assert derive_atom_family_from_path("ageoa/advancedvi/atoms.py") == "advancedvi"
    assert derive_atom_family_from_path("README.md") is None
    matches = matching_atoms_for_family(
        {
            "ageoa.advancedvi.sample": {"atom_id": "1", "fqdn": "ageoa.advancedvi.sample"},
            "ageoa.other.sample": {"atom_id": "2", "fqdn": "ageoa.other.sample"},
        },
        "advancedvi",
    )
    assert matches == [{"atom_id": "1", "fqdn": "ageoa.advancedvi.sample"}]


def test_build_user_family_events_keeps_earliest_commit_per_user_family() -> None:
    entries = [
        {"sha": "sha2", "email": "a@example.com", "date": "2024-02-01T00:00:00Z", "files": ["ageoa/foo/atoms.py"]},
        {"sha": "sha1", "email": "a@example.com", "date": "2024-01-01T00:00:00Z", "files": ["ageoa/foo/atoms.py"]},
        {"sha": "sha3", "email": "other@example.com", "date": "2024-01-05T00:00:00Z", "files": ["ageoa/bar/atoms.py"]},
    ]
    result = build_user_family_events(entries, {"a@example.com": "user-1"})
    assert result == {("user-1", "foo"): {"sha": "sha1", "date": "2024-01-01T00:00:00Z"}}
