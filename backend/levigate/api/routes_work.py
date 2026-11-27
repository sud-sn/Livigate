"""My queue, claim work items (approve / follow up), the claims tracker and claims export."""

from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from levigate import ai, audit, catalog, clock
from levigate.api.deps import Principal, db_session, page_size, require
from levigate.config import get_settings
from levigate.connectors import get_connector
from levigate.models import (
    OPEN_STATUSES,
    Approval,
    CatalogEntry,
    CrmActivity,
    OutreachContact,
    Role,
    WorkItem,
    WorkItemEvent,
    WorkItemStatus,
    WorkItemType,
    as_utc,
    utcnow,
)
from levigate.policy import QUEUE_ACCESS, can_act_on_work_item, can_view_work_item
from levigate.rules import CLAIM_STATUSES, PayerRules, derive_status, fmt_day
from levigate.services import masked_patients, payer_names, user_names

router = APIRouter(prefix="/api", tags=["work"])

TYPE_LABELS = {
    WorkItemType.CLAIM_REJECTED: "Rejected claim",
    WorkItemType.CLAIM_UNPAID: "Unpaid claim",
    WorkItemType.CLAIM_UNDERPAID: "Underpaid claim",
    WorkItemType.REJECT_REVIEW: "Unknown reject code",
}


def _local_time(ts: datetime | None) -> str:
    ts = as_utc(ts)
    if ts is None:
        return ""
    return ts.astimezone(ZoneInfo(get_settings().timezone)).strftime("%-I:%M %p")


def _effective_due(item: WorkItem) -> date:
    if item.status == WorkItemStatus.AWAITING_FOLLOWUP and item.next_followup_date:
        return item.next_followup_date
    return item.due_date


def _visible_queues(principal: Principal) -> frozenset[str]:
    return QUEUE_ACCESS.get(Role(principal.user.role), frozenset())


def _actionable_filter(tenant_id: str, queues: frozenset[str], today: date) -> Any:
    return and_(
        WorkItem.tenant_id == tenant_id,
        WorkItem.queue_role.in_(queues),
        or_(
            WorkItem.status == WorkItemStatus.OPEN,
            and_(WorkItem.status == WorkItemStatus.AWAITING_FOLLOWUP, WorkItem.next_followup_date <= today),
        ),
    )


async def _claims_by_fill(fill_ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(fill_ids), 500):
        for r in await get_connector().fetch("claims_for_fills", fill_ids=fill_ids[i : i + 500]):
            out[str(r["FILL_ID"])] = r
    return out


def _summary(
    item: WorkItem,
    today: date,
    payers: dict[str, str],
    patients: dict[str, dict],
    claims: dict[str, dict],
    names: dict[str, str],
) -> dict[str, Any]:
    fix = item.suggested_fix or {}
    due = _effective_due(item)
    claim = claims.get(item.fill_id, {})
    if item.type == WorkItemType.REJECT_REVIEW:
        headline = "Code not in playbook — review needed"
    elif item.status == WorkItemStatus.AWAITING_FOLLOWUP:
        headline = f"Follow-up due: {fix.get('summary', 'check status')}"
    else:
        headline = fix.get("summary", "")
    return {
        "id": item.id,
        "type": item.type,
        "type_label": TYPE_LABELS.get(WorkItemType(item.type), item.type),
        "status": item.status,
        "queue_role": item.queue_role,
        "rx_number": item.rx_number,
        "fill_number": item.fill_number,
        "rx_label": f"Rx {item.rx_number}-{item.fill_number:02d}",
        "reject_code": item.reject_code,
        "reject_message": claim.get("REJECT_MESSAGE"),
        "payer_id": item.payer_id,
        "payer_name": payers.get(item.payer_id, item.payer_id),
        "drug_name": claim.get("DRUG_NAME"),
        "amount_at_risk": float(item.amount_at_risk),
        "due_date": due.isoformat(),
        "days_overdue": max(0, (today - due).days),
        "headline": headline,
        "patient": patients.get(item.patient_id),
        "assignee": names.get(item.assignee_id or ""),
        "source_status": item.source_status,
        "location": item.location,
        "created_at": as_utc(item.created_at).isoformat() if item.created_at else None,
    }


