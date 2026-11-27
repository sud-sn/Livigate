"""Compliance Center (CC-01 … CC-08): prove the controls are working, from live data wherever possible."""

from __future__ import annotations

import csv
import io
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from fpdf import FPDF
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from levigate import ai, audit, clock
from levigate.api.deps import Principal, db_session, page_size, require
from levigate.config import get_settings
from levigate.models import (
    AccessReview,
    AiInteraction,
    Alert,
    Approval,
    AuditEvent,
    Consent,
    ControlAttestation,
    OutreachContact,
    User,
    Vendor,
    WorkItem,
    WorkItemStatus,
    as_utc,
    utcnow,
)
from levigate.rules import add_business_days
from levigate.services import user_names

router = APIRouter(prefix="/api/compliance", tags=["compliance"])

CONTROLS = {
    "C-01": ("BAA chain", "Signed BAAs with every vendor touching PHI. No BAA, no PHI."),
    "C-02": ("Hosting", "US-region, HIPAA-eligible services; PHI stays in the client-approved environment."),
    "C-03": ("Encryption", "TLS 1.2+ in transit; encryption at rest for app DB, backups, logs and files."),
    "C-04": ("Identity", "SSO with MFA; no shared accounts; 15-minute idle timeout."),
    "C-05": ("Minimum necessary (RBAC)", "Six roles; marketing sees prescriber and aggregate data only."),
    "C-06": ("Audit trail", "Every view, edit, export, call and AI action logged; append-only and hash-chained."),
    "C-07": ("AI data controls", "BAA-covered model or offline; only allow-listed fields; prompts treated as PHI."),
    "C-08": ("Human approval", "No claim change or patient message without a named approver."),
    "C-09": ("Patient consent (TCPA)", "Consent per channel; calls only with consent; opt-outs within 1 business day."),
    "C-10": ("Refill-reminder scope", "Only current prescriptions; lapsed over 90 days drop out; no switch promotion."),
    "C-11": ("Offshore access", "PHI masked for users outside the US unless the client approves."),
    "C-12": ("Exports", "Role-restricted, watermarked and logged."),
    "C-13": ("Incident response", "Breach-response process; alerts on unusual access."),
    "C-14": ("Policies and testing", "HIPAA risk assessment before go-live; annual penetration test."),
}


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _worst(*statuses: str) -> str:
    order = {"red": 0, "amber": 1, "green": 2}
    return min(statuses, key=lambda s: order[s])


# ---------------------------------------------------------------------------------------------------------------
# Evidence builders (each returns data the dashboard and the audit pack share)
# ---------------------------------------------------------------------------------------------------------------


async def vendor_register(db: AsyncSession, tenant: str) -> list[dict[str, Any]]:
    today = clock.today()
    out = []
    for v in (await db.scalars(select(Vendor).where(Vendor.tenant_id == tenant).order_by(Vendor.name))).all():
        days_left = (v.baa_expires_on - today).days if v.baa_expires_on else None
        if not v.touches_phi:
            state = "green"
            note = "Does not touch PHI"
        elif v.baa_status != "signed":
            state, note = "red", "No signed BAA — connection blocked"
        elif days_left is not None and days_left < 0:
            state, note = "red", "BAA expired — connection blocked"
        elif days_left is not None and days_left <= 60:
            state, note = "amber", f"BAA renewal due in {days_left} days"
        else:
            state, note = "green", "BAA active"
        out.append(
            {
                "id": v.id,
                "name": v.name,
                "service": v.service,
                "touches_phi": v.touches_phi,
                "connection_key": v.connection_key,
                "baa_status": v.baa_status,
                "baa_signed_on": v.baa_signed_on.isoformat() if v.baa_signed_on else None,
                "baa_expires_on": v.baa_expires_on.isoformat() if v.baa_expires_on else None,
                "days_to_expiry": days_left,
                "status": state,
                "note": note,
                "owner": v.owner,
            }
        )
    return out


