"""Connector layer: read-only access to client sources through named, parameterized queries.

* One interface (SQLAlchemy) for every database type, so adding a source is configuration, not a rebuild.
* Nothing reaches a client database except a query registered in ``QUERIES``: no free-form SQL from users or the
  model, and every parameter is type-checked and bound, never interpolated.
* Connections use read-only service accounts; for the SQLite demo warehouse the file is opened read-only.
* Hosts must be on the allow-list (API7), and every query runs off the event loop with a timeout.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import anyio
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import URL, Engine, make_url

log = logging.getLogger(__name__)


class ConnectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class NamedQuery:
    name: str
    description: str
    sql: str
    params: Mapping[str, type | tuple[type, ...]] = field(default_factory=dict)
    expanding: frozenset[str] = frozenset()
    optional: frozenset[str] = frozenset()
    max_rows: int = 50_000


# Latest claim row per fill: the current state of every filled Rx.
_LATEST = """
WITH latest AS (
  SELECT c.*, ROW_NUMBER() OVER (PARTITION BY c.FILL_ID ORDER BY c.SUBMITTED_AT DESC, c.CLAIM_ID DESC) AS RN
  FROM FACT_CLAIM c
)
"""

_CLAIM_COLS = """
  l.CLAIM_ID, l.FILL_ID, f.RX_NUMBER, f.FILL_NUMBER, f.PATIENT_ID, f.PRESCRIBER_ID, f.PAYER_ID, f.LOCATION,
  f.FILL_DATE, f.DAYS_SUPPLY, f.QUANTITY, f.DRUG_ID, d.DRUG_NAME, l.RESPONSE_STATUS, l.REJECT_CODE,
  l.REJECT_MESSAGE, l.BILLED_AMOUNT, l.EXPECTED_AMOUNT, l.PAID_AMOUNT, l.SUBMITTED_AT, l.ADJUDICATED_AT,
  l.REMITTED_AT, l.UPDATED_AT
