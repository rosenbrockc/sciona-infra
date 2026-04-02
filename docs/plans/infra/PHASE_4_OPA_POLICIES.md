# Phase 4 — OPA Policy Enforcement (Detailed Implementation Plan)

**Parent plan:** `docs/plans/infra/README.md` Section "Phase 4"
**Depends on:** Phase 1 (OPA container running), Phase 2 (OTel for tracing)
**Independent of:** Phase 3 (Temporal) — if workflows exist, add policy checks there too

---

## Overview

Extract inline authorization checks from `sciona/api/routers/bounty.py` and
business-rule validation from `sciona/clearinghouse/settlement.py` into
declarative Rego policies evaluated by OPA over its REST API.

### Current inline checks being replaced

| File | Check | Replacement |
|---|---|---|
| `bounty.py:98` | `principal_id != user_id` → 403 on fund | `bounty.allow_fund` |
| `bounty.py:131-143` | status == "open" on submit | `bounty.allow_submit` |
| `bounty.py:185` | `principal_id != user_id` → 403 on cancel | `bounty.allow_cancel` |
| `bounty.py:63-84` | implicit: any authed user can create | `bounty.allow_create` |
| `settlement.py:175-181` | `verify_payout_conservation` | `payout.valid_plan` |
| `bounty_state.py:111` | `compute_payout_split` ratios | `payout.valid_split_percentages` |

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPA_URL` | `http://localhost:8181` | OPA REST API base URL |
| `OPA_POLICY_MODE` | `permissive` | `permissive` = fail-open (dev), `strict` = fail-closed (prod) |
| `OPA_TIMEOUT_S` | `2` | HTTP timeout for OPA calls |

---

## Step 1 — Policy files under `docker/opa/policies/`

### Step 1.1 — Create `docker/opa/policies/data.json`

**Action:** Create new file.

```json
{
  "tier_limits": {
    "standard": {
      "max_escrow": 10000.00,
      "verification_budget": 5,
      "max_submissions": 50
    },
    "heavy": {
      "max_escrow": 50000.00,
      "verification_budget": 10,
      "max_submissions": 100
    },
    "gpu": {
      "max_escrow": 100000.00,
      "verification_budget": 15,
      "max_submissions": 100
    }
  },
  "fee_schedule": {
    "platform_pct": 5,
    "architect_pct": 65,
    "originator_pct": 30,
    "cancellation_no_submissions_pct": 10,
    "cancellation_with_submissions_pct": 25
  },
  "identity_tiers": {
    "contributor": {
      "can_create_bounty": false,
      "can_submit": true,
      "can_fund": false
    },
    "payee": {
      "can_create_bounty": true,
      "can_submit": true,
      "can_fund": true
    }
  },
  "cancellable_statuses": ["draft", "open", "submitted"]
}
```

### Step 1.2 — Create `docker/opa/policies/bounty.rego`

**Action:** Create new file.

```rego
package bounty

import data.tier_limits
import data.fee_schedule
import data.identity_tiers
import data.cancellable_statuses
import rego.v1

# -----------------------------------------------------------------------
# allow_create — who can create a bounty
# -----------------------------------------------------------------------
default allow_create := false

allow_create if {
    not input.user.is_blacklisted
    identity_tiers[input.user.identity_tier].can_create_bounty
}

allow_create if {
    not input.user.is_blacklisted
    identity_tiers[input.user.identity_tier].can_fund
}

# Deny reason annotations for debugging
deny_create contains msg if {
    input.user.is_blacklisted
    msg := "user is blacklisted"
}

deny_create contains msg if {
    not identity_tiers[input.user.identity_tier].can_create_bounty
    msg := sprintf("identity tier '%s' cannot create bounties", [input.user.identity_tier])
}

# -----------------------------------------------------------------------
# allow_fund — only the bounty creator can fund, and only from draft
# -----------------------------------------------------------------------
default allow_fund := false

allow_fund if {
    not input.user.is_blacklisted
    input.user.user_id == input.bounty.principal_id
    input.bounty.status == "draft"
    identity_tiers[input.user.identity_tier].can_fund
}

deny_fund contains msg if {
    input.user.user_id != input.bounty.principal_id
    msg := "only the bounty creator can fund it"
}

deny_fund contains msg if {
    input.bounty.status != "draft"
    msg := sprintf("cannot fund bounty in '%s' state", [input.bounty.status])
}

deny_fund contains msg if {
    not identity_tiers[input.user.identity_tier].can_fund
    msg := sprintf("identity tier '%s' cannot fund bounties", [input.user.identity_tier])
}

# -----------------------------------------------------------------------
# allow_submit — non-creator, non-blacklisted, bounty is open/submitted
# -----------------------------------------------------------------------
default allow_submit := false

allow_submit if {
    not input.user.is_blacklisted
    input.user.user_id != input.bounty.principal_id
    input.bounty.status in {"open", "submitted"}
}

deny_submit contains msg if {
    input.user.is_blacklisted
    msg := "user is blacklisted"
}

deny_submit contains msg if {
    input.user.user_id == input.bounty.principal_id
    msg := "bounty creator cannot submit to own bounty"
}

deny_submit contains msg if {
    not input.bounty.status in {"open", "submitted"}
    msg := sprintf("cannot submit to bounty in '%s' state", [input.bounty.status])
}

# -----------------------------------------------------------------------
# allow_cancel — only creator, only cancellable statuses
# -----------------------------------------------------------------------
default allow_cancel := false

allow_cancel if {
    input.user.user_id == input.bounty.principal_id
    input.bounty.status in {"draft", "open", "submitted"}
}

deny_cancel contains msg if {
    input.user.user_id != input.bounty.principal_id
    msg := "only the bounty creator can cancel it"
}

deny_cancel contains msg if {
    not input.bounty.status in {"draft", "open", "submitted"}
    msg := sprintf("cannot cancel bounty in '%s' state", [input.bounty.status])
}

# -----------------------------------------------------------------------
# allow_update_target — only creator, bounty is open or submitted
# -----------------------------------------------------------------------
default allow_update_target := false

allow_update_target if {
    input.user.user_id == input.bounty.principal_id
    input.bounty.status in {"open", "submitted"}
}

deny_update_target contains msg if {
    input.user.user_id != input.bounty.principal_id
    msg := "only the bounty creator can update the target"
}

deny_update_target contains msg if {
    not input.bounty.status in {"open", "submitted"}
    msg := sprintf("can only update target for open/submitted bounties, got '%s'", [input.bounty.status])
}

# -----------------------------------------------------------------------
# valid_escrow — escrow is within tier limits
# -----------------------------------------------------------------------
default valid_escrow := false

valid_escrow if {
    limit := tier_limits[input.bounty.tier]
    input.bounty.escrow_amount <= limit.max_escrow
}
```

