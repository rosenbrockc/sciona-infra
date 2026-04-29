from __future__ import annotations

from pathlib import Path

from sciona_infra.assets.format_scanner import canonical_manifest_hash, sha256_file

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "state_artifacts"

EXPECTED_HASHES = {
    "blocked_pickle.pkl": "183615f73bc3f79ba897282cc6e97a5cac78437ae5ab44887e83f80833f3e44a",
    "dummy_model.onnx": "8c71bc0142214e7a5615c6cb4dfe4fcc99aa97729cb05532fc64ca6089f8649c",
    "manifest.json": "a383b4aabc18c039a91f5517d6db34833d2144014c7da70aa790c221124d6438",
    "org_taxonomy_en.json": "558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
    "renamed_pickle.json": "183615f73bc3f79ba897282cc6e97a5cac78437ae5ab44887e83f80833f3e44a",
}

EXPECTED_MANIFEST_CONTENT_HASH = (
    "4bf5dfc4c3b838088e91bb9f7da6f1947de8687f7b74464dfc647db245ad4629"
)


def fixture_path(name: str) -> Path:
    return FIXTURE_DIR / name


def assert_fixture_hashes() -> None:
    for name, expected_hash in EXPECTED_HASHES.items():
        assert sha256_file(fixture_path(name)) == expected_hash


def manifest_content_hash() -> str:
    return canonical_manifest_hash(fixture_path("manifest.json"))