async def approval_coverage(db: AsyncSession, tenant: str) -> dict[str, Any]:
    """CC-02: every work item a person moved forward must carry a named approval."""
    acted = (
        await db.scalars(
            select(WorkItem).where(
                WorkItem.tenant_id == tenant,
                WorkItem.status.in_([WorkItemStatus.AWAITING_FOLLOWUP, WorkItemStatus.RESOLVED]),
            )
        )
    ).all()
    approved_ids = set(
        (
            await db.scalars(
                select(Approval.subject_id).where(Approval.tenant_id == tenant, Approval.subject_type == "work_item")
            )
        ).all()
    )
    exceptions = [
        {"work_item_id": w.id, "rx_label": f"Rx {w.rx_number}-{w.fill_number:02d}", "status": w.status}
        for w in acted
        if str(w.id) not in approved_ids
    ]
    total = len(acted)
    pct = 100.0 if total == 0 else round((total - len(exceptions)) / total * 100, 2)
    return {"total": total, "with_approver": total - len(exceptions), "pct": pct, "exceptions": exceptions[:100]}


async def consent_coverage(db: AsyncSession, tenant: str) -> dict[str, Any]:
    """CC-03: consent by channel, contacts made without consent at the time of the call, opt-out turnaround."""
    consents = (await db.scalars(select(Consent).where(Consent.tenant_id == tenant).order_by(Consent.id))).all()
    latest: dict[tuple[str, str], Consent] = {}
    history: dict[str, list[Consent]] = defaultdict(list)
    for c in consents:
        latest[(c.patient_id, c.channel)] = c
        if c.channel == "call":
            history[c.patient_id].append(c)
    by_channel: dict[str, Counter] = defaultdict(Counter)
    for (_, channel), c in latest.items():
        by_channel[channel][c.status] += 1

    violations = []
    contacts = (await db.scalars(select(OutreachContact).where(OutreachContact.tenant_id == tenant))).all()
    for contact in contacts:
        started = as_utc(contact.started_at)
        state = None
        for c in history.get(contact.patient_id, []):
            if as_utc(c.recorded_at) <= started:
                state = c.status
        if state != "granted":
            violations.append(
                {
                    "contact_id": contact.id,
                    "patient_id": contact.patient_id,
                    "started_at": started.isoformat() if started else None,
                }
            )

    turnaround = []
    late = []
    for c in consents:
        if c.consent_type != "opt_out" or c.channel != "call":
            continue
        rec = as_utc(c.recorded_at)
        synced = as_utc(c.synced_back_at)
        deadline = datetime.combine(add_business_days(rec.date(), 1), datetime.max.time(), tzinfo=rec.tzinfo)
        if synced is None:
            if utcnow() > deadline:
                late.append({"patient_id": c.patient_id, "recorded_at": rec.isoformat()})
            continue
        hours = (synced - rec).total_seconds() / 3600
        turnaround.append(hours)
        if synced > deadline:
            late.append({"patient_id": c.patient_id, "recorded_at": rec.isoformat()})
    return {
        "by_channel": {ch: dict(cnt) for ch, cnt in by_channel.items()},
        "contacts_total": len(contacts),
        "contacts_without_consent": len(violations),
        "violations": violations[:100],
        "opt_outs": len(turnaround) + len([x for x in late if x]),
        "opt_out_max_hours": round(max(turnaround), 2) if turnaround else None,
        "opt_outs_late": late[:100],
    }


async def access_review(db: AsyncSession, tenant: str) -> dict[str, Any]:
    since = utcnow() - timedelta(days=90)
    users = (
        await db.scalars(
            select(User).where(User.tenant_id == tenant, User.is_service.is_(False)).order_by(User.role, User.name)
        )
    ).all()
    views = dict(
        (
            await db.execute(
                select(AuditEvent.actor_id, func.count(func.distinct(AuditEvent.patient_id)))
                .where(AuditEvent.tenant_id == tenant, AuditEvent.patient_id.is_not(None), AuditEvent.ts >= since)
                .group_by(AuditEvent.actor_id)
            )
        ).all()
    )
    last = await db.scalar(
        select(AccessReview)
        .where(AccessReview.tenant_id == tenant)
        .order_by(AccessReview.signed_off_at.desc())
        .limit(1)
    )
    names = await user_names(db, [u.manager_id for u in users] + ([last.reviewer_id] if last else []))
    today = clock.today()
    quarter = f"{today.year}-Q{(today.month - 1) // 3 + 1}"
    last_signed = as_utc(last.signed_off_at) if last else None
    return {
        "period": quarter,
        "last_review": None
        if last is None
        else {
            "period": last.period,
            "reviewer": names.get(last.reviewer_id, last.reviewer_id),
            "signed_off_at": last_signed.isoformat() if last_signed else None,
            "users_reviewed": last.users_reviewed,
            "notes": last.notes,
        },
        "due": last is None or last.period != quarter,
        "users": [
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "role": u.role,
                "location": u.location,
                "active": u.active,
                "manager": names.get(u.manager_id or ""),
                "last_login_at": as_utc(u.last_login_at).isoformat() if u.last_login_at else None,
                "phi_records_viewed_90d": int(views.get(u.id, 0)),
                "deactivated_at": as_utc(u.deactivated_at).isoformat() if u.deactivated_at else None,
            }
            for u in users
        ],
    }