### Step 1.3 — Create `docker/opa/policies/submission.rego`

**Action:** Create new file.

```rego
package submission

import data.identity_tiers
import rego.v1

# -----------------------------------------------------------------------
# allow_submit — submission-level auth (distinct from bounty.allow_submit
# which checks bounty-level rules; this checks submission-specific rules)
# -----------------------------------------------------------------------
default allow := false

allow if {
    not input.user.is_blacklisted
    input.user.user_id != input.bounty.principal_id
    input.bounty.status in {"open", "submitted"}
    valid_receipt
}

# -----------------------------------------------------------------------
# valid_receipt — submission must include a non-empty receipt
# -----------------------------------------------------------------------
default valid_receipt := false

valid_receipt if {
    count(input.submission.receipt_json) > 0
}

valid_receipt if {
    input.submission.receipt_s3 != ""
}

deny contains msg if {
    input.user.is_blacklisted
    msg := "user is blacklisted"
}

deny contains msg if {
    input.user.user_id == input.bounty.principal_id
    msg := "bounty creator cannot submit to own bounty"
}

deny contains msg if {
    not input.bounty.status in {"open", "submitted"}
    msg := sprintf("bounty is in '%s' state, not accepting submissions", [input.bounty.status])
}

deny contains msg if {
    not valid_receipt
    msg := "submission must include a receipt (receipt_json or receipt_s3)"
}
```

### Step 1.4 — Create `docker/opa/policies/payout.rego`

**Action:** Create new file.

```rego
package payout

import data.fee_schedule
import rego.v1

# -----------------------------------------------------------------------
# valid_plan — the sum of all payouts must equal the escrow amount
# (conservation invariant)
# -----------------------------------------------------------------------
default valid_plan := false

valid_plan if {
    total := sum([p.amount | some p in input.plan.recipients])
    total == input.plan.escrow_amount
}

# -----------------------------------------------------------------------
# valid_split_percentages — platform/architect/originator split matches
# the fee schedule in data.json
# -----------------------------------------------------------------------
default valid_split_percentages := false

valid_split_percentages if {
    platform_total := sum([p.amount | some p in input.plan.recipients; p.role == "platform"])
    architect_total := sum([p.amount | some p in input.plan.recipients; p.role == "architect"])
    originator_total := sum([p.amount | some p in input.plan.recipients; p.role == "originator"])

    escrow := input.plan.escrow_amount

    # Allow 0.01 tolerance for rounding (residual goes to platform)
    abs(platform_total - escrow * fee_schedule.platform_pct / 100) < 0.02
    abs(architect_total - escrow * fee_schedule.architect_pct / 100) < 0.02
    abs(originator_total - escrow * fee_schedule.originator_pct / 100) < 0.02
}

# -----------------------------------------------------------------------
# valid_cancellation_fee — fee matches expected percentage
# -----------------------------------------------------------------------
default valid_cancellation_fee := false

valid_cancellation_fee if {
    not input.has_submissions
    expected := input.escrow_amount * fee_schedule.cancellation_no_submissions_pct / 100
    input.cancellation_fee == expected
}

valid_cancellation_fee if {
    input.has_submissions
    expected := input.escrow_amount * fee_schedule.cancellation_with_submissions_pct / 100
    input.cancellation_fee == expected
}

# -----------------------------------------------------------------------
# all_recipients_have_accounts — every non-platform recipient with
# amount > 0 must have a stripe_account_id
# -----------------------------------------------------------------------
default all_recipients_have_accounts := false

all_recipients_have_accounts if {
    missing := [p |
        some p in input.plan.recipients
        p.role != "platform"
        p.amount > 0
        p.stripe_account_id == ""
    ]
    count(missing) == 0
}

deny contains msg if {
    not valid_plan
    total := sum([p.amount | some p in input.plan.recipients])
    msg := sprintf("payout total %v != escrow %v (conservation violated)", [total, input.plan.escrow_amount])
}
```

