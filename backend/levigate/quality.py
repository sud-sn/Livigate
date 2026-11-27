"""Data quality and BI parity: is the client's data fit to run on, and does Levigate agree with the client's BI?

* ``mapping_contract`` — every canonical relation returns exactly the columns the rules expect.
* ``run_quality_checks`` — the shared checks (written against canonical relations) plus the profile's own raw-table
  checks. Each result is pass / info / warn / fail with a count and an explanation; never patient details.
* ``parity_report`` — compares Levigate's numbers with the client's BI views on the same rows: expected
  reimbursement, claim status, days to payment and prescriber volume. Mismatches are grouped, and a group is marked
  *explained* when Levigate can show it comes from a known difference in definitions (e.g. a flat 30-day unpaid
  window in the BI versus per-payer windows in the catalog) rather than from bad data or a mapping error.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from levigate import catalog
from levigate.connectors import ReadOnlyConnector
from levigate.rules import PayerRules, derive_status, to_date, to_datetime
from levigate.sources import CANONICAL_CHECKS, CANONICAL_RELATIONS

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    key: str
    title: str
    severity: str
    scope: str  # canonical | source
    count: int | None
    status: str  # pass | info | warn | fail | error
    explanation: str
    error: str | None = None


async def mapping_contract(connector: ReadOnlyConnector) -> list[dict[str, Any]]:
    out = []
    for relation, expected in CANONICAL_RELATIONS.items():
        try:
            cols = await connector.relation_columns(relation)
            missing = [c for c in expected if c not in cols]
            extra = [c for c in cols if c not in expected]
            out.append(
                {
                    "relation": relation,
                    "ok": not missing and not extra,
                    "missing": missing,
                    "extra": extra,
                    "source_tables": list(connector.profile.source_tables.get(relation, ())),
                }
            )
        except Exception as exc:  # a broken mapping must show as broken, not crash the page
            log.warning("relation %s failed to map: %s", relation, exc, exc_info=True)
            out.append(
                {
                    "relation": relation,
                    "ok": False,
                    "missing": list(expected),
                    "extra": [],
                    "error": type(exc).__name__,
                    "source_tables": list(connector.profile.source_tables.get(relation, ())),
                }
            )
    return out


async def run_quality_checks(connector: ReadOnlyConnector, today: date) -> list[CheckResult]:
    results = []
    checks = [(c, "canonical") for c in CANONICAL_CHECKS] + [(c, "source") for c in connector.profile.quality_checks]
    for check, scope in checks:
        try:
            n = await connector.run_check(check, today=today)
        except Exception as exc:
            log.warning("quality check %s failed to run: %s", check.key, exc, exc_info=True)
            results.append(
                CheckResult(
                    check.key, check.title, check.severity, scope, None, "error", check.explanation, type(exc).__name__
                )
            )
            continue
        if n == 0:
            status = "pass"
        else:
            status = {"error": "fail", "warning": "warn"}.get(check.severity, "info")
        results.append(CheckResult(check.key, check.title, check.severity, scope, n, status, check.explanation))
    return results


def summarize(results: list[CheckResult]) -> dict[str, int]:
    c = Counter(r.status for r in results)
    return {"fail": c["fail"], "warn": c["warn"], "info": c["info"], "pass": c["pass"], "error": c["error"]}


def as_dicts(results: list[CheckResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]


# ---------------------------------------------------------------------------------------------------------------
# Parity with the client's BI
# ---------------------------------------------------------------------------------------------------------------

BI_TO_LEVIGATE = {
    "PAID": {"paid"},
    "REJECTED": {"rejected"},
    "REVERSED": {"reversed"},
    "PENDING": {"pending"},
    "UNPAID": {"unpaid_past_due"},
}


@dataclass
class _Group:
    label: str
    explanation: str
    explained: bool
    count: int = 0
    samples: list[str] = field(default_factory=list)

    def add(self, sample: str) -> None:
        self.count += 1
        if len(self.samples) < 5:
            self.samples.append(sample)


def _section(key: str, title: str, compared: int, groups: dict[str, _Group]) -> dict[str, Any]:
    mismatched = sum(g.count for g in groups.values())
    explained = sum(g.count for g in groups.values() if g.explained)
    return {
        "key": key,
        "title": title,
        "compared": compared,
        "matched": compared - mismatched,
        "mismatched": mismatched,
        "explained": explained,
        "unexplained": mismatched - explained,
        "groups": [asdict(g) for g in sorted(groups.values(), key=lambda g: -g.count)],
    }


def _rx(rx: Any, fill: Any) -> str:
    return f"Rx {rx}-{int(fill):02d}"


async def parity_report(
    session: AsyncSession, connector: ReadOnlyConnector, tenant_id: str, today: date, window_days: int = 120
) -> dict[str, Any]:
    profile = connector.profile
    if not profile.parity:
        return {
            "available": False,
            "reason": "This source profile names no BI views to compare with. Add the client's BI views to the "
            "profile to compare Levigate's numbers with theirs.",
        }
    start = today - timedelta(days=window_days)
    payer_cfg = await catalog.current_map(session, tenant_id, "payer_config")

    def payer_rules(payer_id: str) -> PayerRules:
        entry = payer_cfg.get(payer_id)
        return PayerRules.from_body(entry.body if entry else None)

    lev_rows = await connector.fetch(
        "claims_in_range",
        start=start,
        end=today,
        payer_id=None,
        reject_code=None,
        location=None,
        min_amount=None,
        max_amount=None,
        rx_number=None,
    )
    lev = {(str(r["RX_NUMBER"]), int(r["FILL_NUMBER"])): r for r in lev_rows}
    bi = {
        (str(r["RX_NUMBER"]), int(r["FILL_NUMBER"])): r
        for r in await connector.fetch("bi_claim_status", start=start, end=today)
    }

    expected_groups: dict[str, _Group] = {}
    status_groups: dict[str, _Group] = {}
    dtp_groups: dict[str, _Group] = {}
    coverage_groups: dict[str, _Group] = {}
    compared_expected = compared_status = compared_dtp = 0

    for key in sorted(set(lev) | set(bi)):
        label = _rx(*key)
        lr, br = lev.get(key), bi.get(key)
        if lr is None or br is None:
            side = "only in the BI view" if lr is None else "only in Levigate"
            g = coverage_groups.setdefault(
                side,
                _Group(
                    f"Fills {side}",
                    "Usually a fill with no claim yet (Levigate tracks fills from their first claim) or a filter "
                    "difference between the BI view and the mapping."
                    if lr is None
                    else "The BI view does not list this fill.",
                    explained=lr is None and br is not None and br.get("BI_STATUS") in (None, "PENDING"),
                ),
            )
            g.add(label)
            continue

        # Expected reimbursement
        compared_expected += 1
        lev_expected = float(lr["EXPECTED_AMOUNT"] or 0)
        bi_expected = float(br["BI_EXPECTED"] or 0)
        if abs(lev_expected - bi_expected) > 0.01:
            expected_groups.setdefault(
                "diff",
                _Group(
                    "Expected reimbursement differs by more than one cent",
                    "The mapping's formula or contract terms do not match the BI's. Compare the two definitions.",
                    explained=False,
                ),
            ).add(f"{label}: {lev_expected:.2f} vs {bi_expected:.2f}")

        # Status
        compared_status += 1
        payer = payer_rules(str(lr["PAYER_ID"]))
        lev_status = derive_status(lr, payer, today)
        bi_status = str(br["BI_STATUS"])
        if lev_status not in BI_TO_LEVIGATE.get(bi_status, set()):
            age = (today - to_date(lr["FILL_DATE"])).days
            window = payer.payment_window_days
            if bi_status == "UNPAID" and lev_status == "pending" and 30 < age <= window:
                text, ok = (
                    f"BI marks a claim unpaid after a flat 30 days; Levigate uses the payer's own window from "
                    f"the catalog ({window} days for payer {lr['PAYER_ID']}).",
                    True,
                )
            elif bi_status == "PENDING" and lev_status == "unpaid_past_due" and window < age <= 30:
                text, ok = (
                    f"Levigate's payer window ({window} days for payer {lr['PAYER_ID']}) is shorter than the "
                    "BI's flat 30 days, so Levigate flags it sooner.",
                    True,
                )
            elif bi_status == "PAID" and lev_status == "underpaid":
                text, ok = (
                    "The BI has no underpaid status; Levigate flags payments below the expected amount by more "
                    "than the payer's threshold.",
                    True,
                )
            else:
                text, ok = (
                    "No known definition difference explains this — check the mapping and the source rows.",
                    False,
                )
            name = f"{bi_status} in BI, {lev_status.replace('_', ' ')} in Levigate"
            if ok:
                name += f" (payer {lr['PAYER_ID']})" if "window" in text else ""
            status_groups.setdefault(name, _Group(name, text, ok)).add(label)

        # Days to payment
        remitted = to_datetime(lr.get("REMITTED_AT"))
        if remitted is not None and br.get("BI_DAYS_TO_PAY") is not None:
            compared_dtp += 1
            lev_days = (remitted.date() - to_date(lr["FILL_DATE"])).days
            if lev_days != int(br["BI_DAYS_TO_PAY"]):
                dtp_groups.setdefault(
                    "diff",
                    _Group(
                        "Days to payment differs",
                        "Both count from the fill date to the last 835 payment; a difference points at the payment "
                        "date mapping.",
                        explained=False,
                    ),
                ).add(f"{label}: {lev_days} vs {br['BI_DAYS_TO_PAY']}")

    # Prescriber volume per month
    month_start = date(start.year, start.month, 1)
    lev_all: Counter = Counter()
    lev_sold: Counter = Counter()
    for r in await connector.fetch("fill_dates_by_prescriber", start=month_start):
        k = (str(r["PRESCRIBER_ID"]), to_date(r["FILL_DATE"]).strftime("%Y-%m"))
        lev_all[k] += 1
        if r["STATUS"] == "picked_up":
            lev_sold[k] += 1
    bi_monthly = {
        (str(r["PRESCRIBER_ID"]), str(r["FILL_MONTH"])): int(r["RX_COUNT"])
        for r in await connector.fetch("bi_prescriber_monthly", start_month=month_start.strftime("%Y-%m"))
    }
    volume_groups: dict[str, _Group] = {}
    keys = set(lev_all) | set(bi_monthly)
    for k in sorted(keys):
        if lev_all.get(k, 0) == bi_monthly.get(k, 0):
            continue
        sample = f"{k[0]} {k[1]}: {lev_all.get(k, 0)} vs {bi_monthly.get(k, 0)}"
        if lev_sold.get(k, 0) == bi_monthly.get(k, 0):
            g = volume_groups.setdefault(
                "sold_only",
                _Group(
                    "BI counts sold fills only",
                    "The BI counts only fills the patient picked up; Levigate counts every fill, including ones "
                    "waiting in the bin or on hold for a claim problem. Volume for scoring follows Levigate's "
                    "definition.",
                    True,
                ),
            )
        else:
            g = volume_groups.setdefault(
                "other", _Group("Prescriber volume differs", "No known definition difference explains this.", False)
            )
        g.add(sample)

    sections = [
        _section("expected", "Expected reimbursement", compared_expected, expected_groups),
        _section("status", "Claim status", compared_status, status_groups),
        _section("days_to_payment", "Days to payment", compared_dtp, dtp_groups),
        _section("prescriber_volume", "Prescriber volume by month", len(keys), volume_groups),
        _section("coverage", "Fills present on both sides", len(set(lev) | set(bi)), coverage_groups),
    ]
    return {
        "available": True,
        "window": {"start": start.isoformat(), "end": today.isoformat()},
        "bi_views": list(profile.parity),
        "sections": sections,
        "unexplained": sum(s["unexplained"] for s in sections),
    }
