"""Generate and backfill dejargonized atom_descriptions rows."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("SCIONA_DEJARGONIZER_MODEL", "gpt-4o-mini")
DEFAULT_THRESHOLD = 0.4
JARGON_HINTS = {
    "bayesian",
    "kalman",
    "hamiltonian",
    "fft",
    "likelihood",
    "posterior",
    "covariance",
    "stateful",
    "semantics",
    "cdg",
    "fidelity",
    "parity",
    "autocorrelation",
    "dedispersion",
}


def create_supabase_client():
    """Create a service-role Supabase client lazily."""
    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError("supabase-py is required to run this script") from exc

    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def build_prompt(
    *,
    fqdn: str,
    technical_content: str,
    parameter_list: str,
    io_specs: str,
    domain_tags: list[str],
) -> str:
    """Build the LLM prompt from the phase plan sketch."""
    domains = ", ".join(domain_tags) if domain_tags else "unknown"
    return (
        "You are rewriting a technical description of a scientific computing function "
        "for a non-specialist audience.\n\n"
        f"Function: {fqdn}\n"
        f"Technical description: {technical_content}\n"
        f"Parameters: {parameter_list or 'none'}\n"
        f"IO specs: {io_specs or 'none'}\n"
        f"Domain: {domains}\n\n"
        "Write a 2-4 sentence plain-language description. Avoid jargon. If a "
        "technical term is unavoidable, define it briefly in plain English."
    )


def estimate_jargon_score(text: str) -> float:
    """Estimate jargon density heuristically on a [0, 1] scale."""
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_+-]*", text.lower())
    if not words:
        return 0.0
    jargon_weight = 0.0
    for word in words:
        if word in JARGON_HINTS:
            jargon_weight += 1.5
        elif "_" in word or len(word) > 14:
            jargon_weight += 1.0
    return min(1.0, jargon_weight / len(words))


def heuristic_dejargonize(technical_content: str, parameter_list: str, domain_tags: list[str]) -> str:
    """Fallback text generator used for dry-runs/tests and as a no-API safety net."""
    domain_text = ", ".join(domain_tags) if domain_tags else "its domain"
    params_text = parameter_list or "its inputs"
    first_sentence = technical_content.strip().rstrip(".")
    return (
        f"This function works with {params_text} to carry out a task in {domain_text}. "
        f"In plain terms, {first_sentence[:160].lower()}."
    )


def generate_dejargonized(
    *,
    technical_content: str,
    parameter_list: str,
    io_specs: str,
    domain_tags: list[str],
    fqdn: str,
    model: str,
    mode: str,
) -> dict[str, Any]:
    """Generate dejargonized content using OpenAI or the heuristic fallback."""
    prompt = build_prompt(
        fqdn=fqdn,
        technical_content=technical_content,
        parameter_list=parameter_list,
        io_specs=io_specs,
        domain_tags=domain_tags,
    )
    if mode == "heuristic":
        content = heuristic_dejargonize(technical_content, parameter_list, domain_tags)
        return {"content": content, "jargon_score": estimate_jargon_score(content), "model_id": "heuristic:v1"}

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai is required for non-heuristic dejargonized generation") from exc

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.responses.create(
        model=model,
        input=prompt,
        temperature=0.2,
    )
    content = getattr(response, "output_text", "").strip()
    if not content:
        raise RuntimeError(f"No output returned for {fqdn}")
    return {"content": content, "jargon_score": estimate_jargon_score(content), "model_id": f"llm:{model}"}


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Log intended writes without mutating Supabase")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on atoms processed")
    parser.add_argument("--mode", choices=("openai", "heuristic"), default=os.environ.get("SCIONA_DEJARGONIZER_MODE", "openai"))
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model id for non-heuristic generation")
    parser.add_argument("--max-jargon-score", type=float, default=DEFAULT_THRESHOLD, help="Publishability threshold to report against")
    return parser.parse_args()


def main() -> None:
    """Run the dejargonized descriptions backfill."""
    args = parse_args()
    supabase = create_supabase_client()

    technical = (
        supabase.table("atom_descriptions")
        .select("atom_id, content")
        .eq("kind", "technical")
        .eq("language", "en")
        .execute()
    )
    existing_dejarg = (
        supabase.table("atom_descriptions")
        .select("atom_id")
        .eq("kind", "dejargonized")
        .eq("language", "en")
        .execute()
    )
    existing_ids = {row["atom_id"] for row in existing_dejarg.data or []}
    pending = [row for row in technical.data or [] if row["atom_id"] not in existing_ids]
    if args.limit is not None:
        pending = pending[: args.limit]

    stats = {"generated": 0, "below_threshold": 0, "above_threshold": 0, "errors": 0}

    for row in pending:
        atom_id = row["atom_id"]
        technical_content = row["content"]
        params = (
            supabase.table("atom_parameters")
            .select("name, type_desc, kind, position")
            .eq("atom_id", atom_id)
            .order("position")
            .execute()
        )
        io_specs = (
            supabase.table("atom_io_specs")
            .select("name, type_desc, direction, ordinal")
            .eq("atom_id", atom_id)
            .order("ordinal")
            .execute()
        )
        atom = (
            supabase.table("atoms")
            .select("fqdn, domain_tags")
            .eq("atom_id", atom_id)
            .single()
            .execute()
        )
        parameter_list = ", ".join(f"{p['name']}: {p.get('type_desc') or 'Any'}" for p in params.data or [])
        io_spec_list = ", ".join(
            f"{spec['direction']} {spec['name']}: {spec.get('type_desc') or 'Any'}"
            for spec in io_specs.data or []
        )

        try:
            result = generate_dejargonized(
                technical_content=technical_content,
                parameter_list=parameter_list,
                io_specs=io_spec_list,
                domain_tags=list(atom.data.get("domain_tags") or []),
                fqdn=str(atom.data["fqdn"]),
                model=args.model,
                mode=args.mode,
            )
            jargon_score = float(result["jargon_score"])
            if jargon_score < args.max_jargon_score:
                stats["below_threshold"] += 1
            else:
                stats["above_threshold"] += 1

            row_payload = {
                "atom_id": atom_id,
                "kind": "dejargonized",
                "language": "en",
                "content": result["content"],
                "generated_by": result["model_id"],
                "reviewed": False,
                "jargon_score": jargon_score,
            }
            if args.dry_run:
                log.info("DRY RUN would upsert dejargonized description for %s", atom.data["fqdn"])
            else:
                supabase.table("atom_descriptions").upsert(
                    row_payload,
                    on_conflict="atom_id,kind,language",
                ).execute()
            stats["generated"] += 1
        except Exception:
            log.exception("Failed to generate dejargonized description for %s", atom_id)
            stats["errors"] += 1

    log.info("Dejargonized descriptions backfill complete: %s", stats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
