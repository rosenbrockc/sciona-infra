"""Badge evaluation engine — event-driven + nightly sweep."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Maps badge_id -> SQL function name
BADGE_FUNCTIONS: dict[str, str] = {
    "prolific": "badge_prolific_progress",
    "keystone": "badge_keystone_progress",
    "sovereign": "badge_sovereign_progress",
    "laureate": "badge_laureate_progress",
    "deadend": "badge_deadend_progress",
    "titan": "badge_titan_progress",
    "synthesizer": "badge_synthesizer_progress",
    "polymath": "badge_polymath_progress",
    "anvil": "badge_anvil_progress",
    "chain_reaction": "badge_chain_reaction_progress",
    "rainmaker": "badge_rainmaker_progress",
    "lab_director": "badge_lab_director_progress",
    "graverobber": "badge_graverobber_progress",
    "frankenstein": "badge_frankenstein_progress",
}

# Maps event hints to the subset of badges to check
EVENT_BADGE_MAP: dict[str, list[str]] = {
    "atom_published": ["prolific", "keystone"],
    "bounty_won": ["deadend", "titan", "synthesizer", "polymath", "graverobber", "frankenstein"],
    "payout_settled": ["sovereign"],
    "bibtex_exported": ["laureate"],
    "fuzz_completed": ["anvil"],
    "referral_value": ["chain_reaction", "rainmaker", "lab_director"],
    "nightly": list(BADGE_FUNCTIONS.keys()),
}

TIER_ORDER = ["node", "edge", "lattice", "single"]


async def evaluate_badges_for_user(
    supabase: Any,
    user_id: str,
    event_hint: str = "nightly",
) -> list[str]:
    """Evaluate badge progress and award milestones.

    Returns list of newly awarded milestone_ids.
    """
    badge_ids = EVENT_BADGE_MAP.get(event_hint, EVENT_BADGE_MAP["nightly"])
    newly_awarded: list[str] = []

    for badge_id in badge_ids:
        func_name = BADGE_FUNCTIONS.get(badge_id)
        if not func_name:
            continue

        try:
            result = await supabase.rpc(func_name, {"p_user_id": user_id}).execute()
            current_value = float(result.data) if result.data is not None else 0.0
        except Exception:
            logger.debug("Badge function %s failed for user %s", func_name, user_id, exc_info=True)
            continue

        # Upsert badge_progress
        try:
            await supabase.table("badge_progress").upsert(
                {
                    "user_id": user_id,
                    "badge_id": badge_id,
                    "current_value": current_value,
                    "updated_at": "now()",
                },
                on_conflict="user_id,badge_id",
            ).execute()
        except Exception:
            logger.debug("Failed to upsert badge_progress for %s/%s", user_id, badge_id, exc_info=True)

        # Check milestones
        milestones_result = await (
            supabase.table("badge_milestones")
            .select("milestone_id, tier, threshold_value")
            .eq("badge_id", badge_id)
            .execute()
        )
        for ms in milestones_result.data or []:
            if current_value >= float(ms["threshold_value"]):
                try:
                    insert_result = await (
                        supabase.table("user_badges")
                        .upsert(
                            {
                                "user_id": user_id,
                                "milestone_id": ms["milestone_id"],
                                "progress_value": current_value,
                            },
                            on_conflict="user_id,milestone_id",
                            ignore_duplicates=True,
                        )
                        .execute()
                    )
                    if insert_result.data:
                        newly_awarded.append(ms["milestone_id"])
                except Exception:
                    logger.debug(
                        "Failed to award milestone %s to %s",
                        ms["milestone_id"],
                        user_id,
                        exc_info=True,
                    )

        # Update highest_awarded_tier
        if newly_awarded:
            earned_result = await (
                supabase.table("user_badges")
                .select("milestone_id")
                .eq("user_id", user_id)
                .execute()
            )
            earned_ids = {r["milestone_id"] for r in (earned_result.data or [])}
            highest = None
            for ms in milestones_result.data or []:
                if ms["milestone_id"] in earned_ids:
                    tier = ms["tier"]
                    if highest is None or TIER_ORDER.index(tier) > TIER_ORDER.index(highest):
                        highest = tier
            if highest:
                try:
                    await supabase.table("badge_progress").upsert(
                        {
                            "user_id": user_id,
                            "badge_id": badge_id,
                            "current_value": current_value,
                            "highest_awarded_tier": highest,
                            "updated_at": "now()",
                        },
                        on_conflict="user_id,badge_id",
                    ).execute()
                except Exception:
                    pass

    return newly_awarded


async def check_referral_value(supabase: Any, user_id: str, event: str) -> None:
    """If user is a referee, mark their value creation and evaluate referrer badges."""
    try:
        ref_result = await (
            supabase.table("referrals")
            .select("id, referrer_id, value_created_at")
            .eq("referee_id", user_id)
            .maybe_single()
            .execute()
        )
        referral = ref_result.data if hasattr(ref_result, "data") else None
        if isinstance(referral, list):
            referral = referral[0] if referral else None
    except Exception:
        return

    if not referral:
        return

    if referral.get("value_created_at") is None:
        try:
            await (
                supabase.table("referrals")
                .update({
                    "first_value_event": event,
                    "value_created_at": "now()",
                })
                .eq("id", referral["id"])
                .execute()
            )
        except Exception:
            logger.debug("Failed to update referral value for %s", user_id, exc_info=True)

    # Evaluate referrer's evangelist badges
    referrer_id = str(referral["referrer_id"])
    await evaluate_badges_for_user(supabase, referrer_id, "referral_value")