### Step 1.5 — Delete the Phase 1 stub

**Action:** Delete `docker/opa/policies/main.rego` if it exists (it was a Phase 1 stub placeholder).

---

## Step 2 — Create `sciona/api/policy.py` (async OPA client)

**Action:** Create new file.

```python
"""Async OPA policy evaluation client with OpenTelemetry tracing."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

_OPA_URL: str = os.getenv("OPA_URL", "http://localhost:8181")
_OPA_TIMEOUT: float = float(os.getenv("OPA_TIMEOUT_S", "2"))
_OPA_MODE: str = os.getenv("OPA_POLICY_MODE", "permissive")  # "permissive" | "strict"


class PolicyDenied(Exception):
    """Raised when OPA denies a request."""

    def __init__(self, package: str, rule: str, reasons: list[str] | None = None):
        self.package = package
        self.rule = rule
        self.reasons = reasons or []
        detail = "; ".join(self.reasons) if self.reasons else f"{package}.{rule} denied"
        super().__init__(detail)


async def evaluate_policy(
    package: str,
    rule: str,
    input_data: dict[str, Any],
) -> bool:
    """Evaluate a single OPA rule and return True if allowed.

    Parameters
    ----------
    package
        The Rego package name (e.g. ``"bounty"``).
    rule
        The rule to evaluate (e.g. ``"allow_fund"``).
    input_data
        The input document to pass to OPA.

    Returns
    -------
    bool
        ``True`` if the rule evaluates to ``true`` in OPA.

    Notes
    -----
    - In ``permissive`` mode (default, for development), OPA failures (network
      errors, timeouts) are logged and the request is **allowed** to proceed.
    - In ``strict`` mode (production), OPA failures cause the request to be
      **denied**.
    """
    with tracer.start_as_current_span(
        f"opa.evaluate",
        attributes={
            "opa.package": package,
            "opa.rule": rule,
            "opa.mode": _OPA_MODE,
        },
    ) as span:
        try:
            async with httpx.AsyncClient(timeout=_OPA_TIMEOUT) as client:
                url = f"{_OPA_URL}/v1/data/{package}/{rule}"
                resp = await client.post(url, json={"input": input_data})
                resp.raise_for_status()
                result = resp.json().get("result", False)
                span.set_attribute("opa.result", result)
                return bool(result)
        except httpx.TimeoutException:
            logger.warning("OPA timeout evaluating %s.%s", package, rule)
            span.set_attribute("opa.error", "timeout")
            return _fallback_result(package, rule)
        except httpx.ConnectError:
            logger.warning("OPA unreachable evaluating %s.%s", package, rule)
            span.set_attribute("opa.error", "connect_error")
            return _fallback_result(package, rule)
        except Exception:
            logger.exception("OPA error evaluating %s.%s", package, rule)
            span.set_attribute("opa.error", "unexpected")
            return _fallback_result(package, rule)


async def evaluate_policy_with_reasons(
    package: str,
    rule: str,
    deny_rule: str,
    input_data: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Evaluate a policy rule and also fetch denial reasons.

    Returns (allowed, reasons) where reasons is populated when denied.
    """
    allowed = await evaluate_policy(package, rule, input_data)
    if allowed:
        return True, []

    # Fetch denial reasons for better error messages
    try:
        async with httpx.AsyncClient(timeout=_OPA_TIMEOUT) as client:
            url = f"{_OPA_URL}/v1/data/{package}/{deny_rule}"
            resp = await client.post(url, json={"input": input_data})
            resp.raise_for_status()
            reasons = resp.json().get("result", [])
            if isinstance(reasons, list):
                return False, reasons
            return False, [str(reasons)]
    except Exception:
        logger.exception("Failed to fetch OPA denial reasons for %s.%s", package, deny_rule)
        return False, []


async def require_policy(
    package: str,
    rule: str,
    input_data: dict[str, Any],
    *,
    deny_rule: str | None = None,
) -> None:
    """Evaluate a policy and raise PolicyDenied if denied.

    Parameters
    ----------
    package
        The Rego package name.
    rule
        The allow rule (e.g. ``"allow_fund"``).
    input_data
        The input document to pass to OPA.
    deny_rule
        Optional deny rule to fetch human-readable reasons (e.g. ``"deny_fund"``).
        Defaults to ``"deny_" + rule.removeprefix("allow_")``.
    """
    if deny_rule is None:
        deny_rule = "deny_" + rule.removeprefix("allow_")

    allowed, reasons = await evaluate_policy_with_reasons(
        package, rule, deny_rule, input_data
    )
    if not allowed:
        raise PolicyDenied(package, rule, reasons)


def _fallback_result(package: str, rule: str) -> bool:
    """Return the fallback result when OPA is unavailable.

    - ``permissive`` mode: return True (allow) — suitable for development.
    - ``strict`` mode: return False (deny) — suitable for production.
    """
    if _OPA_MODE == "strict":
        logger.error(
            "OPA unavailable in strict mode — denying %s.%s", package, rule
        )
        return False
    logger.info(
        "OPA unavailable in permissive mode — allowing %s.%s", package, rule
    )
    return True
```

