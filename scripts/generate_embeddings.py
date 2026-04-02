"""Generate and upsert atom embeddings using OpenAI text-embedding-3-small."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
BATCH_SIZE = 100


def build_embedding_input(atom: dict[str, Any]) -> str:
    """Assemble the text that will be embedded for a single atom."""
    parts = [
        atom.get("fqdn", "") or "",
        atom.get("technical_description", "") or "",
        atom.get("dejargonized_description", "") or "",
        " ".join(atom.get("domain_tags", []) or []),
    ]
    return "\n".join(part for part in parts if part)


def compute_input_hash(text: str) -> str:
    """Return the short hash stored alongside embeddings for drift detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _service_key() -> str:
    for key in (
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_KEY",
        "SCIONA_SUPABASE_SERVICE_ROLE_KEY",
        "SCIONA_SUPABASE_SERVICE_KEY",
    ):
        value = os.environ.get(key, "")
        if value:
            return value
    raise KeyError("SUPABASE_SERVICE_ROLE_KEY")


def embed_batch(openai_client: Any, texts: list[str]) -> list[list[float]]:
    """Call the embeddings API for a batch of texts."""
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
        dimensions=EMBEDDING_DIMENSIONS,
    )
    return [item.embedding for item in response.data]


def _upsert_embeddings(supabase: Any, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    supabase.table("atom_embeddings").upsert(rows).execute()


def backfill(supabase: Any, openai_client: Any) -> None:
    """Generate embeddings for publishable atoms that need them."""
    atoms = supabase.rpc("get_atoms_needing_embeddings", {}).execute().data or []
    if not atoms:
        logger.info("No atoms need embeddings.")
        return

    logger.info("Generating embeddings for %d atoms", len(atoms))
    for index in range(0, len(atoms), BATCH_SIZE):
        batch = atoms[index : index + BATCH_SIZE]
        texts = [build_embedding_input(atom) for atom in batch]
        embeddings = embed_batch(openai_client, texts)
        timestamp = _utc_now()
        rows = [
            {
                "atom_id": atom["atom_id"],
                "embedding": embedding,
                "model": EMBEDDING_MODEL,
                "dimensions": EMBEDDING_DIMENSIONS,
                "input_text_hash": compute_input_hash(text),
                "updated_at": timestamp,
            }
            for atom, text, embedding in zip(batch, texts, embeddings, strict=True)
        ]
        _upsert_embeddings(supabase, rows)
        logger.info(
            "Upserted %d embeddings (batch %d)",
            len(rows),
            index // BATCH_SIZE + 1,
        )
        time.sleep(0.5)


def _mark_queue_entry(
    supabase: Any,
    queue_id: int,
    *,
    status: str,
    attempts: int | None = None,
    error_message: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> None:
    payload: dict[str, Any] = {"status": status}
    if attempts is not None:
        payload["attempts"] = attempts
    if error_message is not None:
        payload["error_message"] = error_message
    if started_at is not None:
        payload["started_at"] = started_at
    if completed_at is not None:
        payload["completed_at"] = completed_at
    supabase.table("embedding_refresh_queue").update(payload).eq("queue_id", queue_id).execute()


def _fetch_atom_catalog_rows(supabase: Any, atom_ids: list[str]) -> dict[str, dict[str, Any]]:
    rows = (
        supabase.table("catalog_atoms_served")
        .select(
            "atom_id, fqdn, technical_description, "
            "dejargonized_description, domain_tags"
        )
        .in_("atom_id", atom_ids)
        .execute()
        .data
        or []
    )
    return {row["atom_id"]: row for row in rows}


def drain_queue(supabase: Any, openai_client: Any) -> None:
    """Process pending entries from embedding_refresh_queue."""
    pending = (
        supabase.table("embedding_refresh_queue")
        .select("queue_id, atom_id, attempts")
        .eq("status", "pending")
        .order("enqueued_at")
        .limit(BATCH_SIZE)
        .execute()
        .data
        or []
    )
    if not pending:
        logger.info("No pending queue entries.")
        return

    started_at = _utc_now()
    for row in pending:
        _mark_queue_entry(
            supabase,
            int(row["queue_id"]),
            status="processing",
            attempts=int(row.get("attempts", 0)) + 1,
            error_message="",
            started_at=started_at,
        )

    atom_ids = [row["atom_id"] for row in pending]
    atoms = _fetch_atom_catalog_rows(supabase, atom_ids)

    succeeded = 0
    failed = 0
    for row in pending:
        queue_id = int(row["queue_id"])
        atom_id = row["atom_id"]
        atom = atoms.get(atom_id)
        if atom is None:
            failed += 1
            _mark_queue_entry(
                supabase,
                queue_id,
                status="failed",
                error_message="atom not found in catalog index",
                completed_at=_utc_now(),
            )
            continue

        text = build_embedding_input(atom)
        try:
            embedding = embed_batch(openai_client, [text])[0]
            _upsert_embeddings(
                supabase,
                [
                    {
                        "atom_id": atom_id,
                        "embedding": embedding,
                        "model": EMBEDDING_MODEL,
                        "dimensions": EMBEDDING_DIMENSIONS,
                        "input_text_hash": compute_input_hash(text),
                        "updated_at": _utc_now(),
                    }
                ],
            )
            _mark_queue_entry(
                supabase,
                queue_id,
                status="completed",
                completed_at=_utc_now(),
            )
            succeeded += 1
        except Exception as exc:
            failed += 1
            _mark_queue_entry(
                supabase,
                queue_id,
                status="failed",
                error_message=str(exc),
                completed_at=_utc_now(),
            )

    logger.info("Queue drain: %d succeeded, %d failed", succeeded, failed)


def embed_atom(supabase: Any, openai_client: Any, atom_id: str) -> None:
    """Re-embed a single publishable atom."""
    atom = (
        supabase.table("catalog_atoms_served")
        .select(
            "atom_id, fqdn, technical_description, "
            "dejargonized_description, domain_tags"
        )
        .eq("atom_id", atom_id)
        .single()
        .execute()
        .data
    )
    if not atom:
        raise SystemExit(f"Atom {atom_id} not found in the served catalog")

    text = build_embedding_input(atom)
    embedding = embed_batch(openai_client, [text])[0]
    _upsert_embeddings(
        supabase,
        [
            {
                "atom_id": atom_id,
                "embedding": embedding,
                "model": EMBEDDING_MODEL,
                "dimensions": EMBEDDING_DIMENSIONS,
                "input_text_hash": compute_input_hash(text),
                "updated_at": _utc_now(),
            }
        ],
    )
    logger.info("Embedded atom %s", atom_id)


def _create_clients() -> tuple[Any, Any]:
    from openai import OpenAI
    from supabase import create_client

    supabase = create_client(
        os.environ["SUPABASE_URL"],
        _service_key(),
    )
    openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return supabase, openai_client


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backfill", action="store_true")
    group.add_argument("--drain-queue", action="store_true")
    group.add_argument("--atom-id", type=str)
    args = parser.parse_args()

    supabase, openai_client = _create_clients()
    if args.backfill:
        backfill(supabase, openai_client)
        return
    if args.drain_queue:
        drain_queue(supabase, openai_client)
        return
    if args.atom_id:
        embed_atom(supabase, openai_client, args.atom_id)


if __name__ == "__main__":
    main()
