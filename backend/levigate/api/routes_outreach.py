"""Patient outreach: daily call lists (PO-01/02), click-to-call with required outcomes (PO-03), AI scripts (PO-04),
opt-outs enforced immediately (PO-05), and the audited PHI reveal."""

from __future__ import annotations

from collections.abc import Mapping
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
from levigate.models import Consent, OutreachContact, as_utc, utcnow
from levigate.policy import phi_visible
from levigate.rules import LIST_TYPES, contact_eligibility, outreach_priority, refill_bucket, to_date
from levigate.services import masked_patient, patient_rows, revealed_patient

router = APIRouter(prefix="/api", tags=["outreach"])

OUTCOMES = ("reached", "voicemail", "refill_confirmed", "opted_out", "wrong_number", "no_answer")
LIST_LABELS = {
    "refill_due": "Refill due",
    "adherence_gap": "Adherence gap",
    "ready_for_pickup": "Ready for pickup",
    "balance_due": "Balance due",
}


async def consent_state(db: AsyncSession, tenant_id: str, channel: str = "call") -> dict[str, Consent]:
    """Latest consent row per patient for a channel."""
    latest = (
        select(Consent.patient_id, func.max(Consent.id).label("max_id"))
        .where(Consent.tenant_id == tenant_id, Consent.channel == channel)
        .group_by(Consent.patient_id)
        .subquery()
    )
    rows = await db.scalars(select(Consent).join(latest, Consent.id == latest.c.max_id))
    return {r.patient_id: r for r in rows}


async def last_contacts(db: AsyncSession, tenant_id: str) -> dict[str, date]:
    rows = await db.execute(
        select(OutreachContact.patient_id, func.max(OutreachContact.started_at))
        .where(OutreachContact.tenant_id == tenant_id)
        .group_by(OutreachContact.patient_id)
    )
    out = {}
    for pid, ts in rows:
        ts = as_utc(ts)
        if ts is not None:
            out[pid] = ts.date()
    return out


def _consent_flags(consents: Mapping[str, Consent], patient_id: str) -> tuple[bool, bool]:
    c = consents.get(patient_id)
    granted = bool(c and c.status == "granted")
    opted_out = bool(c and c.status == "revoked")
    return granted, opted_out


