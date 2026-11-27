"""Business catalog: the written-down business logic the rules engine executes and the AI may only read.

Every entry is versioned. A change is *proposed* by one admin and *approved* by a different one (AD-03); the
approved version becomes current and the old one is superseded, never edited. Work items record the rule key and
version that produced them, so every outcome traces back to the exact rule text in force at the time.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from levigate import audit
from levigate.audit import Actor
from levigate.models import CatalogEntry, utcnow

KINDS = (
    "reject_playbook",
    "payer_config",
    "metric",
    "eligibility_rule",
    "prescriber_scoring",
    "glossary",
    "data_dictionary",
    "team_goal",
)


class CatalogError(ValueError):
    pass


async def current(session: AsyncSession, tenant_id: str, kind: str, key: str) -> CatalogEntry | None:
    return await session.scalar(
        select(CatalogEntry).where(
            CatalogEntry.tenant_id == tenant_id,
            CatalogEntry.kind == kind,
            CatalogEntry.key == key,
            CatalogEntry.is_current.is_(True),
        )
    )


async def current_map(session: AsyncSession, tenant_id: str, kind: str) -> dict[str, CatalogEntry]:
    rows = await session.scalars(
        select(CatalogEntry).where(
            CatalogEntry.tenant_id == tenant_id, CatalogEntry.kind == kind, CatalogEntry.is_current.is_(True)
        )
    )
    return {r.key: r for r in rows}


async def history(session: AsyncSession, tenant_id: str, kind: str, key: str) -> list[CatalogEntry]:
    rows = await session.scalars(
        select(CatalogEntry)
        .where(CatalogEntry.tenant_id == tenant_id, CatalogEntry.kind == kind, CatalogEntry.key == key)
        .order_by(CatalogEntry.version.desc())
    )
    return list(rows)


async def propose(
    session: AsyncSession, actor: Actor, kind: str, key: str, title: str, body: dict[str, Any], note: str
) -> CatalogEntry:
    """Create a proposed new version. Call inside ``audit_transaction``."""
    if kind not in KINDS:
        raise CatalogError(f"unknown catalog kind '{kind}'")
    versions = await history(session, actor.tenant_id, kind, key)
    if any(v.status == "proposed" for v in versions):
        raise CatalogError("A change to this entry is already waiting for approval.")
    entry = CatalogEntry(
        tenant_id=actor.tenant_id,
        kind=kind,
        key=key,
        version=(versions[0].version + 1) if versions else 1,
        title=title,
        body=body,
        status="proposed",
        is_current=False,
        change_note=note,
        created_by=actor.id,
    )
    session.add(entry)
    await session.flush()
    await audit.record(
        session,
        actor,
        "catalog.propose",
        "catalog_entry",
        entry.id,
        detail={"kind": kind, "key": key, "version": entry.version},
    )
    return entry


async def approve(session: AsyncSession, actor: Actor, entry_id: int) -> CatalogEntry:
    """Approve a proposed version (by someone other than its author) and make it current."""
    entry = await session.get(CatalogEntry, entry_id)
    if entry is None or entry.tenant_id != actor.tenant_id:
        raise CatalogError("No such catalog change.")
    if entry.status != "proposed":
        raise CatalogError("Only a proposed change can be approved.")
    if entry.created_by == actor.id:
        raise CatalogError("A change must be approved by a different admin than the one who proposed it.")
    previous = await current(session, actor.tenant_id, entry.kind, entry.key)
    await session.execute(
        update(CatalogEntry)
        .where(
            CatalogEntry.tenant_id == actor.tenant_id,
            CatalogEntry.kind == entry.kind,
            CatalogEntry.key == entry.key,
            CatalogEntry.is_current.is_(True),
        )
        .values(is_current=False, status="superseded")
    )
    entry.status, entry.is_current = "approved", True
    entry.approved_by, entry.approved_at = actor.id, utcnow()
    await audit.record(
        session,
        actor,
        "catalog.approve",
        "catalog_entry",
        entry.id,
        detail={
            "kind": entry.kind,
            "key": entry.key,
            "version": entry.version,
            "previous_version": previous.version if previous else None,
        },
    )
    return entry


# ---------------------------------------------------------------------------------------------------------------
# Seed: the first catalog, as the billing lead and pharmacist would sign it off in weeks 1–2 of the pilot.
# ---------------------------------------------------------------------------------------------------------------

PLAYBOOK: dict[str, dict[str, Any]] = {
    "79": {
        "title": "Refill too soon",
        "handler": "refill_too_soon",
        "owner_role": "billing",
        "rule_text": (
            "Compute the next eligible date from the last dispensed fill and its days supply, using the payer's "
            "early-refill threshold (payer config, default 80%). Schedule the resubmission for that date. Do not "
            "resubmit before it."
        ),
        "params": {"default_refill_pct": 0.8},
    },
    "75": {
        "title": "Prior authorization required",
        "handler": "prior_auth",
        "owner_role": "billing",
        "rule_text": (
            "Start a prior authorization with the prescriber the same day: send the PA request with diagnosis, "
            "tried-and-failed therapies and the compound's medical necessity. Follow up every 2 business days."
        ),
        "params": {"followup_days": 2},
    },
    "76": {
        "title": "Plan limitations exceeded",
        "handler": "plan_limit",
        "owner_role": "billing",
        "rule_text": (
            "Check the quantity and days supply against the plan limit. If the prescriber agrees, resubmit within "
            "the limit; otherwise request a quantity-limit override or PA."
        ),
        "params": {"plan_max_days_supply": 30},
    },
    "70": {
        "title": "Product or service not covered",
        "handler": "not_covered",
        "owner_role": "billing",
        "rule_text": (
            "Ask the prescriber whether to request a formulary exception. Offer the patient the cash price only "
            "if they ask; never suggest switching therapy to the patient (HIPAA refill-reminder scope)."
        ),
        "params": {},
    },
    "MR": {
        "title": "Product not on formulary",
        "handler": "not_covered",
        "owner_role": "billing",
        "rule_text": "Request a formulary exception through the prescriber; attach the medical-necessity letter.",
        "params": {},
    },
    "65": {
        "title": "Patient not covered",
        "handler": "coverage_lookup",
        "owner_role": "billing",
        "rule_text": "Run an eligibility check (E1) for active coverage, update the plan on file, then resubmit.",
        "params": {},
    },
    "41": {
        "title": "Bill primary payer first",
        "handler": "cob_primary",
        "owner_role": "billing",
        "rule_text": "Submit to the primary payer first, then resubmit to this plan as secondary with COB data.",
        "params": {},
    },
    "25": {
        "title": "Missing or invalid prescriber ID",
        "handler": "verify_prescriber",
        "owner_role": "billing",
        "rule_text": "Verify the prescriber's NPI against NPPES, correct it on the claim and resubmit.",
        "params": {},
    },
    "88": {
        "title": "DUR reject",
        "handler": "dur_review",
        "owner_role": "pharmacist",
        "rule_text": (
            "A pharmacist reviews the DUR conflict. If clinically appropriate, resubmit with the DUR/PPS "
            "intervention codes; if not, contact the prescriber."
        ),
        "params": {},
    },
}

PAYER_CONFIG: dict[str, dict[str, Any]] = {
    "PY01": {"payment_window_days": 14, "underpay_pct": 0.05, "underpay_min_amount": 10, "refill_pct": 0.8},
    "PY02": {"payment_window_days": 14, "underpay_pct": 0.05, "underpay_min_amount": 10, "refill_pct": 0.8},
    "PY03": {"payment_window_days": 21, "underpay_pct": 0.05, "underpay_min_amount": 10, "refill_pct": 0.85},
    "PY04": {"payment_window_days": 14, "underpay_pct": 0.05, "underpay_min_amount": 10, "refill_pct": 0.75},
    "PY05": {"payment_window_days": 30, "underpay_pct": 0.08, "underpay_min_amount": 10, "refill_pct": 0.8},
    "PY06": {"payment_window_days": 14, "underpay_pct": 0.05, "underpay_min_amount": 10, "refill_pct": 0.8},
    "PY07": {"payment_window_days": 45, "underpay_pct": 0.05, "underpay_min_amount": 25, "refill_pct": 0.8},
}
DEFAULT_PAYER_CONFIG = {"payment_window_days": 14, "underpay_pct": 0.05, "underpay_min_amount": 10, "refill_pct": 0.8}

METRICS = {
    "unpaid_claim": ("Unpaid claim", "No paid response N days after fill, N set per payer (payer config)."),
    "underpaid_claim": (
        "Underpaid claim",
        "Paid below the expected amount by more than the payer's threshold percentage and minimum dollar amount.",
    ),
    "days_to_payment": ("Days to payment", "Median days from fill date to remittance, for paid claims."),
    "refill_capture_rate": (
        "Refill capture rate",
        "Share of refills that came due (with refills remaining) that were filled within 7 days of the due date.",
    ),
    "rejection_backlog": ("Rejection backlog", "Open rejected claims whose fill is older than 7 days."),
    "dollars_recovered": (
        "Dollars recovered",
        "Dollars paid on claims that went through a Levigate work item with a named approval.",
    ),
    "prescriber_reactivation": (
        "Prescriber reactivation",
        "Declining prescribers contacted who increase Rx volume in the 60 days after contact.",
    ),
}

GLOSSARY = {
    "paid": "Adjudicated and remitted.",
    "captured": "Accepted by the payer, not yet remitted.",
    "rejected": "The payer refused the claim; the response carries a reject code.",
    "reversed": "A paid or captured claim the pharmacy or payer reversed.",
    "pending": "Submitted, no response yet — or captured and still inside the payer's payment window.",
}

DATA_DICTIONARY = {
    "FACT_RXFILL.RX_NUMBER": "Prescription number; joins to claims on Rx number + fill number.",
    "FACT_RXFILL.FILL_NUMBER": "0 = original fill, 1 = first refill, and so on.",
    "FACT_CLAIM.RESPONSE_STATUS": "P paid (remitted), C captured, R rejected, X reversed, empty = no response.",
    "FACT_CLAIM.REJECT_CODE": "NCPDP reject code of a rejected response.",
    "FACT_CLAIM.EXPECTED_AMOUNT": "Contracted reimbursement for the claim.",
}


def _seed_entries() -> list[tuple[str, str, str, dict[str, Any]]]:
    entries: list[tuple[str, str, str, dict[str, Any]]] = []
    for code, body in PLAYBOOK.items():
        entries.append(("reject_playbook", code, body["title"], {"code": code, **body}))
    for payer_id, cfg in PAYER_CONFIG.items():
        entries.append(("payer_config", payer_id, f"Payer rules {payer_id}", cfg))
    for key, (title, text) in METRICS.items():
        entries.append(("metric", key, title, {"definition": text}))
    for key, text in GLOSSARY.items():
        entries.append(("glossary", key, key.title(), {"definition": text}))
    for key, text in DATA_DICTIONARY.items():
        entries.append(("data_dictionary", key, key, {"definition": text}))
    entries.append(
        (
            "eligibility_rule",
            "refill_call",
            "Refill call eligibility",
            {
                "rule_text": (
                    "Refill call if: due within 7 days, call consent on file, not opted out, Rx not lapsed over 90 "
                    "days, refills remaining and Rx not expired. Only currently prescribed drugs; never promote a "
                    "switch to another therapy."
                ),
                "due_within_days": 7,
                "adherence_overdue_days": 8,
                "lapse_days": 90,
                "consent_channel": "call",
                "priority_weights": {"urgency": 0.5, "revenue": 0.3, "contact_gap": 0.2},
            },
        )
    )
    entries.append(
        (
            "prescriber_scoring",
            "default",
            "Prescriber scoring",
            {
                "rule_text": (
                    "Rank by 90-day Rx volume, revenue and trend vs the prior 90 days. Declining = prior volume of at "
                    "least 6 and a drop of 30% or more. New = first Rx in the last 90 days. Dormant = no Rx in 90 days."
                ),
                "weights": {"volume": 0.5, "revenue": 0.35, "trend": 0.15},
                "declining_drop": 0.3,
                "declining_min_prior": 6,
                "new_days": 90,
                "dormant_days": 90,
                "top_n": 25,
            },
        )
    )
    entries.append(
        (
            "team_goal",
            "weekly",
            "Weekly team goals",
            {"billing_recovered_dollars": 9000, "outreach_refills_confirmed": 60, "marketing_contacts": 25},
        )
    )
    return entries


async def seed(session: AsyncSession, tenant_id: str, author_id: str, approver_id: str) -> int:
    """Write version 1 of every entry (plus the reject-79 history to v3). No-op if the catalog is not empty."""
    exists = await session.scalar(select(CatalogEntry.id).where(CatalogEntry.tenant_id == tenant_id).limit(1))
    if exists:
        return 0
    now = utcnow()
    count = 0
    for kind, key, title, body in _seed_entries():
        if kind == "reject_playbook" and key == "79":
            # Show the catalog's history: v1 used a flat 75%, v2 80%, v3 takes the threshold from payer config.
            history_rows = [
                (
                    {
                        **body,
                        "params": {"default_refill_pct": 0.75},
                        "rule_text": "Resubmit when 75% of the days supply has elapsed since the last fill.",
                    },
                    "Initial playbook entry",
                ),
                (
                    {
                        **body,
                        "params": {"default_refill_pct": 0.8},
                        "rule_text": "Resubmit when 80% of the days supply has elapsed since the last fill.",
                    },
                    "Most plans allow refills at 80%",
                ),
            ]
            for version, (old_body, note) in enumerate(history_rows, start=1):
                session.add(
                    CatalogEntry(
                        tenant_id=tenant_id,
                        kind=kind,
                        key=key,
                        version=version,
                        title=title,
                        body=old_body,
                        status="superseded",
                        is_current=False,
                        change_note=note,
                        created_by=author_id,
                        created_at=now,
                        approved_by=approver_id,
                        approved_at=now,
                    )
                )
            session.add(
                CatalogEntry(
                    tenant_id=tenant_id,
                    kind=kind,
                    key=key,
                    version=3,
                    title=title,
                    body=body,
                    status="approved",
                    is_current=True,
                    change_note="Use each payer's early-refill threshold from payer config",
                    created_by=author_id,
                    created_at=now,
                    approved_by=approver_id,
                    approved_at=now,
                )
            )
            count += 3
            continue
        session.add(
            CatalogEntry(
                tenant_id=tenant_id,
                kind=kind,
                key=key,
                version=1,
                title=title,
                body=body,
                status="approved",
                is_current=True,
                change_note="Initial catalog, signed off by billing lead and pharmacist",
                created_by=author_id,
                created_at=now,
                approved_by=approver_id,
                approved_at=now,
            )
        )
        count += 1
    await session.flush()
    return count
