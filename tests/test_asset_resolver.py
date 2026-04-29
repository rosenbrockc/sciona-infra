from __future__ import annotations

from pathlib import Path

import pytest

from conftest_state_artifacts import (
    EXPECTED_MANIFEST_CONTENT_HASH,
    fixture_path,
)
from sciona_infra.assets.cache_db import get_cached_asset
from sciona_infra.assets.resolver import (
    AssetFile,
    AssetRef,
    CryptographicIntegrityError,
    hydrate_asset,
)


def test_resolver_hydrates_file_uri_into_content_addressed_cache(tmp_path: Path) -> None:
    source = fixture_path("org_taxonomy_en.json")
    ref = AssetRef(
        fqdn="sciona.resources.nlp.org_taxonomy.en.v1",
        content_hash="558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
        assets=(
            AssetFile(
                asset_path="taxonomy.json",
                sha256="558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
                byte_size=source.stat().st_size,
                storage_uri=source.as_uri(),
                format="json",
                loader_name="json.load",
            ),
        ),
    )

    hydrated = hydrate_asset(ref, cache_dir=tmp_path)

    assert hydrated == tmp_path / "sha256" / ref.content_hash / "taxonomy.json"
    assert hydrated.read_bytes() == source.read_bytes()
    cached = get_cached_asset(tmp_path, ref.content_hash, "taxonomy.json")
    assert cached is not None
    assert cached.fqdn == ref.fqdn


def test_strict_mode_uses_cache_hit_without_storage_access(tmp_path: Path) -> None:
    source = fixture_path("org_taxonomy_en.json")
    ref = AssetRef(
        fqdn="sciona.resources.nlp.org_taxonomy.en.v1",
        content_hash="558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
        assets=(
            AssetFile(
                asset_path="taxonomy.json",
                sha256="558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
                byte_size=source.stat().st_size,
                storage_uri=source.as_uri(),
                format="json",
                loader_name="json.load",
            ),
        ),
    )
    hydrate_asset(ref, cache_dir=tmp_path)
    strict_ref = AssetRef(
        fqdn=ref.fqdn,
        content_hash=ref.content_hash,
        assets=(
            AssetFile(
                asset_path="taxonomy.json",
                sha256=ref.assets[0].sha256,
                byte_size=ref.assets[0].byte_size,
                storage_uri="file:///does/not/exist.json",
                format="json",
                loader_name="json.load",
            ),
        ),
    )

    assert hydrate_asset(strict_ref, cache_dir=tmp_path, network=False).exists()


def test_strict_mode_fails_on_cache_miss(tmp_path: Path) -> None:
    source = fixture_path("org_taxonomy_en.json")
    ref = AssetRef(
        fqdn="sciona.resources.nlp.org_taxonomy.en.v1",
        content_hash="558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
        assets=(
            AssetFile(
                asset_path="taxonomy.json",
                sha256="558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
                byte_size=source.stat().st_size,
                storage_uri=source.as_uri(),
                format="json",
                loader_name="json.load",
            ),
        ),
    )

    with pytest.raises(CryptographicIntegrityError, match="strict offline cache miss"):
        hydrate_asset(ref, cache_dir=tmp_path, network=False)


def test_hash_mismatch_deletes_temp_file_and_raises(tmp_path: Path) -> None:
    source = fixture_path("org_taxonomy_en.json")
    ref = AssetRef(
        fqdn="sciona.resources.nlp.org_taxonomy.en.v1",
        content_hash="0" * 64,
        assets=(
            AssetFile(
                asset_path="taxonomy.json",
                sha256="0" * 64,
                byte_size=source.stat().st_size,
                storage_uri=source.as_uri(),
                format="json",
                loader_name="json.load",
            ),
        ),
    )

    with pytest.raises(CryptographicIntegrityError, match="sha256 mismatch"):
        hydrate_asset(ref, cache_dir=tmp_path)
    assert not list((tmp_path / "tmp").glob("*.part"))


def test_concurrent_hydration_of_same_ref(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    source = fixture_path("org_taxonomy_en.json")
    ref = AssetRef(
        fqdn="sciona.resources.nlp.org_taxonomy.en.v1",
        content_hash="558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
        assets=(
            AssetFile(
                asset_path="taxonomy.json",
                sha256="558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
                byte_size=source.stat().st_size,
                storage_uri=source.as_uri(),
                format="json",
                loader_name="json.load",
            ),
        ),
    )

    def _hydrate() -> Path:
        return hydrate_asset(ref, cache_dir=tmp_path)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_hydrate) for _ in range(4)]
        results = [f.result() for f in futures]

    for result in results:
        assert result.exists()
        assert result.read_bytes() == source.read_bytes()

    cached = get_cached_asset(tmp_path, ref.content_hash, "taxonomy.json")
    assert cached is not None
    assert cached.fqdn == ref.fqdn


def test_multi_file_manifest_ref_hydrates_to_manifest_hash_directory(tmp_path: Path) -> None:
    taxonomy = fixture_path("org_taxonomy_en.json")
    onnx = fixture_path("dummy_model.onnx")
    ref = AssetRef(
        fqdn="sciona.resources.nlp.bundle.en.v1",
        content_hash=EXPECTED_MANIFEST_CONTENT_HASH,
        assets=(
            AssetFile(
                asset_path="model.onnx",
                sha256="8c71bc0142214e7a5615c6cb4dfe4fcc99aa97729cb05532fc64ca6089f8649c",
                byte_size=onnx.stat().st_size,
                storage_uri=onnx.as_uri(),
                format="onnx",
                loader_name="onnxruntime.InferenceSession",
            ),
            AssetFile(
                asset_path="taxonomy.json",
                sha256="558dc05150363352b3687980efd251853e5e5b8a621f4aae9afb0866b6c6f5b1",
                byte_size=taxonomy.stat().st_size,
                storage_uri=taxonomy.as_uri(),
                format="json",
                loader_name="json.load",
            ),
        ),
    )

    root = hydrate_asset(ref, cache_dir=tmp_path)

    assert root == tmp_path / "sha256" / EXPECTED_MANIFEST_CONTENT_HASH
    assert (root / "model.onnx").exists()
    assert (root / "taxonomy.json").exists()
