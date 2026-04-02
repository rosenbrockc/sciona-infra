"""Focused tests for Phase 2D backfill helpers."""

from __future__ import annotations

from pathlib import Path

from scripts.backfill_uncertainty import build_uncertainty_rows
from scripts.backfill_utils import namespace_from_path, resolve_atom_id
from scripts.backfill_verification_matches import build_verification_match_row, normalize_verification_level


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, parent, result_map):
        self._parent = parent
        self._result_map = result_map
        self._mode = ""
        self._value = ""

    def select(self, _fields: str):
        return self

    def eq(self, _field: str, value: str):
        self._mode = "eq"
        self._value = value
        return self

    def like(self, _field: str, value: str):
        self._mode = "like"
        self._value = value
        return self

    def limit(self, _count: int):
        return self

    def execute(self):
        self._parent.calls.append((self._mode, self._value))
        return _FakeResponse(self._result_map.get((self._mode, self._value), []))


class _FakeSupabase:
    def __init__(self, result_map):
        self.calls = []
        self._result_map = result_map

    def table(self, _name: str):
        return _FakeQuery(self, self._result_map)


def test_namespace_from_path_strips_artifacts_boundary() -> None:
    assert namespace_from_path(Path("ageoa/pulsar_folding/uncertainty.json")) == "ageoa.pulsar_folding"
    assert namespace_from_path(Path("ageoa/mint/_artifacts/apc_module/uncertainty.json")) == "ageoa.mint"
    assert namespace_from_path(Path("ageoa/tempo_jl/tai2utc/matches.json")) == "ageoa.tempo_jl.tai2utc"


def test_resolve_atom_id_prefers_exact_match() -> None:
    supabase = _FakeSupabase({("eq", "ageoa.tempo_jl.tai2utc.isleapyear"): [{"atom_id": "abc"}]})
    assert resolve_atom_id(supabase, "ageoa.tempo_jl.tai2utc", "isleapyear") == "abc"
    assert supabase.calls == [("eq", "ageoa.tempo_jl.tai2utc.isleapyear")]


def test_resolve_atom_id_falls_back_to_suffix_match() -> None:
    supabase = _FakeSupabase(
        {
            ("eq", "ageoa.tempo_jl.tai2utc.isleapyear"): [],
            ("like", "%.isleapyear"): [{"atom_id": "fallback"}],
        }
    )
    assert resolve_atom_id(supabase, "ageoa.tempo_jl.tai2utc", "isleapyear") == "fallback"
    assert supabase.calls == [
        ("eq", "ageoa.tempo_jl.tai2utc.isleapyear"),
        ("like", "%.isleapyear"),
    ]


def test_build_uncertainty_rows_maps_optional_fields() -> None:
    rows = build_uncertainty_rows(
        "atom-1",
        [
            {
                "scalar_factor": 0.7,
                "confidence": 0.9,
                "input_regime": "shape=(256,)",
            }
        ],
    )
    assert rows == [
        {
            "atom_id": "atom-1",
            "version_id": None,
            "mode": "empirical",
            "scalar_factor": 0.7,
            "confidence": 0.9,
            "n_trials": 0,
            "epsilon": 0,
            "input_regime": "shape=(256,)",
            "notes": "",
        }
    ]


def test_normalize_verification_level_guards_unknown_values() -> None:
    assert normalize_verification_level("type_checked") == "type_checked"
    assert normalize_verification_level("surprising") == "unverified"


def test_build_verification_match_row_handles_missing_verified_match() -> None:
    row = build_verification_match_row(
        "atom-2",
        {
            "pdg_node": {
                "predicate_id": "isleapyear",
                "statement": "(year: Any) -> Any",
                "informal_desc": "",
            },
            "all_candidates": [{"name": "candidate"}],
            "all_verifications": [{"status": "attempted"}],
        },
    )
    assert row["atom_id"] == "atom-2"
    assert row["predicate_id"] == "isleapyear"
    assert row["candidate_name"] == ""
    assert row["verification_level"] == "unverified"
    assert row["all_candidates"] == [{"name": "candidate"}]