async def _summaries(db: AsyncSession, items: list[WorkItem], today: date) -> list[dict[str, Any]]:
    if not items:
        return []
    connector = get_connector()
    payers = await payer_names(connector)
    patients = await masked_patients(connector, [i.patient_id for i in items])
    claims = await _claims_by_fill(sorted({i.fill_id for i in items}))
    names = await user_names(db, [i.assignee_id for i in items])
    return [_summary(i, today, payers, patients, claims, names) for i in items]


async def _queue_items(
    db: AsyncSession,
    principal: Principal,
    today: date,
    *,
    item_type: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[WorkItem], Counter, int]:
    """Actionable items for the caller's queues, overdue first, then soonest due, then largest amount.

    Counts are always for the whole queue; ``item_type`` and paging apply to the returned page only.
    """
    queues = _visible_queues(principal)
    flt = _actionable_filter(principal.user.tenant_id, queues, today)
    counts = Counter(
        dict((await db.execute(select(WorkItem.type, func.count()).where(flt).group_by(WorkItem.type))).all())
    )
    q = select(WorkItem).where(flt)
    total = sum(counts.values())
    if item_type:
        q = q.where(WorkItem.type == item_type)
        total = counts.get(item_type, 0)
    page = (await db.scalars(q.order_by(*_queue_order(today)).offset(offset).limit(limit))).all()
    return list(page), counts, total


def _queue_order(today: date) -> tuple[Any, ...]:
    """Urgency, then dollars: overdue items first (largest amount first), then soonest due, then largest amount.
    Equal amounts go longest-waiting first, then by Rx and fill number.

    Sorted in the database so a large queue only loads the page it shows. The due date is the follow-up date for
    items waiting on a follow-up, else the item's own due date."""
    due = case(
        (
            and_(WorkItem.status == WorkItemStatus.AWAITING_FOLLOWUP, WorkItem.next_followup_date.is_not(None)),
            WorkItem.next_followup_date,
        ),
        else_=WorkItem.due_date,
    )
    overdue = due < today
    return (
        case((overdue, 0), else_=1),
        case((overdue, today), else_=due),
        WorkItem.amount_at_risk.desc(),
        # Ties: the longest-waiting first, then business keys, so the order never depends on load order.
        due,
        WorkItem.rx_number,
        WorkItem.fill_number,
        WorkItem.type,
        WorkItem.id,
    )


def _week_start(today: date) -> date:
    return today - timedelta(days=today.weekday())


# ---------------------------------------------------------------------------------------------------------------
# My queue
# ---------------------------------------------------------------------------------------------------------------


