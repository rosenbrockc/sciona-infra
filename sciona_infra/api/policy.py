"""Async OPA policy evaluation client with fail-open/fail-closed behavior."""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

import httpx

try:
    from opentelemetry import trace
except ImportError:  # pragma: no cover - optional dependency in some environments
    trace = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__) if trace is not None else None

_OPA_URL = os.getenv("OPA_URL", "http://localhost:8181").rstrip("/")
_OPA_TIMEOUT = float(os.getenv("OPA_TIMEOUT_S", "2"))
_OPA_MODE = os.getenv("OPA_POLICY_MODE", "permissive").strip().lower()


class PolicyDenied(Exception):
    """Raised when OPA denies a request."""

    def __init__(
        self,
        package: str,
        rule: str,
        reasons: list[str] | None = None,
    ) -> None:
        self.package = package
        self.rule = rule
        self.reasons = list(reasons or [])
        detail = "; ".join(self.reasons) if self.reasons else f"{package}.{rule} denied"
        super().__init__(detail)


async def evaluate_policy(
    package: str,
    rule: str,
    input_data: dict[str, Any],
) -> bool:
    """Evaluate a single OPA allow rule.

    In permissive mode, connectivity failures fail open. In strict mode, they
    fail closed.
    """
    with _span(
        "opa.evaluate",
        opa_package=package,
        opa_rule=rule,
        opa_mode=_OPA_MODE,
    ):
        try:
            async with httpx.AsyncClient(timeout=_OPA_TIMEOUT) as client:
                response = await client.post(_opa_url(package, rule), json={"input": input_data})
                response.raise_for_status()
                result = response.json().get("result", False)
                return bool(result)
        except Exception:
            logger.warning(
                "OPA evaluation failed for %s.%s; mode=%s",
                package,
                rule,
                _OPA_MODE,
                exc_info=True,
            )
            return _fallback_result(package, rule)


async def evaluate_policy_with_reasons(
    package: str,
    rule: str,
    deny_rule: str,
    input_data: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Evaluate a policy rule and, if denied, fetch deny reasons."""
    allowed = await evaluate_policy(package, rule, input_data)
    if allowed:
        return True, []

    try:
        async with httpx.AsyncClient(timeout=_OPA_TIMEOUT) as client:
            response = await client.post(_opa_url(package, deny_rule), json={"input": input_data})
            response.raise_for_status()
            reasons = response.json().get("result", [])
            if isinstance(reasons, list):
                return False, [str(item) for item in reasons]
            if reasons:
                return False, [str(reasons)]
    except Exception:
        logger.debug(
            "OPA denial reason lookup failed for %s.%s",
            package,
            deny_rule,
            exc_info=True,
        )

    return False, []


async def require_policy(
    package: str,
    rule: str,
    input_data: dict[str, Any],
    *,
    deny_rule: str | None = None,
) -> None:
    """Raise PolicyDenied if the policy rule evaluates to false."""
    if deny_rule is None:
        deny_rule = "deny_" + rule.removeprefix("allow_")

    allowed, reasons = await evaluate_policy_with_reasons(
        package,
        rule,
        deny_rule,
        input_data,
    )
    if not allowed:
        raise PolicyDenied(package, rule, reasons)


def _fallback_result(package: str, rule: str) -> bool:
    if _OPA_MODE == "strict":
        logger.error("OPA unavailable in strict mode, denying %s.%s", package, rule)
        return False
    logger.info("OPA unavailable in permissive mode, allowing %s.%s", package, rule)
    return True


def _opa_url(package: str, rule: str) -> str:
    return f"{_OPA_URL}/v1/data/{package}/{rule}"


@contextlib.contextmanager
def _span(name: str, **attributes: Any):
    if _tracer is None:
        yield None
        return

    with _tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is None:
                continue
            try:
                span.set_attribute(key, value)
            except Exception:
                logger.debug("Failed to annotate OPA span attribute %s", key, exc_info=True)
        yield span
