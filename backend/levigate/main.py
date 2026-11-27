"""Levigate API application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from levigate import clock
from levigate.api import (
    routes_admin,
    routes_auth,
    routes_compliance,
    routes_insights,
    routes_outreach,
    routes_prescribers,
    routes_work,
)
from levigate.config import get_settings
from levigate.connectors import init_connector
from levigate.db import create_schema, init_engine, sessionmaker
from levigate.sync import scheduler

log = logging.getLogger("levigate")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    engine = init_engine(s.app_db_url)
    await create_schema(engine)
    connector = init_connector(s.warehouse_url, s.connector_allowed_hosts, s.connector_query_timeout_s)
    task = None
    if s.sync_enabled and s.env != "test":
        task = asyncio.create_task(
            scheduler(sessionmaker(), connector, s.tenant_id, s.sync_interval_minutes, clock.today)
        )
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await engine.dispose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Levigate API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if s.env == "prod" else "/api/docs",
        redoc_url=None,
        openapi_url=None if s.env == "prod" else "/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["content-type", "x-csrf-token"],
    )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if s.session_cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e.get("loc", [])[1:]) for e in exc.errors()})
        return JSONResponse({"error": "Some fields need attention.", "fields": fields}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never a raw stack trace to the client; the full error goes to the (PHI-redacted) server log.
        log.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        return JSONResponse(
            {"error": "Something went wrong on our side. Your work was not lost — try again."}, status_code=500
        )

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    for module in (
        routes_auth,
        routes_work,
        routes_outreach,
        routes_prescribers,
        routes_insights,
        routes_compliance,
        routes_admin,
    ):
        app.include_router(module.router)
    return app


app = create_app()
