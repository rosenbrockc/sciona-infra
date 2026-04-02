"""SQLite snapshot generation from Supabase-backed catalog data."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_PAGE_SIZE = 1000


def _auth_headers(access_token: str) -> dict[str, str]:
    return {
        "apikey": access_token,
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _domain_tags_to_text(tags: Any) -> str:
    if isinstance(tags, list):
        return ",".join(str(tag) for tag in tags)
    if tags is None:
        return ""
    return str(tags)


def _chunk(values: Sequence[str], size: int) -> list[list[str]]:
    return [list(values[i : i + size]) for i in range(0, len(values), size)]


async def _fetch_all_rows(
    base_url: str,
    access_token: str,
    table: str,
    *,
    select: str = "*",
    filters: Mapping[str, str] | None = None,
    order: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    created_client = client is None
    http_client = client or httpx.AsyncClient(timeout=60.0)
    try:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            params: dict[str, str] = {
                "select": select,
                "limit": str(page_size),
                "offset": str(offset),
            }
            if order:
                params["order"] = order
            if filters:
                params.update(filters)

            response = await http_client.get(
                f"{base_url.rstrip('/')}/rest/v1/{table}",
                params=params,
                headers=_auth_headers(access_token),
            )
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list):
                raise TypeError(f"Supabase table {table!r} did not return a list")
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return rows
    finally:
        if created_client:
            await http_client.aclose()


async def _call_rpc(
    base_url: str,
    access_token: str,
    rpc_name: str,
    payload: Mapping[str, Any] | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> Any:
    created_client = client is None
    http_client = client or httpx.AsyncClient(timeout=60.0)
    try:
        response = await http_client.post(
            f"{base_url.rstrip('/')}/rest/v1/rpc/{rpc_name}",
            json=dict(payload or {}),
            headers=_auth_headers(access_token),
        )
        response.raise_for_status()
        return response.json()
    finally:
        if created_client:
            await http_client.aclose()


async def fetch_manifest_data(
    base_url: str,
    access_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch all manifest inputs from Supabase REST endpoints."""
    atoms = await _fetch_all_rows(
        base_url,
        access_token,
        "atoms",
        select=(
            "atom_id,fqdn,status,domain_tags,description,visibility_tier,"
            "source_kind,stateful_kind,is_stochastic,is_ffi,namespace_root,"
            "namespace_path,source_repo_id,source_package,source_module_path,"
            "source_symbol,is_publishable"
        ),
        filters={
            "status": "eq.approved",
            "is_publishable": "eq.true",
        },
        order="fqdn.asc",
        client=client,
    )

    atom_ids = [str(row["atom_id"]) for row in atoms if row.get("atom_id")]
    hyperparams: list[dict[str, Any]] = []
    rollups: list[dict[str, Any]] = []
    descriptions: list[dict[str, Any]] = []
    benchmarks: list[dict[str, Any]] = []

    for batch in _chunk(atom_ids, DEFAULT_PAGE_SIZE):
        if not batch:
            continue
        in_filter = f"in.({','.join(batch)})"
        hyperparams.extend(
            await _fetch_all_rows(
                base_url,
                access_token,
                "hyperparams",
                select=(
                    "hp_id,atom_id,name,kind,default_value,min_value,max_value,"
                    "step_value,log_scale,choices_json,constraints_json,"
                    "semantic_role,status"
                ),
                filters={
                    "atom_id": in_filter,
                    "status": "eq.approved",
                },
                order="atom_id.asc,name.asc",
                client=client,
            )
        )
        rollups.extend(
            await _fetch_all_rows(
                base_url,
                access_token,
                "atom_audit_rollups",
                select=(
                    "atom_id,overall_verdict,structural_status,runtime_status,"
                    "semantic_status,developer_semantics_status,risk_tier,"
                    "risk_score,risk_dimensions,risk_reasons,acceptability_score,"
                    "acceptability_band,parity_coverage_level,parity_test_status,"
                    "parity_fixture_count,parity_case_count,review_status,"
                    "review_semantic_verdict,review_developer_semantics_verdict,"
                    "review_limitations,review_required_actions,trust_readiness,"
                    "trust_blockers,updated_at"
                ),
                filters={"atom_id": in_filter},
                order="atom_id.asc",
                client=client,
            )
        )
        descriptions.extend(
            await _fetch_all_rows(
                base_url,
                access_token,
                "atom_descriptions",
                select=(
                    "description_id,atom_id,kind,content,language,generated_by,"
                    "reviewed,jargon_score,created_at,updated_at"
                ),
                filters={
                    "atom_id": in_filter,
                    "kind": "eq.dejargonized",
                    "language": "eq.en",
                },
                order="atom_id.asc,updated_at.desc",
                client=client,
            )
        )

    try:
        rpc_rows = await _call_rpc(
            base_url,
            access_token,
            "get_manifest_benchmarks",
            client=client,
        )
        if isinstance(rpc_rows, list):
            source_rows = rpc_rows
        elif isinstance(rpc_rows, dict) and "data" in rpc_rows:
            source_rows = list(rpc_rows["data"] or [])
        else:
            source_rows = []
        for row in source_rows:
            benchmark_id = row.get("benchmark_id") or row.get("benchmark_name") or ""
            benchmarks.append(
                {
                    "atom_fqdn": row.get("atom_fqdn", ""),
                    "content_hash": row.get("content_hash", ""),
                    "benchmark_id": benchmark_id,
                    "benchmark_name": row.get("benchmark_name", benchmark_id),
                    "metric_name": row.get("metric_name", ""),
                    "metric_value": row.get("metric_value"),
                    "dataset_tag": row.get("dataset_tag", ""),
                    "measured_at": row.get("measured_at", ""),
                }
            )
    except httpx.HTTPStatusError:
        source_rows = await _fetch_all_rows(
            base_url,
            access_token,
            "atom_benchmarks",
            select=(
                "benchmark_id,version_id,benchmark_name,metric_name,metric_value,"
                "dataset_tag,measured_at"
            ),
            order="benchmark_name.asc,metric_name.asc",
            client=client,
        )
        version_rows = await _fetch_all_rows(
            base_url,
            access_token,
            "atom_versions",
            select="version_id,atom_id,content_hash",
            client=client,
        )
        version_by_id = {
            str(row["version_id"]): row for row in version_rows if row.get("version_id")
        }
        atom_rows = {str(row["atom_id"]): row for row in atoms if row.get("atom_id")}
        for row in source_rows:
            version = version_by_id.get(str(row.get("version_id", "")), {})
            atom = atom_rows.get(str(version.get("atom_id", "")), {})
            benchmark_id = row.get("benchmark_id") or row.get("benchmark_name") or ""
            benchmarks.append(
                {
                    "atom_fqdn": atom.get("fqdn", ""),
                    "content_hash": version.get("content_hash", ""),
                    "benchmark_id": benchmark_id,
                    "benchmark_name": row.get("benchmark_name", benchmark_id),
                    "metric_name": row.get("metric_name", ""),
                    "metric_value": row.get("metric_value"),
                    "dataset_tag": row.get("dataset_tag", ""),
                    "measured_at": row.get("measured_at", ""),
                }
            )

    return {
        "atoms": atoms,
        "hyperparams": hyperparams,
        "benchmarks": benchmarks,
        "rollups": rollups,
        "descriptions": descriptions,
    }


