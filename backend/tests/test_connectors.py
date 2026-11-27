"""Connector layer: only named, typed, read-only queries reach a client source."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from levigate.connectors import ConnectorError, ReadOnlyConnector


@pytest.fixture
def connector(env_paths, profile) -> ReadOnlyConnector:  # type: ignore[no-untyped-def]
    return ReadOnlyConnector(f"sqlite:///{env_paths['warehouse']}", profile=profile)


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_named_query_runs(connector: ReadOnlyConnector, demo) -> None:  # type: ignore[no-untyped-def]
    rows = run(connector.fetch("claims_for_fills", fill_ids=[demo["story"]["rx_4471_fill_id"]]))
    assert rows and rows[0]["REJECT_CODE"] == "79"
    assert rows[0]["RX_NUMBER"] == "4471" and int(rows[0]["FILL_NUMBER"]) == 2


def test_unknown_query_is_refused(connector: ReadOnlyConnector) -> None:
    with pytest.raises(ConnectorError, match="unknown query"):
        run(connector.fetch("DROP TABLE FACT_CLAIM"))


def test_unexpected_parameter_is_refused(connector: ReadOnlyConnector) -> None:
    with pytest.raises(ConnectorError, match="unexpected parameters"):
        run(connector.fetch("payers", sql="1; DELETE FROM DIM_PAYER"))


def test_wrong_type_is_refused(connector: ReadOnlyConnector) -> None:
    with pytest.raises(ConnectorError, match="wrong type"):
        run(connector.fetch("claims_changed_since", since="2026-01-01", min_fill_date="2026-01-01"))


def test_injection_text_is_bound_not_interpolated(connector: ReadOnlyConnector) -> None:
    rows = run(connector.fetch("search_prescribers", pattern="%' OR 1=1 --%"))
    assert rows == []


def test_warehouse_is_opened_read_only(connector: ReadOnlyConnector, env_paths) -> None:  # type: ignore[no-untyped-def]
    # A table that exists in this layout's raw warehouse: writes must be refused at the driver.
    raw_table = "DIM_PAYER" if connector.profile.name == "canonical" else "PAYER"
    with connector.engine.connect() as conn, pytest.raises(OperationalError, match="readonly"):
        conn.execute(text(f"DELETE FROM {raw_table}"))  # noqa: S608
    with sqlite3.connect(env_paths["warehouse"]) as con:
        assert con.execute(f"SELECT COUNT(*) FROM {raw_table}").fetchone()[0] > 0  # noqa: S608


def test_host_not_on_allow_list_is_refused() -> None:
    with pytest.raises(ConnectorError, match="allow-list"):
        ReadOnlyConnector("postgresql://reader@evil.example.com/db", allowed_hosts=["warehouse.client.internal"])


def test_dates_are_typed(connector: ReadOnlyConnector) -> None:
    rows = run(connector.fetch("prescriber_stats", cur_start=date(2026, 8, 29), prior_start=date(2026, 5, 31)))
    assert len(rows) == 40
