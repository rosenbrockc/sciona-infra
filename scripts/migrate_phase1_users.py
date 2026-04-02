"""Phase 1 user migration: source PostgreSQL -> Supabase Auth + public.users."""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from phase1_common import (
    DatabaseSettings,
    SupabaseSettings,
    chunked,
    env_float,
    env_int,
    github_noreply_email,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserMigrationConfig:
    batch_size: int
    max_retries: int
    retry_delay_s: float
    noreply_domain: str
    dry_run: bool

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "UserMigrationConfig":
        return cls(
            batch_size=args.batch_size,
            max_retries=args.max_retries,
            retry_delay_s=args.retry_delay_s,
            noreply_domain=args.noreply_domain,
            dry_run=args.dry_run,
        )


def build_auth_user_payload(
    user: dict[str, Any],
    *,
    noreply_domain: str,
) -> dict[str, Any]:
    email = user.get("email") or github_noreply_email(
        str(user.get("github_login", "")),
        domain=noreply_domain,
    )
    return {
        "id": str(user["user_id"]),
        "email": email,
        "email_confirm": True,
        "user_metadata": {
            "provider_id": str(user.get("github_id", "")),
            "user_name": user.get("github_login", ""),
            "full_name": user.get("display_name", ""),
            "avatar_url": user.get("avatar_url", ""),
        },
    }


def auth_request_headers(service_key: str) -> dict[str, str]:
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }


def is_idempotent_auth_response(status_code: int, response_text: str) -> bool:
    if status_code not in {409, 422}:
        return False
    lowered = response_text.lower()
    phrases = (
        "already been registered",
        "already exists",
        "duplicate key",
        "user already exists",
    )
    return any(phrase in lowered for phrase in phrases)


async def fetch_source_users(src: Any) -> list[dict[str, Any]]:
    rows = await src.fetch("SELECT * FROM users ORDER BY created_at")
    return [dict(row) for row in rows]


async def create_auth_user(
    client: Any,
    supabase: SupabaseSettings,
    user: dict[str, Any],
    config: UserMigrationConfig,
) -> None:
    payload = build_auth_user_payload(user, noreply_domain=config.noreply_domain)
    for attempt in range(1, config.max_retries + 1):
        response = await client.post(
            f"{supabase.supabase_url.rstrip('/')}/auth/v1/admin/users",
            json=payload,
            headers=auth_request_headers(supabase.service_key),
        )
        if response.status_code in {200, 201}:
            return
        if is_idempotent_auth_response(response.status_code, response.text):
            log.info(
                "auth.users row already exists for %s; skipping",
                user.get("github_login", "<unknown>"),
            )
            return
        if response.status_code == 429:
            retry_after = float(
                response.headers.get("retry-after", config.retry_delay_s * attempt)
            )
            log.warning(
                "Rate limited creating %s; retrying in %.1fs",
                user.get("github_login", "<unknown>"),
                retry_after,
            )
            await asyncio.sleep(retry_after)
            continue
        if attempt < config.max_retries:
            log.warning(
                "Auth create failed for %s (%s %s), retrying",
                user.get("github_login", "<unknown>"),
                response.status_code,
                response.text[:200],
            )
            await asyncio.sleep(config.retry_delay_s * attempt)
            continue
        raise RuntimeError(
            f"Failed to create auth user {user.get('github_login', '<unknown>')}: "
            f"{response.status_code} {response.text[:500]}"
        )


async def insert_public_users_batch(dst: Any, batch: list[dict[str, Any]]) -> None:
    await dst.executemany(
        """
        INSERT INTO public.users (
            user_id, github_id, github_login, display_name, avatar_url,
            email, identity_tier, stripe_account_id, reputation_score,
            is_blacklisted, effective_tier, created_at, updated_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (user_id) DO UPDATE
        SET github_id = EXCLUDED.github_id,
            github_login = EXCLUDED.github_login,
            display_name = EXCLUDED.display_name,
            avatar_url = EXCLUDED.avatar_url,
            email = EXCLUDED.email,
            identity_tier = EXCLUDED.identity_tier,
            stripe_account_id = EXCLUDED.stripe_account_id,
            reputation_score = EXCLUDED.reputation_score,
            is_blacklisted = EXCLUDED.is_blacklisted,
            effective_tier = EXCLUDED.effective_tier,
            created_at = EXCLUDED.created_at,
            updated_at = EXCLUDED.updated_at
        """,
        [
            (
                row["user_id"],
                row["github_id"],
                row["github_login"],
                row["display_name"],
                row["avatar_url"],
                row["email"],
                row["identity_tier"],
                row.get("stripe_account_id"),
                row["reputation_score"],
                row["is_blacklisted"],
                "general",
                row["created_at"],
                row["updated_at"],
            )
            for row in batch
        ],
    )


async def set_profile_trigger(dst: Any, *, enabled: bool) -> None:
    state = "ENABLE" if enabled else "DISABLE"
    await dst.execute(f"ALTER TABLE auth.users {state} TRIGGER on_auth_user_created")


async def migrate_users(
    database: DatabaseSettings,
    supabase: SupabaseSettings,
    config: UserMigrationConfig,
) -> None:
    import asyncpg
    import httpx

    src = await asyncpg.connect(database.source_database_url)
    dst = await asyncpg.connect(database.supabase_database_url)
    trigger_disabled = False

    try:
        users = await fetch_source_users(src)
        log.info("Fetched %d users from source", len(users))
        if not users:
            return

        if not config.dry_run:
            await set_profile_trigger(dst, enabled=False)
            trigger_disabled = True
            log.info("Disabled on_auth_user_created trigger")

        async with httpx.AsyncClient(timeout=30.0) as client:
            for batch_number, batch in enumerate(chunked(users, config.batch_size), start=1):
                for user in batch:
                    if config.dry_run:
                        build_auth_user_payload(user, noreply_domain=config.noreply_domain)
                    else:
                        await create_auth_user(client, supabase, user, config)
                log.info("Processed auth.users batch %d (%d rows)", batch_number, len(batch))

        for batch_number, batch in enumerate(chunked(users, config.batch_size), start=1):
            if not config.dry_run:
                await insert_public_users_batch(dst, list(batch))
            log.info(
                "Processed public.users batch %d (%d rows)%s",
                batch_number,
                len(batch),
                " [dry-run]" if config.dry_run else "",
            )
    finally:
        if trigger_disabled:
            await set_profile_trigger(dst, enabled=True)
            log.info("Re-enabled on_auth_user_created trigger")
        await src.close()
        await dst.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=env_int("PHASE1_USER_BATCH_SIZE", 500))
    parser.add_argument("--max-retries", type=int, default=env_int("PHASE1_MAX_RETRIES", 3))
    parser.add_argument(
        "--retry-delay-s",
        type=float,
        default=env_float("PHASE1_RETRY_DELAY_S", 2.0),
    )
    parser.add_argument(
        "--noreply-domain",
        default="github-noreply.example",
        help="Fallback email domain for users without a stored email",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    await migrate_users(
        DatabaseSettings.from_env(),
        SupabaseSettings.from_env(),
        UserMigrationConfig.from_args(args),
    )


if __name__ == "__main__":
    asyncio.run(main())