"""


def _sql(template: str) -> str:
    """Expand the shared fragments in a named-query template. Templates and fragments are module constants."""
    return template.replace("__LATEST__", _LATEST).replace("__CLAIM_COLS__", _CLAIM_COLS)


_q = NamedQuery
QUERIES: dict[str, NamedQuery] = {
    q.name: q
    for q in [
        _q(
            "claims_changed_since",
            "Current claim state for every fill whose latest claim changed since the watermark",
            _sql(
                """__LATEST__ SELECT __CLAIM_COLS__
            FROM latest l JOIN FACT_RXFILL f ON f.FILL_ID = l.FILL_ID JOIN DIM_DRUG d ON d.DRUG_ID = f.DRUG_ID
            WHERE l.RN = 1 AND l.UPDATED_AT >= :since AND f.FILL_DATE >= :min_fill_date"""
            ),
            {"since": str, "min_fill_date": date},
        ),
        _q(
            "claims_for_fills",
            "Current claim state for specific fills (reconciliation and on-demand reads)",
            _sql(
                """__LATEST__ SELECT __CLAIM_COLS__
            FROM latest l JOIN FACT_RXFILL f ON f.FILL_ID = l.FILL_ID JOIN DIM_DRUG d ON d.DRUG_ID = f.DRUG_ID
            WHERE l.RN = 1 AND l.FILL_ID IN :fill_ids"""
            ),
            {"fill_ids": list},
            expanding=frozenset({"fill_ids"}),
        ),
        _q(
            "claims_in_range",
            "Claims tracker: current claim state for fills in a date range, with optional filters",
            _sql(
                """__LATEST__ SELECT __CLAIM_COLS__
            FROM latest l JOIN FACT_RXFILL f ON f.FILL_ID = l.FILL_ID JOIN DIM_DRUG d ON d.DRUG_ID = f.DRUG_ID
            WHERE l.RN = 1 AND f.FILL_DATE >= :start AND f.FILL_DATE <= :end
              AND (:payer_id IS NULL OR f.PAYER_ID = :payer_id)
              AND (:reject_code IS NULL OR l.REJECT_CODE = :reject_code)
              AND (:location IS NULL OR f.LOCATION = :location)
              AND (:min_amount IS NULL OR l.EXPECTED_AMOUNT >= :min_amount)
              AND (:max_amount IS NULL OR l.EXPECTED_AMOUNT <= :max_amount)
              AND (:rx_number IS NULL OR f.RX_NUMBER = :rx_number)
            ORDER BY f.FILL_DATE DESC, f.RX_NUMBER"""
            ),
            {
                "start": date,
                "end": date,
                "payer_id": str,
                "reject_code": str,
                "location": str,
                "min_amount": (int, float),
                "max_amount": (int, float),
                "rx_number": str,
            },
            optional=frozenset({"payer_id", "reject_code", "location", "min_amount", "max_amount", "rx_number"}),
        ),
        _q(
            "claim_history",
            "Every claim submission for one fill, oldest first (CT-05)",
            """SELECT c.CLAIM_ID, c.SUBMITTED_AT, c.RESPONSE_STATUS, c.REJECT_CODE, c.REJECT_MESSAGE,
                      c.BILLED_AMOUNT, c.PAID_AMOUNT, c.ADJUDICATED_AT, c.REMITTED_AT
               FROM FACT_CLAIM c WHERE c.FILL_ID = :fill_id ORDER BY c.SUBMITTED_AT, c.CLAIM_ID""",
            {"fill_id": str},
        ),
        _q(
            "fills_for_rx",
            "All fills of the given prescriptions (refill-too-soon dates, adherence)",
            """SELECT f.FILL_ID, f.RX_NUMBER, f.FILL_NUMBER, f.FILL_DATE, f.DAYS_SUPPLY, f.STATUS
               FROM FACT_RXFILL f WHERE f.RX_NUMBER IN :rx_numbers ORDER BY f.RX_NUMBER, f.FILL_NUMBER""",
            {"rx_numbers": list},
            expanding=frozenset({"rx_numbers"}),
        ),
        _q(
            "patients_by_ids",
            "Patient identity for display (masked unless the role may see PHI)",
            """SELECT PATIENT_ID, FIRST_NAME, LAST_NAME, DOB, PHONE, PREFERRED_LANGUAGE
               FROM DIM_PATIENT WHERE PATIENT_ID IN :patient_ids""",
            {"patient_ids": list},
            expanding=frozenset({"patient_ids"}),
        ),
        _q(
            "refill_candidates",
            "Latest dispensed fill per active prescription",
            """WITH ranked AS (
                 SELECT f.*, ROW_NUMBER() OVER (PARTITION BY f.RX_NUMBER ORDER BY f.FILL_NUMBER DESC) AS RN
                 FROM FACT_RXFILL f WHERE f.STATUS IN ('picked_up', 'ready_for_pickup')
               )
               SELECT r.RX_NUMBER, r.FILL_NUMBER, r.PATIENT_ID, r.PRESCRIBER_ID, r.PAYER_ID, r.FILL_DATE,
                      r.DAYS_SUPPLY, r.REFILLS_REMAINING, r.RX_EXPIRES_DATE, r.EXPECTED_AMOUNT, r.DRUG_ID,
                      d.DRUG_NAME, d.IS_COMPOUND
               FROM ranked r JOIN DIM_DRUG d ON d.DRUG_ID = r.DRUG_ID
               WHERE r.RN = 1 AND r.FILL_DATE >= :min_fill_date""",
            {"min_fill_date": date},
        ),
        _q(
            "pickup_ready",
            "Fills waiting at the counter",
            """SELECT f.RX_NUMBER, f.FILL_NUMBER, f.PATIENT_ID, f.PICKUP_READY_DATE, f.EXPECTED_AMOUNT,
                      f.PATIENT_BALANCE, d.DRUG_NAME
               FROM FACT_RXFILL f JOIN DIM_DRUG d ON d.DRUG_ID = f.DRUG_ID
               WHERE f.STATUS = 'ready_for_pickup'""",
        ),
        _q(
            "balances_due",
            "Patients with an outstanding balance",
            """SELECT f.PATIENT_ID, SUM(f.PATIENT_BALANCE) AS BALANCE, MAX(f.FILL_DATE) AS LAST_FILL_DATE,
                      COUNT(*) AS FILLS
               FROM FACT_RXFILL f WHERE f.PATIENT_BALANCE > 0 GROUP BY f.PATIENT_ID""",
        ),
        _q(
            "prescriber_stats",
            "Rx volume and revenue per prescriber: current vs prior 90 days",
            """SELECT p.PRESCRIBER_ID, p.NPI, p.FIRST_NAME, p.LAST_NAME, p.CREDENTIAL, p.SPECIALTY, p.PRACTICE_NAME,
                      p.CITY, p.STATE,
                      SUM(CASE WHEN f.FILL_DATE >= :cur_start THEN 1 ELSE 0 END) AS RX_90,
                      SUM(CASE WHEN f.FILL_DATE >= :prior_start AND f.FILL_DATE < :cur_start THEN 1 ELSE 0 END)
                        AS RX_PRIOR_90,
                      SUM(CASE WHEN f.FILL_DATE >= :cur_start THEN f.EXPECTED_AMOUNT ELSE 0 END) AS REV_90,
                      SUM(CASE WHEN f.FILL_DATE >= :prior_start AND f.FILL_DATE < :cur_start
                               THEN f.EXPECTED_AMOUNT ELSE 0 END) AS REV_PRIOR_90,
                      MIN(f.FILL_DATE) AS FIRST_FILL, MAX(f.FILL_DATE) AS LAST_FILL
               FROM DIM_PRESCRIBER p LEFT JOIN FACT_RXFILL f ON f.PRESCRIBER_ID = p.PRESCRIBER_ID
               GROUP BY p.PRESCRIBER_ID, p.NPI, p.FIRST_NAME, p.LAST_NAME, p.CREDENTIAL, p.SPECIALTY,
                        p.PRACTICE_NAME, p.CITY, p.STATE""",
            {"cur_start": date, "prior_start": date},
        ),
        _q(
            "prescriber_detail",
            "Prescriber record (practice and contact; not PHI)",
            """SELECT PRESCRIBER_ID, NPI, FIRST_NAME, LAST_NAME, CREDENTIAL, SPECIALTY, PRACTICE_NAME, PHONE,
                      FAX, EMAIL, CITY, STATE FROM DIM_PRESCRIBER WHERE PRESCRIBER_ID = :prescriber_id""",
            {"prescriber_id": str},
        ),
        _q(
            "prescriber_fills",
            "Fill dates and revenue for one prescriber (bucketed into months by the API)",
            """SELECT f.FILL_DATE, f.EXPECTED_AMOUNT, f.FILL_NUMBER FROM FACT_RXFILL f
               WHERE f.PRESCRIBER_ID = :prescriber_id AND f.FILL_DATE >= :start""",
            {"prescriber_id": str, "start": date},
        ),
        _q(
            "prescriber_rejects",
            "Rejection events among one prescriber's fills in a window, by reject code (aggregate counts only)",
            """SELECT c.REJECT_CODE, MAX(c.REJECT_MESSAGE) AS REJECT_MESSAGE, COUNT(*) AS N,
                      SUM(c.EXPECTED_AMOUNT) AS AMOUNT
               FROM FACT_CLAIM c JOIN FACT_RXFILL f ON f.FILL_ID = c.FILL_ID
               WHERE c.RESPONSE_STATUS = 'R' AND f.PRESCRIBER_ID = :prescriber_id AND f.FILL_DATE >= :start
               GROUP BY c.REJECT_CODE ORDER BY N DESC""",
            {"prescriber_id": str, "start": date},
        ),
        _q(
            "reject_counts_by_prescriber",
            "Rejection events per prescriber in a window (aggregate counts only)",
            """SELECT f.PRESCRIBER_ID, COUNT(*) AS N
               FROM FACT_CLAIM c JOIN FACT_RXFILL f ON f.FILL_ID = c.FILL_ID
               WHERE c.RESPONSE_STATUS = 'R' AND f.FILL_DATE >= :start
               GROUP BY f.PRESCRIBER_ID""",
            {"start": date},
        ),
        _q(
            "payment_facts",
            "Paid claims with fill and remittance dates (days to payment)",
            _sql(
                """__LATEST__ SELECT f.FILL_DATE, l.REMITTED_AT, l.PAID_AMOUNT
                 FROM latest l JOIN FACT_RXFILL f ON f.FILL_ID = l.FILL_ID
                 WHERE l.RN = 1 AND l.RESPONSE_STATUS = 'P' AND f.FILL_DATE >= :start"""
            ),
            {"start": date},
        ),
        _q(
            "refill_facts",
            "Every dispensed fill since a date (refill capture rate)",
            """SELECT f.RX_NUMBER, f.FILL_NUMBER, f.FILL_DATE, f.DAYS_SUPPLY, f.REFILLS_REMAINING, f.RX_EXPIRES_DATE
               FROM FACT_RXFILL f WHERE f.STATUS IN ('picked_up', 'ready_for_pickup') AND f.FILL_DATE >= :start
               ORDER BY f.RX_NUMBER, f.FILL_NUMBER""",
            {"start": date},
        ),
        _q(
            "rejections_by_payer",
            "Rejected claims per payer in a date range",
            """SELECT p.PAYER_ID, p.PAYER_NAME, COUNT(*) AS REJECTIONS, SUM(c.BILLED_AMOUNT) AS AMOUNT
               FROM FACT_CLAIM c JOIN DIM_PAYER p ON p.PAYER_ID = c.PAYER_ID
               WHERE c.RESPONSE_STATUS = 'R' AND c.SUBMITTED_AT >= :start AND c.SUBMITTED_AT < :end
               GROUP BY p.PAYER_ID, p.PAYER_NAME ORDER BY REJECTIONS DESC""",
            {"start": str, "end": str},
        ),
        _q("payers", "Payer list", "SELECT PAYER_ID, PAYER_NAME, PLAN_TYPE FROM DIM_PAYER ORDER BY PAYER_NAME"),
        _q("locations", "Pharmacy locations", "SELECT DISTINCT LOCATION FROM FACT_RXFILL ORDER BY LOCATION"),
        _q(
            "search_prescribers",
            "Prescriber name search",
            """SELECT PRESCRIBER_ID, FIRST_NAME, LAST_NAME, CREDENTIAL, PRACTICE_NAME FROM DIM_PRESCRIBER
               WHERE LOWER(LAST_NAME) LIKE :pattern OR LOWER(FIRST_NAME) LIKE :pattern
                  OR LOWER(PRACTICE_NAME) LIKE :pattern
               ORDER BY LAST_NAME LIMIT 10""",
            {"pattern": str},
        ),
        _q(
            "search_rx",
            "Fills for an Rx number",
            """SELECT f.FILL_ID, f.RX_NUMBER, f.FILL_NUMBER, f.FILL_DATE, f.PATIENT_ID, d.DRUG_NAME
               FROM FACT_RXFILL f JOIN DIM_DRUG d ON d.DRUG_ID = f.DRUG_ID
               WHERE f.RX_NUMBER = :rx_number ORDER BY f.FILL_NUMBER DESC LIMIT 10""",
            {"rx_number": str},
        ),
        _q(
            "patient_exists",
            "Whether a patient ID exists",
            "SELECT PATIENT_ID FROM DIM_PATIENT WHERE PATIENT_ID = :patient_id",
            {"patient_id": str},
        ),
        _q("ping", "Connectivity check", "SELECT 1 AS OK"),
    ]
}


def _validate(q: NamedQuery, params: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(params) - set(q.params)
    if unknown:
        raise ConnectorError(f"{q.name}: unexpected parameters {sorted(unknown)}")
    bound: dict[str, Any] = {}
    for name, typ in q.params.items():
        value = params.get(name)
        if value is None:
            if name not in q.optional:
                raise ConnectorError(f"{q.name}: missing parameter '{name}'")
            bound[name] = None
            continue
        if typ is list:
            if not isinstance(value, Sequence) or isinstance(value, str | bytes):
                raise ConnectorError(f"{q.name}: '{name}' must be a list")
            if not value:
                raise ConnectorError(f"{q.name}: '{name}' must not be empty")
            if not all(isinstance(v, str | int) for v in value):
                raise ConnectorError(f"{q.name}: '{name}' must hold IDs")
            bound[name] = list(value)
            continue
        if isinstance(value, bool) or not isinstance(value, typ):
            raise ConnectorError(f"{q.name}: '{name}' has the wrong type")
        bound[name] = value
    return bound


class ReadOnlyConnector:
    def __init__(self, url: str, *, allowed_hosts: Sequence[str] = (), timeout_s: float = 20.0) -> None:
        parsed: URL = make_url(url)
        self.dialect = parsed.get_backend_name()
        self.timeout_s = timeout_s
        if parsed.host and parsed.host not in allowed_hosts:
            raise ConnectorError(f"host '{parsed.host}' is not on the connector allow-list")
        if self.dialect == "sqlite":
            path = parsed.database or ""

            def _creator() -> sqlite3.Connection:
                # mode=ro: the driver itself refuses writes; the demo warehouse is never modified by the app.
                return sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)

            self.engine: Engine = create_engine("sqlite://", creator=_creator)
        else:
            self.engine = create_engine(parsed, pool_pre_ping=True, pool_size=5, max_overflow=5)

    def _coerce(self, value: Any) -> Any:
        if self.dialect == "sqlite" and isinstance(value, date | datetime):
            return value.isoformat()
        return value

    def _run(self, q: NamedQuery, bound: dict[str, Any]) -> list[dict[str, Any]]:
        stmt = text(q.sql)
        if q.expanding:
            stmt = stmt.bindparams(*(bindparam(n, expanding=True) for n in q.expanding))
        with self.engine.connect() as conn:
            result = conn.execute(stmt, {k: self._coerce(v) for k, v in bound.items()})
            rows = result.mappings().fetchmany(q.max_rows + 1)
        if len(rows) > q.max_rows:
            raise ConnectorError(f"{q.name}: more than {q.max_rows} rows; narrow the request")
        return [{k.upper(): v for k, v in row.items()} for row in rows]

    async def fetch(self, name: str, **params: Any) -> list[dict[str, Any]]:
        q = QUERIES.get(name)
        if q is None:
            raise ConnectorError(f"unknown query '{name}'")
        bound = _validate(q, params)
        with anyio.fail_after(self.timeout_s):
            return await anyio.to_thread.run_sync(self._run, q, bound)

    async def test(self) -> tuple[bool, str]:
        try:
            rows = await self.fetch("ping")
            return (bool(rows) and rows[0].get("OK") == 1, "Connected")
        except Exception as exc:  # surfaced to the admin as pass/fail, logged in full here
            log.warning("connection test failed: %s", exc, exc_info=True)
            return (False, type(exc).__name__)


_connector: ReadOnlyConnector | None = None


def init_connector(url: str, allowed_hosts: Sequence[str], timeout_s: float) -> ReadOnlyConnector:
    global _connector
    _connector = ReadOnlyConnector(url, allowed_hosts=allowed_hosts, timeout_s=timeout_s)
    return _connector


def get_connector() -> ReadOnlyConnector:
    if _connector is None:
        raise RuntimeError("connector not initialised")
    return _connector
