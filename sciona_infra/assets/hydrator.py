from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from sciona_infra.assets.cache_db import get_cached_asset
from sciona_infra.assets.resolver import AssetFile, AssetRef, hydrate_asset


@dataclass(frozen=True)
class ResolvedAsset:
    path: str
    sha256: str
    cached_at: str
    cache_path: Path


@dataclass(frozen=True)
class ResolvedDependency:
    fqdn: str
    content_hash: str
    assets: tuple[ResolvedAsset, ...]


@dataclass(frozen=True)
class HydrationReceipt:
    cdg_fqdn: str
    resolved_dependencies: tuple[ResolvedDependency, ...]
    lockfile_path: Path
    all_verified: bool


async def hydrate_cdg(
    fqdn: str,
    *,
    supabase: Any,
    cache_dir: Path,
    strict: bool = False,
) -> HydrationReceipt:
    document = await _get_artifact_document(supabase, fqdn)
    refs = await _collect_state_refs(supabase, document)
    dependencies = hydrate_refs(fqdn, refs, cache_dir=cache_dir, strict=strict)
    lockfile_path = write_lockfile(fqdn, dependencies, cache_dir=cache_dir)
    return HydrationReceipt(
        cdg_fqdn=fqdn,
        resolved_dependencies=dependencies,
        lockfile_path=lockfile_path,
        all_verified=True,
    )


def hydrate_refs(
    cdg_fqdn: str,
    refs: Iterable[AssetRef],
    *,
    cache_dir: Path,
    strict: bool = False,
) -> tuple[ResolvedDependency, ...]:
    resolved: list[ResolvedDependency] = []
    for ref in refs:
        hydrate_asset(ref, cache_dir=cache_dir, network=not strict)
        resolved_assets: list[ResolvedAsset] = []
        for asset in ref.assets:
            cached = get_cached_asset(cache_dir, ref.content_hash, asset.asset_path)
            if cached is None:
                raise RuntimeError(f"asset was hydrated but not recorded: {asset.asset_path}")
            resolved_assets.append(
                ResolvedAsset(
                    path=asset.asset_path,
                    sha256=asset.sha256,
                    cached_at=cached.verified_at,
                    cache_path=cached.cache_path,
                )
            )
        resolved.append(
            ResolvedDependency(
                fqdn=ref.fqdn,
                content_hash=ref.content_hash,
                assets=tuple(resolved_assets),
            )
        )
    return tuple(resolved)


def write_lockfile(
    cdg_fqdn: str,
    dependencies: Iterable[ResolvedDependency],
    *,
    cache_dir: Path,
    content_hash: str | None = None,
) -> Path:
    lock_dir = cache_dir / "lockfiles"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_name = content_hash or hashlib.sha256(cdg_fqdn.encode("utf-8")).hexdigest()
    lockfile_path = lock_dir / f"{lock_name}.lock.json"
    payload = {
        "cdg_fqdn": cdg_fqdn,
        "hydrated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "dependencies": [
            {
                "fqdn": dependency.fqdn,
                "content_hash": dependency.content_hash,
                "assets": [
                    {
                        "path": asset.path,
                        "sha256": asset.sha256,
                        "cached_at": asset.cached_at,
                        "cache_path": str(asset.cache_path),
                    }
                    for asset in dependency.assets
                ],
            }
            for dependency in dependencies
        ],
    }
    tmp_path = lockfile_path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(lockfile_path)
    return lockfile_path


def asset_ref_from_document(
    document: Mapping[str, Any],
    *,
    content_hash: str = "",
) -> AssetRef | None:
    assets = document.get("assets") or []
    if not assets:
        return None
    artifact = document.get("artifact")
    if not isinstance(artifact, Mapping):
        artifact = {}
    fqdn = str(
        document.get("fqdn")
        or document.get("artifact_fqdn")
        or artifact.get("fqdn")
        or ""
    )
    single_asset_hash = ""
    if len(assets) == 1 and isinstance(assets[0], Mapping):
        single_asset_hash = str(assets[0].get("sha256") or "")
    resolved_content_hash = str(
        content_hash
        or document.get("content_hash")
        or _nested_mapping_value(document.get("version"), "content_hash")
        or _nested_mapping_value(document.get("artifact_version"), "content_hash")
        or single_asset_hash
        or ""
    )
    asset_files = tuple(
        AssetFile(
            asset_path=str(asset.get("asset_path") or asset.get("path")),
            sha256=str(asset["sha256"]),
            byte_size=int(asset["byte_size"]),
            storage_uri=str(asset.get("storage_uri") or ""),
            format=str(asset.get("format") or ""),
            loader_name=str(asset.get("loader_name") or ""),
        )
        for asset in assets
    )
    return AssetRef(fqdn=fqdn, content_hash=resolved_content_hash, assets=asset_files)


async def _collect_state_refs(
    supabase: Any,
    document: Mapping[str, Any],
) -> tuple[AssetRef, ...]:
    refs: list[AssetRef] = []
    own_ref = asset_ref_from_document(document)
    if own_ref is not None:
        refs.append(own_ref)
    for dependency in document.get("dependencies") or []:
        if dependency.get("dependency_role") != "state_artifact":
            continue
        embedded = dependency.get("document")
        if embedded:
            ref = asset_ref_from_document(
                embedded,
                content_hash=str(dependency.get("dependency_content_hash") or ""),
            )
        else:
            dependency_document = await _get_artifact_document(
                supabase,
                dependency["dependency_artifact_fqdn"],
            )
            ref = asset_ref_from_document(
                dependency_document,
                content_hash=str(dependency.get("dependency_content_hash") or ""),
            )
        if ref is not None:
            refs.append(ref)
    return tuple(refs)


async def _get_artifact_document(supabase: Any, fqdn: str) -> Mapping[str, Any]:
    result = supabase.rpc("get_artifact_document", {"request_fqdn": fqdn})
    if asyncio.iscoroutine(result):
        result = await result
    if hasattr(result, "execute"):
        result = result.execute()
        if asyncio.iscoroutine(result):
            result = await result
    data = getattr(result, "data", result)
    if isinstance(data, list):
        if not data:
            raise LookupError(f"artifact document not found: {fqdn}")
        data = data[0]
    if not isinstance(data, Mapping):
        raise TypeError(f"unexpected artifact document result for {fqdn}: {type(data)!r}")
    return data


def _nested_mapping_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return None
