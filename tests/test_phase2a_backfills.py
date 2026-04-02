"""Tests for Phase 2A documentation-table backfill helpers."""

from __future__ import annotations

from pathlib import Path

from scripts.backfill_dejargonized_descriptions import (
    build_prompt,
    estimate_jargon_score,
    heuristic_dejargonize,
)
from scripts.backfill_io_specs import build_io_spec_rows, derive_atom_fqdn, input_name_mismatch
from scripts.backfill_parameters import build_parameter_rows
from scripts.backfill_technical_descriptions import choose_technical_content


def test_derive_atom_fqdn_uses_relative_cdg_path() -> None:
    atoms_root = Path("/tmp/ageoa")
    cdg_path = atoms_root / "astroflow" / "cdg.json"
    assert derive_atom_fqdn(cdg_path, atoms_root, "dedispersionkernel") == "ageoa.astroflow.dedispersionkernel"


def test_build_io_spec_rows_maps_inputs_and_outputs() -> None:
    rows = build_io_spec_rows(
        "atom-1",
        {
            "inputs": [{"name": "x", "type_desc": "float", "constraints": ">= 0"}],
            "outputs": [{"name": "y"}],
        },
    )
    assert rows == [
        {
            "atom_id": "atom-1",
            "version_id": None,
            "direction": "input",
            "name": "x",
            "type_desc": "float",
            "constraints": ">= 0",
            "required": True,
            "default_value_repr": "",
            "ordinal": 0,
        },
        {
            "atom_id": "atom-1",
            "version_id": None,
            "direction": "output",
            "name": "y",
            "type_desc": "Any",
            "constraints": "",
            "required": True,
            "default_value_repr": "",
            "ordinal": 0,
        },
    ]


def test_input_name_mismatch_only_warns_when_manifest_present() -> None:
    assert not input_name_mismatch(["x"], [])
    assert input_name_mismatch(["x"], ["y"])


def test_build_parameter_rows_appends_varargs_and_kwargs() -> None:
    rows = build_parameter_rows(
        "atom-1",
        {
            "argument_details": [{"name": "q", "annotation": "np.ndarray", "required": True}],
            "uses_varargs": True,
            "uses_kwargs": True,
        },
    )
    assert [row["name"] for row in rows] == ["q", "*args", "**kwargs"]
    assert [row["kind"] for row in rows] == ["positional_or_keyword", "varargs", "kwargs"]


def test_choose_technical_content_prefers_docstring_summary() -> None:
    assert (
        choose_technical_content({"docstring_summary": "Doc summary"}, {"description": "Fallback"})
        == "Doc summary"
    )
    assert choose_technical_content({}, {"description": "Fallback"}) == "Fallback"


def test_build_prompt_includes_context_fields() -> None:
    prompt = build_prompt(
        fqdn="ageoa.foo.bar",
        technical_content="Compute a posterior covariance estimate.",
        parameter_list="q: float, r: float",
        io_specs="input q: float, output score: float",
        domain_tags=["bayesian", "filtering"],
    )
    assert "ageoa.foo.bar" in prompt
    assert "q: float" in prompt
    assert "bayesian, filtering" in prompt


def test_estimate_jargon_score_distinguishes_plain_and_technical_text() -> None:
    plain = "This function checks dates and returns whether a year has an extra day."
    technical = "Compute posterior covariance for a Hamiltonian Monte Carlo kernel state."
    assert estimate_jargon_score(plain) < 0.4
    assert estimate_jargon_score(technical) >= 0.4


def test_heuristic_dejargonize_mentions_inputs_and_domain() -> None:
    text = heuristic_dejargonize("Compute a Gaussian log density.", "q: float", ["statistics"])
    assert "q: float" in text
    assert "statistics" in text