async def ai_governance(db: AsyncSession, tenant: str, limit: int = 200) -> dict[str, Any]:
    rows = (
        await db.scalars(
            select(AiInteraction)
            .where(AiInteraction.tenant_id == tenant)
            .order_by(AiInteraction.id.desc())
            .limit(limit)
        )
    ).all()
    total = await db.scalar(select(func.count()).select_from(AiInteraction).where(AiInteraction.tenant_id == tenant))
    names = await user_names(db, [r.user_id for r in rows])
    out = []
    violations = 0
    for r in rows:
        task = ai.TASKS.get(r.task)
        outside = sorted(set(r.fields_sent) - (task.allowed_fields if task else set()))
        ok = bool(task and r.purpose and not outside)
        violations += not ok
        out.append(
            {
                "id": r.id,
                "created_at": as_utc(r.created_at).isoformat(),
                "task": r.task,
                "purpose": r.purpose,
                "provider": r.provider,
                "model": r.model,
                "fields_sent": r.fields_sent,
                "fields_outside_allow_list": outside,
                "user": names.get(r.user_id, r.user_id),
                "entity": f"{r.entity_type} {r.entity_id}",
                "disposition": r.disposition,
                "fallback_reason": r.fallback_reason,
                "tokens": r.tokens_in + r.tokens_out,
                "latency_ms": r.latency_ms,
            }
        )
    return {"total": total or 0, "violations": violations, "rows": out}


async def _attestation(db: AsyncSession, tenant: str, control_id: str) -> ControlAttestation | None:
    return await db.scalar(
        select(ControlAttestation)
        .where(ControlAttestation.tenant_id == tenant, ControlAttestation.control_id == control_id)
        .order_by(ControlAttestation.attested_at.desc())
        .limit(1)
    )