---

## Step 3 — Modify `sciona/api/routers/bounty.py`

**Action:** Edit existing file. Add OPA policy checks before each state transition.

### Step 3.1 — Add imports

At the top of the file, after the existing imports, add:

```python
from sciona.api.policy import PolicyDenied, require_policy
```

### Step 3.2 — Add helper to build OPA input

After the `_json_obj` function (line 56), add:

```python
def _opa_input(user: UserRow, bounty: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the OPA input document from the user and optional bounty row."""
    doc: dict[str, Any] = {
        "user": {
            "user_id": str(user.user_id),
            "identity_tier": user.identity_tier,
            "effective_tier": user.effective_tier,
            "is_blacklisted": user.is_blacklisted,
            "reputation_score": user.reputation_score,
        },
    }
    if bounty is not None:
        doc["bounty"] = {
            "bounty_id": str(bounty.get("bounty_id", "")),
            "principal_id": str(bounty.get("principal_id", "")),
            "status": bounty.get("status", ""),
            "escrow_amount": float(bounty.get("escrow_amount", 0)),
            "tier": bounty.get("tier", "standard"),
        }
    return doc
```

### Step 3.3 — Add error handler for PolicyDenied

After the `_opa_input` helper, add:

```python
from fastapi import Request
from fastapi.responses import JSONResponse

@router.exception_handler(PolicyDenied)  # Note: register on app, not router — see step 3.7
```

Actually, we should register the exception handler at the app level. Instead, wrap the `require_policy` calls in try/except within each endpoint. This is cleaner for a router-scoped change:

```python
async def _enforce(package: str, rule: str, input_data: dict[str, Any]) -> None:
    """Enforce OPA policy, raising HTTPException(403) on denial."""
    try:
        await require_policy(package, rule, input_data)
    except PolicyDenied as exc:
        raise HTTPException(403, f"Policy denied: {exc}")
```

### Step 3.4 — Edit `create_bounty`

**Find** the existing `create_bounty` function body. Add the policy check as the first operation:

```python
@router.post("")
async def create_bounty(
    body: BountyCreateRequest,
    user: UserRow = Depends(require_auth),
    supabase=Depends(api_deps.get_supabase),
) -> BountyResponse:
    """Create a draft bounty."""
    # --- OPA policy check ---
    await _enforce("bounty", "allow_create", _opa_input(user))
    # --- end policy check ---
    user_id = str(user.user_id)
    # ... rest unchanged ...
```

Exact edit — insert after `"""Create a draft bounty."""` and before `user_id = str(user.user_id)`:

```
    await _enforce("bounty", "allow_create", _opa_input(user))
```

### Step 3.5 — Edit `fund_bounty`

**Replace** the inline ownership check with OPA policy. The existing code:

```python
    if str(row["principal_id"]) != str(user.user_id):
        raise HTTPException(403, "Only the bounty creator can fund it")
```

**Replace with:**

```python
    await _enforce("bounty", "allow_fund", _opa_input(user, row))
```

The OPA policy `bounty.allow_fund` checks both ownership and status, so the
`validate_transition` call on the next line provides defense-in-depth (it stays).

### Step 3.6 — Edit `submit_to_bounty`

After fetching the bounty row and checking it exists, **add** before the status check:

```python
    await _enforce("bounty", "allow_submit", _opa_input(user, row))
```

This replaces the implicit allowance. The inline status checks remain for
state machine correctness (belt-and-suspenders).

### Step 3.7 — Edit `cancel_bounty`

**Replace** the inline ownership check:

```python
    if str(row["principal_id"]) != str(user.user_id):
        raise HTTPException(403, "Only the bounty creator can cancel it")
```

**With:**

```python
    await _enforce("bounty", "allow_cancel", _opa_input(user, row))
```

### Step 3.8 — Edit `update_target`

**Replace** the inline ownership and status checks:

```python
    if str(row["principal_id"]) != str(user.user_id):
        raise HTTPException(403, "Only the bounty creator can update the target")
    if row["status"] not in ("open", "submitted"):
        raise HTTPException(409, "Can only update target for open/submitted bounties")
```

**With:**

```python
    await _enforce("bounty", "allow_update_target", _opa_input(user, row))
```

### Step 3.9 — Full diff of `bounty.py`

For the implementing agent, here is the complete set of edits as find/replace pairs.

**Edit 1:** Add imports (after line 11, the last import block):

Old:
```python
from sciona.api.models import (
    BountyCancelResponse,
    BountyCreateRequest,
    BountyFundResponse,
    BountyResponse,
    BountySummaryResponse,
    PaginatedResponse,
    SubmissionRequest,
    SubmissionResponse,
    UpdateTargetRequest,
)
```

New:
```python
from sciona.api.models import (
    BountyCancelResponse,
    BountyCreateRequest,
    BountyFundResponse,
    BountyResponse,
    BountySummaryResponse,
    PaginatedResponse,
    SubmissionRequest,
    SubmissionResponse,
    UpdateTargetRequest,
)
from sciona.api.policy import PolicyDenied, require_policy
```

**Edit 2:** Add `_opa_input` and `_enforce` helpers (after `_json_obj` function, before `@router.post("")`):

Old:
```python
    return value


@router.post("")
```

New:
```python
    return value


def _opa_input(user: UserRow, bounty: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the OPA input document from the user and optional bounty row."""
    doc: dict[str, Any] = {
        "user": {
            "user_id": str(user.user_id),
            "identity_tier": user.identity_tier,
            "effective_tier": user.effective_tier,
            "is_blacklisted": user.is_blacklisted,
            "reputation_score": user.reputation_score,
        },
    }
    if bounty is not None:
        doc["bounty"] = {
            "bounty_id": str(bounty.get("bounty_id", "")),
            "principal_id": str(bounty.get("principal_id", "")),
            "status": bounty.get("status", ""),
            "escrow_amount": float(bounty.get("escrow_amount", 0)),
            "tier": bounty.get("tier", "standard"),
        }
    return doc


async def _enforce(package: str, rule: str, input_data: dict[str, Any]) -> None:
    """Enforce OPA policy, raising HTTPException(403) on denial."""
    try:
        await require_policy(package, rule, input_data)
    except PolicyDenied as exc:
        raise HTTPException(403, f"Policy denied: {exc}")


@router.post("")
```

**Edit 3:** Add policy check to `create_bounty`:

Old:
```python
    """Create a draft bounty."""
    user_id = str(user.user_id)
```

New:
```python
    """Create a draft bounty."""
    await _enforce("bounty", "allow_create", _opa_input(user))
    user_id = str(user.user_id)
```

**Edit 4:** Replace ownership check in `fund_bounty`:

Old:
```python
    if str(row["principal_id"]) != str(user.user_id):
        raise HTTPException(403, "Only the bounty creator can fund it")
```

New:
```python
    await _enforce("bounty", "allow_fund", _opa_input(user, row))
```

**Edit 5:** Add policy check to `submit_to_bounty`:

Old:
```python
    if not row:
        raise HTTPException(404, "Bounty not found")

    if row["status"] == "open":
```

New:
```python
    if not row:
        raise HTTPException(404, "Bounty not found")

    await _enforce("bounty", "allow_submit", _opa_input(user, row))

    if row["status"] == "open":
```

**Edit 6:** Replace ownership check in `cancel_bounty`:

Old:
```python
    if str(row["principal_id"]) != str(user.user_id):
        raise HTTPException(403, "Only the bounty creator can cancel it")
```

New:
```python
    await _enforce("bounty", "allow_cancel", _opa_input(user, row))
```

**Edit 7:** Replace ownership and status checks in `update_target`:

Old:
```python
    if str(row["principal_id"]) != str(user.user_id):
        raise HTTPException(403, "Only the bounty creator can update the target")
    if row["status"] not in ("open", "submitted"):
        raise HTTPException(409, "Can only update target for open/submitted bounties")
```

New:
```python
    await _enforce("bounty", "allow_update_target", _opa_input(user, row))
```

---

## Step 4 — Modify `sciona/workflows/bounty_activities.py` (conditional)

**Condition:** Only apply this step if Phase 3 has been completed and this file
exists. If Phase 3 is not done, skip this step entirely — the router-level
checks from Step 3 are sufficient.

**Action:** Edit existing file. Add payout plan validation after computing the
settlement and before executing Stripe transfers.

### Step 4.1 — Add import

```python
from sciona.api.policy import evaluate_policy
```

### Step 4.2 — Add validation in `compute_settlement` activity

After the call to `settlement.compute_settlement(...)` and before returning
or executing payouts, add:

