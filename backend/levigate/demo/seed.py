"""One-command demo reset: rebuild the synthetic warehouse and the app database, then run the first sync.

    uv run python -m levigate.demo.seed

Everything here is fictional. The environment it builds is for demos and development only.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import make_url

from levigate import audit, catalog, clock
from levigate.config import Settings, get_settings
from levigate.connectors import ReadOnlyConnector, init_connector
from levigate.db import Base, create_schema, init_engine, sessionmaker
from levigate.demo.warehouse import World, build_warehouse
from levigate.models import (
    AccessReview,
    Approval,
    Consent,
    ControlAttestation,
    CrmActivity,
    DataConnection,
    OutreachContact,
    PrescriberOwner,
    User,
    Vendor,
    WorkItem,
    WorkItemEvent,
    WorkItemStatus,
    WorkItemType,
)
from levigate.sources import DEMO_LAYOUT
from levigate.sync import SERVICE_USER_ID, run_sync, service_actor

USERS = [
    # id, name, email, role, location, manager
    ("u-billing", "Jamie Patel", "jamie.patel@demo.levigate.example", "billing", "US", "u-admin"),
    ("u-billing-in", "Ravi Kumar", "ravi.kumar@demo.levigate.example", "billing", "IN", "u-billing"),
    ("u-outreach", "Ana Ruiz", "ana.ruiz@demo.levigate.example", "outreach", "US", "u-admin"),
    ("u-marketing", "Marcus Lee", "marcus.lee@demo.levigate.example", "marketing", "US", "u-admin"),
    ("u-pharmacist", "Priya Shah, PharmD", "priya.shah@demo.levigate.example", "pharmacist", "US", "u-admin"),
    ("u-admin", "Sam Okafor", "sam.okafor@demo.levigate.example", "admin", "US", None),
    ("u-admin-2", "Alex Chen", "alex.chen@demo.levigate.example", "admin", "US", "u-admin"),
    ("u-auditor", "Dana Whitfield", "dana.whitfield@demo.levigate.example", "auditor", "US", None),
]


def _sqlite_path(url: str) -> Path:
    return Path(make_url(url).database or "")


def _at(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(d, time(hour, minute), tzinfo=UTC)


async def reset_demo(
    settings: Settings | None = None,
    *,
    today: date | None = None,
    as_of: date | None = None,
    seed: int = 4471,
    n_patients: int = 2000,
    n_prescribers: int = 150,
) -> dict[str, Any]:
    s = settings or get_settings()
    today = today or clock.today()
    rng = random.Random(seed)
    tenant = s.tenant_id

    wh_path = _sqlite_path(s.warehouse_url)
    layout = DEMO_LAYOUT[s.source_profile]
    world = build_warehouse(
        wh_path, today, layout=layout, as_of=as_of, seed=seed, n_patients=n_patients, n_prescribers=n_prescribers
    )
    story = world.story

    if s.app_db_url.startswith("sqlite"):
        app_path = _sqlite_path(s.app_db_url)
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(app_path) + suffix)
            if p.exists():
                p.unlink()
    engine = init_engine(s.app_db_url)
    if not s.app_db_url.startswith("sqlite"):
        from levigate import models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await create_schema(engine)
    connector = init_connector(
        s.warehouse_url, s.connector_allowed_hosts, s.connector_query_timeout_s, s.source_profile
    )

    sm = sessionmaker()
    async with sm() as db:
        db.add(
            User(
                id=SERVICE_USER_ID,
                tenant_id=tenant,
                email="svc-sync@levigate.internal",
                name="Levigate sync",
                role="service",
                is_service=True,
            )
        )
        for uid, name, email, role, loc, mgr in USERS:
            db.add(User(id=uid, tenant_id=tenant, name=name, email=email, role=role, location=loc, manager_id=mgr))
        await db.flush()
        await catalog.seed(db, tenant, author_id="u-admin", approver_id="u-pharmacist")

        db.add_all(
            [
                Vendor(
                    tenant_id=tenant,
                    name="Microsoft Azure",
                    service="Hosting, app database, Key Vault (US regions)",
                    connection_key="cloud",
                    touches_phi=True,
                    baa_status="signed",
                    baa_signed_on=today - timedelta(days=300),
                    baa_expires_on=today + timedelta(days=430),
                    owner="Sam Okafor",
                ),
                Vendor(
                    tenant_id=tenant,
                    name="Azure OpenAI Service",
                    service="LLM endpoint (BAA-covered deployment)",
                    connection_key="llm",
                    touches_phi=True,
                    baa_status="signed",
                    baa_signed_on=today - timedelta(days=300),
                    baa_expires_on=today + timedelta(days=430),
                    owner="Sam Okafor",
                ),
                Vendor(
                    tenant_id=tenant,
                    name="VoiceBridge Telephony (demo)",
                    service="Click-to-call and call status",
                    connection_key="telephony",
                    touches_phi=True,
                    baa_status="signed",
                    baa_signed_on=today - timedelta(days=320),
                    baa_expires_on=today + timedelta(days=45),
                    owner="Ana Ruiz",
                    notes="Renewal paperwork sent to vendor",
                ),
                Vendor(
                    tenant_id=tenant,
                    name="Snowflake (client warehouse)",
                    service="Client data warehouse (read-only)",
                    connection_key="warehouse",
                    touches_phi=True,
                    baa_status="signed",
                    baa_signed_on=today - timedelta(days=500),
                    baa_expires_on=today + timedelta(days=230),
                    owner="Client IT",
                ),
                Vendor(
                    tenant_id=tenant,
                    name="HubSpot",
                    service="Marketing CRM (being replaced, HS-01…HS-08)",
                    connection_key="crm",
                    touches_phi=False,
                    baa_status="none",
                    owner="Marcus Lee",
                    notes="Scores and ranked lists only; HS-02 data inventory confirms no PHI",
                ),
            ]
        )
        db.add_all(
            [
                ControlAttestation(
                    tenant_id=tenant,
                    control_id="C-02",
                    owner="Sam Okafor",
                    status="green",
                    evidence="App, database and Key Vault in the client-approved Azure US tenant "
                    "(East US 2); no PHI outside it.",
                    attested_by="u-admin",
                    next_review_on=today + timedelta(days=90),
                ),
                ControlAttestation(
                    tenant_id=tenant,
                    control_id="C-03",
                    owner="Sam Okafor",
                    status="green",
                    evidence="TLS 1.2+ enforced at the gateway; Azure Database for PostgreSQL, backups, "
                    "logs and storage encrypted at rest (platform keys).",
                    attested_by="u-admin",
                    next_review_on=today + timedelta(days=90),
                ),
                ControlAttestation(
                    tenant_id=tenant,
                    control_id="C-04",
                    owner="Sam Okafor",
                    status="green",
                    evidence="Entra ID conditional access requires MFA for the Levigate app.",
                    attested_by="u-admin",
                    next_review_on=today + timedelta(days=90),
                ),
                ControlAttestation(
                    tenant_id=tenant,
                    control_id="C-13",
                    owner="Dana Whitfield",
                    status="green",
                    evidence="Breach-response runbook v1 with client contacts; alerts route to the compliance owner.",
                    attested_by="u-auditor",
                    next_review_on=today + timedelta(days=180),
                ),
                ControlAttestation(
                    tenant_id=tenant,
                    control_id="C-14",
                    owner="Dana Whitfield",
                    status="amber",
                    evidence="HIPAA risk assessment scheduled before go-live; penetration test booked. "
                    "Builds on GainInsights ISO 27001 controls.",
                    attested_by="u-auditor",
                    next_review_on=today + timedelta(days=30),
                ),
            ]
        )
        db.add_all(
            [
                DataConnection(
                    tenant_id=tenant,
                    name="Demo warehouse (synthetic, read-only)"
                    if layout == "canonical"
                    else "Demo pharmacy system via Talend (synthetic, read-only)",
                    kind="sqlite",
                    database=wh_path.name,
                    auth_method="file",
                    mode="read",
                    enabled=True,
                ),
                DataConnection(
                    tenant_id=tenant,
                    name="Client Snowflake warehouse",
                    kind="snowflake",
                    host="client-account.snowflakecomputing.com",
                    database="PHARMACY",
                    schema_name="ANALYTICS",
                    auth_method="key_pair",
                    secret_ref="kv://levigate/snowflake-reader",  # nosec B106 (vault reference, not a secret)
                    mode="read",
                    enabled=False,
                ),
                DataConnection(
                    tenant_id=tenant,
                    name="PK reporting replica",
                    kind="sqlserver",
                    host="pk-replica.client.internal",
                    database="PKREPORT",
                    auth_method="password",
                    secret_ref="kv://levigate/pk-replica-reader",  # nosec B106 (vault reference, not a secret)
                    mode="read",
                    enabled=False,
                ),
            ]
        )

        # Consent comes from the pharmacy system: the first sync imports it (levigate.sync._import_consent).
        stats_rows = await connector.fetch(
            "prescriber_stats", cur_start=today - timedelta(days=90), prior_start=today - timedelta(days=180)
        )
        cities = {str(r["PRESCRIBER_ID"]): r["CITY"] for r in stats_rows}
        for rid, city in cities.items():
            db.add(PrescriberOwner(tenant_id=tenant, prescriber_id=rid, owner_id="u-marketing", territory=city))

        # CRM history: recent calls and visits, a few next actions due this week, the declining story untouched
        # for 75 days so it surfaces as "no recent contact".
        prescriber_ids = sorted(cities)
        for rid in rng.sample(prescriber_ids, min(45, len(prescriber_ids) // 3)):
            if rid == story.declining_prescriber:
                continue
            for _ in range(rng.randint(1, 3)):
                when = datetime.now(UTC) - timedelta(days=rng.randint(3, 80), hours=rng.randint(0, 8))
                db.add(
                    CrmActivity(
                        tenant_id=tenant,
                        prescriber_id=rid,
                        kind=rng.choice(["call", "visit", "email"]),
                        outcome=rng.choice(
                            [
                                "Spoke with office manager",
                                "Left materials",
                                "Discussed PA turnaround",
                                "Booked lunch-and-learn",
                            ]
                        ),
                        owner_id="u-marketing",
                        created_at=when,
                        next_action_done=True,
                    )
                )
        for rid in rng.sample([p for p in prescriber_ids if p != story.declining_prescriber], 6):
            db.add(
                CrmActivity(
                    tenant_id=tenant,
                    prescriber_id=rid,
                    kind="note",
                    outcome=None,
                    notes="Follow-up planned",
                    owner_id="u-marketing",
                    created_at=datetime.now(UTC) - timedelta(days=10),
                    next_action=rng.choice(
                        ["Drop off formulary guide", "Call office manager", "Visit with PA checklist"]
                    ),
                    next_action_due=today + timedelta(days=rng.randint(-2, 6)),
                )
            )
        db.add(
            CrmActivity(
                tenant_id=tenant,
                prescriber_id=story.declining_prescriber,
                kind="visit",
                outcome="Lunch-and-learn on topical compounds",
                owner_id="u-marketing",
                created_at=datetime.now(UTC) - timedelta(days=75),
                next_action_done=True,
            )
        )

        db.add(
            AccessReview(
                tenant_id=tenant,
                period=_previous_quarter(today),
                reviewer_id="u-admin",
                signed_off_at=datetime.now(UTC) - timedelta(days=rng.randint(20, 60)),
                notes="All roles confirmed with managers.",
                users_reviewed=len(USERS),
            )
        )
        await db.commit()

    async with sm() as db:
        run = await run_sync(db, connector, tenant, today, kind="manual")

    async with sm() as db:
        history = await _seed_history(db, connector, world, tenant, rng)
        async with audit.audit_transaction(db):
            await audit.record(
                db,
                service_actor(tenant),
                "demo.reset",
                "environment",
                tenant,
                detail={"seed": seed, "work_items": run.stats, **history},
            )
    await engine.dispose()
    return {
        "today": today.isoformat(),
        "sync": run.stats,
        **history,
        "story": {
            "rx_4471": "4471-02",
            "rx_4471_fill_id": story.rx_4471_fill_id,
            "rx_4471_patient": story.rx_4471_patient,
            "declining_prescriber": story.declining_prescriber,
            "unknown_code_rx": story.unknown_code_rx,
        },
        "source_profile": s.source_profile,
        "layout": layout,
        "mess": world.mess,
    }


def _previous_quarter(today: date) -> str:
    q = (today.month - 1) // 3 + 1
    return f"{today.year}-Q{q - 1}" if q > 1 else f"{today.year - 1}-Q4"


async def _seed_history(
    db: Any, connector: ReadOnlyConnector, world: World, tenant: str, rng: random.Random
) -> dict[str, int]:
    """Eight weeks of resolved work: rejected claims a biller approved a fix for, which the payer then paid.

    The history comes from the simulated world; each fill's source ID is looked up through the connector, so this
    works on any source layout."""
    history = world.resolved_history
    rx_numbers = sorted({h["rx_number"] for h in history})
    fill_ids: dict[tuple[str, int], str] = {}
    for i in range(0, len(rx_numbers), 500):
        for f in await connector.fetch("fills_for_rx", rx_numbers=rx_numbers[i : i + 500]):
            fill_ids[(str(f["RX_NUMBER"]), int(f["FILL_NUMBER"]))] = str(f["FILL_ID"])
    wanted = sorted(
        {
            fill_ids[(h["rx_number"], h["fill_number"])]
            for h in history
            if (h["rx_number"], h["fill_number"]) in fill_ids
        }
    )
    claim_ids: dict[str, str] = {}
    for i in range(0, len(wanted), 500):
        for c in await connector.fetch("claims_for_fills", fill_ids=wanted[i : i + 500]):
            claim_ids[str(c["FILL_ID"])] = str(c["CLAIM_ID"])
    playbook = await catalog.current_map(db, tenant, "reject_playbook")
    approvers = ["u-billing", "u-billing", "u-billing", "u-pharmacist"]
    count = 0
    for h in history:
        if rng.random() > 0.7:
            continue  # some were fixed outside Levigate before the pilot
        fill_id = fill_ids.get((h["rx_number"], h["fill_number"]))
        if fill_id is None:
            continue
        r = {
            "FILL_ID": fill_id,
            "CLAIM_ID": claim_ids.get(fill_id, ""),
            "RX_NUMBER": h["rx_number"],
            "FILL_NUMBER": h["fill_number"],
            "PATIENT_ID": h["patient_id"],
            "PRESCRIBER_ID": h["prescriber_id"],
            "PAYER_ID": h["payer_id"],
            "LOCATION": h["location"],
            "FILL_DATE": h["fill_date"].isoformat(),
            "REJECT_CODE": h["reject_code"],
            "REJECTED_AT": h["rejected_at"].isoformat(),
            "RESUBMITTED_AT": h["resubmitted_at"].isoformat(),
            "PAID_AMOUNT": h["paid"],
            "REMITTED_AT": h["remitted_at"].isoformat(),
            "EXPECTED_AMOUNT": h["expected"],
        }
        entry = playbook[r["REJECT_CODE"]]
        rejected_at = datetime.fromisoformat(r["REJECTED_AT"]).replace(tzinfo=UTC)
        resubmitted = datetime.fromisoformat(r["RESUBMITTED_AT"]).replace(tzinfo=UTC)
        remitted = datetime.fromisoformat(r["REMITTED_AT"]).replace(tzinfo=UTC)
        approved_at = max(rejected_at + timedelta(minutes=20), resubmitted - timedelta(hours=rng.randint(1, 20)))
        approver = rng.choice(approvers) if entry.body["owner_role"] == "billing" else "u-pharmacist"
        item = WorkItem(
            tenant_id=tenant,
            type=WorkItemType.CLAIM_REJECTED,
            status=WorkItemStatus.RESOLVED,
            queue_role=entry.body["owner_role"],
            fill_id=r["FILL_ID"],
            claim_id=r["CLAIM_ID"],
            rx_number=r["RX_NUMBER"],
            fill_number=r["FILL_NUMBER"],
            patient_id=r["PATIENT_ID"],
            prescriber_id=r["PRESCRIBER_ID"],
            payer_id=r["PAYER_ID"],
            location=r["LOCATION"],
            reject_code=r["REJECT_CODE"],
            fill_date=date.fromisoformat(r["FILL_DATE"]),
            amount_at_risk=Decimal(str(r["EXPECTED_AMOUNT"])),
            due_date=approved_at.date(),
            rule_kind="reject_playbook",
            rule_key=entry.key,
            rule_version=entry.version,
            suggested_fix={
                "action": "resubmit",
                "summary": "Resubmitted after fix",
                "due_date": resubmitted.date().isoformat(),
                "owner_role": entry.body["owner_role"],
                "approve_label": "Approve",
                "steps": [],
                "computed": {},
            },
            outcome="paid_after_fix",
            source_status="paid",
            assignee_id=approver,
            recovered_amount=Decimal(str(r["PAID_AMOUNT"])),
            created_at=rejected_at,
            updated_at=remitted,
            resolved_at=remitted,
        )
        db.add(item)
        await db.flush()
        appr = Approval(
            tenant_id=tenant,
            subject_type="work_item",
            subject_id=str(item.id),
            action="resubmit",
            approver_id=approver,
            approved_at=approved_at,
            rule_key=entry.key,
            rule_version=entry.version,
            idempotency_key=f"seed-{item.id}",
        )
        db.add(appr)
        db.add_all(
            [
                WorkItemEvent(
                    work_item_id=item.id,
                    actor_id=SERVICE_USER_ID,
                    ts=rejected_at,
                    kind="created",
                    summary=f"Rejected {r['REJECT_CODE']}; playbook rule {entry.key} v{entry.version} applied",
                ),
                WorkItemEvent(
                    work_item_id=item.id,
                    actor_id=approver,
                    ts=approved_at,
                    kind="approved",
                    summary="Approved fix and resubmission",
                ),
                WorkItemEvent(
                    work_item_id=item.id,
                    actor_id=SERVICE_USER_ID,
                    ts=remitted,
                    kind="resolved",
                    summary=f"Paid after approved fix: ${float(r['PAID_AMOUNT']):,.2f} recovered",
                ),
            ]
        )
        count += 1

    # Outreach history: calls made only to consented patients, with outcomes.
    consented = list(
        (
            await db.scalars(
                select(Consent.patient_id).where(
                    Consent.tenant_id == tenant, Consent.channel == "call", Consent.status == "granted"
                )
            )
        ).all()
    )
    calls = 0
    today = world.as_of
    for days_ago in range(1, 57):
        day = today - timedelta(days=days_ago)
        if day.weekday() >= 5:
            continue
        for _ in range(rng.randint(5, 9)):
            started = _at(day, rng.randint(14, 22), rng.randint(0, 59))
            outcome = rng.choices(
                ["refill_confirmed", "reached", "voicemail", "no_answer", "wrong_number"], [42, 18, 22, 14, 4]
            )[0]
            db.add(
                OutreachContact(
                    tenant_id=tenant,
                    patient_id=rng.choice(consented),
                    list_type="refill_due",
                    user_id="u-outreach",
                    started_at=started,
                    consent_verified=True,
                    outcome=outcome,
                    logged_at=started + timedelta(minutes=rng.randint(2, 6)),
                )
            )
            calls += 1
    await db.commit()
    return {"history_resolved_items": count, "history_calls": calls}


def main() -> None:
    result = asyncio.run(reset_demo())
    print("Demo environment reset (synthetic data only).")
    for k, v in result.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
