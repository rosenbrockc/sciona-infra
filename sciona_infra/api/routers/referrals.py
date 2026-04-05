"""Referral code and tracking API endpoints."""

from __future__ import annotations

import secrets
import string
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sciona_infra.api import deps as api_deps
from sciona_infra.api.models import ReferralCodeResponse, ReferralResponse

router = APIRouter()


def _rows(data: Any) -> list[dict]:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    return [data]


def _first_row(data: Any) -> dict | None:
    if data is None:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


def _generate_code() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


class AcceptReferralRequest(BaseModel):
    code: str


@router.post("/referrals/code")
async def generate_referral_code(
    user: api_deps.UserProfile = Depends(api_deps.require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> ReferralCodeResponse:
    """Generate a new 8-char invite code."""
    code = _generate_code()
    user_id = str(user.user_id)

    result = await (
        supabase.table("referral_codes")
        .insert({
            "code": code,
            "referrer_id": user_id,
        })
        .execute()
    )
    row = _first_row(result.data)
    if not row:
        raise HTTPException(500, "Failed to create referral code")

    return ReferralCodeResponse(**row)


@router.get("/referrals/codes")
async def list_my_codes(
    user: api_deps.UserProfile = Depends(api_deps.require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> list[ReferralCodeResponse]:
    """List caller's referral codes with use counts."""
    result = await (
        supabase.table("referral_codes")
        .select("*")
        .eq("referrer_id", str(user.user_id))
        .order("created_at", desc=True)
        .execute()
    )
    return [ReferralCodeResponse(**r) for r in _rows(result.data)]


@router.post("/referrals/accept")
async def accept_referral(
    body: AcceptReferralRequest,
    user: api_deps.UserProfile = Depends(api_deps.require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Accept a referral code (called post-signup)."""
    user_id = str(user.user_id)

    # Check not already referred
    existing = await (
        supabase.table("referrals")
        .select("id")
        .eq("referee_id", user_id)
        .maybe_single()
        .execute()
    )
    if _first_row(existing.data):
        return {"status": "already_referred"}

    # Validate code
    code_result = await (
        supabase.table("referral_codes")
        .select("*")
        .eq("code", body.code)
        .maybe_single()
        .execute()
    )
    code_row = _first_row(code_result.data)
    if not code_row:
        raise HTTPException(404, "Invalid referral code")

    if code_row["referrer_id"] == user_id:
        raise HTTPException(400, "Cannot refer yourself")

    if code_row["use_count"] >= code_row["max_uses"]:
        raise HTTPException(410, "Referral code exhausted")

    # Create referral
    await (
        supabase.table("referrals")
        .insert({
            "referrer_id": code_row["referrer_id"],
            "referee_id": user_id,
            "code": body.code,
        })
        .execute()
    )

    # Increment use count
    await (
        supabase.table("referral_codes")
        .update({"use_count": code_row["use_count"] + 1})
        .eq("code", body.code)
        .execute()
    )

    return {"status": "accepted"}


@router.get("/referrals/mine")
async def list_my_referrals(
    user: api_deps.UserProfile = Depends(api_deps.require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> list[ReferralResponse]:
    """List caller's referrals with value status."""
    result = await (
        supabase.table("referrals")
        .select("*")
        .eq("referrer_id", str(user.user_id))
        .order("created_at", desc=True)
        .execute()
    )
    return [ReferralResponse(**r) for r in _rows(result.data)]
