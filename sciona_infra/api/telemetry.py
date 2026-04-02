"""OpenTelemetry and Sentry initialization for the platform API."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def setup_telemetry(app: Any) -> None:
    """Configure optional OpenTelemetry tracing and Sentry reporting."""
    _setup_opentelemetry(app)
    _setup_sentry()


def _setup_opentelemetry(app: Any) -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.info("OpenTelemetry packages not installed; skipping tracing setup")
        return

    endpoint = os.getenv("OTEL_EXPORTER_ENDPOINT", "http://localhost:4317")
    service_name = os.getenv("OTEL_SERVICE_NAME", "sciona-api")
    environment = os.getenv("SCIONA_ENV", "development")
    exporter_endpoint = endpoint.removeprefix("http://").removeprefix("https://")
    insecure = not endpoint.startswith("https://")

    try:
        resource = Resource.create(
            {
                "service.name": service_name,
                "deployment.environment": environment,
            }
        )
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(
            endpoint=exporter_endpoint,
            insecure=insecure,
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        try:
            HTTPXClientInstrumentor().instrument()
        except Exception:
            logger.debug("HTTPX instrumentation unavailable", exc_info=True)
    except Exception:
        logger.exception("Failed to configure OpenTelemetry")
        return

    logger.info(
        "OpenTelemetry configured: service=%s endpoint=%s env=%s",
        service_name,
        endpoint,
        environment,
    )


def _setup_sentry() -> None:
    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        logger.info("SENTRY_DSN not set; Sentry disabled")
        return

    try:
        import sentry_sdk
    except ImportError:
        logger.info("sentry-sdk not installed; skipping Sentry setup")
        return

    environment = os.getenv("SCIONA_ENV", "development")

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            traces_sample_rate=0.1,
        )
    except Exception:
        logger.exception("Failed to configure Sentry")
        return

    logger.info("Sentry configured: env=%s", environment)