def _coerce_manifest_data(
    atoms_or_data: list[dict[str, Any]] | Mapping[str, list[dict[str, Any]]],
    hyperparams: list[dict[str, Any]] | None,
    benchmarks: list[dict[str, Any]] | None,
) -> dict[str, list[dict[str, Any]]]:
    if isinstance(atoms_or_data, Mapping) and hyperparams is None and benchmarks is None:
        return {
            "atoms": list(atoms_or_data.get("atoms", [])),
            "hyperparams": list(atoms_or_data.get("hyperparams", [])),
            "benchmarks": list(atoms_or_data.get("benchmarks", [])),
            "rollups": list(atoms_or_data.get("rollups", [])),
            "descriptions": list(atoms_or_data.get("descriptions", [])),
        }

    return {
        "atoms": list(atoms_or_data),
        "hyperparams": list(hyperparams or []),
        "benchmarks": list(benchmarks or []),
        "rollups": [],
        "descriptions": [],
    }


def _create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        DROP TABLE IF EXISTS atoms;
        DROP TABLE IF EXISTS hyperparams;
        DROP TABLE IF EXISTS benchmarks;
        DROP TABLE IF EXISTS audit_rollups;
        DROP TABLE IF EXISTS descriptions;

        CREATE TABLE atoms (
            atom_id TEXT PRIMARY KEY,
            fqdn TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'approved',
            domain_tags TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            visibility_tier TEXT NOT NULL DEFAULT 'general',
            source_kind TEXT NOT NULL DEFAULT 'hand_written',
            stateful_kind TEXT NOT NULL DEFAULT 'none',
            is_stochastic INTEGER NOT NULL DEFAULT 0,
            is_ffi INTEGER NOT NULL DEFAULT 0,
            namespace_root TEXT NOT NULL DEFAULT 'sciona.atoms',
            namespace_path TEXT NOT NULL DEFAULT '',
            source_repo_id TEXT NOT NULL DEFAULT '',
            source_package TEXT NOT NULL DEFAULT '',
            source_module_path TEXT NOT NULL DEFAULT '',
            source_symbol TEXT NOT NULL DEFAULT '',
            is_publishable INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE hyperparams (
            hp_id TEXT PRIMARY KEY,
            atom_id TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            default_value TEXT,
            min_value TEXT,
            max_value TEXT,
            step_value TEXT,
            log_scale INTEGER NOT NULL DEFAULT 0,
            choices_json TEXT,
            constraints_json TEXT,
            semantic_role TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'approved',
            UNIQUE (atom_id, name)
        );

        CREATE TABLE benchmarks (
            atom_fqdn TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            benchmark_id TEXT NOT NULL,
            benchmark_name TEXT NOT NULL DEFAULT '',
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            dataset_tag TEXT NOT NULL DEFAULT '',
            measured_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (atom_fqdn, content_hash, benchmark_id, metric_name)
        );

        CREATE TABLE audit_rollups (
            atom_id TEXT PRIMARY KEY,
            overall_verdict TEXT NOT NULL DEFAULT 'unknown',
            structural_status TEXT NOT NULL DEFAULT 'unknown',
            runtime_status TEXT NOT NULL DEFAULT 'unknown',
            semantic_status TEXT NOT NULL DEFAULT 'unknown',
            developer_semantics_status TEXT NOT NULL DEFAULT 'unknown',
            risk_tier TEXT NOT NULL DEFAULT 'medium',
            risk_score INTEGER NOT NULL DEFAULT 0,
            risk_dimensions TEXT NOT NULL DEFAULT '{}',
            risk_reasons TEXT NOT NULL DEFAULT '[]',
            acceptability_score INTEGER NOT NULL DEFAULT 0,
            acceptability_band TEXT NOT NULL DEFAULT 'unknown',
            parity_coverage_level TEXT NOT NULL DEFAULT 'unknown',
            parity_test_status TEXT NOT NULL DEFAULT 'unknown',
            parity_fixture_count INTEGER NOT NULL DEFAULT 0,
            parity_case_count INTEGER NOT NULL DEFAULT 0,
            review_status TEXT NOT NULL DEFAULT 'missing',
            review_semantic_verdict TEXT NOT NULL DEFAULT 'unknown',
            review_developer_semantics_verdict TEXT NOT NULL DEFAULT 'unknown',
            review_limitations TEXT NOT NULL DEFAULT '[]',
            review_required_actions TEXT NOT NULL DEFAULT '[]',
            trust_readiness TEXT NOT NULL DEFAULT 'not_ready',
            trust_blockers TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE descriptions (
            description_id TEXT PRIMARY KEY,
            atom_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT 'en',
            generated_by TEXT NOT NULL DEFAULT '',
            reviewed INTEGER NOT NULL DEFAULT 0,
            jargon_score REAL NOT NULL DEFAULT 1.0,
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        """
    )


def _insert_atom(con: sqlite3.Connection, atom: Mapping[str, Any]) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO atoms (
            atom_id, fqdn, status, domain_tags, description, visibility_tier,
            source_kind, stateful_kind, is_stochastic, is_ffi, namespace_root,
            namespace_path, source_repo_id, source_package, source_module_path,
            source_symbol, is_publishable
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _stringify(atom.get("atom_id")),
            atom.get("fqdn", ""),
            atom.get("status", "approved"),
            _domain_tags_to_text(atom.get("domain_tags", [])),
            atom.get("description", ""),
            atom.get("visibility_tier", "general"),
            atom.get("source_kind", "hand_written"),
            atom.get("stateful_kind", "none"),
            int(bool(atom.get("is_stochastic", False))),
            int(bool(atom.get("is_ffi", False))),
            atom.get("namespace_root", "sciona.atoms"),
            atom.get("namespace_path", ""),
            _stringify(atom.get("source_repo_id")),
            atom.get("source_package", ""),
            atom.get("source_module_path", ""),
            atom.get("source_symbol", ""),
            int(bool(atom.get("is_publishable", False))),
        ),
    )


def _insert_hyperparam(con: sqlite3.Connection, hp: Mapping[str, Any]) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO hyperparams (
            hp_id, atom_id, name, kind, default_value, min_value, max_value,
            step_value, log_scale, choices_json, constraints_json,
            semantic_role, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _stringify(hp.get("hp_id")),
            _stringify(hp.get("atom_id")),
            hp.get("name", ""),
            hp.get("kind", ""),
            _stringify(hp.get("default_value")) if hp.get("default_value") is not None else None,
            _stringify(hp.get("min_value")) if hp.get("min_value") is not None else None,
            _stringify(hp.get("max_value")) if hp.get("max_value") is not None else None,
            _stringify(hp.get("step_value")) if hp.get("step_value") is not None else None,
            int(bool(hp.get("log_scale", False))),
            _stringify(hp.get("choices_json")) if hp.get("choices_json") is not None else None,
            _stringify(hp.get("constraints_json")) if hp.get("constraints_json") is not None else None,
            hp.get("semantic_role", ""),
            hp.get("status", "approved"),
        ),
    )


def _insert_benchmark(con: sqlite3.Connection, bm: Mapping[str, Any]) -> None:
    benchmark_id = bm.get("benchmark_id") or bm.get("benchmark_name") or ""
    benchmark_name = bm.get("benchmark_name") or benchmark_id
    con.execute(
        """
        INSERT OR REPLACE INTO benchmarks (
            atom_fqdn, content_hash, benchmark_id, benchmark_name, metric_name,
            metric_value, dataset_tag, measured_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bm.get("atom_fqdn", ""),
            bm.get("content_hash", ""),
            benchmark_id,
            benchmark_name,
            bm.get("metric_name", ""),
            bm.get("metric_value", 0.0),
            bm.get("dataset_tag", ""),
            _stringify(bm.get("measured_at")),
        ),
    )


def _insert_rollup(con: sqlite3.Connection, rollup: Mapping[str, Any]) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO audit_rollups (
            atom_id, overall_verdict, structural_status, runtime_status,
            semantic_status, developer_semantics_status, risk_tier, risk_score,
            risk_dimensions, risk_reasons, acceptability_score,
            acceptability_band, parity_coverage_level, parity_test_status,
            parity_fixture_count, parity_case_count, review_status,
            review_semantic_verdict, review_developer_semantics_verdict,
            review_limitations, review_required_actions, trust_readiness,
            trust_blockers, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _stringify(rollup.get("atom_id")),
            rollup.get("overall_verdict", "unknown"),
            rollup.get("structural_status", "unknown"),
            rollup.get("runtime_status", "unknown"),
            rollup.get("semantic_status", "unknown"),
            rollup.get("developer_semantics_status", "unknown"),
            rollup.get("risk_tier", "medium"),
            int(rollup.get("risk_score", 0)),
            _stringify(rollup.get("risk_dimensions", {})),
            _stringify(rollup.get("risk_reasons", [])),
            int(rollup.get("acceptability_score", 0)),
            rollup.get("acceptability_band", "unknown"),
            rollup.get("parity_coverage_level", "unknown"),
            rollup.get("parity_test_status", "unknown"),
            int(rollup.get("parity_fixture_count", 0)),
            int(rollup.get("parity_case_count", 0)),
            rollup.get("review_status", "missing"),
            rollup.get("review_semantic_verdict", "unknown"),
            rollup.get("review_developer_semantics_verdict", "unknown"),
            _stringify(rollup.get("review_limitations", [])),
            _stringify(rollup.get("review_required_actions", [])),
            rollup.get("trust_readiness", "not_ready"),
            _stringify(rollup.get("trust_blockers", [])),
            _stringify(rollup.get("updated_at")),
        ),
    )


def _insert_description(con: sqlite3.Connection, desc: Mapping[str, Any]) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO descriptions (
            description_id, atom_id, kind, content, language, generated_by,
            reviewed, jargon_score, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _stringify(desc.get("description_id")),
            _stringify(desc.get("atom_id")),
            desc.get("kind", ""),
            desc.get("content", ""),
            desc.get("language", "en"),
            desc.get("generated_by", ""),
            int(bool(desc.get("reviewed", False))),
            float(desc.get("jargon_score", 1.0)),
            _stringify(desc.get("created_at")),
            _stringify(desc.get("updated_at")),
        ),
    )


def generate_manifest_sqlite(
    atoms_or_data: list[dict[str, Any]] | Mapping[str, list[dict[str, Any]]],
    hyperparams: list[dict[str, Any]] | None = None,
    benchmarks: list[dict[str, Any]] | None = None,
    output_path: Path | None = None,
) -> sqlite3.Connection:
    """Generate a manifest.sqlite from Supabase-fetched data.

    The function remains backward-compatible with the legacy
    ``generate_manifest_sqlite(atoms, hyperparams, benchmarks, output_path)``
    call pattern while also accepting the new manifest data mapping returned by
    ``fetch_manifest_data()``.
    """
    data = _coerce_manifest_data(atoms_or_data, hyperparams, benchmarks)

    db_str = str(output_path) if output_path else ":memory:"
    con = sqlite3.connect(db_str)
    _create_schema(con)

    for atom in data["atoms"]:
        _insert_atom(con, atom)
    for hp in data["hyperparams"]:
        _insert_hyperparam(con, hp)
    for bm in data["benchmarks"]:
        _insert_benchmark(con, bm)
    for rollup in data["rollups"]:
        _insert_rollup(con, rollup)
    for desc in data["descriptions"]:
        _insert_description(con, desc)

    con.commit()
    return con
