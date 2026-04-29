"""State artifact publish and asset verification endpoints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException

from sciona_infra.api import deps as api_deps
from sciona_infra.assets.format_scanner import (
    ALLOWED_FORMATS,
    BLOCKED_EXTENSIONS,
    BLOCKED_MAGIC_BYTES,
    sha256_file,
)
from sciona_infra.api.models import (
    AssetEntry,
    PresignAssetsRequest,
    StateArtifactPublishRequest,
    StateArtifactPublishResponse,
    VerifyAssetResult,
    VerifyAssetsRequest,
    VerifyAssetsResponse,
)

UserRow = getattr(api_deps, "UserProfile", None) or api_deps.UserRow
require_auth = api_deps.require_auth

router = APIRouter()

EXTENSIONS_BY_FORMAT = {
    "safetensors": {".safetensors"},
    "onnx": {".onnx"},
    "json": {".json"},
    "jsonl": {".jsonl"},
    "parquet": {".parquet"},
    "npy": {".npy"},
    "npz": {".npz"},
    "txt": {".txt"},
    "vocab": {".vocab", ".txt"},
}


def _first_row(data: Any) -> dict[str, Any] | None:
    if data is None:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


def _dump_model(model: Any, **kwargs: Any) -> dict[str, Any]:
    return model.model_dump(**kwargs)


def _canonical_content_hash(assets: list[AssetEntry]) -> str:
    if len(assets) == 1:
        return assets[0].sha256
    manifest = {
        "files": [
            {
                "path": asset.asset_path,
                "sha256": asset.sha256,
                "byte_size": asset.byte_size,
            }
            for asset in sorted(assets, key=lambda item: item.asset_path)
        ]
    }
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _presign_placeholder(fqdn: str, asset: AssetEntry) -> str:
    return f"placeholder://upload/{fqdn}/{asset.sha256}/{asset.asset_path}"


async def _ensure_state_artifact(
    *,
    supabase: Any,
    body: StateArtifactPublishRequest,
    user_id: str,
) -> tuple[str, bool]:
    result = await (
        supabase.table("artifacts")
        .select("artifact_id, artifact_kind")
        .eq("fqdn", body.fqdn)
        .maybe_single()
        .execute()
    )
    row = _first_row(result.data)
    if row:
        if row.get("artifact_kind") != "state_artifact":
            raise HTTPException(409, f"Artifact {body.fqdn!r} is not a state artifact")
        return str(row["artifact_id"]), False

    inserted = await (
        supabase.table("artifacts")
        .insert(
            {
                "artifact_kind": "state_artifact",
                "fqdn": body.fqdn,
                "owner_id": user_id,
                "description": body.description,
                "source_module_path": "",
                "source_symbol": "",
                "stateful_kind": "explicit_state_model",
                "is_stochastic": False,
                "is_ffi": False,
            }
        )
        .execute()
    )
    inserted_row = _first_row(inserted.data)
    if not inserted_row:
        raise HTTPException(500, "Failed to create state artifact")
    return str(inserted_row["artifact_id"]), True


async def _require_state_artifact(
    *,
    supabase: Any,
    fqdn: str,
) -> str:
    result = await (
        supabase.table("artifacts")
        .select("artifact_id, artifact_kind")
        .eq("fqdn", fqdn)
        .maybe_single()
        .execute()
    )
    row = _first_row(result.data)
    if not row:
        raise HTTPException(404, f"State artifact {fqdn!r} not found")
    if row.get("artifact_kind") != "state_artifact":
        raise HTTPException(409, f"Artifact {fqdn!r} is not a state artifact")
    return str(row["artifact_id"])


async def _create_state_artifact_version(
    *,
    supabase: Any,
    artifact_id: str,
    is_new_artifact: bool,
    body: StateArtifactPublishRequest,
) -> StateArtifactPublishResponse:
    content_hash = _canonical_content_hash(body.assets)
    if body.declared_content_hash and body.declared_content_hash != content_hash:
        raise HTTPException(422, "Declared content hash does not match asset manifest")

    existing = await (
        supabase.table("artifact_versions")
        .select("version_id")
        .eq("content_hash", content_hash)
        .maybe_single()
        .execute()
    )
    if _first_row(existing.data):
        raise HTTPException(409, f"Content hash {content_hash[:16]}... already exists")

    await (
        supabase.table("artifact_versions")
        .update({"is_latest": False})
        .eq("artifact_id", artifact_id)
        .execute()
    )
    version_result = await (
        supabase.table("artifact_versions")
        .insert(
            {
                "artifact_id": artifact_id,
                "content_hash": content_hash,
                "semver": body.semver,
                "is_latest": True,
                "s3_key": f"artifacts/{content_hash}/manifest.json",
                "fingerprint": content_hash,
            }
        )
        .execute()
    )
    version_row = _first_row(version_result.data)
    if not version_row:
        raise HTTPException(500, "Failed to create artifact version")
    version_id = str(version_row["version_id"])

    asset_payloads = []
    for asset in body.assets:
        payload = _dump_model(asset, exclude={"asset_id"})
        payload["version_id"] = version_id
        asset_payloads.append(payload)
    asset_result = await supabase.table("artifact_assets").insert(asset_payloads).execute()
    asset_rows = asset_result.data or []
    response_assets = [AssetEntry(**dict(row)) for row in asset_rows] or body.assets

    metadata_payload = _dump_model(body.metadata)
    metadata_payload["version_id"] = version_id
    await supabase.table("state_artifact_metadata").insert(metadata_payload).execute()

    if body.dependencies:
        dependency_payloads = []
        for dependency in body.dependencies:
            payload = _dump_model(dependency)
            payload["dependent_version_id"] = version_id
            dependency_payloads.append(payload)
        await supabase.table("artifact_dependencies").insert(dependency_payloads).execute()

    return StateArtifactPublishResponse(
        artifact_id=artifact_id,
        version_id=version_id,
        fqdn=body.fqdn,
        content_hash=content_hash,
        semver=body.semver,
        is_new_artifact=is_new_artifact,
        assets=response_assets,
        presigned_uploads={
            asset.asset_path: _presign_placeholder(body.fqdn, asset)
            for asset in body.assets
        },
    )


@router.post("/state")
async def create_state_artifact(
    body: StateArtifactPublishRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> StateArtifactPublishResponse:
    """Create a state artifact and its first immutable asset version."""
    artifact_id, is_new = await _ensure_state_artifact(
        supabase=supabase,
        body=body,
        user_id=str(user.user_id),
    )
    return await _create_state_artifact_version(
        supabase=supabase,
        artifact_id=artifact_id,
        is_new_artifact=is_new,
        body=body,
    )


@router.post("/{fqdn}/versions")
async def create_state_artifact_version(
    fqdn: str,
    body: StateArtifactPublishRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> StateArtifactPublishResponse:
    """Create a new immutable version for an existing state artifact."""
    _ = user
    if body.fqdn != fqdn:
        raise HTTPException(422, "Request body fqdn must match the path fqdn")
    artifact_id = await _require_state_artifact(
        supabase=supabase,
        fqdn=fqdn,
    )
    return await _create_state_artifact_version(
        supabase=supabase,
        artifact_id=artifact_id,
        is_new_artifact=False,
        body=body,
    )


@router.post("/{fqdn}/assets/presign")
async def presign_state_artifact_assets(
    fqdn: str,
    body: PresignAssetsRequest,
    user: UserRow = Depends(require_auth),
) -> dict[str, str]:
    """Return deterministic placeholder upload URLs until S3 signing is wired."""
    _ = user
    return {
        asset.asset_path: _presign_placeholder(fqdn, asset)
        for asset in body.assets
    }


def _resolve_local_path(asset: dict[str, Any], body: VerifyAssetsRequest) -> Path:
    asset_path = str(asset.get("asset_path", ""))
    if body.local_base_path:
        base = Path(body.local_base_path)
        resolved = (base / asset_path).resolve()
        if not resolved.is_relative_to(base.resolve()):
            raise ValueError(f"asset path escapes base directory: {asset_path}")
        return resolved

    uri = body.storage_uris.get(asset_path) or str(asset.get("storage_uri", ""))
    if not uri:
        raise ValueError("No storage_uri or local_base_path provided")

    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme:
        raise ValueError(f"Unsupported storage URI scheme: {parsed.scheme}")
    return Path(uri)


def _fallback_scan_asset(path: Path, declared_format: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    suffix = path.suffix.lower()
    if declared_format not in ALLOWED_FORMATS:
        errors.append(f"format {declared_format!r} is not allowlisted")
    if suffix in BLOCKED_EXTENSIONS:
        errors.append(f"blocked serialized resource extension: {suffix}")
    expected_suffixes = EXTENSIONS_BY_FORMAT.get(declared_format, set())
    if expected_suffixes and suffix not in expected_suffixes:
        errors.append(
            f"extension {suffix!r} does not match declared format {declared_format!r}"
        )
    prefix = path.read_bytes()[:1024 * 1024]
    for magic, label in BLOCKED_MAGIC_BYTES.items():
        if prefix.startswith(magic):
            errors.append(f"blocked magic bytes detected: {label}")
    if b"torch._utils" in prefix or b"torch.storage" in prefix:
        errors.append("blocked torch serialization marker detected")
    return not errors, errors


def _scan_asset(path: Path, declared_format: str) -> tuple[bool, list[str]]:
    try:
        from sciona_infra.assets.format_scanner import scan_asset
    except ImportError:
        return _fallback_scan_asset(path, declared_format)
    result = scan_asset(path, declared_format)
    return result.ok, list(result.errors)


async def _write_audit_evidence(
    *,
    supabase: Any,
    artifact_id: str,
    version_id: str,
    results: list[VerifyAssetResult],
) -> None:
    payloads: list[dict[str, Any]] = []
    for result in results:
        base_details = {
            "asset_id": str(result.asset_id) if result.asset_id else "",
            "asset_path": result.asset_path,
            "errors": result.errors,
        }
        payloads.append(
            {
                "artifact_id": artifact_id,
                "version_id": version_id,
                "audit_type": "asset_integrity_check",
                "passed": result.actual_sha256 == result.expected_sha256,
                "details": {
                    **base_details,
                    "expected_sha256": result.expected_sha256,
                    "actual_sha256": result.actual_sha256,
                },
                "source_kind": "automated",
                "runner_version": "api-state-artifacts-v1",
            }
        )
        payloads.append(
            {
                "artifact_id": artifact_id,
                "version_id": version_id,
                "audit_type": "format_security_scan",
                "passed": result.scan_passed,
                "details": base_details,
                "source_kind": "automated",
                "runner_version": "api-state-artifacts-v1",
            }
        )
    if payloads:
        await supabase.table("artifact_audit_evidence").insert(payloads).execute()


@router.post("/{fqdn}/versions/{version_id}/verify-assets")
async def verify_state_artifact_assets(
    fqdn: str,
    version_id: str,
    body: VerifyAssetsRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> VerifyAssetsResponse:
    """Verify local or file:// asset bytes against declared SHA-256 hashes."""
    _ = user
    artifact_result = await (
        supabase.table("artifacts")
        .select("artifact_id, artifact_kind")
        .eq("fqdn", fqdn)
        .maybe_single()
        .execute()
    )
    artifact = _first_row(artifact_result.data)
    if not artifact or artifact.get("artifact_kind") != "state_artifact":
        raise HTTPException(404, f"State artifact {fqdn!r} not found")

    artifact_id = str(artifact["artifact_id"])
    version_result = await (
        supabase.table("artifact_versions")
        .select("version_id")
        .eq("version_id", version_id)
        .eq("artifact_id", artifact_id)
        .maybe_single()
        .execute()
    )
    if not _first_row(version_result.data):
        raise HTTPException(404, f"Version {version_id!r} not found for {fqdn!r}")

    assets_result = await (
        supabase.table("artifact_assets")
        .select("*")
        .eq("version_id", version_id)
        .execute()
    )
    asset_rows = assets_result.data or []
    if not asset_rows:
        raise HTTPException(404, "No assets declared for version")

    results: list[VerifyAssetResult] = []
    for asset in asset_rows:
        errors: list[str] = []
        actual_sha256 = ""
        scan_passed = False
        try:
            local_path = _resolve_local_path(asset, body)
            actual_sha256 = sha256_file(local_path)
            scan_passed, scan_errors = _scan_asset(
                local_path,
                str(asset.get("format", "")),
            )
            errors.extend(scan_errors)
            if actual_sha256 != asset.get("sha256"):
                errors.append("sha256 mismatch")
        except Exception as exc:
            errors.append(str(exc))

        results.append(
            VerifyAssetResult(
                asset_id=asset.get("asset_id"),
                asset_path=str(asset.get("asset_path", "")),
                passed=not errors,
                expected_sha256=str(asset.get("sha256", "")),
                actual_sha256=actual_sha256,
                scan_passed=scan_passed,
                errors=errors,
            )
        )

    if body.write_audit_evidence:
        await _write_audit_evidence(
            supabase=supabase,
            artifact_id=artifact_id,
            version_id=version_id,
            results=results,
        )

    return VerifyAssetsResponse(
        artifact_id=artifact_id,
        version_id=version_id,
        passed=all(result.passed for result in results),
        results=results,
    )
