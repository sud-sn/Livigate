"""Test environment: a fresh synthetic warehouse and app database per test session, driven through the real app.

Nothing here talks to a real client system; every patient and prescriber is generated.

``LEVIGATE_TEST_PROFILE`` picks the warehouse layout the whole suite runs against:

* ``canonical`` (default) — the warehouse holds Levigate's seven canonical relations directly;
* ``pms_snowflake`` — a client's normalised pharmacy-system tables as Talend lands them in Snowflake (reload
  duplicates, soft deletes, split 835 payments, COB claims, …), mapped by the source profile;
* ``pms_snowflake_bi`` — the same tables, with expected reimbursement read from the client's own BI view.

CI runs the suite once per profile; every test must pass on all of them.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

TODAY = date(2026, 11, 27)
PROFILE = os.environ.get("LEVIGATE_TEST_PROFILE", "canonical")


def pytest_report_header() -> str:
    return f"levigate source profile: {PROFILE}"


@pytest.fixture(scope="session")
def profile() -> str:
    return PROFILE


@pytest.fixture(scope="session")
def demo(env_paths: dict[str, Path]) -> dict[str, Any]:
    """What the demo reset reported: the scripted story (fills used by the demo script), injected mess, …"""
    return _DEMO


_DEMO: dict[str, Any] = {}


@pytest.fixture(scope="session")
def env_paths(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("levigate")
    paths = {"app": root / "app.db", "warehouse": root / "warehouse.db"}
    os.environ.update(
        {
            "LEVIGATE_ENV": "test",
            "LEVIGATE_APP_DB_URL": f"sqlite+aiosqlite:///{paths['app']}",
            "LEVIGATE_WAREHOUSE_URL": f"sqlite:///{paths['warehouse']}",
            "LEVIGATE_SYNC_ENABLED": "false",
            "LEVIGATE_RATE_LIMIT_PER_MINUTE": "100000",
            "LEVIGATE_SOURCE_PROFILE": PROFILE,
        }
    )
    from levigate import clock
    from levigate.config import get_settings

    get_settings.cache_clear()
    clock.set_today(TODAY)
    from levigate.demo.seed import reset_demo

    _DEMO.update(asyncio.run(reset_demo(get_settings(), today=TODAY, n_patients=600, n_prescribers=40)))
    return paths


@pytest.fixture(scope="session")
def app_client(env_paths: dict[str, Path]) -> Iterator[TestClient]:
    from levigate.main import create_app

    with TestClient(create_app(), base_url="http://testserver") as client:
        yield client


class Session:
    """A signed-in user. All sessions share one TestClient (one event loop); each keeps its own session cookie and
    CSRF token and swaps its cookie into the jar before every request."""

    def __init__(self, client: TestClient, user_id: str) -> None:
        self.client = client
        client.cookies.clear()
        resp = client.post("/api/auth/demo-login", json={"user_id": user_id})
        assert resp.status_code == 200, resp.text
        self.me = resp.json()
        self.csrf = self.me["csrf_token"]
        self._cookies = list(client.cookies.jar)
        assert self._cookies, "login did not set a session cookie"

    def _use(self) -> None:
        self.client.cookies.clear()
        for c in self._cookies:
            self.client.cookies.jar.set_cookie(c)

    def get(self, url: str, **kw):  # type: ignore[no-untyped-def]
        self._use()
        return self.client.get(url, **kw)

    def post(self, url: str, json: dict | None = None, csrf: bool = True, **kw):  # type: ignore[no-untyped-def]
        self._use()
        headers = {"x-csrf-token": self.csrf} if csrf else {}
        return self.client.post(url, json=json or {}, headers=headers, **kw)

    def patch(self, url: str, json: dict | None = None):  # type: ignore[no-untyped-def]
        self._use()
        return self.client.patch(url, json=json or {}, headers={"x-csrf-token": self.csrf})


@pytest.fixture(scope="session")
def login(app_client: TestClient):  # type: ignore[no-untyped-def]
    def _login(user_id: str) -> Session:
        return Session(app_client, user_id)

    return _login


@pytest.fixture
def app_db(env_paths: dict[str, Path]) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(env_paths["app"])
    con.row_factory = sqlite3.Row
    yield con
    con.close()


@pytest.fixture
def warehouse_db(env_paths: dict[str, Path]) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(env_paths["warehouse"])
    con.row_factory = sqlite3.Row
    yield con
    con.close()


@pytest.fixture
def canonical_sql(warehouse_db: sqlite3.Connection) -> Callable[..., list[sqlite3.Row]]:
    """Run SQL written against the canonical relations on whatever layout the warehouse has (via the profile)."""
    from levigate.sources import compose, get_profile

    profile = get_profile(PROFILE)

    def _run(sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return warehouse_db.execute(compose(sql, profile, materialize=True), params).fetchall()

    return _run
