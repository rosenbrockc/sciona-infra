from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CachedAsset:
    fqdn: str
    content_hash: str
    asset_path: str
    sha256: str
    byte_size: int
    storage_uri: str
    loader_name: str
    cache_path: Path
    verified_at: str


def db_path(cache_dir: Path) -> Path:
    return cache_dir / "manifest.sqlite"


def connect(cache_dir: Path) -> sqlite3.Connection:
    cache_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path(cache_dir))
    connection.row_factory = sqlite3.Row
    init_db(connection)
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS cached_assets (
            fqdn TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            asset_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            storage_uri TEXT NOT NULL DEFAULT '',
            loader_name TEXT NOT NULL DEFAULT '',
            cache_path TEXT NOT NULL,
            verified_at TEXT NOT NULL,
            PRIMARY KEY (content_hash, asset_path)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_cached_assets_sha256 ON cached_assets(sha256)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_cached_assets_fqdn ON cached_assets(fqdn)"
    )
    connection.commit()


def record_asset(
    cache_dir: Path,
    *,
    fqdn: str,
    content_hash: str,
    asset_path: str,
    sha256: str,
    byte_size: int,
    storage_uri: str,
    loader_name: str,
    cache_path: Path,
    verified_at: str | None = None,
) -> None:
    timestamp = verified_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with connect(cache_dir) as connection:
        connection.execute(
            """
            INSERT INTO cached_assets (
                fqdn, content_hash, asset_path, sha256, byte_size, storage_uri,
                loader_name, cache_path, verified_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_hash, asset_path) DO UPDATE SET
                fqdn = excluded.fqdn,
                sha256 = excluded.sha256,
                byte_size = excluded.byte_size,
                storage_uri = excluded.storage_uri,
                loader_name = excluded.loader_name,
                cache_path = excluded.cache_path,
                verified_at = excluded.verified_at
            """,
            (
                fqdn,
                content_hash,
                asset_path,
                sha256,
                byte_size,
                storage_uri,
                loader_name,
                str(cache_path),
                timestamp,
            ),
        )
        connection.commit()


def get_cached_asset(cache_dir: Path, content_hash: str, asset_path: str) -> CachedAsset | None:
    with connect(cache_dir) as connection:
        row = connection.execute(
            """
            SELECT * FROM cached_assets
            WHERE content_hash = ? AND asset_path = ?
            """,
            (content_hash, asset_path),
        ).fetchone()
    return _row_to_cached_asset(row) if row else None


def list_cached_assets(cache_dir: Path, *, fqdn: str | None = None) -> tuple[CachedAsset, ...]:
    query = "SELECT * FROM cached_assets"
    params: tuple[str, ...] = ()
    if fqdn:
        query += " WHERE fqdn = ?"
        params = (fqdn,)
    query += " ORDER BY fqdn, content_hash, asset_path"
    with connect(cache_dir) as connection:
        rows = connection.execute(query, params).fetchall()
    return tuple(_row_to_cached_asset(row) for row in rows)


def prune_missing(cache_dir: Path) -> int:
    removed = 0
    with connect(cache_dir) as connection:
        rows = connection.execute("SELECT content_hash, asset_path, cache_path FROM cached_assets").fetchall()
        for row in rows:
            if Path(row["cache_path"]).exists():
                continue
            connection.execute(
                "DELETE FROM cached_assets WHERE content_hash = ? AND asset_path = ?",
                (row["content_hash"], row["asset_path"]),
            )
            removed += 1
        connection.commit()
    return removed


def _row_to_cached_asset(row: sqlite3.Row) -> CachedAsset:
    return CachedAsset(
        fqdn=row["fqdn"],
        content_hash=row["content_hash"],
        asset_path=row["asset_path"],
        sha256=row["sha256"],
        byte_size=int(row["byte_size"]),
        storage_uri=row["storage_uri"],
        loader_name=row["loader_name"],
        cache_path=Path(row["cache_path"]),
        verified_at=row["verified_at"],
    )