```python
    # Validate payout plan against OPA policy
    plan_input = {
        "plan": {
            "escrow_amount": float(plan.escrow_amount),
            "recipients": [
                {
                    "recipient_id": r.recipient_id,
                    "role": r.role,
                    "amount": float(r.amount),
                    "stripe_account_id": r.stripe_account_id,
                }
                for r in plan.recipients
            ],
        },
    }
    valid = await evaluate_policy("payout", "valid_plan", plan_input)
    if not valid:
        from temporalio.exceptions import ApplicationError
        raise ApplicationError(
            "Payout plan failed conservation invariant policy check",
            non_retryable=True,
        )
```

---

## Step 5 — Create `tests/test_opa_policies.py`

**Action:** Create new file.

This test file tests Rego policies in two ways:
1. **Unit tests:** Use `opa eval` CLI to test policies without a running server.
2. **Integration tests:** Use `httpx` against a running OPA instance (marked with
   `pytest.mark.integration`).

```python
"""Tests for OPA Rego policies and the Python OPA client."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# Path to the OPA policy directory
POLICY_DIR = Path(__file__).resolve().parent.parent / "docker" / "opa" / "policies"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def opa_eval(
    package: str,
    rule: str,
    input_data: dict[str, Any],
    *,
    policy_dir: Path = POLICY_DIR,
) -> Any:
    """Evaluate a Rego rule using the OPA CLI.

    Requires `opa` to be on PATH. Install with:
        brew install opa  # macOS
        # or download from https://www.openpolicyagent.org/docs/latest/#running-opa

    Returns the result value (typically bool for allow rules, list for deny rules).
    """
    query = f"data.{package}.{rule}"
    input_json = json.dumps(input_data)

    result = subprocess.run(
        [
            "opa", "eval",
            "--data", str(policy_dir),
            "--input", "/dev/stdin",
            "--format", "json",
            query,
        ],
        input=input_json,
        capture_output=True,
        text=True,
        timeout=10,
    )

    if result.returncode != 0:
        pytest.fail(f"opa eval failed: {result.stderr}")

    parsed = json.loads(result.stdout)
    # OPA eval returns {"result": [{"expressions": [{"value": <val>}]}]}
    try:
        return parsed["result"][0]["expressions"][0]["value"]
    except (KeyError, IndexError):
        return None


def _user(
    *,
    user_id: str = "user-1",
    identity_tier: str = "payee",
    is_blacklisted: bool = False,
    reputation_score: int = 100,
) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "identity_tier": identity_tier,
        "effective_tier": "general",
        "is_blacklisted": is_blacklisted,
        "reputation_score": reputation_score,
    }


def _bounty(
    *,
    bounty_id: str = "bounty-1",
    principal_id: str = "user-1",
    status: str = "draft",
    escrow_amount: float = 1000.0,
    tier: str = "standard",
) -> dict[str, Any]:
    return {
        "bounty_id": bounty_id,
        "principal_id": principal_id,
        "status": status,
        "escrow_amount": escrow_amount,
        "tier": tier,
    }


# ---------------------------------------------------------------------------
# Skip if OPA CLI is not installed
# ---------------------------------------------------------------------------

_opa_available = subprocess.run(
    ["opa", "version"], capture_output=True
).returncode == 0 if subprocess.run(
    ["which", "opa"], capture_output=True
).returncode == 0 else False

opa_required = pytest.mark.skipif(
    not _opa_available, reason="opa CLI not installed"
)


# ===========================================================================
# Bounty policy tests (using opa eval CLI)
# ===========================================================================


@opa_required
class TestBountyAllowCreate:
    def test_payee_can_create(self):
        result = opa_eval("bounty", "allow_create", {
            "user": _user(identity_tier="payee"),
        })
        assert result is True

    def test_contributor_cannot_create(self):
        result = opa_eval("bounty", "allow_create", {
            "user": _user(identity_tier="contributor"),
        })
        assert result is False

    def test_blacklisted_cannot_create(self):
        result = opa_eval("bounty", "allow_create", {
            "user": _user(is_blacklisted=True),
        })
        assert result is False

    def test_deny_reasons_for_contributor(self):
        result = opa_eval("bounty", "deny_create", {
            "user": _user(identity_tier="contributor"),
        })
        assert isinstance(result, list)
        assert any("contributor" in r for r in result)


@opa_required
class TestBountyAllowFund:
    def test_creator_can_fund_draft(self):
        result = opa_eval("bounty", "allow_fund", {
            "user": _user(user_id="user-1"),
            "bounty": _bounty(principal_id="user-1", status="draft"),
        })
        assert result is True

    def test_non_creator_cannot_fund(self):
        result = opa_eval("bounty", "allow_fund", {
            "user": _user(user_id="user-2"),
            "bounty": _bounty(principal_id="user-1", status="draft"),
        })
        assert result is False

    def test_cannot_fund_open_bounty(self):
        result = opa_eval("bounty", "allow_fund", {
            "user": _user(user_id="user-1"),
            "bounty": _bounty(principal_id="user-1", status="open"),
        })
        assert result is False

    def test_contributor_cannot_fund(self):
        result = opa_eval("bounty", "allow_fund", {
            "user": _user(user_id="user-1", identity_tier="contributor"),
            "bounty": _bounty(principal_id="user-1", status="draft"),
        })
        assert result is False


@opa_required
class TestBountyAllowSubmit:
    def test_non_creator_can_submit_to_open(self):
        result = opa_eval("bounty", "allow_submit", {
            "user": _user(user_id="user-2"),
            "bounty": _bounty(principal_id="user-1", status="open"),
        })
        assert result is True

    def test_non_creator_can_submit_to_submitted(self):
        result = opa_eval("bounty", "allow_submit", {
            "user": _user(user_id="user-2"),
            "bounty": _bounty(principal_id="user-1", status="submitted"),
        })
        assert result is True

    def test_creator_cannot_submit(self):
        result = opa_eval("bounty", "allow_submit", {
            "user": _user(user_id="user-1"),
            "bounty": _bounty(principal_id="user-1", status="open"),
        })
        assert result is False

    def test_cannot_submit_to_draft(self):
        result = opa_eval("bounty", "allow_submit", {
            "user": _user(user_id="user-2"),
            "bounty": _bounty(principal_id="user-1", status="draft"),
        })
        assert result is False

    def test_blacklisted_cannot_submit(self):
        result = opa_eval("bounty", "allow_submit", {
            "user": _user(user_id="user-2", is_blacklisted=True),
            "bounty": _bounty(principal_id="user-1", status="open"),
        })
        assert result is False


@opa_required
class TestBountyAllowCancel:
    def test_creator_can_cancel_draft(self):
        result = opa_eval("bounty", "allow_cancel", {
            "user": _user(user_id="user-1"),
            "bounty": _bounty(principal_id="user-1", status="draft"),
        })
        assert result is True

    def test_creator_can_cancel_open(self):
        result = opa_eval("bounty", "allow_cancel", {
            "user": _user(user_id="user-1"),
            "bounty": _bounty(principal_id="user-1", status="open"),
        })
        assert result is True

    def test_creator_can_cancel_submitted(self):
        result = opa_eval("bounty", "allow_cancel", {
            "user": _user(user_id="user-1"),
            "bounty": _bounty(principal_id="user-1", status="submitted"),
        })
        assert result is True

    def test_cannot_cancel_verified(self):
        result = opa_eval("bounty", "allow_cancel", {
            "user": _user(user_id="user-1"),
            "bounty": _bounty(principal_id="user-1", status="verified"),
        })
        assert result is False

    def test_non_creator_cannot_cancel(self):
        result = opa_eval("bounty", "allow_cancel", {
            "user": _user(user_id="user-2"),
            "bounty": _bounty(principal_id="user-1", status="draft"),
        })
        assert result is False


# ===========================================================================
# Payout policy tests
# ===========================================================================


@opa_required
class TestPayoutValidPlan:
    def test_valid_conservation(self):
        result = opa_eval("payout", "valid_plan", {
            "plan": {
                "escrow_amount": 1000.0,
                "recipients": [
                    {"role": "platform", "amount": 50.0},
                    {"role": "architect", "amount": 650.0},
                    {"role": "originator", "amount": 300.0},
                ],
            },
        })
        assert result is True

    def test_invalid_conservation(self):
        result = opa_eval("payout", "valid_plan", {
            "plan": {
                "escrow_amount": 1000.0,
                "recipients": [
                    {"role": "platform", "amount": 50.0},
                    {"role": "architect", "amount": 650.0},
                    {"role": "originator", "amount": 200.0},  # 100 short
                ],
            },
        })
        assert result is False


@opa_required
class TestPayoutValidCancellationFee:
    def test_no_submissions_10pct(self):
        result = opa_eval("payout", "valid_cancellation_fee", {
            "escrow_amount": 1000.0,
            "has_submissions": False,
            "cancellation_fee": 100.0,
        })
        assert result is True

    def test_with_submissions_25pct(self):
        result = opa_eval("payout", "valid_cancellation_fee", {
            "escrow_amount": 1000.0,
            "has_submissions": True,
            "cancellation_fee": 250.0,
        })
        assert result is True

    def test_wrong_fee_rejected(self):
        result = opa_eval("payout", "valid_cancellation_fee", {
            "escrow_amount": 1000.0,
            "has_submissions": False,
            "cancellation_fee": 50.0,  # wrong
        })
        assert result is False


# ===========================================================================
# Python OPA client tests (mocked — no OPA server needed)
# ===========================================================================


class TestPolicyClient:
    """Test sciona.api.policy module with mocked HTTP."""

    @pytest.fixture(autouse=True)
    def _import_policy(self):
        """Import the policy module (may fail if deps missing)."""
        try:
            from sciona.api import policy
            self.policy = policy
        except ImportError:
            pytest.skip("sciona.api.policy not importable (missing deps)")

    @pytest.mark.asyncio
    async def test_evaluate_policy_allowed(self):
        mock_response = AsyncMock()
        mock_response.json.return_value = {"result": True}
        mock_response.raise_for_status = AsyncMock()

        with patch("sciona.api.policy.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await self.policy.evaluate_policy(
                "bounty", "allow_create", {"user": {"identity_tier": "payee"}}
            )
            assert result is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_policy_denied(self):
        mock_response = AsyncMock()
        mock_response.json.return_value = {"result": False}
        mock_response.raise_for_status = AsyncMock()

        with patch("sciona.api.policy.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await self.policy.evaluate_policy(
                "bounty", "allow_create", {"user": {"identity_tier": "contributor"}}
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_fallback_permissive_on_timeout(self):
        import httpx as httpx_mod

        with patch("sciona.api.policy.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx_mod.TimeoutException("timeout")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with patch.object(self.policy, "_OPA_MODE", "permissive"):
                result = await self.policy.evaluate_policy(
                    "bounty", "allow_create", {}
                )
                assert result is True  # fail-open in permissive mode

    @pytest.mark.asyncio
    async def test_fallback_strict_on_timeout(self):
        import httpx as httpx_mod

        with patch("sciona.api.policy.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx_mod.TimeoutException("timeout")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with patch.object(self.policy, "_OPA_MODE", "strict"):
                result = await self.policy.evaluate_policy(
                    "bounty", "allow_create", {}
                )
                assert result is False  # fail-closed in strict mode

    @pytest.mark.asyncio
    async def test_require_policy_raises_on_denial(self):
        with patch.object(self.policy, "evaluate_policy_with_reasons", return_value=(False, ["not allowed"])):
            with pytest.raises(self.policy.PolicyDenied) as exc_info:
                await self.policy.require_policy(
                    "bounty", "allow_create", {}
                )
            assert "not allowed" in str(exc_info.value)
```

