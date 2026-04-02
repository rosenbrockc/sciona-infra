"""Phase 1 organization seeding for Supabase."""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from phase1_common import OrganizationSeed, load_organization_seeds_from_env, require_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrganizationMigrationConfig:
    dry_run: bool


async def get_existing_organization_id(dst: Any, name: str) -> Any | None:
    return await dst.fetchval(
        "SELECT organization_id FROM public.organizations WHERE name = $1",
        name,
    )


async def ensure_organization(dst: Any, seed: OrganizationSeed) -> Any:
    existing = await get_existing_organization_id(dst, seed.name)
    if existing is not None:
        await dst.execute(
            """
            UPDATE public.organizations
               SET entitlement_tier = $2
             WHERE organization_id = $1
            """,
            existing,
            seed.entitlement_tier,
        )
        return existing

    return await dst.fetchval(
        """
        INSERT INTO public.organizations (name, entitlement_tier)
        VALUES ($1, $2)
        RETURNING organization_id
        """,
        seed.name,
        seed.entitlement_tier,
    )


async def ensure_domains(dst: Any, organization_id: Any, domains: tuple[str, ...]) -> None:
    for domain in domains:
        await dst.execute(
            """
            INSERT INTO public.organization_email_domains (organization_id, email_domain)
            VALUES ($1, $2)
            ON CONFLICT (organization_id, email_domain) DO NOTHING
            """,
            organization_id,
            domain,
        )


async def assign_memberships(dst: Any) -> str:
    return await dst.execute(
        """
        INSERT INTO public.organization_memberships (
            organization_id, user_id, membership_source
        )
        SELECT oed.organization_id, u.user_id, 'email_domain'
          FROM public.users u
          JOIN public.organization_email_domains oed
            ON lower(split_part(u.email, '@', 2)) = lower(oed.email_domain)
        ON CONFLICT (organization_id, user_id) DO NOTHING
        """
    )


async def seed_organizations(
    supabase_database_url: str,
    seeds: list[OrganizationSeed],
    config: OrganizationMigrationConfig,
) -> None:
    import asyncpg

    if not seeds:
        log.warning(
            "No organization seed config found; set PHASE1_ORGANIZATIONS_JSON or "
            "PHASE1_ORGANIZATIONS_FILE to run this step"
        )
        return

    dst = await asyncpg.connect(supabase_database_url)
    try:
        for seed in seeds:
            if not seed.name:
                raise ValueError("organization seed entries must include a non-empty name")
            if config.dry_run:
                log.info(
                    "[dry-run] would seed organization %s with domains %s",
                    seed.name,
                    list(seed.email_domains),
                )
                continue
            organization_id = await ensure_organization(dst, seed)
            await ensure_domains(dst, organization_id, seed.email_domains)
            log.info(
                "Seeded organization %s (%s) with %d domains",
                seed.name,
                organization_id,
                len(seed.email_domains),
            )

        if config.dry_run:
            log.info("[dry-run] would auto-assign organization memberships")
        else:
            result = await assign_memberships(dst)
            log.info("Auto-assigned organization memberships: %s", result)
    finally:
        await dst.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    await seed_organizations(
        require_env("SUPABASE_DATABASE_URL"),
        load_organization_seeds_from_env(),
        OrganizationMigrationConfig(dry_run=args.dry_run),
    )


if __name__ == "__main__":
    asyncio.run(main())
