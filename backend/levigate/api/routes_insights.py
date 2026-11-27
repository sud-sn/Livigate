"""Results dashboard (AD-05), top search, and plain-English questions over approved named queries."""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from levigate import audit, catalog, clock
from levigate.api.deps import Principal, db_session, require
from levigate.connectors import get_connector
from levigate.models import OPEN_STATUSES, AiInteraction, CrmActivity, WorkItem, WorkItemType, as_utc
from levigate.policy import has_permission
from levigate.rules import score_prescribers, to_date, to_datetime
from levigate.services import masked_patients, payer_names, prescriber_name

router = APIRouter(prefix="/api", tags=["insights"])


def _week(d: date) -> date:
    return d - timedelta(days=d.weekday())


@router.get("/dashboard")
async def dashboard(
    weeks: int = Query(12, ge=4, le=26),
    principal: Principal = Depends(require("dashboard.view")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    today = clock.today()
    tenant = principal.user.tenant_id
    connector = get_connector()
    first_week = _week(today) - timedelta(weeks=weeks - 1)
    week_keys = [first_week + timedelta(weeks=i) for i in range(weeks)]

    # Dollars recovered: paid on claims that went through an approved work item.
    recovered: dict[date, float] = defaultdict(float)
    for amount, resolved_at in (
        await db.execute(
            select(WorkItem.recovered_amount, WorkItem.resolved_at).where(
                WorkItem.tenant_id == tenant, WorkItem.recovered_amount.is_not(None)
            )
        )
    ).all():
        ts = as_utc(resolved_at)
        if ts is not None and _week(ts.date()) >= first_week:
            recovered[_week(ts.date())] += float(amount or 0)

    # Days to payment: median days from fill to remittance, by remittance week.
    by_week: dict[date, list[int]] = defaultdict(list)
    for r in await connector.fetch("payment_facts", start=first_week - timedelta(days=90)):
        remitted = to_datetime(r["REMITTED_AT"])
        if remitted is None:
            continue
        wk = _week(remitted.date())
        if wk >= first_week:
            by_week[wk].append((remitted.date() - to_date(r["FILL_DATE"])).days)

    # Refill capture: refills that came due (and are at least 7 days past due) filled within 7 days of due date.
    fills_by_rx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in await connector.fetch("refill_facts", start=first_week - timedelta(days=120)):
        fills_by_rx[str(f["RX_NUMBER"])].append(f)
    due_total: dict[date, int] = defaultdict(int)
    due_captured: dict[date, int] = defaultdict(int)
    for seq in fills_by_rx.values():
        seq.sort(key=lambda f: int(f["FILL_NUMBER"]))
        for i, f in enumerate(seq):
            if int(f["REFILLS_REMAINING"] or 0) <= 0:
                continue
            due = to_date(f["FILL_DATE"]) + timedelta(days=int(f["DAYS_SUPPLY"]))
            if due > today - timedelta(days=7) or _week(due) < first_week:
                continue
            if f.get("RX_EXPIRES_DATE") and to_date(f["RX_EXPIRES_DATE"]) < due:
                continue
            due_total[_week(due)] += 1
            nxt = seq[i + 1] if i + 1 < len(seq) else None
            if nxt is not None and to_date(nxt["FILL_DATE"]) <= due + timedelta(days=7):
                due_captured[_week(due)] += 1

    backlog = await db.scalar(
        select(func.count())
        .select_from(WorkItem)
        .where(
            WorkItem.tenant_id == tenant,
            WorkItem.status.in_(OPEN_STATUSES),
            WorkItem.type.in_([WorkItemType.CLAIM_REJECTED, WorkItemType.REJECT_REVIEW]),
            WorkItem.fill_date < today - timedelta(days=7),
        )
    )

    # Prescriber reactivation: declining prescribers contacted, and whether Rx rose in the 60 days after.
    stats = await connector.fetch(
        "prescriber_stats", cur_start=today - timedelta(days=90), prior_start=today - timedelta(days=180)
    )
    scoring = await catalog.current(db, tenant, "prescriber_scoring", "default")
    declining = {
        p["PRESCRIBER_ID"]
        for p in score_prescribers(
            stats, scoring.body if scoring else {"weights": {"volume": 0.5, "revenue": 0.35, "trend": 0.15}}, today
        )
        if p["IS_DECLINING"]
    }
    contacted = (
        await db.execute(
            select(CrmActivity.prescriber_id, func.min(CrmActivity.created_at))
            .where(
                CrmActivity.tenant_id == tenant,
                CrmActivity.kind.in_(["call", "visit", "email"]),
                CrmActivity.prescriber_id.in_(declining or {""}),
            )
            .group_by(CrmActivity.prescriber_id)
        )
    ).all()
    measurable: list[tuple[str, datetime]] = []
    for pid, ts in contacted:
        first = as_utc(ts)
        if first is not None and first.date() <= today - timedelta(days=60):
            measurable.append((pid, first))
    reactivated = 0
    for pid, first in measurable:
        fills = await connector.fetch("prescriber_fills", prescriber_id=pid, start=first.date() - timedelta(days=60))
        before = sum(1 for f in fills if to_date(f["FILL_DATE"]) < first.date())
        after = sum(1 for f in fills if first.date() <= to_date(f["FILL_DATE"]) < first.date() + timedelta(days=60))
        reactivated += after > before

    decided = dict(
        (
            await db.execute(
                select(AiInteraction.disposition, func.count())
                .where(AiInteraction.tenant_id == tenant)
                .group_by(AiInteraction.disposition)
            )
        ).all()
    )
    accepted, edited, discarded = decided.get("accepted", 0), decided.get("edited", 0), decided.get("discarded", 0)
    reviewed = accepted + edited + discarded

    def series(values: dict[date, float]) -> list[dict[str, Any]]:
        return [{"week": w.isoformat(), "value": round(values.get(w, 0.0), 2)} for w in week_keys]

    dtp = {w: float(statistics.median(v)) for w, v in by_week.items() if v}
    capture = {w: round(due_captured[w] / due_total[w] * 100, 1) for w in due_total if due_total[w]}
    last_full = [w for w in week_keys if w in capture]
    return {
        "weeks": [w.isoformat() for w in week_keys],
        "metrics": [
            {
                "key": "dollars_recovered",
                "label": "Dollars recovered",
                "unit": "usd",
                "value": round(sum(recovered.values()), 2),
                "value_label": f"last {weeks} weeks",
                "series": series(recovered),
                "source": "Work items resolved after a named approval whose claim was then paid (app DB).",
            },
            {
                "key": "days_to_payment",
                "label": "Days to payment",
                "unit": "days",
                "value": dtp.get(week_keys[-1]) or dtp.get(week_keys[-2]) or None,
                "value_label": "median, this week",
                "series": [{"week": w.isoformat(), "value": dtp.get(w)} for w in week_keys],
                "source": "Median days from fill to remittance, by remittance week (warehouse FACT_CLAIM).",
            },
            {
                "key": "refill_capture_rate",
                "label": "Refill capture",
                "unit": "pct",
                "value": capture[last_full[-1]] if last_full else None,
                "value_label": "latest measurable week",
                "series": [{"week": w.isoformat(), "value": capture.get(w)} for w in week_keys],
                "source": "Refills due with refills remaining, filled within 7 days of the due date (warehouse).",
            },
            {
                "key": "prescriber_reactivation",
                "label": "Prescriber reactivation",
                "unit": "count",
                "value": reactivated,
                "value_label": f"of {len(measurable)} measurable",
                "series": [],
                "source": f"{len(declining)} declining prescribers; {len(contacted)} contacted; measured 60 days "
                "after first contact (tracked from pilot week 8).",
            },
        ],
        "backlog": {"value": backlog or 0, "label": "Rejections older than 7 days, still open"},
        "ai_usefulness": {
            "accepted": accepted,
            "edited": edited,
            "discarded": discarded,
            "rate": round(accepted / reviewed * 100, 1) if reviewed else None,
        },
    }


@router.get("/search")
async def search(
    q: str = Query(min_length=2, max_length=64),
    principal: Principal = Depends(require("search")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """Top search across Rx number, patient ID and prescriber name; results respect the caller's role."""
    user = principal.user
    connector = get_connector()
    term = q.strip()
    results: dict[str, list[dict[str, Any]]] = {"rx": [], "patients": [], "prescribers": []}
    rx = term.lower().removeprefix("rx").strip().split("-")[0]
    if rx.isdigit() and has_permission(user, "claims.view"):
        fills = await connector.fetch("search_rx", rx_number=rx)
        items = {
            w.fill_id: w.id
            for w in (
                await db.scalars(select(WorkItem).where(WorkItem.tenant_id == user.tenant_id, WorkItem.rx_number == rx))
            ).all()
        }
        results["rx"] = [
            {
                "rx_label": f"Rx {f['RX_NUMBER']}-{int(f['FILL_NUMBER']):02d}",
                "fill_date": f["FILL_DATE"],
                "drug_name": f["DRUG_NAME"],
                "work_item_id": items.get(str(f["FILL_ID"])),
            }
            for f in fills
        ]
    if re.fullmatch(r"(?i)pt\d{3,8}", term) and has_permission(user, "patients.phi"):
        pid = term.upper()
        if await connector.fetch("patient_exists", patient_id=pid):
            results["patients"] = [{"patient": (await masked_patients(connector, [pid]))[pid]}]
    if not rx.isdigit() and has_permission(user, "prescribers.view"):
        rows = await connector.fetch("search_prescribers", pattern=f"%{term.lower()}%")
        results["prescribers"] = [
            {"id": r["PRESCRIBER_ID"], "name": prescriber_name(r), "practice": r["PRACTICE_NAME"]} for r in rows
        ]
    return results


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=300)


SUPPORTED_QUESTIONS = [
    "Which payers rejected most this month?",
    "Which payers rejected most last month?",
    "How many days to payment this month?",
]


@router.post("/ask")
async def ask(
    body: AskIn, principal: Principal = Depends(require("claims.view")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    """Plain-English questions answered only through approved named queries — never generated SQL.

    The pilot maps a question to an intent deterministically; phase 2 lets the model pick the named query and its
    parameters, still validated by the same connector registry."""
    text = body.question.lower()
    today = clock.today()
    month_start = today.replace(day=1)
    if "last month" in text:
        end = month_start
        start = (month_start - timedelta(days=1)).replace(day=1)
        period = f"{start:%B %Y}"
    else:
        start, end, period = month_start, today + timedelta(days=1), f"{month_start:%B %Y} to date"
    answer: dict[str, Any]
    if "payer" in text and "reject" in text:
        rows = await get_connector().fetch(
            "rejections_by_payer",
            start=datetime.combine(start, datetime.min.time()).isoformat(),
            end=datetime.combine(end, datetime.min.time()).isoformat(),
        )
        top = rows[0] if rows else None
        answer = {
            "answer": (
                f"{top['PAYER_NAME']} rejected the most claims in {period}: {top['REJECTIONS']} "
                f"(${float(top['AMOUNT'] or 0):,.0f} billed)."
            )
            if top
            else f"No rejections in {period}.",
            "chart": {
                "type": "bar",
                "x": "Payer",
                "y": "Rejected claims",
                "data": [{"label": r["PAYER_NAME"], "value": int(r["REJECTIONS"])} for r in rows],
            },
            "query": "rejections_by_payer",
            "params": {"start": start.isoformat(), "end": end.isoformat()},
        }
    elif "days to payment" in text or ("pay" in text and "days" in text):
        rows = await get_connector().fetch("payment_facts", start=start - timedelta(days=90))
        days = []
        for r in rows:
            remitted = to_datetime(r["REMITTED_AT"])
            if remitted is not None and start <= remitted.date() < end:
                days.append((remitted.date() - to_date(r["FILL_DATE"])).days)
        med = statistics.median(days) if days else None
        answer = {
            "answer": f"Median days to payment for remittances in {period}: {med:.0f} days ({len(days)} paid claims)."
            if med is not None
            else f"No remittances in {period}.",
            "chart": None,
            "query": "payment_facts",
            "params": {"start": start.isoformat()},
        }
    else:
        return {
            "answer": None,
            "supported_questions": SUPPORTED_QUESTIONS,
            "message": "I can only answer questions that map to an approved query. Try one of these.",
        }
    async with audit.audit_transaction(db):
        await audit.record(
            db, principal.actor, "insights.ask", "named_query", answer["query"], detail={"params": answer["params"]}
        )
    answer["source"] = f"Approved named query '{answer['query']}' (connector registry), read-only."
    _ = await payer_names(get_connector())
    return answer
