"""App database tables.

The app database holds workflow state, outcomes, consent, the business catalog and the audit log. It stores the
*IDs* of source records (claims, patients, prescribers) — not a second copy of patient data. Names, dates of
birth and phone numbers are read live from the client source when a permitted user opens a record.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, overload

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from levigate.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Role(StrEnum):
    BILLING = "billing"
    OUTREACH = "outreach"
    MARKETING = "marketing"
    PHARMACIST = "pharmacist"
    ADMIN = "admin"
    AUDITOR = "auditor"


class WorkItemType(StrEnum):
    CLAIM_REJECTED = "claim_rejected"
    CLAIM_UNPAID = "claim_unpaid"
    CLAIM_UNDERPAID = "claim_underpaid"
    REJECT_REVIEW = "reject_review"  # unknown reject code: no automated suggestion (IF-05)


class WorkItemStatus(StrEnum):
    OPEN = "open"
    AWAITING_FOLLOWUP = "awaiting_followup"  # approved; waiting on plan, prescriber or resubmission date
    RESOLVED = "resolved"
    CLOSED_UPSTREAM = "closed_upstream"  # paid or reversed at the source; closed by reconciliation


OPEN_STATUSES = (WorkItemStatus.OPEN, WorkItemStatus.AWAITING_FOLLOWUP)


# Timezone-aware timestamps everywhere; SQLite drops tzinfo, so readers normalise through as_utc().
TS = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    location: Mapped[str] = mapped_column(String(2), default="US")  # ISO country of the user's workplace
    offshore_phi_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    manager_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_service: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class UserSession(Base):
    """Server-side session: the cookie carries a random token; only its SHA-256 is stored."""

    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CatalogEntry(Base):
    """Business catalog (semantic layer): versioned, approved, auditable.

    kinds: data_dictionary, metric, reject_playbook, eligibility_rule, prescriber_scoring, glossary, payer_config.
    Only one version per (kind, key) is current. A proposed change waits for approval by a different admin.
    """

    __tablename__ = "catalog_entries"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "key", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    key: Mapped[str] = mapped_column(String(96))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="approved")  # proposed | approved | superseded
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    approved_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (
        # At most one *open* item per fill is enforced by the sync (it runs under the audit chain lock).
        Index("ix_work_items_fill", "tenant_id", "fill_id"),
        Index("ix_work_items_queue", "tenant_id", "queue_role", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default=WorkItemStatus.OPEN)
    queue_role: Mapped[str] = mapped_column(String(32))
    assignee_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Source references (IDs only). A work item follows one fill; claim_id is the latest claim row seen for it.
    fill_id: Mapped[str] = mapped_column(String(64))
    claim_id: Mapped[str] = mapped_column(String(64))
    rx_number: Mapped[str] = mapped_column(String(32), index=True)
    fill_number: Mapped[int] = mapped_column(Integer)
    patient_id: Mapped[str] = mapped_column(String(64), index=True)
    prescriber_id: Mapped[str] = mapped_column(String(64), index=True)
    payer_id: Mapped[str] = mapped_column(String(64))
    location: Mapped[str] = mapped_column(String(64))
    reject_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    fill_date: Mapped[date] = mapped_column(Date)

    amount_at_risk: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    due_date: Mapped[date] = mapped_column(Date)

    # The rule that produced this item, and its computed (deterministic) result.
    rule_kind: Mapped[str | None] = mapped_column(String(48), nullable=True)
    rule_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suggested_fix: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ai_explanation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Follow-up tracking (IF-04).
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_followup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recovered_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class WorkItemEvent(Base):
    """Timeline of a work item (CT-05 / IF-04): what happened, by whom, from which source."""

    __tablename__ = "work_item_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(TS, default=utcnow)
    actor_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(48))  # created | approved | followup | note | assigned | closed_upstream
    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Approval(Base):
    """A named human approval (C-08). Nothing changes state without one of these."""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    subject_type: Mapped[str] = mapped_column(String(32))  # work_item | patient_message | catalog_entry
    subject_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64))
    approver_id: Mapped[str] = mapped_column(String(32))
    approved_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    rule_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)


class Consent(Base):
    """Patient contact consent per channel (C-09). Latest row per (patient, channel) is the current state."""

    __tablename__ = "consents"
    __table_args__ = (Index("ix_consents_patient_channel", "tenant_id", "patient_id", "channel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    patient_id: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(16))  # call | sms
    status: Mapped[str] = mapped_column(String(16))  # granted | revoked
    consent_type: Mapped[str] = mapped_column(String(32))  # prior_express | prior_express_written | opt_out
    source: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    recorded_by: Mapped[str] = mapped_column(String(32))
    synced_back_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)  # opt-out pushed to PMS/telephony


class OutreachContact(Base):
    """One outreach call attempt. ``outcome`` is required before the same user can start the next call (PO-03)."""

    __tablename__ = "outreach_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    patient_id: Mapped[str] = mapped_column(String(64), index=True)
    rx_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    list_type: Mapped[str] = mapped_column(String(32))
    channel: Mapped[str] = mapped_column(String(16), default="call")
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    consent_verified: Mapped[bool] = mapped_column(Boolean)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    logged_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    script_interaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CrmActivity(Base):
    __tablename__ = "crm_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    prescriber_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # call | visit | email | note
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    next_action: Mapped[str | None] = mapped_column(String(255), nullable=True)
    next_action_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_action_done: Mapped[bool] = mapped_column(Boolean, default=False)
    campaign: Mapped[str | None] = mapped_column(String(96), nullable=True)


class PrescriberOwner(Base):
    """Territory / rep assignment (PR-05): exactly one owner per prescriber."""

    __tablename__ = "prescriber_owners"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prescriber_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(32))
    territory: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Vendor(Base):
    """Vendor and BAA register (C-01 / CC-07). A PHI vendor without an active BAA blocks its connection."""

    __tablename__ = "vendors"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    service: Mapped[str] = mapped_column(String(128))
    connection_key: Mapped[str | None] = mapped_column(String(48), nullable=True)  # cloud | llm | telephony | email
    touches_phi: Mapped[bool] = mapped_column(Boolean)
    baa_status: Mapped[str] = mapped_column(String(16))  # signed | pending | none | not_required
    baa_signed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    baa_expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ControlAttestation(Base):
    """Manual evidence for controls that cannot be checked from live data (e.g. C-02 hosting, C-14 pen test)."""

    __tablename__ = "control_attestations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    control_id: Mapped[str] = mapped_column(String(8))
    owner: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))  # green | amber | red
    evidence: Mapped[str] = mapped_column(Text)
    attested_by: Mapped[str] = mapped_column(String(32))
    attested_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    next_review_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class AccessReview(Base):
    __tablename__ = "access_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    period: Mapped[str] = mapped_column(String(16))  # e.g. 2026-Q4
    reviewer_id: Mapped[str] = mapped_column(String(32))
    signed_off_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    users_reviewed: Mapped[int] = mapped_column(Integer)


class Alert(Base):
    """Unusual-access alerts (CC-05): bulk views, off-hours exports, offshore PHI access."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    acknowledged_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AiInteraction(Base):
    """AI governance log (C-07 / CC-06). Prompts and responses are PHI: only their SHA-256 is kept here."""

    __tablename__ = "ai_interactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    task: Mapped[str] = mapped_column(String(48))
    purpose: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(96))
    fields_sent: Mapped[list[str]] = mapped_column(JSON)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    response_sha256: Mapped[str] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    fallback_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    disposition: Mapped[str] = mapped_column(String(16), default="shown")  # shown | accepted | edited | discarded
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class DataConnection(Base):
    """A configured client source. ``secret_ref`` names a vault secret; the secret itself is never stored here."""

    __tablename__ = "data_connections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))  # snowflake | sqlserver | postgres | mysql | oracle | sqlite
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    database: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    auth_method: Mapped[str] = mapped_column(String(32))  # key_pair | password | managed_identity | file
    secret_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="read")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    vendor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(String(255), nullable=True)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # scheduled | manual | reconcile
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    watermark: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AuditEvent(Base):
    """Append-only, hash-chained audit log (C-06). Details hold IDs and counts, never PHI values."""

    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    actor_id: Mapped[str] = mapped_column(String(32), index=True)
    actor_role: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    patient_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


@overload
def as_utc(ts: datetime) -> datetime: ...
@overload
def as_utc(ts: None) -> None: ...
@overload
def as_utc(ts: datetime | None) -> datetime | None: ...
def as_utc(ts: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything we write is UTC, so re-attach the zone."""
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
