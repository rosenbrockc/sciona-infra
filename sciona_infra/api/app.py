"""FastAPI application for the Algorithmic Commons platform API."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sciona_infra.api.telemetry import setup_telemetry

logger = logging.getLogger(__name__)


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    return ""


async def _create_supabase_client(url: str, key: str):
    try:
        from supabase import acreate_client
    except ImportError:
        logger.warning("supabase package is not installed")
        return None

    try:
        return await acreate_client(url, key)
    except Exception:
        logger.exception("Failed to initialise Supabase client")
        return None


async def _create_temporal_client(address: str) -> Any | None:
    """Create an optional Temporal client, returning ``None`` on failure."""
    if not address:
        return None

    try:
        from temporalio.client import Client as TemporalClient
    except ImportError:
        logger.info("temporalio package is not installed")
        return None

    try:
        return await TemporalClient.connect(address)
    except Exception:
        logger.exception("Failed to initialise Temporal client")
        return None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Initialise Supabase clients and the graph driver on startup."""
    supabase_public = None
    supabase_admin = None
    graph_driver = None
    temporal_client = None

    supabase_url = _first_env("SCIONA_SUPABASE_URL", "SUPABASE_URL")
    anon_key = _first_env(
        "SCIONA_SUPABASE_ANON_KEY",
        "SUPABASE_ANON_KEY",
    )
    service_key = _first_env(
        "SCIONA_SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SCIONA_SUPABASE_SERVICE_KEY",
        "SUPABASE_SERVICE_KEY",
    )

    if supabase_url and anon_key:
        supabase_public = await _create_supabase_client(supabase_url, anon_key)
    if supabase_url and service_key:
        supabase_admin = await _create_supabase_client(supabase_url, service_key)
    if supabase_public is None and supabase_admin is not None:
        supabase_public = supabase_admin

    if supabase_public is not None:
        app.state.supabase = supabase_public
    if supabase_admin is not None:
        app.state.supabase_admin = supabase_admin

    memgraph_uri = os.environ.get("SCIONA_MEMGRAPH_URI", "")
    if memgraph_uri:
        try:
            from neo4j import AsyncGraphDatabase

            graph_driver = AsyncGraphDatabase.driver(memgraph_uri, auth=None)
            app.state.graph_driver = graph_driver
        except Exception:
            logger.exception("Failed to initialise graph driver")
            graph_driver = None

    temporal_address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    temporal_client = await _create_temporal_client(temporal_address)
    if temporal_client is not None:
        app.state.temporal = temporal_client

    yield

    if graph_driver is not None:
        try:
            await graph_driver.close()
        except Exception:
            pass
    if temporal_client is not None:
        try:
            await temporal_client.close()
        except Exception:
            pass


def create_app() -> FastAPI:
    """Factory for the platform API."""
    application = FastAPI(
        title="Algorithmic Commons API",
        version="0.1.0",
        lifespan=_lifespan,
    )

    from sciona_infra.api.routers.auth import router as auth_router
    from sciona_infra.api.routers.bounty import router as bounty_router
    from sciona_infra.api.routers.catalog import router as catalog_router
    from sciona_infra.api.routers.dashboard import router as dashboard_router
    from sciona_infra.api.routers.registry import router as registry_router
    from sciona_infra.api.routers.badges import router as badges_router
    from sciona_infra.api.routers.referrals import router as referrals_router
    from sciona_infra.api.routers.scim import router as scim_router
    from sciona_infra.api.routers.verification import router as verification_router

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(auth_router, tags=["auth"])
    application.include_router(registry_router, prefix="/atoms", tags=["registry"])
    application.include_router(bounty_router, prefix="/bounties", tags=["bounties"])
    application.include_router(catalog_router, prefix="/catalog", tags=["catalog"])
    application.include_router(verification_router, tags=["verification"])
    application.include_router(dashboard_router, tags=["dashboard"])
    application.include_router(badges_router, tags=["badges"])
    application.include_router(referrals_router, tags=["referrals"])
    application.include_router(scim_router, tags=["scim"])

    setup_telemetry(application)

    return application


app = create_app()
