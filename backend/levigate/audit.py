"""Append-only, hash-chained audit log (C-06) and unusual-access alerting (C-13 / CC-05).

Every write in the application happens inside ``audit_transaction``: the business change and its audit event
commit together or not at all, and the chain head is locked for the duration so two writers can never fork it.
Each event's hash covers its content and the previous event's hash, so deleting, editing or reordering any
event breaks verification from that point on.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from levigate.config import get_settings
from levigate.models import Alert, AuditEvent, as_utc, utcnow

log = logging.getLogger(__name__)

GENESIS = "0" * 64
_AUDIT_LOCK_KEY = 4_771_002  # arbitrary, stable advisory-lock key for the chain head
_local_lock = asyncio.Lock()

# Audit details carry IDs, counts and codes. A key that names a PHI value is a bug in the caller.
_PHI_KEYS = {"name", "first_name", "last_name", "dob", "date_of_birth", "phone", "address", "email", "ssn"}

BULK_VIEW_THRESHOLD = 40
BULK_VIEW_WINDOW = timedelta(minutes=10)
PHI_VIEW_ACTIONS = ("phi.reveal", "patient.view")


@dataclass(frozen=True)
class Actor:
    id: str
    role: str
    tenant_id: str
    location: str = "US"
    ip: str | None = None


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, event: AuditEvent) -> str:
    ts = as_utc(event.ts)
    payload = {
        "tenant_id": event.tenant_id,
        "ts": ts.isoformat() if ts else None,
        "actor_id": event.actor_id,
        "actor_role": event.actor_role,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "patient_id": event.patient_id,
        "ip": event.ip,
        "detail": event.detail,
        "prev_hash": prev_hash,
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


@asynccontextmanager
async def audit_transaction(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Hold the chain head, run the caller's changes, append audit events, commit once."""
    async with _local_lock:
        try:
            if session.get_bind().dialect.name == "postgresql":
                await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY})
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def record(
    session: AsyncSession,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: str | int,
    *,
    patient_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append one event. Must be called inside ``audit_transaction``."""
    detail = dict(detail or {})
    leaked = _PHI_KEYS.intersection(k.lower() for k in detail)
    if leaked:
        raise ValueError(f"audit detail must not contain PHI fields: {sorted(leaked)}")

    prev = await session.scalar(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))
    event = AuditEvent(
        tenant_id=actor.tenant_id,
        ts=utcnow(),
        actor_id=actor.id,
        actor_role=actor.role,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        patient_id=patient_id,
        ip=actor.ip,
        detail=detail,
        prev_hash=prev or GENESIS,
        hash="",
    )
    event.hash = compute_hash(event.prev_hash, event)
    session.add(event)
    await session.flush()
    await _check_alerts(session, actor, event)
    return event


async def _check_alerts(session: AsyncSession, actor: Actor, event: AuditEvent) -> None:
    settings = get_settings()
    if event.action in PHI_VIEW_ACTIONS:
        if actor.location != "US":
            session.add(
                Alert(
                    tenant_id=actor.tenant_id,
                    kind="offshore_phi_access",
                    severity="high",
                    user_id=actor.id,
                    summary=f"PHI viewed from outside the US ({actor.location}); event #{event.seq}",
                )
            )
        since = utcnow() - BULK_VIEW_WINDOW
        views = await session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.actor_id == actor.id,
                AuditEvent.action.in_(PHI_VIEW_ACTIONS),
                AuditEvent.ts >= since,
            )
        )
        if views == BULK_VIEW_THRESHOLD:  # alert once when the window crosses the threshold
            session.add(
                Alert(
                    tenant_id=actor.tenant_id,
                    kind="bulk_view",
                    severity="high",
                    user_id=actor.id,
                    summary=f"{views} patient records viewed in {BULK_VIEW_WINDOW.seconds // 60} minutes",
                )
            )
    if event.action.startswith("export."):
        local = datetime.now(ZoneInfo(settings.timezone))
        if not settings.business_hours_start <= local.hour < settings.business_hours_end or local.weekday() >= 5:
            session.add(
                Alert(
                    tenant_id=actor.tenant_id,
                    kind="off_hours_export",
                    severity="medium",
                    user_id=actor.id,
                    summary=f"Export '{event.action}' at {local:%a %H:%M} local time (event #{event.seq})",
                )
            )


@dataclass
class ChainVerification:
    ok: bool
    checked: int
    first_bad_seq: int | None
    head_hash: str | None


async def verify_chain(session: AsyncSession, batch: int = 5000) -> ChainVerification:
    """Recompute every hash in order. Any edit, deletion or reordering shows up as the first bad sequence."""
    prev = GENESIS
    checked = 0
    last_seq = 0
    while True:
        rows = (
            await session.scalars(
                select(AuditEvent).where(AuditEvent.seq > last_seq).order_by(AuditEvent.seq).limit(batch)
            )
        ).all()
        if not rows:
            break
        for ev in rows:
            if ev.prev_hash != prev or compute_hash(prev, ev) != ev.hash:
                log.warning("audit chain broken at seq %s", ev.seq)
                return ChainVerification(False, checked, ev.seq, prev)
            prev = ev.hash
            checked += 1
            last_seq = ev.seq
    return ChainVerification(True, checked, None, prev if checked else None)