---

## Step 6 — Environment variable documentation

### Step 6.1 — Add to `docker/.env.example`

Append the following (or create if Step 1 hasn't been done yet):

```bash
# --- OPA Policy Engine ---
OPA_URL=http://localhost:8181
OPA_POLICY_MODE=permissive    # "permissive" (dev, fail-open) or "strict" (prod, fail-closed)
OPA_TIMEOUT_S=2
```

### Step 6.2 — Update `docker/opa/compose.yml` (from Phase 1)

Ensure the OPA container serves the policies with the data file loaded. The
existing compose already mounts `./policies:/policies:ro` and runs
`--server /policies`, which loads both `.rego` files and `data.json`
automatically. No changes needed beyond what Phase 1 provides.

Verify OPA serves the bundle by running:

```bash
docker compose -f docker/opa/compose.yml up -d
curl http://localhost:8181/v1/data/bounty/allow_create \
  -d '{"input":{"user":{"identity_tier":"payee","is_blacklisted":false}}}' \
  -H 'Content-Type: application/json'
# Expected: {"result":true}
```

---

## File summary

| File | Action | Step |
|---|---|---|
| `docker/opa/policies/data.json` | Create | 1.1 |
| `docker/opa/policies/bounty.rego` | Create | 1.2 |
| `docker/opa/policies/submission.rego` | Create | 1.3 |
| `docker/opa/policies/payout.rego` | Create | 1.4 |
| `docker/opa/policies/main.rego` | Delete (Phase 1 stub) | 1.5 |
| `sciona/api/policy.py` | Create | 2 |
| `sciona/api/routers/bounty.py` | Modify (7 edits) | 3 |
| `sciona/workflows/bounty_activities.py` | Modify (conditional on Phase 3) | 4 |
| `tests/test_opa_policies.py` | Create | 5 |
| `docker/.env.example` | Modify (append OPA vars) | 6.1 |

## Execution order

1. Create all policy files (Steps 1.1-1.5) — these are pure Rego, no Python deps.
2. Create `sciona/api/policy.py` (Step 2) — depends on `httpx` and `opentelemetry` from Phase 2.
3. Modify `bounty.py` (Step 3) — depends on Step 2.
4. Conditionally modify `bounty_activities.py` (Step 4) — depends on Phase 3 existence.
5. Create tests (Step 5) — depends on Steps 1-2.
6. Update env docs (Step 6) — independent.

Steps 1 and 6 can run in parallel with Step 2. Step 5 can run as soon as
Steps 1 and 2 are both done.

## Verification checklist

- [ ] `opa eval --data docker/opa/policies/ --input /dev/stdin 'data.bounty.allow_create'` returns expected results
- [ ] `pytest tests/test_opa_policies.py` passes (Rego tests require `opa` CLI; Python client tests use mocks)
- [ ] Existing `tests/test_bounty_state.py` still passes (state machine unchanged)
- [ ] Existing `tests/test_bounty_router.py` still passes (policy client returns True in permissive mode when OPA unavailable)
- [ ] `docker compose -f docker/opa/compose.yml up -d && curl localhost:8181/v1/data/bounty` returns loaded policies
- [ ] Full test suite completes in under 60 seconds