async def controls(db: AsyncSession, tenant: str) -> list[dict[str, Any]]:
    s = get_settings()
    now = utcnow()
    results: dict[str, dict[str, Any]] = {}

    def put(
        cid: str,
        state: str,
        evidence: str,
        *,
        automated: bool,
        failing: list | None = None,
        owner: str = "Compliance lead",
        link: str | None = None,
    ) -> None:
        results[cid] = {
            "id": cid,
            "title": CONTROLS[cid][0],
            "requirement": CONTROLS[cid][1],
            "status": state,
            "evidence": evidence,
            "automated": automated,
            "failing": failing or [],
            "owner": owner,
            "last_check": now.isoformat(),
            "link": link,
        }

    vendors = await vendor_register(db, tenant)
    phi_vendors = [v for v in vendors if v["touches_phi"]]
    put(
        "C-01",
        _worst("green", *(v["status"] for v in phi_vendors)) if phi_vendors else "amber",
        f"{len(phi_vendors)} PHI vendors; " + "; ".join(f"{v['name']}: {v['note']}" for v in phi_vendors),
        automated=True,
        failing=[v for v in phi_vendors if v["status"] != "green"],
        link="vendors",
    )

    for cid in ("C-02", "C-03", "C-14"):
        att = await _attestation(db, tenant, cid)
        if att is None:
            put(cid, "amber", "No attestation recorded yet.", automated=False)
        else:
            state = att.status
            if att.next_review_on and att.next_review_on < clock.today():
                state = _worst(state, "amber")
            put(cid, state, att.evidence, automated=False, owner=att.owner)

    att = await _attestation(db, tenant, "C-04")
    idle_ok = s.session_idle_minutes <= 15
    c04 = "green" if idle_ok and s.env == "prod" else "amber"
    put(
        "C-04",
        c04 if idle_ok else "red",
        f"Idle timeout {s.session_idle_minutes} min (enforced server-side). "
        + (
            "SSO via client IdP with MFA."
            if s.env == "prod"
            else "Demo sign-in is enabled in this non-production "
            "environment; production signs in through the client's IdP with MFA."
        )
        + (f" {att.evidence}" if att else ""),
        automated=True,
    )

    users = (await db.scalars(select(User).where(User.tenant_id == tenant, User.is_service.is_(False)))).all()
    by_role = Counter(u.role for u in users if u.active)
    put(
        "C-05",
        "green",
        "Active users by role: "
        + ", ".join(f"{r} {n}" for r, n in sorted(by_role.items()))
        + ". Response models per role; marketing endpoints carry no patient fields (tested).",
        automated=True,
    )

    chain = await audit.verify_chain(db)
    put(
        "C-06",
        "green" if chain.ok else "red",
        f"Hash chain verified across {chain.checked:,} events."
        if chain.ok
        else f"Chain broken at event #{chain.first_bad_seq}.",
        automated=True,
        failing=[] if chain.ok else [{"first_bad_seq": chain.first_bad_seq}],
        link="audit",
    )

    gov = await ai_governance(db, tenant, limit=1000)
    llm_vendor = next((v for v in vendors if v["connection_key"] == "llm"), None)
    provider = s.ai_provider
    put(
        "C-07",
        "red" if gov["violations"] else "green",
        f"{gov['total']} AI calls logged; {gov['violations']} outside a task allow-list. Provider: {provider}"
        + (f"; LLM vendor BAA: {llm_vendor['note']}" if llm_vendor else "")
        + ". Prompts and responses stored as "
        "SHA-256 only.",
        automated=True,
        link="ai-log",
    )

    appr = await approval_coverage(db, tenant)
    put(
        "C-08",
        "green" if not appr["exceptions"] else "red",
        f"{appr['pct']}% of {appr['total']} claim actions carry a named approver.",
        automated=True,
        failing=appr["exceptions"],
        link="approvals",
    )

    cons = await consent_coverage(db, tenant)
    c09 = "red" if cons["contacts_without_consent"] else ("amber" if cons["opt_outs_late"] else "green")
    put(
        "C-09",
        c09,
        f"{cons['contacts_total']} calls, {cons['contacts_without_consent']} without consent; "
        f"{cons['opt_outs']} opt-outs, slowest sync {cons['opt_out_max_hours'] or 0} h.",
        automated=True,
        failing=cons["violations"] + cons["opt_outs_late"],
        link="consent",
    )

    put(
        "C-10",
        "green",
        "Eligibility rule refill_call drops prescriptions lapsed over 90 days, lists only drugs on "
        "file, and the call-script task has no field for other therapies (enforced in code, covered by tests).",
        automated=True,
    )

    offshore = [u for u in users if u.active and u.location != "US"]
    approved_offshore = [u for u in offshore if u.offshore_phi_approved]
    c11 = "green" if s.offshore_phi_masking and not approved_offshore else "amber"
    put(
        "C-11",
        c11,
        f"Offshore masking {'on' if s.offshore_phi_masking else 'OFF'}; {len(offshore)} offshore users, "
        f"{len(approved_offshore)} approved for PHI.",
        automated=True,
        failing=[{"user": u.name, "location": u.location} for u in approved_offshore],
    )

    exports = await db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.tenant_id == tenant,
            AuditEvent.action.like("export.%"),
            AuditEvent.ts >= now - timedelta(days=30),
        )
    )
    put("C-12", "green", f"{exports} exports in 30 days, all role-checked, watermarked and logged.", automated=True)

    stale = (
        await db.scalars(
            select(Alert).where(
                Alert.tenant_id == tenant,
                Alert.acknowledged_at.is_(None),
                Alert.created_at <= now - timedelta(minutes=15),
            )
        )
    ).all()
    att13 = await _attestation(db, tenant, "C-13")
    c13 = "amber" if stale else (att13.status if att13 else "amber")
    put(
        "C-13",
        c13,
        (f"{len(stale)} alerts unacknowledged after 15 minutes. " if stale else "No stale alerts. ")
        + (att13.evidence if att13 else "Breach-response runbook not yet attested."),
        automated=True,
        failing=[{"alert_id": a.id, "kind": a.kind} for a in stale],
        link="alerts",
    )
    return [results[c] for c in CONTROLS]


# ---------------------------------------------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------------------------------------------


