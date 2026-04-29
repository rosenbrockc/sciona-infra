from __future__ import annotations

import json
import zipfile
from pathlib import Path

from conftest_state_artifacts import (
    EXPECTED_MANIFEST_CONTENT_HASH,
    assert_fixture_hashes,
    fixture_path,
    manifest_content_hash,
)
from sciona_infra.assets.format_scanner import canonical_manifest_hash, scan_asset, verify_sha256


def test_state_artifact_fixture_hashes_are_stable() -> None:
    assert_fixture_hashes()
    assert manifest_content_hash() == EXPECTED_MANIFEST_CONTENT_HASH


def test_canonical_manifest_hash_is_independent_of_key_order() -> None:
    manifest_path = fixture_path("manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    shuffled = {
        "files": [
            {
                "byte_size": file_entry["byte_size"],
                "sha256": file_entry["sha256"],
                "path": file_entry["path"],
            }
            for file_entry in reversed(manifest["files"])
        ]
    }
    assert canonical_manifest_hash(manifest_path) == EXPECTED_MANIFEST_CONTENT_HASH
    assert canonical_manifest_hash(shuffled) != EXPECTED_MANIFEST_CONTENT_HASH
    assert canonical_manifest_hash(manifest) == EXPECTED_MANIFEST_CONTENT_HASH


def test_valid_json_and_dummy_onnx_assets_pass_scanner() -> None:
    taxonomy = fixture_path("org_taxonomy_en.json")
    onnx = fixture_path("dummy_model.onnx")

    assert scan_asset(taxonomy, "json").ok
    assert scan_asset(onnx, "onnx").ok
    assert verify_sha256(
        taxonomy,
        "558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
    )


def test_blocked_pickle_extension_is_rejected() -> None:
    result = scan_asset(fixture_path("blocked_pickle.pkl"), "json")
    assert not result.ok
    assert "blocked_extension:.pkl" in result.errors
    assert any(error.startswith("blocked_magic:pickle_") for error in result.errors)


def test_renamed_pickle_is_rejected_by_magic_bytes() -> None:
    result = scan_asset(fixture_path("renamed_pickle.json"), "json")
    assert not result.ok
    assert any(error.startswith("blocked_magic:pickle_") for error in result.errors)


def test_valid_safetensors_passes_scan(tmp_path: Path) -> None:
    header = b'{"weight": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}}'
    header_len = len(header).to_bytes(8, "little")
    data = b"\x00" * 8
    safetensors_path = tmp_path / "model.safetensors"
    safetensors_path.write_bytes(header_len + header + data)
    result = scan_asset(safetensors_path, "safetensors")
    assert result.ok


def test_safetensors_with_truncated_header_fails(tmp_path: Path) -> None:
    safetensors_path = tmp_path / "bad.safetensors"
    safetensors_path.write_bytes(b"\x00" * 4)
    result = scan_asset(safetensors_path, "safetensors")
    assert not result.ok
    assert "invalid_safetensors_header" in result.errors


def test_npz_with_embedded_pickle_rejected(tmp_path: Path) -> None:
    npz_path = tmp_path / "bad.npz"
    with zipfile.ZipFile(npz_path, "w") as archive:
        archive.writestr("payload.pkl", b"\x80\x04data")
    result = scan_asset(npz_path, "npz")
    assert not result.ok
    assert any("invalid_npz_member:payload.pkl" in e for e in result.errors)


def test_parquet_missing_magic_bytes_fails(tmp_path: Path) -> None:
    parquet_path = tmp_path / "bad.parquet"
    parquet_path.write_bytes(b"NOT_PARQUET_DATA_XXXX")
    result = scan_asset(parquet_path, "parquet")
    assert not result.ok
    assert "invalid_parquet_magic" in result.errors


def test_parquet_missing_trailer_magic_fails(tmp_path: Path) -> None:
    parquet_path = tmp_path / "bad.parquet"
    parquet_path.write_bytes(b"PAR1" + b"\x00" * 100 + b"NOPE")
    result = scan_asset(parquet_path, "parquet")
    assert not result.ok
    assert "invalid_parquet_magic" in result.errors


def test_jsonl_with_malformed_second_line(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "mixed.jsonl"
    jsonl_path.write_text('{"valid": true}\n{broken\n', encoding="utf-8")
    result = scan_asset(jsonl_path, "jsonl")
    assert not result.ok
    assert any(e.startswith("invalid_jsonl:2:") for e in result.errors)


def test_invalid_npy_header_rejected(tmp_path: Path) -> None:
    npy_path = tmp_path / "bad.npy"
    npy_path.write_bytes(b"\x00\x00NOTANPY" + b"\x00" * 64)
    result = scan_asset(npy_path, "npy")
    assert not result.ok
    assert "invalid_npy_header" in result.errors