async def build_lists(db: AsyncSession, tenant_id: str, today: date) -> dict[str, dict[str, Any]]:
    """The rules engine output for today's lists, before masking. Used by the API and the compliance checks."""
    connector = get_connector()
    rule_entry = await catalog.current(db, tenant_id, "eligibility_rule", "refill_call")
    rule = rule_entry.body if rule_entry else {}
    weights = rule.get("priority_weights", {"urgency": 0.5, "revenue": 0.3, "contact_gap": 0.2})
    consents = await consent_state(db, tenant_id)
    contacts = await last_contacts(db, tenant_id)

    lists: dict[str, dict[str, Any]] = {t: {"entries": [], "exclusions": []} for t in LIST_TYPES}
    candidates = await connector.fetch("refill_candidates", min_fill_date=today - timedelta(days=300))
    pickups = await connector.fetch("pickup_ready")
    balances = await connector.fetch("balances_due")
    # A patient with no usable phone number cannot be called: listed under exclusions with the reason instead.
    everyone = {str(r["PATIENT_ID"]) for r in (*candidates, *pickups, *balances)}
    reachable = {pid for pid, row in (await patient_rows(connector, everyone)).items() if row.get("PHONE")}
    max_rev = max((float(c["EXPECTED_AMOUNT"] or 0) for c in candidates), default=1.0)
    for c in candidates:
        bucket, due = refill_bucket(c, today, rule)
        if bucket is None:
            continue
        pid = str(c["PATIENT_ID"])
        granted, opted_out = _consent_flags(consents, pid)
        lapsed = bucket == "lapsed"
        target = "adherence_gap" if lapsed else bucket
        elig = contact_eligibility(
            consent_granted=granted, opted_out=opted_out, lapsed=lapsed, has_phone=pid in reachable
        )
        entry = {
            "patient_id": pid,
            "rx_number": str(c["RX_NUMBER"]),
            "drug_name": c["DRUG_NAME"],
            "due_date": due.isoformat(),
            "days_to_due": (due - today).days,
            "amount": round(float(c["EXPECTED_AMOUNT"] or 0), 2),
            "last_contact": contacts[pid].isoformat() if pid in contacts else None,
        }
        if not elig.eligible:
            if lapsed and (today - due).days > 180:
                continue  # long-gone prescriptions are not worth listing even as exclusions
            lists[target]["exclusions"].append({**entry, "reasons": elig.reasons})
            continue
        score, parts = outreach_priority(
            due=due,
            revenue=entry["amount"],
            last_contact=contacts.get(pid),
            today=today,
            weights=weights,
            max_revenue=max_rev,
        )
        lists[target]["entries"].append({**entry, "score": score, "score_parts": parts})

    for r in pickups:
        pid = str(r["PATIENT_ID"])
        granted, opted_out = _consent_flags(consents, pid)
        ready = to_date(r["PICKUP_READY_DATE"])
        entry = {
            "patient_id": pid,
            "rx_number": str(r["RX_NUMBER"]),
            "drug_name": r["DRUG_NAME"],
            "due_date": ready.isoformat(),
            "days_to_due": (ready - today).days,
            "amount": round(float(r["EXPECTED_AMOUNT"] or 0), 2),
            "last_contact": contacts[pid].isoformat() if pid in contacts else None,
        }
        elig = contact_eligibility(
            consent_granted=granted, opted_out=opted_out, lapsed=False, has_phone=pid in reachable
        )
        if not elig.eligible:
            lists["ready_for_pickup"]["exclusions"].append({**entry, "reasons": elig.reasons})
            continue
        score, parts = outreach_priority(
            due=ready - timedelta(days=7),
            revenue=entry["amount"],
            last_contact=contacts.get(pid),
            today=today,
            weights=weights,
            max_revenue=max_rev,
        )
        lists["ready_for_pickup"]["entries"].append({**entry, "score": score, "score_parts": parts})

    for r in balances:
        pid = str(r["PATIENT_ID"])
        granted, opted_out = _consent_flags(consents, pid)
        last_fill = to_date(r["LAST_FILL_DATE"])
        entry = {
            "patient_id": pid,
            "rx_number": None,
            "drug_name": None,
            "due_date": last_fill.isoformat(),
            "days_to_due": (last_fill - today).days,
            "amount": round(float(r["BALANCE"] or 0), 2),
            "last_contact": contacts[pid].isoformat() if pid in contacts else None,
        }
        elig = contact_eligibility(
            consent_granted=granted, opted_out=opted_out, lapsed=False, has_phone=pid in reachable
        )
        if not elig.eligible:
            lists["balance_due"]["exclusions"].append({**entry, "reasons": elig.reasons})
            continue
        score, parts = outreach_priority(
            due=last_fill,
            revenue=entry["amount"] * 4,
            last_contact=contacts.get(pid),
            today=today,
            weights=weights,
            max_revenue=max_rev,
        )
        lists["balance_due"]["entries"].append({**entry, "score": score, "score_parts": parts})

    for data in lists.values():
        # Ties broken by business keys so the list never depends on the order the warehouse returned rows in.
        data["entries"].sort(key=lambda e: (-e["score"], *_tiebreak(e)))
        data["exclusions"].sort(key=lambda e: (e["days_to_due"], *_tiebreak(e)))
    lists["_meta"] = {
        "rule_version": rule_entry.version if rule_entry else None,
        "weights": weights,
        "rule_text": rule.get("rule_text"),
    }
    return lists


