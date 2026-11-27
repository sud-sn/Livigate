"""Source profiles: every client layout maps onto the canonical relations exactly, in SQL that Snowflake accepts.

The same simulated pharmacy is written twice — as Levigate's reference tables and as a client's normalised PMS
tables landed by Talend (with reload duplicates, voids, split 835 payments, COB claims, extra reject codes, …). Every
named query must return the same business rows from both, through the mapping. Mutated mappings prove the checks
have teeth.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlglot
from sqlglot import exp

from levigate.connectors import QUERIES, ReadOnlyConnector
from levigate.demo.warehouse import World, build_warehouse
from levigate.quality import mapping_contract, run_quality_checks
from levigate.services import normalize_patient
from levigate.sources import (
    CANONICAL_CHECKS,
    CANONICAL_RELATIONS,
    DEMO_LAYOUT,
    PROFILES,
    SourceProfile,
    compose,
    pms_snowflake,
)

TODAY = date(2026, 11, 27)
EPOCH = "2000-01-01T00:00:00"
SURROGATE = {"FILL_ID", "CLAIM_ID"}  # each layout has its own surrogate keys; business keys must match
# The source's own change marker (Talend's LOAD_TS here, the table's update time in the reference layout): it only
# drives the incremental pull, which test_layouts.py checks end to end.
LAYOUT_SPECIFIC = {"UPDATED_AT"}
DATE_ONLY = {"REMITTED_AT"}  # an 835 payment carries a date, not a time of day


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def worlds(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, World]]:
    root = tmp_path_factory.mktemp("layouts")
    out = {}
    for layout in ("canonical", "pms"):
        path = root / f"{layout}.db"
        out[layout] = (path, build_warehouse(path, TODAY, layout=layout, n_patients=600, n_prescribers=40))
    return out


def _connector(worlds: dict[str, tuple[Path, World]], profile: str | SourceProfile) -> ReadOnlyConnector:
    name = profile if isinstance(profile, str) else "pms_snowflake"
    path, _ = worlds[DEMO_LAYOUT[name]]
    conn = ReadOnlyConnector(f"sqlite:///{path}", profile=name)
    if isinstance(profile, SourceProfile):
        conn.profile = profile
    return conn


def _norm_value(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int | float | Decimal):
        return round(float(v), 2)
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


def _norm_field(k: str, v: Any) -> Any:
    v = _norm_value(v)
    return v[:10] if k in DATE_ONLY and isinstance(v, str) else v


def _norm(rows: list[dict[str, Any]]) -> list[tuple]:
    skip = SURROGATE | LAYOUT_SPECIFIC
    return sorted(
        (tuple(sorted((k, _norm_field(k, v)) for k, v in r.items() if k not in skip)) for r in rows), key=repr
    )


# ---------------------------------------------------------------------------------------------------------------
# Contract and dialect
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_every_relation_returns_exactly_the_canonical_columns(worlds, profile: str) -> None:  # type: ignore[no-untyped-def]
    contract = run(mapping_contract(_connector(worlds, profile)))
    assert [c["relation"] for c in contract] == list(CANONICAL_RELATIONS)
    broken = [c for c in contract if not c["ok"]]
    assert not broken, broken


def _all_sql(profile: SourceProfile) -> list[tuple[str, str]]:
    sql = [(f"query {n}", q.sql) for n, q in QUERIES.items()]
    sql += [(f"check {c.key}", c.sql) for c in (*CANONICAL_CHECKS, *profile.quality_checks)]
    sql += [(f"relation {r}", f"SELECT * FROM {r}") for r in CANONICAL_RELATIONS]  # noqa: S608
    return sql


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_all_composed_sql_is_valid_snowflake(profile: str) -> None:
    """Every query, check and mapping, composed exactly as it would be sent to Snowflake, parses as Snowflake SQL
    and uses only functions Snowflake has (sqlglot accepts unknown functions as 'anonymous': none are allowed)."""
    prof = PROFILES[profile]
    for name, sql in _all_sql(prof):
        composed = compose(sql, prof)
        assert "MATERIALIZED" not in composed.upper(), name  # SQLite-only hint never reaches the client
        tree = sqlglot.parse_one(composed, read="snowflake")
        unknown = sorted({f.name for f in tree.find_all(exp.Anonymous)})
        assert not unknown, f"{name}: functions Snowflake may not have: {unknown}"
        assert not list(tree.find_all(exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create)), name


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_profile_definitions_are_well_formed(profile: str) -> None:
    prof = PROFILES[profile]
    assert set(prof.relations) <= set(CANONICAL_RELATIONS)
    assert all(n.startswith(("SRC_", "BI_")) for n in prof.helpers), "helpers must be namespaced"
    assert set(prof.source_tables) == set(CANONICAL_RELATIONS), "document the source tables of every relation"
    assert set(prof.parity) <= set(prof.helpers)
    for name in prof.definitions():
        compose(f"SELECT * FROM {name}", prof)  # noqa: S608 - raises on circular definitions
    keys = [c.key for c in (*CANONICAL_CHECKS, *prof.quality_checks)]
    assert len(keys) == len(set(keys)), "check keys must be unique"


def test_composition_only_includes_what_a_query_needs() -> None:
    prof = PROFILES["pms_snowflake"]
    sql = compose("SELECT PAYER_ID FROM DIM_PAYER", prof)
    assert "DIM_PAYER AS (" in sql and "FACT_CLAIM" not in sql and "SRC_FILL" not in sql
    claim = compose("SELECT * FROM FACT_CLAIM", prof)
    assert claim.index("SRC_FILL AS (") < claim.index("FACT_CLAIM AS (")  # dependencies first
    assert compose("SELECT 1 AS N", PROFILES["canonical"]) == "SELECT 1 AS N"


# ---------------------------------------------------------------------------------------------------------------
# Same business answers from every layout
# ---------------------------------------------------------------------------------------------------------------


def _params(conn: ReadOnlyConnector, world: World) -> dict[str, dict[str, Any]]:
    claims = run(
        conn.fetch(
            "claims_in_range",
            start=TODAY - timedelta(days=120),
            end=TODAY,
            payer_id=None,
            reject_code=None,
            location=None,
            min_amount=None,
            max_amount=None,
            rx_number=None,
        )
    )
    fill_ids = sorted({str(c["FILL_ID"]) for c in claims})
    patients = sorted({str(c["PATIENT_ID"]) for c in claims})[:60]
    rx = [*sorted({str(c["RX_NUMBER"]) for c in claims})[:40], "4471"]
    prescriber = world.story.declining_prescriber
    return {
        "claims_changed_since": {"since": EPOCH, "min_fill_date": TODAY - timedelta(days=120)},
        "claims_for_fills": {"fill_ids": fill_ids},
        "claims_in_range": {
            "start": TODAY - timedelta(days=120),
            "end": TODAY,
            "payer_id": None,
            "reject_code": None,
            "location": None,
            "min_amount": None,
            "max_amount": None,
            "rx_number": None,
        },
        "claim_history": {"fill_id": world.story.rx_4471_fill_id},
        "fills_for_rx": {"rx_numbers": rx},
        "patients_by_ids": {"patient_ids": patients},
        "refill_candidates": {"min_fill_date": TODAY - timedelta(days=200)},
        "pickup_ready": {},
        "balances_due": {},
        "prescriber_stats": {"cur_start": TODAY - timedelta(days=90), "prior_start": TODAY - timedelta(days=180)},
        "prescriber_detail": {"prescriber_id": prescriber},
        "prescriber_fills": {"prescriber_id": prescriber, "start": TODAY - timedelta(days=180)},
        "prescriber_rejects": {"prescriber_id": prescriber, "start": TODAY - timedelta(days=180)},
        "reject_counts_by_prescriber": {"start": TODAY - timedelta(days=90)},
        "payment_facts": {"start": TODAY - timedelta(days=180)},
        "refill_facts": {"start": TODAY - timedelta(days=180)},
        "rejections_by_payer": {"start": "2026-08-01", "end": "2026-11-27"},
        "payers": {},
        "locations": {},
        "search_prescribers": {"pattern": "%a%"},
        "search_rx": {"rx_number": "4471"},
        "patient_exists": {"patient_id": world.story.rx_4471_patient},
        "consent_changed_since": {"since": EPOCH},
        "fill_dates_by_prescriber": {"start": TODAY - timedelta(days=120)},
        "bi_claim_status": {"start": TODAY - timedelta(days=120), "end": TODAY},
        "bi_prescriber_monthly": {"start_month": "2026-08"},
        "ping": {},
    }


BI_ONLY = {"bi_claim_status", "bi_prescriber_monthly"}  # the reference layout has no client BI views


def _answers(conn: ReadOnlyConnector, world: World, names: set[str]) -> dict[str, list[tuple]]:
    params = _params(conn, world)
    assert set(params) == set(QUERIES), "every named query needs parameters here (and a cross-layout comparison)"
    out = {}
    for n in sorted(names):
        rows = run(conn.fetch(n, **params[n]))
        if n == "patients_by_ids":  # every screen reads patients through this normalisation (name case, phone)
            rows = [normalize_patient(r) for r in rows]
        out[n] = _norm(rows)
    return out


def test_every_named_query_gives_the_same_business_answer_on_every_layout(worlds) -> None:  # type: ignore[no-untyped-def]
    shared = set(QUERIES) - BI_ONLY
    reference = _answers(_connector(worlds, "canonical"), worlds["canonical"][1], shared)
    non_empty = [n for n, rows in reference.items() if rows]
    assert len(non_empty) >= len(shared) - 1, f"comparison would be vacuous: empty {set(shared) - set(non_empty)}"
    for profile in ("pms_snowflake", "pms_snowflake_bi"):
        answers = _answers(_connector(worlds, profile), worlds["pms"][1], set(QUERIES))
        for name in sorted(shared):
            assert answers[name] == reference[name], f"{profile}: {name} differs from the reference layout"
    # Both PMS variants read the same BI views
    bi = _answers(_connector(worlds, "pms_snowflake"), worlds["pms"][1], BI_ONLY)
    assert all(bi.values()) and bi == _answers(_connector(worlds, "pms_snowflake_bi"), worlds["pms"][1], BI_ONLY)


def test_ported_formula_matches_the_clients_bi_view_to_the_cent(worlds) -> None:  # type: ignore[no-untyped-def]
    """Two ways to get expected reimbursement: Levigate's port of the BI formula, or the BI view's own column."""
    formula, view = (_connector(worlds, p) for p in ("pms_snowflake", "pms_snowflake_bi"))
    q = "SELECT RX_NUMBER, FILL_NUMBER, EXPECTED_AMOUNT FROM FACT_RXFILL"
    a = {(r[0], r[1]): r[2] for r in formula.engine.connect().exec_driver_sql(formula_sql(formula, q)).fetchall()}
    b = {(r[0], r[1]): r[2] for r in view.engine.connect().exec_driver_sql(formula_sql(view, q)).fetchall()}
    assert len(a) > 1000 and a.keys() == b.keys()
    assert max(abs(a[k] - b[k]) for k in a) < 0.005
    # Both halves of "lesser of U&C and contract" occur in the data, so both are compared.
    uc = compose(
        "SELECT COUNT(*) FROM SRC_FILL_EXPECTED e JOIN SRC_FILL f ON f.FILL_KEY = e.FILL_KEY "
        "WHERE e.EXPECTED_AMOUNT = f.USUAL_CUSTOMARY_AMT",
        formula.profile,
        materialize=True,
    )
    capped = formula.engine.connect().exec_driver_sql(uc).scalar()
    assert 50 < capped < len(a) - 50


def formula_sql(conn: ReadOnlyConnector, sql: str) -> str:
    return compose(sql, conn.profile, materialize=True)


# ---------------------------------------------------------------------------------------------------------------
# Data quality: the checks find exactly the mess that was injected
# ---------------------------------------------------------------------------------------------------------------

CHECK_FOR_MESS = {
    "reload_duplicates_removed": "duplicate_fill_rows",
    "soft_deleted_fills": "soft_deleted_fills",
    "orphan_claim_transactions": "orphan_claims",
    "secondary_payer_claims": "secondary_payer_claims",
    "secondary_reject_codes": "secondary_reject_codes",
    "split_remittances": "split_remittances",
    "email_preferences_ignored": "email_preferences_ignored",
    "patients_without_phone": "patients_without_phone",
}


@pytest.mark.parametrize("profile", ["pms_snowflake", "pms_snowflake_bi"])
def test_quality_checks_count_exactly_the_injected_mess(worlds, profile: str) -> None:  # type: ignore[no-untyped-def]
    mess = worlds["pms"][1].mess
    results = {r.key: r for r in run(run_quality_checks(_connector(worlds, profile), TODAY))}
    for check, injected in CHECK_FOR_MESS.items():
        assert mess[injected] > 0, f"the generator injected no {injected}: the check proves nothing"
        assert results[check].count == mess[injected], check
    failing = {k: r.count for k, r in results.items() if r.status in ("fail", "error")}
    assert not failing, f"a correct mapping must not fail any check: {failing}"


def test_reference_layout_is_clean(worlds) -> None:  # type: ignore[no-untyped-def]
    results = run(run_quality_checks(_connector(worlds, "canonical"), TODAY))
    assert {r.key for r in results} == {c.key for c in CANONICAL_CHECKS}
    assert {r.key: r.count for r in results if r.status not in ("pass", "info")} == {}


# ---------------------------------------------------------------------------------------------------------------
# Mutations: a wrong mapping is caught
# ---------------------------------------------------------------------------------------------------------------


def _mutant(**changes: Any) -> SourceProfile:
    base = pms_snowflake.PROFILE
    helpers = {**base.helpers, **changes.pop("helpers", {})}
    relations = {**base.relations, **changes.pop("relations", {})}
    return dataclasses.replace(base, helpers=helpers, relations=relations, **changes)


def test_mapping_that_keeps_reload_duplicates_is_caught(worlds) -> None:  # type: ignore[no-untyped-def]
    bad = _mutant(helpers={"SRC_FILL": "SELECT x.*, 1 AS SRC_RN FROM RX_FILL x WHERE x.IS_DELETED = 'N'"})
    results = {r.key: r for r in run(run_quality_checks(_connector(worlds, bad), TODAY))}
    assert results["duplicate_fills"].status == "fail"
    assert results["duplicate_fills"].count == worlds["pms"][1].mess["duplicate_fill_rows"]


def test_mapping_that_ignores_contract_dates_is_caught(worlds) -> None:  # type: ignore[no-untyped-def]
    """Joining every contract a plan ever had (not the one in force on the fill date) duplicates fills."""
    bad_join = "LEFT JOIN PLAN_CONTRACT c ON c.PLAN_KEY = f.PLAN_KEY_BILLED"
    bad = _mutant(
        relations={"FACT_RXFILL": pms_snowflake._fact_rxfill(pms_snowflake._EXPECTED_FORMULA, bad_join)},
    )
    results = {r.key: r for r in run(run_quality_checks(_connector(worlds, bad), TODAY))}
    assert results["duplicate_fills"].status == "fail"


def test_wrong_expected_formula_is_caught_by_the_cross_layout_comparison(worlds) -> None:  # type: ignore[no-untyped-def]
    """Forgetting the lesser-of-U&C rule passes every data check but disagrees with the reference answers."""
    no_uc = "ROUND(fo.AWP_PER_UNIT * f.QTY_DISPENSED * c.CONTRACT_RATE + c.DISPENSING_FEE, 2)"
    bad = _mutant(relations={"FACT_RXFILL": pms_snowflake._fact_rxfill(no_uc, pms_snowflake._CONTRACT_JOIN)})
    results = {r.key: r for r in run(run_quality_checks(_connector(worlds, bad), TODAY))}
    assert not [k for k, r in results.items() if r.status == "fail"], "this mistake passes every data check"
    names = {"refill_candidates", "pickup_ready", "balances_due"}
    good = _answers(_connector(worlds, "canonical"), worlds["canonical"][1], names)
    wrong = _answers(_connector(worlds, bad), worlds["pms"][1], names)
    assert wrong != good


def test_missing_column_is_reported_by_the_contract(worlds) -> None:  # type: ignore[no-untyped-def]
    original = pms_snowflake._DIMENSIONS["DIM_PAYER"]
    sql = original.replace("pl.BIN_NBR AS BIN, pl.PCN,", "pl.BIN_NBR AS BIN,")
    assert sql != original
    bad = _mutant(relations={"DIM_PAYER": sql})
    payer = next(c for c in run(mapping_contract(_connector(worlds, bad))) if c["relation"] == "DIM_PAYER")
    assert not payer["ok"] and payer["missing"] == ["PCN"]


def test_untranslated_code_is_caught_by_the_vocabulary_check(worlds) -> None:  # type: ignore[no-untyped-def]
    """A mapping that passes the client's plan-type codes through untranslated fails a data check."""
    original = pms_snowflake._DIMENSIONS["DIM_PAYER"]
    start, end = original.index("CASE py.PLAN_TYPE_CD"), original.index("END AS PLAN_TYPE")
    sql = original[:start] + "py.PLAN_TYPE_CD" + original[end + 3 :]
    bad = _mutant(relations={"DIM_PAYER": sql})
    results = {r.key: r for r in run(run_quality_checks(_connector(worlds, bad), TODAY))}
    assert results["vocabulary_dim_payer_plan_type"].status == "fail"
    assert results["vocabulary_dim_payer_plan_type"].count == 7
