from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator
from urllib.parse import urlparse
from urllib.request import urlopen

from sciona_infra.assets import cache_db
from sciona_infra.assets.format_scanner import scan_asset, sha256_file
from sciona_infra.assets.loader_policy import validate_loader_policy


class CryptographicIntegrityError(RuntimeError):
    """Raised when local or remote bytes do not match a declared SHA-256 hash."""


@dataclass(frozen=True)
class AssetFile:
    asset_path: str
    sha256: str
    byte_size: int
    storage_uri: str = ""
    format: str = ""
    loader_name: str = ""


@dataclass(frozen=True)
class AssetRef:
    fqdn: str
    content_hash: str
    assets: tuple[AssetFile, ...]


def hydrate_asset(ref: AssetRef, *, cache_dir: Path, network: bool = True) -> Path:
    """Hydrate and verify all files in an asset ref.

    Returns the local file path for single-file refs and the version cache
    directory for multi-file refs.
    """
    if not ref.assets:
        raise CryptographicIntegrityError("asset ref contains no files")
    _validate_ref(ref)

    root = _ref_cache_root(cache_dir, ref)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with _file_lock(lock_path):
        for asset in ref.assets:
            target = _asset_cache_path(cache_dir, ref, asset)
            if _verified_cache_hit(target, asset):
                _record(cache_dir, ref, asset, target)
                continue
            if not network:
                raise CryptographicIntegrityError(
                    f"strict offline cache miss for {ref.fqdn}:{asset.asset_path}"
                )
            _hydrate_one(ref, asset, cache_dir, target)

    if len(ref.assets) == 1:
        return _asset_cache_path(cache_dir, ref, ref.assets[0])
    return root


def resolve_asset_path(ref: AssetRef, *, cache_dir: Path, network: bool = True) -> Path:
    return hydrate_asset(ref, cache_dir=cache_dir, network=network)


def _hydrate_one(ref: AssetRef, asset: AssetFile, cache_dir: Path, target: Path) -> None:
    if not asset.storage_uri:
        raise CryptographicIntegrityError(
            f"no storage URI available for cache miss: {ref.fqdn}:{asset.asset_path}"
        )

    tmp_dir = cache_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"{uuid.uuid4().hex}-{Path(asset.asset_path).name}"
    try:
        _download_to_path(asset.storage_uri, tmp_path)
        actual = sha256_file(tmp_path)
        if actual != asset.sha256.lower():
            raise CryptographicIntegrityError(
                f"sha256 mismatch for {asset.asset_path}: expected {asset.sha256}, got {actual}"
            )
        if tmp_path.stat().st_size != asset.byte_size:
            raise CryptographicIntegrityError(
                f"byte size mismatch for {asset.asset_path}: "
                f"expected {asset.byte_size}, got {tmp_path.stat().st_size}"
            )
        scan = scan_asset(tmp_path, asset.format)
        if not scan.ok:
            raise CryptographicIntegrityError(
                f"format scan failed for {asset.asset_path}: {', '.join(scan.errors)}"
            )
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_path, target)
    _record(cache_dir, ref, asset, target)


def _download_to_path(storage_uri: str, target: Path) -> None:
    parsed = urlparse(storage_uri)
    if parsed.scheme in {"", "file"}:
        source = Path(parsed.path if parsed.scheme == "file" else storage_uri)
        with source.open("rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        return
    if parsed.scheme in {"http", "https"}:
        with urlopen(storage_uri, timeout=30) as response, target.open("wb") as dst:
            shutil.copyfileobj(response, dst, length=1024 * 1024)
        return
    if parsed.scheme == "s3":
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("boto3 is required to hydrate s3:// assets") from exc
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        boto3.client("s3").download_file(bucket, key, str(target))
        return
    raise RuntimeError(f"unsupported storage URI scheme: {parsed.scheme}")


def _validate_ref(ref: AssetRef) -> None:
    _validate_hash(ref.content_hash, "content_hash")
    seen_paths: set[str] = set()
    for asset in ref.assets:
        _validate_hash(asset.sha256, f"sha256:{asset.asset_path}")
        if asset.byte_size < 0:
            raise ValueError(f"negative byte size for {asset.asset_path}")
        if asset.asset_path in seen_paths:
            raise ValueError(f"duplicate asset path: {asset.asset_path}")
        seen_paths.add(asset.asset_path)
        _safe_relative_path(asset.asset_path)
        ok, errors = validate_loader_policy(asset.format, asset.loader_name)
        if not ok:
            raise ValueError(", ".join(errors))


def _validate_hash(value: str, label: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{label} must be a 64-character lowercase hex SHA-256 digest, got: {value[:16]}...")


def _safe_relative_path(asset_path: str) -> Path:
    posix = PurePosixPath(asset_path)
    if posix.is_absolute() or ".." in posix.parts or not posix.parts:
        raise ValueError(f"unsafe asset path: {asset_path}")
    return Path(*posix.parts)


def _ref_cache_root(cache_dir: Path, ref: AssetRef) -> Path:
    return cache_dir / "sha256" / ref.content_hash.lower()


def _asset_cache_path(cache_dir: Path, ref: AssetRef, asset: AssetFile) -> Path:
    return _ref_cache_root(cache_dir, ref) / _safe_relative_path(asset.asset_path)


def _verified_cache_hit(path: Path, asset: AssetFile) -> bool:
    if not path.exists() or not path.is_file():
        return False
    if path.stat().st_size != asset.byte_size:
        return False
    return sha256_file(path) == asset.sha256.lower()


def _record(cache_dir: Path, ref: AssetRef, asset: AssetFile, target: Path) -> None:
    cache_db.record_asset(
        cache_dir,
        fqdn=ref.fqdn,
        content_hash=ref.content_hash.lower(),
        asset_path=asset.asset_path,
        sha256=asset.sha256.lower(),
        byte_size=asset.byte_size,
        storage_uri=asset.storage_uri,
        loader_name=asset.loader_name,
        cache_path=target,
    )


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:
            yield