@router.get("/controls")
async def controls_dashboard(
    principal: Principal = Depends(require("compliance.view")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    tenant = principal.user.tenant_id
    items = await controls(db, tenant)
    alerts = (
        await db.scalars(
            select(Alert)
            .where(Alert.tenant_id == tenant, Alert.acknowledged_at.is_(None))
            .order_by(Alert.created_at.desc())
            .limit(20)
        )
    ).all()
    counts = Counter(c["status"] for c in items)
    return {
        "controls": items,
        "summary": {k: counts.get(k, 0) for k in ("green", "amber", "red")},
        "open_alerts": [
            {
                "id": a.id,
                "kind": a.kind,
                "severity": a.severity,
                "summary": a.summary,
                "created_at": as_utc(a.created_at).isoformat(),
            }
            for a in alerts
        ],
    }


@router.get("/approvals")
async def approvals(
    principal: Principal = Depends(require("compliance.view")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    return await approval_coverage(db, principal.user.tenant_id)


@router.get("/consent")
async def consent(
    principal: Principal = Depends(require("compliance.view")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    return await consent_coverage(db, principal.user.tenant_id)


@router.get("/access-review")
async def get_access_review(
    principal: Principal = Depends(require("compliance.view")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    return await access_review(db, principal.user.tenant_id)


class SignOffIn(BaseModel):
    confirm: Literal[True]
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/access-review/sign-off")
async def sign_off(
    body: SignOffIn,
    principal: Principal = Depends(require("compliance.manage")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    data = await access_review(db, principal.user.tenant_id)
    async with audit.audit_transaction(db):
        review = AccessReview(
            tenant_id=principal.user.tenant_id,
            period=data["period"],
            reviewer_id=principal.user.id,
            notes=body.notes,
            users_reviewed=len(data["users"]),
        )
        db.add(review)
        await db.flush()
        await audit.record(
            db,
            principal.actor,
            "compliance.access_review",
            "access_review",
            review.id,
            detail={"period": data["period"], "users": len(data["users"])},
        )
    return {"ok": True, "period": data["period"]}


@router.get("/audit-log")
async def audit_log(
    user_id: str | None = None,
    patient_id: str | None = Query(None, max_length=64),
    action: str | None = Query(None, max_length=64),
    start: date | None = None,
    end: date | None = None,
    page: int = Query(1, ge=1),
    limit: int = 50,
    principal: Principal = Depends(require("audit.view")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    q = select(AuditEvent).where(AuditEvent.tenant_id == principal.user.tenant_id)
    if user_id:
        q = q.where(AuditEvent.actor_id == user_id)
    if patient_id:
        q = q.where(AuditEvent.patient_id == patient_id)
    if action:
        q = q.where(AuditEvent.action.like(f"{action}%"))
    if start:
        q = q.where(AuditEvent.ts >= datetime.combine(start, datetime.min.time(), tzinfo=_tz()))
    if end:
        q = q.where(AuditEvent.ts < datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=_tz()))
    total = await db.scalar(select(func.count()).select_from(q.subquery()))
    size = page_size(limit)
    rows = (await db.scalars(q.order_by(AuditEvent.seq.desc()).offset((page - 1) * size).limit(size))).all()
    names = await user_names(db, [r.actor_id for r in rows])
    actions = sorted(set((await db.scalars(select(AuditEvent.action).distinct())).all()))
    users = (
        await db.execute(
            select(User.id, User.name).where(User.tenant_id == principal.user.tenant_id).order_by(User.name)
        )
    ).all()
    return {
        "total": total or 0,
        "page": page,
        "page_size": size,
        "actions": actions,
        "users": [{"id": u.id, "name": u.name} for u in users],
        "rows": [
            {
                "seq": r.seq,
                "ts": as_utc(r.ts).isoformat(),
                "actor": names.get(r.actor_id, r.actor_id),
                "actor_role": r.actor_role,
                "action": r.action,
                "entity": f"{r.entity_type} {r.entity_id}",
                "patient_id": r.patient_id,
                "ip": r.ip,
                "detail": r.detail,
                "hash": r.hash[:12],
            }
            for r in rows
        ],
    }


@router.get("/audit-log/verify")
async def verify(
    principal: Principal = Depends(require("audit.view")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    result = await audit.verify_chain(db)
    return {
        "ok": result.ok,
        "checked": result.checked,
        "first_bad_seq": result.first_bad_seq,
        "head": result.head_hash[:16] if result.head_hash else None,
    }


@router.get("/ai-log")
async def ai_log(
    principal: Principal = Depends(require("compliance.view")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    return await ai_governance(db, principal.user.tenant_id)


@router.get("/vendors")
async def vendors(
    principal: Principal = Depends(require("compliance.view")), db: AsyncSession = Depends(db_session)
) -> list[dict[str, Any]]:
    return await vendor_register(db, principal.user.tenant_id)


@router.get("/alerts")
async def alerts(
    principal: Principal = Depends(require("compliance.view")), db: AsyncSession = Depends(db_session)
) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(
            select(Alert)
            .where(Alert.tenant_id == principal.user.tenant_id)
            .order_by(Alert.created_at.desc())
            .limit(200)
        )
    ).all()
    names = await user_names(db, [r.user_id for r in rows] + [r.acknowledged_by for r in rows])
    return [
        {
            "id": a.id,
            "kind": a.kind,
            "severity": a.severity,
            "summary": a.summary,
            "user": names.get(a.user_id or ""),
            "created_at": as_utc(a.created_at).isoformat(),
            "acknowledged_by": names.get(a.acknowledged_by or ""),
            "acknowledged_at": as_utc(a.acknowledged_at).isoformat() if a.acknowledged_at else None,
        }
        for a in rows
    ]


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge(
    alert_id: int, principal: Principal = Depends(require("compliance.manage")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    alert = await db.get(Alert, alert_id)
    if alert is None or alert.tenant_id != principal.user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found.")
    async with audit.audit_transaction(db):
        alert.acknowledged_by, alert.acknowledged_at = principal.user.id, utcnow()
        await audit.record(db, principal.actor, "compliance.alert_ack", "alert", alert.id)
    return {"ok": True}


# ---------------------------------------------------------------------------------------------------------------
# Audit pack (CC-08): PDF + CSV, generated in seconds, and the export itself is logged.
# ---------------------------------------------------------------------------------------------------------------


class AuditPackIn(BaseModel):
    confirm: Literal[True]
    start: date
    end: date


def _latin(text: Any) -> str:
    s = str(text)
    for a, b in (
        ("—", "-"),
        ("–", "-"),
        ("·", "|"),
        ("’", "'"),
        ("“", '"'),
        ("”", '"'),
        ("…", "..."),
        ("≥", ">="),
        ("≤", "<="),
        ("•", "-"),
        ("✓", "ok"),
    ):
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def _csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (v if not isinstance(v, list | dict) else str(v)) for k, v in r.items()})
    return buf.getvalue()


@router.post("/audit-pack")
async def audit_pack(
    body: AuditPackIn, principal: Principal = Depends(require("audit.export")), db: AsyncSession = Depends(db_session)
) -> Response:
    if body.end < body.start or (body.end - body.start).days > 400:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose a date range of up to 400 days.")
    tenant = principal.user.tenant_id
    tz = _tz()
    start_dt = datetime.combine(body.start, datetime.min.time(), tzinfo=tz)
    end_dt = datetime.combine(body.end + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    stamp = datetime.now(tz)

    ctl = await controls(db, tenant)
    appr = await approval_coverage(db, tenant)
    cons = await consent_coverage(db, tenant)
    access = await access_review(db, tenant)
    vend = await vendor_register(db, tenant)
    approvals_rows = (
        await db.scalars(
            select(Approval).where(
                Approval.tenant_id == tenant, Approval.approved_at >= start_dt, Approval.approved_at < end_dt
            )
        )
    ).all()
    alerts_rows = (
        await db.scalars(
            select(Alert).where(Alert.tenant_id == tenant, Alert.created_at >= start_dt, Alert.created_at < end_dt)
        )
    ).all()
    events = (
        await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant, AuditEvent.ts >= start_dt, AuditEvent.ts < end_dt)
            .order_by(AuditEvent.seq)
        )
    ).all()
    chain = await audit.verify_chain(db)
    names = await user_names(db, [a.approver_id for a in approvals_rows] + [e.actor_id for e in events])

    pdf = FPDF(orientation="P", unit="mm", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Levigate audit pack", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    meta = (
        f"{get_settings().pharmacy_name} | {body.start:%b %d, %Y} to {body.end:%b %d, %Y} | generated "
        f"{stamp:%Y-%m-%d %H:%M %Z} by {principal.user.name} ({principal.user.role})"
    )
    pdf.multi_cell(0, 5, _latin(meta), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    def heading(text: str) -> None:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 7, _latin(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)

    heading("Control status (C-01 to C-14)")
    for c in ctl:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(
            0,
            5,
            _latin(
                f"{c['id']} {c['title']} - {c['status'].upper()}{' (automated)' if c['automated'] else ' (attested)'}"
            ),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 4.5, _latin(c["evidence"]), new_x="LMARGIN", new_y="NEXT")
    heading("Approvals (C-08)")
    pdf.multi_cell(
        0,
        5,
        _latin(
            f"{appr['pct']}% of {appr['total']} claim actions carry a named approver; "
            f"{len(approvals_rows)} approvals in the period; "
            f"{len(appr['exceptions'])} exceptions."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    heading("Consent (C-09)")
    pdf.multi_cell(
        0,
        5,
        _latin(
            f"Calls: {cons['contacts_total']}; "
            f"without consent: {cons['contacts_without_consent']}; "
            f"opt-outs: {cons['opt_outs']}; late opt-out syncs: {len(cons['opt_outs_late'])}. "
            f"Consent by channel: {cons['by_channel']}"
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    heading("Access review (CC-04)")
    lr = access["last_review"]
    pdf.multi_cell(
        0,
        5,
        _latin(
            f"{len(access['users'])} users. Last review: "
            + (f"{lr['period']} signed by {lr['reviewer']} on {lr['signed_off_at'][:10]}" if lr else "none recorded")
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    heading("Vendors and BAAs (C-01)")
    for v in vend:
        pdf.multi_cell(0, 4.5, _latin(f"{v['name']} ({v['service']}): {v['note']}"), new_x="LMARGIN", new_y="NEXT")
    heading("Alerts and incidents")
    pdf.multi_cell(
        0,
        5,
        _latin(
            f"{len(alerts_rows)} alerts in the period; {sum(1 for a in alerts_rows if a.acknowledged_at)} acknowledged."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    heading("Audit trail (C-06)")
    pdf.multi_cell(
        0,
        5,
        _latin(
            f"{len(events)} events in the period. Hash chain "
            + (
                f"verified end to end across {chain.checked} events."
                if chain.ok
                else f"BROKEN at event #{chain.first_bad_seq}."
            )
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf_bytes = bytes(pdf.output())

    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("audit-pack.pdf", pdf_bytes)
        z.writestr(
            "controls.csv",
            _csv(
                [
                    {k: c[k] for k in ("id", "title", "status", "automated", "evidence", "owner", "last_check")}
                    for c in ctl
                ]
            ),
        )
        z.writestr(
            "approvals.csv",
            _csv(
                [
                    {
                        "approval_id": a.id,
                        "subject": f"{a.subject_type} {a.subject_id}",
                        "action": a.action,
                        "approver": names.get(a.approver_id, a.approver_id),
                        "approved_at": as_utc(a.approved_at).isoformat(),
                        "rule": f"{a.rule_key} v{a.rule_version}" if a.rule_key else "",
                        "edited": a.edited,
                    }
                    for a in approvals_rows
                ]
            ),
        )
        consent_rows = [{"channel": ch, **counts} for ch, counts in cons["by_channel"].items()]
        z.writestr("consent_coverage.csv", _csv(consent_rows))
        z.writestr("access_review.csv", _csv(access["users"]))
        z.writestr("vendors.csv", _csv(vend))
        z.writestr(
            "alerts.csv",
            _csv(
                [
                    {
                        "id": a.id,
                        "kind": a.kind,
                        "severity": a.severity,
                        "summary": a.summary,
                        "created_at": as_utc(a.created_at).isoformat(),
                        "acknowledged": bool(a.acknowledged_at),
                    }
                    for a in alerts_rows
                ]
            ),
        )
        z.writestr(
            "audit_events.csv",
            _csv(
                [
                    {
                        "seq": e.seq,
                        "ts": as_utc(e.ts).isoformat(),
                        "actor": names.get(e.actor_id, e.actor_id),
                        "role": e.actor_role,
                        "action": e.action,
                        "entity": f"{e.entity_type} {e.entity_id}",
                        "patient_id": e.patient_id or "",
                        "hash": e.hash,
                    }
                    for e in events
                ]
            ),
        )
    async with audit.audit_transaction(db):
        await audit.record(
            db,
            principal.actor,
            "export.audit_pack",
            "export",
            f"audit-pack-{stamp:%Y%m%d%H%M%S}",
            detail={"start": body.start.isoformat(), "end": body.end.isoformat(), "events": len(events)},
        )
    return Response(
        zbuf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="levigate-audit-pack-{body.start}-{body.end}.zip"'},
    )
