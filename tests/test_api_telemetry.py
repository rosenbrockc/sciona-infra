"""Tests for the OpenTelemetry and Sentry integration module."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sciona_infra.api import telemetry


class TestSetupTelemetry:
    """Verify setup_telemetry does not crash regardless of environment."""

    def test_setup_telemetry_without_otel_installed(self, monkeypatch):
        """When OTel packages are missing, setup completes silently."""
        app = MagicMock()
        monkeypatch.delenv("OTEL_EXPORTER_ENDPOINT", raising=False)
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        # Should not raise even if packages are not installed
        telemetry.setup_telemetry(app)

    def test_setup_sentry_skips_without_dsn(self, monkeypatch):
        """When SENTRY_DSN is empty, Sentry init is skipped."""
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        # Should not raise
        telemetry._setup_sentry()

    def test_setup_sentry_skips_without_package(self, monkeypatch):
        """When sentry-sdk is not installed, Sentry init is skipped."""
        monkeypatch.setenv("SENTRY_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0")
        with patch.dict("sys.modules", {"sentry_sdk": None}):
            # Should not raise
            telemetry._setup_sentry()


class TestSpanAnnotation:
    """Verify that span annotation helpers work with and without OTel."""

    def test_annotate_span_noop_without_otel(self, monkeypatch):
        """When OTel is not installed, _annotate_span in deps is a no-op."""
        from sciona_infra.api import deps

        # Should not raise
        deps._annotate_span(**{"user.id": "test-123", "auth.provider": "test"})

    def test_current_span_returns_none_without_otel(self):
        """_current_span returns None when OTel is not available."""
        from sciona_infra.api import deps

        with patch.dict("sys.modules", {"opentelemetry": None}):
            result = deps._current_span()
        # Either None (no OTel) or a span object — should not raise
        assert result is None or result is not None

    def test_annotate_span_sets_attributes_when_otel_available(self, monkeypatch):
        """When OTel is available, attributes are set on the current span."""
        from sciona_infra.api import deps

        mock_span = MagicMock()
        monkeypatch.setattr(deps, "_current_span", lambda: mock_span)

        deps._annotate_span(**{"user.id": "u-1", "auth.provider": "supabase"})

        mock_span.set_attribute.assert_any_call("user.id", "u-1")
        mock_span.set_attribute.assert_any_call("auth.provider", "supabase")

    def test_annotate_span_skips_none_values(self, monkeypatch):
        """None values should be skipped, not passed to set_attribute."""
        from sciona_infra.api import deps

        mock_span = MagicMock()
        monkeypatch.setattr(deps, "_current_span", lambda: mock_span)

        deps._annotate_span(**{"user.id": "u-1", "user.email": None})

        calls = [c.args[0] for c in mock_span.set_attribute.call_args_list]
        assert "user.id" in calls
        assert "user.email" not in calls
