"""Prescriber CRM (PR-01 … PR-06). Responses carry prescriber data and aggregate counts only — never patient fields
(C-05, API3): the response models below have no field that could hold one."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from levigate import ai, audit, catalog, clock
from levigate.api.deps import Principal, db_session, require
from levigate.config import get_settings
from levigate.connectors import get_connector
from levigate.models import CrmActivity, PrescriberOwner, as_utc
from levigate.rules import score_prescribers, to_date
from levigate.services import prescriber_name, user_names

router = APIRouter(prefix="/api/prescribers", tags=["prescribers"])

SEGMENTS = ("top", "declining", "new", "dormant", "all")


class ScoreParts(BaseModel):
    volume: float
    revenue: float
    trend: float


class PrescriberRow(BaseModel):
    id: str
    name: str
    specialty: str
    practice: str
    city: str
    rx_90: int
    rx_prior_90: int
    revenue_90: float
    trend: float
    score: float
    score_parts: ScoreParts
    segments: list[str]
    owner: str | None
    last_contact: str | None
    rejected_claims_90: int


class Activity(BaseModel):
    id: int
    kind: str
    outcome: str | None
    notes: str | None
    owner: str | None
    created_at: str
    next_action: str | None
    next_action_due: str | None
    next_action_done: bool


class MonthPoint(BaseModel):
    month: str
    rx: int
    revenue: float


class RejectReason(BaseModel):
    code: str
    reason: str
    count: int
    amount: float


class PrescriberProfile(BaseModel):
    id: str
    npi: str
    name: str
    credential: str | None
    specialty: str
    practice: str
    phone: str | None
    fax: str | None
    email: str | None
    city: str
    state: str
    stats: PrescriberRow
    monthly: list[MonthPoint]
    reject_reasons: list[RejectReason]
    activities: list[Activity]
    next_action: Activity | None
    formula: str


async def _scored(db: AsyncSession, tenant_id: str, today: date) -> tuple[list[dict[str, Any]], str]:
    connector = get_connector()
    rows = await connector.fetch(
        "prescriber_stats", cur_start=today - timedelta(days=90), prior_start=today - timedelta(days=180)
    )
    entry = await catalog.current(db, tenant_id, "prescriber_scoring", "default")
    body = entry.body if entry else {"weights": {"volume": 0.5, "revenue": 0.35, "trend": 0.15}}
    scored = score_prescribers(rows, body, today)
    w = body["weights"]
    formula = (
        f"Score = {round(w['volume'] * 100)}% volume + {round(w['revenue'] * 100)}% revenue + "
        f"{round(w['trend'] * 100)}% trend (90 days vs prior 90). "
        f"Scoring rule v{entry.version if entry else 1}."
    )
    return scored, formula


async def _context(db: AsyncSession, tenant_id: str, today: date) -> tuple[dict, dict, dict]:
    owners = {
        o.prescriber_id: o.owner_id
        for o in (await db.scalars(select(PrescriberOwner).where(PrescriberOwner.tenant_id == tenant_id))).all()
    }
    last = dict(
        (
            await db.execute(
                select(CrmActivity.prescriber_id, func.max(CrmActivity.created_at))
                .where(CrmActivity.tenant_id == tenant_id, CrmActivity.kind.in_(["call", "visit", "email"]))
                .group_by(CrmActivity.prescriber_id)
            )
        ).all()
    )
    rejects = {
        str(r["PRESCRIBER_ID"]): int(r["N"])
        for r in await get_connector().fetch("reject_counts_by_prescriber", start=today - timedelta(days=90))
    }
    return owners, last, rejects


def _row(p: dict[str, Any], owners: dict, names: dict, last: dict, rejects: dict) -> PrescriberRow:
    segs = [
        s
        for s, flag in (("top", "IS_TOP"), ("declining", "IS_DECLINING"), ("new", "IS_NEW"), ("dormant", "IS_DORMANT"))
        if p.get(flag)
    ]
    lc = as_utc(last.get(p["PRESCRIBER_ID"]))
    return PrescriberRow(
        id=p["PRESCRIBER_ID"],
        name=prescriber_name(p),
        specialty=p["SPECIALTY"],
        practice=p["PRACTICE_NAME"],
        city=f"{p['CITY']}, {p['STATE']}",
        rx_90=int(p["RX_90"] or 0),
        rx_prior_90=int(p["RX_PRIOR_90"] or 0),
        revenue_90=round(float(p["REV_90"] or 0), 2),
        trend=p["TREND"],
        score=p["SCORE"],
        score_parts=ScoreParts(**p["SCORE_PARTS"]),
        segments=segs,
        owner=names.get(owners.get(p["PRESCRIBER_ID"], "")),
        last_contact=lc.date().isoformat() if lc else None,
        rejected_claims_90=rejects.get(p["PRESCRIBER_ID"], 0),
    )


@router.get("")
async def ranked(
    segment: str = Query("top"),
    q: str | None = Query(None, max_length=64),
    principal: Principal = Depends(require("prescribers.view")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    if segment not in SEGMENTS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown segment.")
    today = clock.today()
    scored, formula = await _scored(db, principal.user.tenant_id, today)
    owners, last, rejects = await _context(db, principal.user.tenant_id, today)
    names = await user_names(db, owners.values())
    counts = {s: sum(1 for p in scored if p.get(f"IS_{s.upper()}")) for s in SEGMENTS if s != "all"}
    counts["all"] = len(scored)
    if segment != "all":
        scored = [p for p in scored if p.get(f"IS_{segment.upper()}")]
    if q:
        needle = q.lower()
        scored = [p for p in scored if needle in prescriber_name(p).lower() or needle in p["PRACTICE_NAME"].lower()]
    if segment == "declining":
        scored.sort(key=lambda p: (p["TREND"], str(p["PRESCRIBER_ID"])))
    else:
        scored.sort(key=lambda p: (-p["SCORE"], str(p["PRESCRIBER_ID"])))
    rows = [_row(p, owners, names, last, rejects) for p in scored[:200]]
    return {"segment": segment, "counts": counts, "formula": formula, "rows": [r.model_dump() for r in rows]}


@router.get("/{prescriber_id}", response_model=PrescriberProfile)
async def profile(
    prescriber_id: str,
    principal: Principal = Depends(require("prescribers.view")),
    db: AsyncSession = Depends(db_session),
) -> PrescriberProfile:
    today = clock.today()
    connector = get_connector()
    detail = await connector.fetch("prescriber_detail", prescriber_id=prescriber_id)
    if not detail:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prescriber not found.")
    d = detail[0]
    scored, formula = await _scored(db, principal.user.tenant_id, today)
    p = next(x for x in scored if x["PRESCRIBER_ID"] == prescriber_id)
    owners, last, rejects = await _context(db, principal.user.tenant_id, today)
    acts = (
        await db.scalars(
            select(CrmActivity)
            .where(
                CrmActivity.tenant_id == principal.user.tenant_id,
                CrmActivity.prescriber_id == prescriber_id,
            )
            .order_by(CrmActivity.created_at.desc())
        )
    ).all()
    names = await user_names(db, [*owners.values(), *(a.owner_id for a in acts)])

    months: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    start = (today.replace(day=1) - timedelta(days=150)).replace(day=1)
    for f in await connector.fetch("prescriber_fills", prescriber_id=prescriber_id, start=start):
        key = to_date(f["FILL_DATE"]).strftime("%Y-%m")
        months[key][0] += 1
        months[key][1] += float(f["EXPECTED_AMOUNT"] or 0)
    series = []
    cursor = start
    while cursor <= today:
        key = cursor.strftime("%Y-%m")
        series.append(MonthPoint(month=key, rx=int(months[key][0]), revenue=round(months[key][1], 2)))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    reasons = [
        RejectReason(
            code=str(r["REJECT_CODE"]),
            reason=str(r["REJECT_MESSAGE"]),
            count=int(r["N"]),
            amount=round(float(r["AMOUNT"] or 0), 2),
        )
        for r in await connector.fetch(
            "prescriber_rejects", prescriber_id=prescriber_id, start=today - timedelta(days=90)
        )
    ]

    def act(a: CrmActivity) -> Activity:
        return Activity(
            id=a.id,
            kind=a.kind,
            outcome=a.outcome,
            notes=a.notes,
            owner=names.get(a.owner_id),
            created_at=as_utc(a.created_at).isoformat(),
            next_action=a.next_action,
            next_action_due=a.next_action_due.isoformat() if a.next_action_due else None,
            next_action_done=a.next_action_done,
        )

    pending = [a for a in acts if a.next_action and not a.next_action_done]
    pending.sort(key=lambda a: a.next_action_due or date.max)
    async with audit.audit_transaction(db):
        await audit.record(db, principal.actor, "prescriber.view", "prescriber", prescriber_id)
    return PrescriberProfile(
        id=prescriber_id,
        npi=d["NPI"],
        name=prescriber_name(d),
        credential=d.get("CREDENTIAL"),
        specialty=d["SPECIALTY"],
        practice=d["PRACTICE_NAME"],
        phone=d.get("PHONE"),
        fax=d.get("FAX"),
        email=d.get("EMAIL"),
        city=d["CITY"],
        state=d["STATE"],
        stats=_row(p, owners, names, last, rejects),
        monthly=series,
        reject_reasons=reasons,
        activities=[act(a) for a in acts],
        next_action=act(pending[0]) if pending else None,
        formula=formula,
    )


class ActivityIn(BaseModel):
    kind: Literal["call", "visit", "email", "note"]
    outcome: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)
    next_action: str | None = Field(default=None, max_length=255)
    next_action_due: date | None = None
    campaign: str | None = Field(default=None, max_length=96)
    draft_interaction_id: int | None = None
    draft_disposition: Literal["accepted", "edited", "discarded"] | None = None


@router.post("/{prescriber_id}/activities")
async def log_activity(
    prescriber_id: str,
    body: ActivityIn,
    principal: Principal = Depends(require("crm.write")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    if not await get_connector().fetch("prescriber_detail", prescriber_id=prescriber_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Prescriber not found.")
    if body.next_action and not body.next_action_due:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Add a due date for the next action.")
    async with audit.audit_transaction(db):
        # Completing a logged contact closes the open next action it answered.
        for prior in (
            await db.scalars(
                select(CrmActivity).where(
                    CrmActivity.tenant_id == principal.user.tenant_id,
                    CrmActivity.prescriber_id == prescriber_id,
                    CrmActivity.next_action_done.is_(False),
                    CrmActivity.owner_id == principal.user.id,
                )
            )
        ).all():
            if body.kind != "note":
                prior.next_action_done = True
        a = CrmActivity(
            tenant_id=principal.user.tenant_id,
            prescriber_id=prescriber_id,
            kind=body.kind,
            outcome=body.outcome,
            notes=body.notes,
            owner_id=principal.user.id,
            next_action=body.next_action,
            next_action_due=body.next_action_due,
            campaign=body.campaign,
        )
        db.add(a)
        await db.flush()
        if body.draft_interaction_id and body.draft_disposition:
            await ai.set_disposition(db, principal.user, body.draft_interaction_id, body.draft_disposition)
        await audit.record(
            db,
            principal.actor,
            "crm.activity",
            "prescriber",
            prescriber_id,
            detail={"activity_id": a.id, "kind": body.kind},
        )
    return {"ok": True, "id": a.id}


class DraftIn(BaseModel):
    kind: Literal["prescriber_email", "visit_brief"]


@router.post("/{prescriber_id}/draft")
async def draft(
    prescriber_id: str,
    body: DraftIn,
    principal: Principal = Depends(require("crm.write")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    prof = await profile(prescriber_id, principal, db)
    s = prof.stats
    reasons = [{"reason": r.reason, "count": r.count} for r in prof.reject_reasons[:3]]
    fields: dict[str, Any] = {
        "prescriber_name": prof.name,
        "practice_name": prof.practice,
        "specialty": prof.specialty,
        "rx_90": s.rx_90,
        "rx_prior_90": s.rx_prior_90,
        "trend_pct": s.trend,
        "top_reject_reasons": reasons,
    }
    if body.kind == "prescriber_email":
        fields.update(pharmacy_name=get_settings().pharmacy_name, rep_name=principal.user.name)
    else:
        fields.update(revenue_90=s.revenue_90, last_contact=s.last_contact, segment=", ".join(s.segments) or "steady")
    async with audit.audit_transaction(db):
        try:
            d = await ai.draft(
                db,
                principal.user,
                principal.actor,
                body.kind,
                fields,
                entity_type="prescriber",
                entity_id=prescriber_id,
            )
        except ai.AiPolicyError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except ai.AiBudgetError as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
    return {
        "text": d.text,
        "interaction_id": d.interaction_id,
        "provider": d.provider,
        "model": d.model,
        "fallback_reason": d.fallback_reason,
        "fields_sent": d.fields_sent,
        "label": "AI draft",
        "sources": ["Prescriber stats (90 days, aggregate)", "Rejected-claim counts (aggregate)"],
    }