@router.get("/queue")
async def my_queue(
    item_type: str | None = Query(None, alias="type", max_length=32),
    page: int = Query(1, ge=1),
    limit: int = 50,
    principal: Principal = Depends(require("queue.view")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    today = clock.today()
    user = principal.user
    role = Role(user.role)
    goals = await catalog.current(db, user.tenant_id, "team_goal", "weekly")
    goal_body = goals.body if goals else {}
    week_start = _week_start(today)
    tz = ZoneInfo(get_settings().timezone)
    # Midnight in the pharmacy's time zone (an aware datetime), compared against UTC timestamps in the DB.
    week_start_dt = datetime.combine(week_start, datetime.min.time(), tzinfo=tz)
    day_start_dt = datetime.combine(today, datetime.min.time(), tzinfo=tz)
    result: dict[str, Any] = {"role": role.value, "today": today.isoformat()}

    if role in (Role.BILLING, Role.PHARMACIST, Role.ADMIN):
        if item_type and item_type not in {t.value for t in WorkItemType}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown item type.")
        size = page_size(limit)
        items, counts, filtered_total = await _queue_items(
            db, principal, today, item_type=item_type, offset=(page - 1) * size, limit=size
        )
        total = sum(counts.values())
        overdue = await db.scalar(
            select(func.count())
            .select_from(WorkItem)
            .where(
                _actionable_filter(user.tenant_id, _visible_queues(principal), today),
                or_(
                    and_(WorkItem.status == WorkItemStatus.OPEN, WorkItem.due_date < today),
                    and_(WorkItem.status == WorkItemStatus.AWAITING_FOLLOWUP, WorkItem.next_followup_date < today),
                ),
            )
        )
        summaries = await _summaries(db, items, today)
        at_risk = await db.scalar(
            select(func.coalesce(func.sum(WorkItem.amount_at_risk), 0)).where(
                _actionable_filter(user.tenant_id, _visible_queues(principal), today)
            )
        )
        approvals_today = await db.scalar(
            select(func.count())
            .select_from(Approval)
            .where(Approval.approver_id == user.id, Approval.approved_at >= day_start_dt)
        )
        recovered_week = await db.scalar(
            select(func.coalesce(func.sum(WorkItem.recovered_amount), 0)).where(
                WorkItem.tenant_id == user.tenant_id, WorkItem.resolved_at >= week_start_dt
            )
        )
        recovered_today = await db.scalar(
            select(func.coalesce(func.sum(WorkItem.recovered_amount), 0)).where(
                WorkItem.tenant_id == user.tenant_id, WorkItem.resolved_at >= day_start_dt
            )
        )
        result.update(
            {
                "kind": "claims",
                "total": total,
                "dollars_at_risk": float(at_risk or 0),
                "overdue": overdue or 0,
                "filter": item_type,
                "filtered_total": filtered_total,
                "page": page,
                "page_size": size,
                "counts": [
                    {"type": t, "label": TYPE_LABELS[WorkItemType(t)], "count": counts.get(t, 0)} for t in WorkItemType
                ],
                "items": summaries,
                "wins": {"approvals_today": approvals_today or 0, "recovered_today": float(recovered_today or 0)},
                "goal": {
                    "label": "Recovered this week",
                    "unit": "dollars",
                    "progress": float(recovered_week or 0),
                    "target": float(goal_body.get("billing_recovered_dollars", 0)),
                },
            }
        )
    elif role == Role.OUTREACH:
        pending = await db.scalar(
            select(OutreachContact).where(OutreachContact.user_id == user.id, OutreachContact.outcome.is_(None))
        )
        calls_today = await db.scalar(
            select(func.count())
            .select_from(OutreachContact)
            .where(OutreachContact.user_id == user.id, OutreachContact.started_at >= day_start_dt)
        )
        confirmed_week = await db.scalar(
            select(func.count())
            .select_from(OutreachContact)
            .where(
                OutreachContact.tenant_id == user.tenant_id,
                OutreachContact.outcome == "refill_confirmed",
                OutreachContact.logged_at >= week_start_dt,
            )
        )
        confirmed_today = await db.scalar(
            select(func.count())
            .select_from(OutreachContact)
            .where(
                OutreachContact.user_id == user.id,
                OutreachContact.outcome == "refill_confirmed",
                OutreachContact.logged_at >= day_start_dt,
            )
        )
        result.update(
            {
                "kind": "outreach",
                "pending_call_id": pending.id if pending else None,
                "wins": {"calls_today": calls_today or 0, "refills_confirmed_today": confirmed_today or 0},
                "goal": {
                    "label": "Refills confirmed this week",
                    "unit": "count",
                    "progress": confirmed_week or 0,
                    "target": goal_body.get("outreach_refills_confirmed", 0),
                },
            }
        )
    elif role == Role.MARKETING:
        due = (
            await db.scalars(
                select(CrmActivity)
                .where(
                    CrmActivity.tenant_id == user.tenant_id,
                    CrmActivity.owner_id == user.id,
                    CrmActivity.next_action_done.is_(False),
                    CrmActivity.next_action.is_not(None),
                    CrmActivity.next_action_due <= today + timedelta(days=7),
                )
                .order_by(CrmActivity.next_action_due)
            )
        ).all()
        contacts_week = await db.scalar(
            select(func.count())
            .select_from(CrmActivity)
            .where(
                CrmActivity.owner_id == user.id,
                CrmActivity.created_at >= week_start_dt,
                CrmActivity.kind.in_(["call", "visit", "email"]),
            )
        )
        contacts_today = await db.scalar(
            select(func.count())
            .select_from(CrmActivity)
            .where(
                CrmActivity.owner_id == user.id,
                CrmActivity.created_at >= day_start_dt,
                CrmActivity.kind.in_(["call", "visit", "email"]),
            )
        )
        result.update(
            {
                "kind": "crm",
                "tasks": [
                    {
                        "id": a.id,
                        "prescriber_id": a.prescriber_id,
                        "next_action": a.next_action,
                        "due_date": a.next_action_due.isoformat() if a.next_action_due else None,
                        "overdue": bool(a.next_action_due and a.next_action_due < today),
                    }
                    for a in due
                ],
                "wins": {"contacts_today": contacts_today or 0},
                "goal": {
                    "label": "Prescriber contacts this week",
                    "unit": "count",
                    "progress": contacts_week or 0,
                    "target": goal_body.get("marketing_contacts", 0),
                },
            }
        )
    return result


# ---------------------------------------------------------------------------------------------------------------
# Work items
# ---------------------------------------------------------------------------------------------------------------


async def _load_item(db: AsyncSession, principal: Principal, item_id: int) -> WorkItem:
    item = await db.get(WorkItem, item_id)
    # API1: same 404 whether the item does not exist or belongs to someone this caller may not see.
    if item is None or not can_view_work_item(principal.user, item):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Work item not found.")
    return item


async def _rule_entry(db: AsyncSession, item: WorkItem) -> CatalogEntry | None:
    if not item.rule_kind or not item.rule_key or item.rule_version is None:
        return None
    return await db.scalar(
        select(CatalogEntry).where(
            CatalogEntry.tenant_id == item.tenant_id,
            CatalogEntry.kind == item.rule_kind,
            CatalogEntry.key == item.rule_key,
            CatalogEntry.version == item.rule_version,
        )
    )


async def _next_item_id(db: AsyncSession, principal: Principal, after: WorkItem, today: date) -> int | None:
    items, _, _ = await _queue_items(db, principal, today, limit=2)
    for i in items:
        if i.id != after.id:
            return i.id
    return None


@router.get("/work-items/{item_id}")
async def work_item_detail(
    item_id: int, principal: Principal = Depends(require("claims.view")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    today = clock.today()
    item = await _load_item(db, principal, item_id)
    connector = get_connector()
    summary = (await _summaries(db, [item], today))[0]
    claims = await _claims_by_fill([item.fill_id])
    claim = claims.get(item.fill_id, {})
    history = await connector.fetch("claim_history", fill_id=item.fill_id)
    prescriber = (await connector.fetch("prescriber_detail", prescriber_id=item.prescriber_id) or [{}])[0]
    rule = await _rule_entry(db, item)
    events = (
        await db.scalars(select(WorkItemEvent).where(WorkItemEvent.work_item_id == item.id).order_by(WorkItemEvent.ts))
    ).all()
    approvals = (
        await db.scalars(
            select(Approval)
            .where(Approval.subject_type == "work_item", Approval.subject_id == str(item.id))
            .order_by(Approval.approved_at)
        )
    ).all()
    names = await user_names(db, [e.actor_id for e in events] + [a.approver_id for a in approvals])

    timeline: list[dict[str, Any]] = [
        {
            "ts": as_utc(e.ts).isoformat(),
            "kind": e.kind,
            "summary": e.summary,
            "actor": names.get(e.actor_id, e.actor_id),
            "source": "Levigate",
        }
        for e in events
    ]
    for h in history:
        label = {
            "R": f"Rejected {h['REJECT_CODE']} — {h['REJECT_MESSAGE']}",
            "P": "Paid (remitted)",
            "C": "Accepted by payer (captured)",
            "X": "Reversed",
        }.get(h["RESPONSE_STATUS"], "Submitted")
        timeline.append(
            {
                "ts": str(h["SUBMITTED_AT"]),
                "kind": "claim_submission",
                "summary": f"Claim {h['CLAIM_ID']} submitted · {label}",
                "actor": None,
                "source": "Warehouse · FACT_CLAIM",
            }
        )
        if h.get("REMITTED_AT"):
            timeline.append(
                {
                    "ts": str(h["REMITTED_AT"]),
                    "kind": "payment",
                    "summary": f"Remittance ${float(h['PAID_AMOUNT'] or 0):,.2f}",
                    "actor": None,
                    "source": "Warehouse · FACT_CLAIM",
                }
            )
    timeline.sort(key=lambda t: t["ts"])

    async with audit.audit_transaction(db):
        await audit.record(db, principal.actor, "work_item.view", "work_item", item.id, patient_id=item.patient_id)

    return {
        **summary,
        "fill_id": item.fill_id,
        "fill_date": item.fill_date.isoformat(),
        "claim": {
            "claim_id": item.claim_id,
            "billed": float(claim.get("BILLED_AMOUNT") or 0),
            "expected": float(claim.get("EXPECTED_AMOUNT") or 0),
            "paid": float(claim["PAID_AMOUNT"]) if claim.get("PAID_AMOUNT") is not None else None,
            "days_supply": claim.get("DAYS_SUPPLY"),
            "quantity": claim.get("QUANTITY"),
            "submitted_at": str(claim.get("SUBMITTED_AT") or ""),
        },
        "prescriber": {
            "id": item.prescriber_id,
            "npi": prescriber.get("NPI"),
            "name": f"{prescriber.get('FIRST_NAME', '')} {prescriber.get('LAST_NAME', '')}".strip(),
            "practice": prescriber.get("PRACTICE_NAME"),
        },
        "rule": None
        if rule is None
        else {
            "kind": rule.kind,
            "key": rule.key,
            "version": rule.version,
            "title": rule.title,
            "rule_text": rule.body.get("rule_text")
            if rule.kind == "reject_playbook"
            else f"Payment window {rule.body.get('payment_window_days')} days; underpayment threshold "
            f"{round(float(rule.body.get('underpay_pct', 0)) * 100)}% and ${rule.body.get('underpay_min_amount')}",
            "is_current": rule.is_current,
        },
        "fix": item.suggested_fix,
        "ai_explanation": item.ai_explanation,
        "timeline": timeline,
        "approvals": [
            {
                "id": a.id,
                "action": a.action,
                "approver": names.get(a.approver_id, a.approver_id),
                "approved_at": as_utc(a.approved_at).isoformat(),
                "edited": a.edited,
                "note": a.note,
                "rule": f"rule {a.rule_key} v{a.rule_version}" if a.rule_key else None,
            }
            for a in approvals
        ],
        "attempts": item.attempts,
        "next_followup_date": item.next_followup_date.isoformat() if item.next_followup_date else None,
        "can_act": can_act_on_work_item(principal.user, item) and item.status in OPEN_STATUSES,
        "can_approve": can_act_on_work_item(principal.user, item) and item.status == WorkItemStatus.OPEN,
        "next_item_id": await _next_item_id(db, principal, item, today),
    }


@router.post("/work-items/{item_id}/explanation")
async def explain(
    item_id: int, principal: Principal = Depends(require("claims.view")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    item = await _load_item(db, principal, item_id)
    if item.type == WorkItemType.REJECT_REVIEW or not item.suggested_fix:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This code is not in the playbook, so there is no automated suggestion to explain.",
        )
    if item.ai_explanation:
        return item.ai_explanation
    claim = (await _claims_by_fill([item.fill_id])).get(item.fill_id, {})
    rule = await _rule_entry(db, item)
    payers = await payer_names(get_connector())
    fix = item.suggested_fix
    fields = {  # minimum necessary: no patient identity at all
        "rx_number": item.rx_number,
        "fill_number": item.fill_number,
        "reject_code": item.reject_code,
        "reject_message": claim.get("REJECT_MESSAGE"),
        "payer_name": payers.get(item.payer_id),
        "drug_name": claim.get("DRUG_NAME"),
        "rule_key": item.rule_key,
        "rule_version": item.rule_version,
        "rule_title": rule.title if rule else None,
        "rule_text": (rule.body.get("rule_text") if rule else None),
        "fix_summary": fix.get("summary"),
        "fix_steps": fix.get("steps"),
        "computed": fix.get("computed"),
    }
    async with audit.audit_transaction(db):
        try:
            d = await ai.draft(
                db,
                principal.user,
                principal.actor,
                "claim_explanation",
                fields,
                entity_type="work_item",
                entity_id=item.id,
                patient_id=item.patient_id,
            )
        except ai.AiPolicyError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
        except ai.AiBudgetError as exc:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc)) from exc
        item.ai_explanation = {
            "text": d.text,
            "interaction_id": d.interaction_id,
            "provider": d.provider,
            "model": d.model,
            "fallback_reason": d.fallback_reason,
            "fields_sent": d.fields_sent,
            "label": "AI draft",
            "sources": [f"Playbook rule {item.rule_key} v{item.rule_version}", "Claim response (warehouse)"],
            "generated_at": utcnow().isoformat(),
        }
    return item.ai_explanation


class ApproveIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    confirm: Literal[True]
    note: str | None = Field(default=None, max_length=2000)
    edited_summary: str | None = Field(default=None, max_length=300)
    ai_disposition: Literal["accepted", "edited", "discarded"] | None = None


@router.post("/work-items/{item_id}/approve")
async def approve(
    item_id: int,
    body: ApproveIn,
    principal: Principal = Depends(require("claims.approve")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """C-08 / IF-03: a named human approves; the response returns only after approval and audit are committed."""
    today = clock.today()
    item = await _load_item(db, principal, item_id)
    if not can_act_on_work_item(principal.user, item):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot approve this item.")

    prior = await db.scalar(select(Approval).where(Approval.idempotency_key == body.idempotency_key))
    if prior is not None:
        if prior.subject_id != str(item.id) or prior.approver_id != principal.user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This request was already used for another item.")
        return await _approval_response(db, principal, item, prior, today)
    if item.status != WorkItemStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, "This item was already approved.")
    is_review = item.type == WorkItemType.REJECT_REVIEW
    if is_review and not (body.note and body.note.strip()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Add a note saying how this code was handled before closing the review.",
        )

    fix = dict(item.suggested_fix or {})
    edited = bool(body.edited_summary and body.edited_summary != fix.get("summary"))
    async with audit.audit_transaction(db):
        approval = Approval(
            tenant_id=item.tenant_id,
            subject_type="work_item",
            subject_id=str(item.id),
            action="review_resolved" if is_review else fix.get("action", "approve"),
            approver_id=principal.user.id,
            rule_key=item.rule_key,
            rule_version=item.rule_version,
            edited=edited,
            note=body.note,
            idempotency_key=body.idempotency_key,
            detail={"summary": body.edited_summary if edited else fix.get("summary"), "due_date": fix.get("due_date")},
        )
        db.add(approval)
        if is_review:
            item.status, item.outcome, item.resolved_at = WorkItemStatus.RESOLVED, "handled_manually", utcnow()
        else:
            item.status, item.outcome = WorkItemStatus.AWAITING_FOLLOWUP, "fix_approved"
            item.next_followup_date = date.fromisoformat(fix["due_date"]) if fix.get("due_date") else today
            if edited:
                item.suggested_fix = {**fix, "summary": body.edited_summary, "edited_by_user": True}
        item.assignee_id = item.assignee_id or principal.user.id
        await db.flush()
        db.add(
            WorkItemEvent(
                work_item_id=item.id,
                actor_id=principal.user.id,
                kind="approved",
                summary=("Review closed: " + (body.note or ""))
                if is_review
                else f"Approved: {body.edited_summary if edited else fix.get('summary')}",
                detail={"approval_id": approval.id, "rule_key": item.rule_key, "rule_version": item.rule_version},
            )
        )
        if body.ai_disposition and item.ai_explanation:
            await ai.set_disposition(
                db, principal.user, int(item.ai_explanation["interaction_id"]), body.ai_disposition
            )
        event = await audit.record(
            db,
            principal.actor,
            "work_item.approve",
            "work_item",
            item.id,
            patient_id=item.patient_id,
            detail={
                "approval_id": approval.id,
                "action": approval.action,
                "rule_key": item.rule_key,
                "rule_version": item.rule_version,
                "edited": edited,
            },
        )
        approval.detail = {**approval.detail, "audit_seq": event.seq}
    return await _approval_response(db, principal, item, approval, today)


async def _approval_response(
    db: AsyncSession, principal: Principal, item: WorkItem, approval: Approval, today: date
) -> dict[str, Any]:
    names = await user_names(db, [approval.approver_id])
    approver = names.get(approval.approver_id, approval.approver_id)
    short = approver.split()[0][0] + ". " + approver.split()[-1] if " " in approver else approver
    rule = f"rule {approval.rule_key} v{approval.rule_version}" if approval.rule_key else "manual review"
    due = item.next_followup_date
    if item.type == WorkItemType.REJECT_REVIEW:
        message = "Review closed"
    elif approval.action == "resubmit_on_date" and due:
        message = f"Approved · resubmission set for {fmt_day(due)}"
    else:
        message = f"Approved · follow-up on {fmt_day(due)}" if due else "Approved"
    return {
        "ok": True,
        "message": message,
        "audit_line": f"Audit trail: approved by {short}, {rule}, {_local_time(approval.approved_at)}",
        "audit_seq": approval.detail.get("audit_seq"),
        "approval_id": approval.id,
        "status": item.status,
        "next_item_id": await _next_item_id(db, principal, item, today),
    }


class FollowupIn(BaseModel):
    party: Literal["plan", "prescriber"]
    outcome: str = Field(min_length=2, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    next_followup_date: date | None = None


@router.post("/work-items/{item_id}/followups")
async def log_followup(
    item_id: int,
    body: FollowupIn,
    principal: Principal = Depends(require("claims.followup")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    item = await _load_item(db, principal, item_id)
    if item.status not in OPEN_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "This item is closed.")
    async with audit.audit_transaction(db):
        item.attempts += 1
        if body.next_followup_date:
            item.next_followup_date = body.next_followup_date
        db.add(
            WorkItemEvent(
                work_item_id=item.id,
                actor_id=principal.user.id,
                kind="followup",
                summary=f"Follow-up with {body.party}: {body.outcome}",
                detail={
                    "attempt": item.attempts,
                    "next": body.next_followup_date.isoformat() if body.next_followup_date else None,
                    "note": body.note,
                },
            )
        )
        await audit.record(
            db,
            principal.actor,
            "work_item.followup",
            "work_item",
            item.id,
            patient_id=item.patient_id,
            detail={"party": body.party, "attempt": item.attempts},
        )
    return {
        "ok": True,
        "attempts": item.attempts,
        "next_followup_date": item.next_followup_date.isoformat() if item.next_followup_date else None,
    }


# ---------------------------------------------------------------------------------------------------------------
# Claims tracker (CT-01 … CT-04)
# ---------------------------------------------------------------------------------------------------------------


async def _tracker_rows(
    db: AsyncSession,
    tenant_id: str,
    start: date,
    end: date,
    payer_id: str | None,
    reject_code: str | None,
    location: str | None,
    min_amount: float | None,
    max_amount: float | None,
    rx_number: str | None,
) -> list[dict[str, Any]]:
    today = clock.today()
    rows = await get_connector().fetch(
        "claims_in_range",
        start=start,
        end=end,
        payer_id=payer_id,
        reject_code=reject_code,
        location=location,
        min_amount=min_amount,
        max_amount=max_amount,
        rx_number=rx_number,
    )
    payer_cfg = await catalog.current_map(db, tenant_id, "payer_config")
    for r in rows:
        cfg = payer_cfg.get(str(r["PAYER_ID"]))
        r["STATUS"] = derive_status(r, PayerRules.from_body(cfg.body if cfg else None), today)
    return rows


@router.get("/claims")
async def claims_tracker(
    start: date | None = None,
    end: date | None = None,
    payer_id: str | None = None,
    reject_code: str | None = Query(default=None, max_length=8),
    claim_status: str | None = Query(None, alias="status"),
    location: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    rx_number: str | None = Query(default=None, max_length=16),
    page: int = Query(1, ge=1),
    limit: int = 50,
    principal: Principal = Depends(require("claims.view")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    today = clock.today()
    end = end or today
    start = start or end - timedelta(days=30)
    if (end - start).days > 200:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a date range of 200 days or less.")
    if claim_status and claim_status not in CLAIM_STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown status filter.")
    rows = await _tracker_rows(
        db, principal.user.tenant_id, start, end, payer_id, reject_code, location, min_amount, max_amount, rx_number
    )
    counts = Counter(r["STATUS"] for r in rows)
    if claim_status:
        rows = [r for r in rows if r["STATUS"] == claim_status]
    size = page_size(limit)
    page_rows = rows[(page - 1) * size : page * size]
    connector = get_connector()
    payers = await payer_names(connector)
    patients = await masked_patients(connector, [str(r["PATIENT_ID"]) for r in page_rows]) if page_rows else {}
    open_items = {}
    if page_rows:
        open_items = {
            w.fill_id: w.id
            for w in (
                await db.scalars(
                    select(WorkItem).where(
                        WorkItem.tenant_id == principal.user.tenant_id,
                        WorkItem.fill_id.in_([str(r["FILL_ID"]) for r in page_rows]),
                        WorkItem.status.in_(OPEN_STATUSES),
                    )
                )
            ).all()
        }
    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "counts": {s: counts.get(s, 0) for s in CLAIM_STATUSES},
        "total": len(rows),
        "page": page,
        "page_size": size,
        "rows": [
            {
                "fill_id": r["FILL_ID"],
                "rx_label": f"Rx {r['RX_NUMBER']}-{int(r['FILL_NUMBER']):02d}",
                "fill_date": str(r["FILL_DATE"]),
                "payer_name": payers.get(str(r["PAYER_ID"])),
                "drug_name": r["DRUG_NAME"],
                "status": r["STATUS"],
                "reject_code": r["REJECT_CODE"],
                "reject_message": r["REJECT_MESSAGE"],
                "expected": float(r["EXPECTED_AMOUNT"] or 0),
                "paid": float(r["PAID_AMOUNT"]) if r["PAID_AMOUNT"] is not None else None,
                "location": r["LOCATION"],
                "patient": patients.get(str(r["PATIENT_ID"])),
                "work_item_id": open_items.get(str(r["FILL_ID"])),
            }
            for r in page_rows
        ],
    }


@router.get("/claims/filters")
async def claims_filters(principal: Principal = Depends(require("claims.view"))) -> dict[str, Any]:
    connector = get_connector()
    payers = await payer_names(connector)
    from levigate.services import locations

    return {
        "payers": [{"id": k, "name": v} for k, v in sorted(payers.items(), key=lambda kv: kv[1])],
        "locations": await locations(connector),
        "statuses": list(CLAIM_STATUSES),
    }


class ExportIn(BaseModel):
    confirm: Literal[True]
    start: date
    end: date
    status: str | None = None
    payer_id: str | None = None


@router.post("/exports/claims")
async def export_claims(
    body: ExportIn, principal: Principal = Depends(require("claims.export")), db: AsyncSession = Depends(db_session)
) -> Response:
    """C-12: role-restricted, step-up confirmed, watermarked with user and time, and logged."""
    if (body.end - body.start).days > 200:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose a date range of 200 days or less.")
    rows = await _tracker_rows(
        db, principal.user.tenant_id, body.start, body.end, body.payer_id, None, None, None, None, None
    )
    if body.status:
        rows = [r for r in rows if r["STATUS"] == body.status]
    payers = await payer_names(get_connector())
    stamp = datetime.now(ZoneInfo(get_settings().timezone))
    buf = io.StringIO()
    buf.write(f"# Levigate export · {principal.user.name} ({principal.user.role}) · {stamp:%Y-%m-%d %H:%M %Z}\n")
    buf.write("# Contains claim identifiers. Handle under the pharmacy's HIPAA policies; do not forward.\n")
    w = csv.writer(buf)
    w.writerow(["rx", "fill", "fill_date", "payer", "status", "reject_code", "expected", "paid", "location"])
    for r in rows:
        w.writerow(
            [
                r["RX_NUMBER"],
                r["FILL_NUMBER"],
                r["FILL_DATE"],
                payers.get(str(r["PAYER_ID"])),
                r["STATUS"],
                r["REJECT_CODE"] or "",
                r["EXPECTED_AMOUNT"],
                r["PAID_AMOUNT"] or "",
                r["LOCATION"],
            ]
        )
    async with audit.audit_transaction(db):
        await audit.record(
            db,
            principal.actor,
            "export.claims",
            "export",
            f"claims-{stamp:%Y%m%d%H%M%S}",
            detail={
                "rows": len(rows),
                "start": body.start.isoformat(),
                "end": body.end.isoformat(),
                "status": body.status,
            },
        )
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="levigate-claims-{stamp:%Y%m%d-%H%M}.csv"'},
    )
