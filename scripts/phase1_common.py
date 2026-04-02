"""Shared helpers for the Phase 1 Supabase migration scripts."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


@dataclass(frozen=True)
class DatabaseSettings:
    source_database_url: str
    supabase_database_url: str

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        return cls(
            source_database_url=require_env("SOURCE_DATABASE_URL"),
            supabase_database_url=require_env("SUPABASE_DATABASE_URL"),
        )


@dataclass(frozen=True)
class SupabaseSettings:
    supabase_url: str
    service_key: str

    @classmethod
    def from_env(cls) -> "SupabaseSettings":
        return cls(
            supabase_url=require_env("SUPABASE_URL"),
            service_key=require_env("SUPABASE_SERVICE_KEY"),
        )


@dataclass(frozen=True)
class OrganizationSeed:
    name: str
    entitlement_tier: str
    email_domains: tuple[str, ...]


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def chunked(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    for start in range(0, len(items), size):
        yield items[start : start + size]


def github_noreply_email(github_login: str, *, domain: str) -> str:
    login = (github_login or "").strip() or "unknown"
    return f"{login}@{domain}"


def normalize_email_domain(value: str) -> str:
    domain = value.strip().lower()
    if domain.startswith("@"):
        domain = domain[1:]
    return domain


def load_organization_seeds_from_env() -> list[OrganizationSeed]:
    raw_json = os.environ.get("PHASE1_ORGANIZATIONS_JSON", "").strip()
    raw_path = os.environ.get("PHASE1_ORGANIZATIONS_FILE", "").strip()
    payload: Any

    if raw_json:
        payload = json.loads(raw_json)
    elif raw_path:
        payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    else:
        return []

    if not isinstance(payload, list):
        raise ValueError("organization seed config must be a JSON list")

    seeds: list[OrganizationSeed] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("organization seed entries must be objects")
        domains_raw = item.get("email_domains", [])
        if not isinstance(domains_raw, list):
            raise ValueError("organization seed email_domains must be a list")
        domains = tuple(
            sorted(
                {
                    normalize_email_domain(str(domain))
                    for domain in domains_raw
                    if str(domain).strip()
                }
            )
        )
        seeds.append(
            OrganizationSeed(
                name=str(item.get("name", "")).strip(),
                entitlement_tier=str(item.get("entitlement_tier", "early_access")).strip()
                or "early_access",
                email_domains=domains,
            )
        )
    return seeds


def build_batch_params(
    rows: Iterable[dict[str, Any]],
    param_columns: Sequence[str],
) -> list[tuple[Any, ...]]:
    return [
        tuple(row.get(column) for column in param_columns)
        for row in rows
    ]