@router.get("/outreach/lists")
async def call_lists(
    list_type: str = Query("refill_due"),
    principal: Principal = Depends(require("outreach.view")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    if list_type not in LIST_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown list.")
    today = clock.today()
    lists = await build_lists(db, principal.user.tenant_id, today)
    meta = lists.pop("_meta")
    data = lists[list_type]
    # Remove patients already contacted today for this list.
    start_of_day = utcnow() - timedelta(hours=24)
    called = set(
        (
            await db.scalars(
                select(OutreachContact.patient_id).where(
                    OutreachContact.tenant_id == principal.user.tenant_id,
                    OutreachContact.list_type == list_type,
                    OutreachContact.outcome.is_not(None),
                    OutreachContact.logged_at >= start_of_day,
                )
            )
        ).all()
    )
    entries = [e for e in data["entries"] if e["patient_id"] not in called][:200]
    exclusions = data["exclusions"][:50]
    rows = await patient_rows(get_connector(), [e["patient_id"] for e in entries + exclusions])
    for e in entries + exclusions:
        e["patient"] = masked_patient(e["patient_id"], rows.get(e["patient_id"]))
    pending = await db.scalar(
        select(OutreachContact).where(OutreachContact.user_id == principal.user.id, OutreachContact.outcome.is_(None))
    )
    w = meta["weights"]
    return {
        "list_type": list_type,
        "lists": [
            {
                "type": t,
                "label": LIST_LABELS[t],
                "count": len(lists[t]["entries"]),
                "excluded": len(lists[t]["exclusions"]),
            }
            for t in LIST_TYPES
        ],
        "entries": entries,
        "exclusions": exclusions,
        "sort_explanation": (
            f"Ranked by due date ({round(w['urgency'] * 100)}%), revenue ({round(w['revenue'] * 100)}%) and days "
            f"since last contact ({round(w['contact_gap'] * 100)}%). Consent and the 90-day lapse rule are applied "
            f"first (eligibility rule v{meta['rule_version']})."
        ),
        "rule_text": meta["rule_text"],
        "pending_call": None
        if pending is None
        else {"id": pending.id, "patient_id": pending.patient_id, "list_type": pending.list_type},
        "can_reveal": phi_visible(principal.user),
    }


class CallIn(BaseModel):
    patient_id: str = Field(max_length=64)
    list_type: str
    rx_number: str | None = Field(default=None, max_length=32)


@router.post("/outreach/calls")
async def start_call(
    body: CallIn, principal: Principal = Depends(require("outreach.call")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    if body.list_type not in LIST_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown list.")
    pending = await db.scalar(
        select(OutreachContact).where(OutreachContact.user_id == principal.user.id, OutreachContact.outcome.is_(None))
    )
    if pending is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Log the outcome of your current call before the next one.")
    # Consent is checked again here, server-side, at the moment of the call (C-09) — not trusted from the list.
    consents = await consent_state(db, principal.user.tenant_id)
    granted, opted_out = _consent_flags(consents, body.patient_id)
    if opted_out or not granted:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This patient has no call consent on file or has opted out. The call was not placed.",
        )
    if not await get_connector().fetch("patient_exists", patient_id=body.patient_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found.")
    async with audit.audit_transaction(db):
        contact = OutreachContact(
            tenant_id=principal.user.tenant_id,
            patient_id=body.patient_id,
            rx_number=body.rx_number,
            list_type=body.list_type,
            user_id=principal.user.id,
            consent_verified=True,
        )
        db.add(contact)
        await db.flush()
        await audit.record(
            db,
            principal.actor,
            "outreach.call_start",
            "outreach_contact",
            contact.id,
            patient_id=body.patient_id,
            detail={"list_type": body.list_type},
        )
    # Telephony: the provider dials the number server-side, so the browser never needs the phone number.
    return {"call_id": contact.id, "telephony": {"provider": "demo", "status": "dialing"}}


class OutcomeIn(BaseModel):
    outcome: Literal["reached", "voicemail", "refill_confirmed", "opted_out", "wrong_number", "no_answer"]
    notes: str | None = Field(default=None, max_length=2000)
    script_interaction_id: int | None = None
    script_disposition: Literal["accepted", "edited", "discarded"] | None = None


@router.post("/outreach/calls/{call_id}/outcome")
async def log_outcome(
    call_id: int,
    body: OutcomeIn,
    principal: Principal = Depends(require("outreach.call")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    contact = await db.get(OutreachContact, call_id)
    if contact is None or contact.user_id != principal.user.id or contact.tenant_id != principal.user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found.")
    if contact.outcome is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An outcome is already logged for this call.")
    async with audit.audit_transaction(db):
        contact.outcome, contact.notes, contact.logged_at = body.outcome, body.notes, utcnow()
        contact.script_interaction_id = body.script_interaction_id
        if body.script_interaction_id and body.script_disposition:
            await ai.set_disposition(db, principal.user, body.script_interaction_id, body.script_disposition)
        await audit.record(
            db,
            principal.actor,
            "outreach.outcome",
            "outreach_contact",
            contact.id,
            patient_id=contact.patient_id,
            detail={"outcome": body.outcome},
        )
        if body.outcome == "opted_out":
            # PO-05: enforced immediately for every channel, and pushed to the telephony do-not-call list.
            for channel in ("call", "sms"):
                db.add(
                    Consent(
                        tenant_id=contact.tenant_id,
                        patient_id=contact.patient_id,
                        channel=channel,
                        status="revoked",
                        consent_type="opt_out",
                        source="outreach call",
                        recorded_by=principal.user.id,
                        synced_back_at=utcnow(),
                    )
                )
            await audit.record(
                db,
                principal.actor,
                "consent.opt_out",
                "patient",
                contact.patient_id,
                patient_id=contact.patient_id,
                detail={"channels": ["call", "sms"]},
            )
    return {"ok": True, "outcome": body.outcome}


class ScriptIn(BaseModel):
    patient_id: str = Field(max_length=64)
    list_type: str
    rx_number: str | None = Field(default=None, max_length=32)


@router.post("/outreach/script")
async def call_script(
    body: ScriptIn, principal: Principal = Depends(require("outreach.view")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    if body.list_type not in LIST_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown list.")
    today = clock.today()
    lists = await build_lists(db, principal.user.tenant_id, today)
    entry = next(
        (
            e
            for e in lists[body.list_type]["entries"]
            if e["patient_id"] == body.patient_id and (body.rx_number is None or e["rx_number"] == body.rx_number)
        ),
        None,
    )
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This patient is not on that list today.")
    row = (await patient_rows(get_connector(), [body.patient_id])).get(body.patient_id, {})
    fields: dict[str, Any] = {
        "list_type": body.list_type,
        "pharmacy_name": get_settings().pharmacy_name,
        "preferred_language": row.get("PREFERRED_LANGUAGE"),
    }
    if entry.get("drug_name"):
        fields["drug_name"] = entry["drug_name"]
    if body.list_type in ("refill_due", "adherence_gap"):
        fields["due_date"] = entry["due_date"]
    if body.list_type == "balance_due":
        fields["balance_amount"] = entry["amount"]
    if phi_visible(principal.user):  # PO-04: masked users get a script without the patient's name
        fields["patient_first_name"] = row.get("FIRST_NAME")
    async with audit.audit_transaction(db):
        try:
            d = await ai.draft(
                db,
                principal.user,
                principal.actor,
                "call_script",
                fields,
                entity_type="patient",
                entity_id=body.patient_id,
                patient_id=body.patient_id,
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
        "sources": [f"{LIST_LABELS[body.list_type]} list", "Eligibility rule refill_call"],
    }


@router.post("/patients/{patient_id}/reveal")
async def reveal_patient(
    patient_id: str, principal: Principal = Depends(require("patients.phi")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    """Full details on click — every reveal is audited and counts toward bulk-view alerts (C-06, CC-05)."""
    if not phi_visible(principal.user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Patient details are masked for your location (C-11). Ask an admin if you need access.",
        )
    rows = await patient_rows(get_connector(), [patient_id])
    row = rows.get(patient_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found.")
    async with audit.audit_transaction(db):
        await audit.record(db, principal.actor, "phi.reveal", "patient", patient_id, patient_id=patient_id)
    return revealed_patient(row)


def _tiebreak(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (str(entry.get("due_date") or ""), str(entry.get("patient_id") or ""), str(entry.get("rx_number") or ""))
