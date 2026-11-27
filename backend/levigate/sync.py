"""Sync model: scheduled pull → rules → work items, plus reconciliation against the source.

* Scheduled pull (every 15 minutes, configurable): claims whose latest state changed since the last watermark.
  The watermark is the source's own change marker (e.g. Talend's LOAD_TS), re-read with a 30-minute overlap.
* Full scan: the first run of each business day, and at least every 24 hours. A claim's status also depends on the
  date (a claim with no payment becomes past due when the payer's window ends), and that changes nothing in the
  source, so an incremental pull alone would never see it. Status is computed per business day, so one full scan
  per day is exactly enough.
* Reconciliation (every run, cheap at pilot scale): every open work item is re-checked against its source fill;
  anything paid or reversed upstream closes automatically. Approved items that end up paid count as recovered.
* The job runs as its own least-privilege service identity and writes to the audit log like a user does.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from levigate import audit, catalog, quality
from levigate.audit import Actor
from levigate.connectors import ReadOnlyConnector
from levigate.models import (
    OPEN_STATUSES,
    Consent,
    SyncRun,
    WorkItem,
    WorkItemEvent,
    WorkItemStatus,
    WorkItemType,
    as_utc,
    utcnow,
)
from levigate.rules import (
    WORK_TYPE_FOR_STATUS,
    PayerRules,
    add_business_days,
    amount_at_risk,
    derive_status,
    playbook_fix,
    to_date,
    to_datetime,
    underpaid_fix,
    unpaid_fix,
)

log = logging.getLogger(__name__)

SERVICE_USER_ID = "svc-sync"
LOOKBACK_DAYS = 120

STEP_LABELS = [
    ("connect", "Connected to warehouse"),
    ("rules", "Reject-code rules applied"),
    ("items", "Building work items"),
    ("reconcile", "Reconciled with source"),
    ("consent", "Consent synced from the pharmacy system"),
    ("quality", "Data checks"),
]

# The incremental pull follows the *source's* change marker (e.g. Talend's LOAD_TS), not our own clock: a row that
# lands late with an older timestamp must not be skipped. A small overlap re-reads the edge; a full scan of the
# lookback window once a day is the safety net for anything an incremental pull could still miss.
WATERMARK_OVERLAP = timedelta(minutes=30)
FULL_SCAN_EVERY = timedelta(hours=24)
EPOCH = "2000-01-01T00:00:00"


def service_actor(tenant_id: str) -> Actor:
    return Actor(id=SERVICE_USER_ID, role="service", tenant_id=tenant_id, location="US")


class _RuleSet:
    def __init__(self, playbook: Mapping[str, Any], payers: Mapping[str, Any]) -> None:
        self.playbook = playbook
        self.payers = payers

    @classmethod
    async def load(cls, session: AsyncSession, tenant_id: str) -> _RuleSet:
        return cls(
            await catalog.current_map(session, tenant_id, "reject_playbook"),
            await catalog.current_map(session, tenant_id, "payer_config"),
        )

    def payer(self, payer_id: str) -> PayerRules:
        entry = self.payers.get(payer_id)
        return PayerRules.from_body(entry.body if entry else catalog.DEFAULT_PAYER_CONFIG)


def _item_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": str(row["CLAIM_ID"]),
        "rx_number": str(row["RX_NUMBER"]),
        "fill_number": int(row["FILL_NUMBER"]),
        "patient_id": str(row["PATIENT_ID"]),
        "prescriber_id": str(row["PRESCRIBER_ID"]),
        "payer_id": str(row["PAYER_ID"]),
        "location": str(row["LOCATION"]),
        "fill_date": to_date(row["FILL_DATE"]),
    }


# Service levels: how many business days a person has to act once the work exists (catalog playbook may override).
DEFAULT_SLA_DAYS = {
    WorkItemType.CLAIM_REJECTED: 1,
    WorkItemType.REJECT_REVIEW: 1,
    WorkItemType.CLAIM_UNPAID: 2,
    WorkItemType.CLAIM_UNDERPAID: 5,
}


def action_due_date(item_type: str, row: Mapping[str, Any], payer: PayerRules, entry: Any | None) -> date:
    """When a person must act — counted from when the problem appeared at the source, not from when we synced it.

    So a backlog loaded on day one shows as overdue (IF-04), and the fix's own date (e.g. "resubmit on 12 Dec")
    becomes the follow-up date once the fix is approved.
    """
    if item_type == WorkItemType.CLAIM_UNPAID:
        started = to_date(row["FILL_DATE"]) + timedelta(days=payer.payment_window_days)
    elif item_type == WorkItemType.CLAIM_UNDERPAID:
        started = to_date(row.get("REMITTED_AT") or row["FILL_DATE"])
    else:
        started = to_date(row.get("ADJUDICATED_AT") or row.get("SUBMITTED_AT") or row["FILL_DATE"])
    sla = DEFAULT_SLA_DAYS.get(WorkItemType(item_type), 1)
    if entry is not None and item_type == WorkItemType.CLAIM_REJECTED:
        sla = int(entry.body.get("sla_days", sla))
    return add_business_days(started, sla)


def _build_item(
    tenant_id: str,
    row: Mapping[str, Any],
    status: str,
    rules: _RuleSet,
    today: date,
    prior_fills: list[Mapping[str, Any]],
) -> WorkItem:
    payer = rules.payer(str(row["PAYER_ID"]))
    item_type = WORK_TYPE_FOR_STATUS[status]
    rule_kind = rule_key = None
    rule_version = None
    fix = None
    queue = "billing"
    if item_type == WorkItemType.CLAIM_REJECTED:
        entry = rules.playbook.get(str(row["REJECT_CODE"]))
        if entry is None:
            item_type = WorkItemType.REJECT_REVIEW  # IF-05: no guess for an unknown code
        else:
            fix_obj = playbook_fix(entry.body, row, payer, today, prior_fills)
            fix, queue = fix_obj.to_dict(), fix_obj.owner_role
            rule_kind, rule_key, rule_version = "reject_playbook", entry.key, entry.version
    elif item_type == WorkItemType.CLAIM_UNPAID:
        fix = unpaid_fix(row, payer, today).to_dict()
        rule_kind, rule_key = "payer_config", str(row["PAYER_ID"])
        rule_version = rules.payers[rule_key].version if rule_key in rules.payers else None
    elif item_type == WorkItemType.CLAIM_UNDERPAID:
        fix = underpaid_fix(row, payer, today).to_dict()
        rule_kind, rule_key = "payer_config", str(row["PAYER_ID"])
        rule_version = rules.payers[rule_key].version if rule_key in rules.payers else None

    return WorkItem(
        tenant_id=tenant_id,
        type=item_type,
        status=WorkItemStatus.OPEN,
        queue_role=queue,
        fill_id=str(row["FILL_ID"]),
        reject_code=row.get("REJECT_CODE"),
        amount_at_risk=Decimal(str(amount_at_risk(row, status))),
        due_date=action_due_date(item_type, row, payer, rules.playbook.get(str(row.get("REJECT_CODE")))),
        rule_kind=rule_kind,
        rule_key=rule_key,
        rule_version=rule_version,
        suggested_fix=fix,
        source_status=status,
        **_item_fields(row),
    )


def _settle(session: AsyncSession, item: WorkItem, row: Mapping[str, Any], status: str, actor: Actor) -> str | None:
    """Close an open item whose claim settled upstream (paid or reversed). Returns the new status or None."""
    item.claim_id, item.source_status = str(row["CLAIM_ID"]), status
    if status not in ("paid", "reversed"):
        return None
    approved = item.status == WorkItemStatus.AWAITING_FOLLOWUP
    if status == "paid" and approved:
        item.status, item.outcome = WorkItemStatus.RESOLVED, "paid_after_fix"
        item.recovered_amount = (
            item.amount_at_risk
            if item.type == WorkItemType.CLAIM_UNDERPAID
            else Decimal(str(round(float(row.get("PAID_AMOUNT") or 0), 2)))
        )
        summary = f"Paid after approved fix: ${float(item.recovered_amount):,.2f} recovered"
    else:
        item.status, item.outcome = WorkItemStatus.CLOSED_UPSTREAM, f"{status}_upstream"
        summary = f"Closed: claim {status} at the source without a Levigate action"
    item.resolved_at = utcnow()
    session.add(
        WorkItemEvent(
            work_item_id=item.id,
            actor_id=actor.id,
            kind="resolved" if approved else "closed_upstream",
            summary=summary,
            detail={"claim_id": str(row["CLAIM_ID"]), "source_status": status},
        )
    )
    return item.status


def _max_change(rows: Sequence[Mapping[str, Any]], column: str, cap: datetime) -> datetime | None:
    """Newest change marker in the batch, ignoring timestamps in the future (bad source clocks)."""
    newest = None
    for r in rows:
        ts = to_datetime(r.get(column))
        if ts is None:
            continue
        ts = ts.replace(tzinfo=None)
        if ts <= cap and (newest is None or ts > newest):
            newest = ts
    return newest


async def _import_consent(
    session: AsyncSession, connector: ReadOnlyConnector, tenant_id: str, since: str
) -> tuple[int, list[dict[str, Any]]]:
    """Bring consent recorded in the pharmacy system into Levigate.

    A source change wins only if it is newer than what Levigate already holds: an opt-out taken on a Levigate call
    stands until the pharmacy system records a newer consent for that channel.
    """
    rows = await connector.fetch("consent_changed_since", since=since)
    if not rows:
        return 0, rows
    latest: dict[tuple[str, str], Consent] = {}
    for c in (await session.scalars(select(Consent).where(Consent.tenant_id == tenant_id).order_by(Consent.id))).all():
        latest[(c.patient_id, c.channel)] = c
    imported = 0
    for r in rows:
        key = (str(r["PATIENT_ID"]), str(r["CHANNEL"]))
        recorded = to_datetime(r["RECORDED_AT"])
        if recorded is None or r["STATUS"] not in ("granted", "revoked"):
            continue
        recorded = recorded if recorded.tzinfo else recorded.replace(tzinfo=UTC)
        current = latest.get(key)
        if current is not None and (current.status == r["STATUS"] or as_utc(current.recorded_at) >= recorded):
            continue
        row = Consent(
            tenant_id=tenant_id,
            patient_id=key[0],
            channel=key[1],
            status=str(r["STATUS"]),
            consent_type=str(r["CONSENT_TYPE"]),
            source="pharmacy system",
            recorded_at=recorded,
            recorded_by=SERVICE_USER_ID,
            synced_back_at=recorded if r["STATUS"] == "revoked" else None,
        )
        session.add(row)
        latest[key] = row
        imported += 1
    return imported, rows


async def run_sync(
    session: AsyncSession,
    connector: ReadOnlyConnector,
    tenant_id: str,
    today: date,
    *,
    kind: str = "scheduled",
    force_full: bool = False,
) -> SyncRun:
    actor = service_actor(tenant_id)
    run = SyncRun(
        tenant_id=tenant_id, kind=kind, steps=[{"key": k, "label": v, "status": "pending"} for k, v in STEP_LABELS]
    )
    session.add(run)
    await session.commit()

    def step(key: str, status: str, label: str | None = None) -> None:
        run.steps = [
            {**s, "status": status, **({"label": label} if label else {})} if s["key"] == key else s for s in run.steps
        ]

    stats: dict[str, Any] = {"rows": 0, "created": 0, "updated": 0, "resolved": 0, "closed_upstream": 0, "review": 0}
    try:
        started = utcnow()
        ok, message = await connector.test()
        if not ok:
            raise RuntimeError(f"warehouse connection failed: {message}")
        step("connect", "done")

        recent = (
            await session.scalars(
                select(SyncRun)
                .where(SyncRun.tenant_id == tenant_id, SyncRun.status == "ok")
                .order_by(SyncRun.id.desc())
                .limit(200)
            )
        ).all()
        last_full = next((r for r in recent if (r.stats or {}).get("full_scan")), None)
        full_scan = (
            force_full
            or last_full is None
            or (last_full.stats or {}).get("business_date") != today.isoformat()
            or started - as_utc(last_full.started_at) >= FULL_SCAN_EVERY
        )
        previous = recent[0] if recent else None
        prev_mark = as_utc(previous.watermark) if previous and previous.watermark else None
        since = (
            EPOCH
            if full_scan or prev_mark is None
            else ((prev_mark - WATERMARK_OVERLAP).replace(tzinfo=None).isoformat(timespec="seconds"))
        )
        rows = await connector.fetch(
            "claims_changed_since", since=since, min_fill_date=today - timedelta(days=LOOKBACK_DAYS)
        )
        cap = started.replace(tzinfo=None) + timedelta(days=1)
        newest = _max_change(rows, "UPDATED_AT", cap)
        mark = max(filter(None, [prev_mark.replace(tzinfo=None) if prev_mark else None, newest]), default=None)
        stats.update(rows=len(rows), full_scan=full_scan, since=since, business_date=today.isoformat())
        rules = await _RuleSet.load(session, tenant_id)
        step("rules", "done")

        async with audit.audit_transaction(session):
            await _apply_rows(session, connector, tenant_id, rows, rules, today, actor, stats)
            step("items", "done")

            # Reconciliation: everything still open, re-read from the source.
            open_items = (
                await session.scalars(
                    select(WorkItem).where(WorkItem.tenant_id == tenant_id, WorkItem.status.in_(OPEN_STATUSES))
                )
            ).all()
            fill_ids = sorted({i.fill_id for i in open_items})
            current: dict[str, Mapping[str, Any]] = {}
            for chunk_start in range(0, len(fill_ids), 500):
                chunk = fill_ids[chunk_start : chunk_start + 500]
                for r in await connector.fetch("claims_for_fills", fill_ids=chunk):
                    current[str(r["FILL_ID"])] = r
            for item in open_items:
                row = current.get(item.fill_id)
                if row is None:
                    continue
                status = derive_status(row, rules.payer(item.payer_id), today)
                new_status = _settle(session, item, row, status, actor)
                if new_status == WorkItemStatus.RESOLVED:
                    stats["resolved"] += 1
                elif new_status == WorkItemStatus.CLOSED_UPSTREAM:
                    stats["closed_upstream"] += 1
            step("reconcile", "done")

            prev_consent = (previous.stats or {}).get("consent_watermark") if previous and not full_scan else None
            imported, consent_rows = await _import_consent(session, connector, tenant_id, prev_consent or EPOCH)
            consent_mark = _max_change(consent_rows, "UPDATED_AT", cap)
            stats["consent_imported"] = imported
            stats["consent_watermark"] = consent_mark.isoformat(timespec="seconds") if consent_mark else prev_consent
            step("consent", "done", f"Consent synced from the pharmacy system ({imported} changes)")

            try:
                checks = await quality.run_quality_checks(connector, today)
                summary = quality.summarize(checks)
                stats["quality"] = summary
                stats["quality_findings"] = [
                    {"key": c.key, "status": c.status, "count": c.count} for c in checks if c.status != "pass"
                ]
                label = (
                    f"Data checks: {summary['fail']} failing, {summary['warn']} warnings"
                    if summary["fail"] or summary["warn"]
                    else "Data checks passed"
                )
                step("quality", "failed" if summary["fail"] else "done", label)
            except Exception:  # data checks must never stop work items from being built
                log.warning("data checks could not run during sync %s", run.id, exc_info=True)
                step("quality", "failed", "Data checks could not run")

            run.status, run.finished_at, run.stats = "ok", utcnow(), stats
            run.watermark = mark.replace(tzinfo=UTC) if mark else None
            await audit.record(
                session,
                actor,
                "sync.run",
                "sync_run",
                run.id,
                detail={"kind": kind, **{k: v for k, v in stats.items() if k != "quality_findings"}},
            )
        return run
    except Exception as exc:
        log.error("sync run %s failed: %s", run.id, exc, exc_info=True)
        await session.rollback()
        failed = await session.get(SyncRun, run.id)
        if failed is not None:
            failed.status, failed.finished_at, failed.error = "failed", utcnow(), type(exc).__name__
            failed.steps = [{**s, "status": "failed"} if s["status"] == "pending" else s for s in run.steps]
            await session.commit()
        raise


async def _apply_rows(
    session: AsyncSession,
    connector: ReadOnlyConnector,
    tenant_id: str,
    rows: list[dict[str, Any]],
    rules: _RuleSet,
    today: date,
    actor: Actor,
    stats: dict[str, int],
) -> None:
    flagged = []
    for row in rows:
        status = derive_status(row, rules.payer(str(row["PAYER_ID"])), today)
        if status in WORK_TYPE_FOR_STATUS:
            flagged.append((row, status))
    if not flagged:
        return

    rx_needing_history = sorted({str(r["RX_NUMBER"]) for r, s in flagged if s == "rejected"})
    prior: dict[str, list[Mapping[str, Any]]] = {}
    for i in range(0, len(rx_needing_history), 500):
        for f in await connector.fetch("fills_for_rx", rx_numbers=rx_needing_history[i : i + 500]):
            prior.setdefault(str(f["RX_NUMBER"]), []).append(f)

    fill_ids = [str(r["FILL_ID"]) for r, _ in flagged]
    open_by_fill: dict[str, WorkItem] = {}
    for i in range(0, len(fill_ids), 500):
        for w in (
            await session.scalars(
                select(WorkItem).where(
                    WorkItem.tenant_id == tenant_id,
                    WorkItem.fill_id.in_(fill_ids[i : i + 500]),
                    WorkItem.status.in_(OPEN_STATUSES),
                )
            )
        ).all():
            open_by_fill[w.fill_id] = w

    for row, status in flagged:
        candidate = _build_item(tenant_id, row, status, rules, today, prior.get(str(row["RX_NUMBER"]), []))
        current = open_by_fill.get(candidate.fill_id)
        if current is None:
            session.add(candidate)
            await session.flush()
            open_by_fill[candidate.fill_id] = candidate
            stats["created"] += 1
            if candidate.type == WorkItemType.REJECT_REVIEW:
                stats["review"] += 1
            session.add(
                WorkItemEvent(
                    work_item_id=candidate.id,
                    actor_id=actor.id,
                    kind="created",
                    summary=_created_summary(candidate, row),
                    detail={
                        "claim_id": candidate.claim_id,
                        "rule_key": candidate.rule_key,
                        "rule_version": candidate.rule_version,
                        "source": "warehouse sync",
                    },
                )
            )
            continue
        changed = (current.type, current.reject_code) != (candidate.type, candidate.reject_code)
        current.claim_id, current.source_status = candidate.claim_id, status
        if changed and current.status == WorkItemStatus.OPEN:
            # A different response on the same fill: re-route with the current catalog.
            for attr in (
                "type",
                "queue_role",
                "reject_code",
                "rule_kind",
                "rule_key",
                "rule_version",
                "suggested_fix",
                "due_date",
                "amount_at_risk",
            ):
                setattr(current, attr, getattr(candidate, attr))
            current.ai_explanation = None
            session.add(
                WorkItemEvent(
                    work_item_id=current.id,
                    actor_id=actor.id,
                    kind="rerouted",
                    summary=_created_summary(current, row),
                    detail={"claim_id": candidate.claim_id, "rule_version": candidate.rule_version},
                )
            )
            stats["updated"] += 1


def _created_summary(item: WorkItem, row: Mapping[str, Any]) -> str:
    if item.type == WorkItemType.REJECT_REVIEW:
        return f"Rejected {item.reject_code} ({row.get('REJECT_MESSAGE')}); code not in playbook — sent to review"
    if item.type == WorkItemType.CLAIM_REJECTED:
        return (
            f"Rejected {item.reject_code} ({row.get('REJECT_MESSAGE')}); playbook rule {item.rule_key} "
            f"v{item.rule_version} applied"
        )
    if item.type == WorkItemType.CLAIM_UNPAID:
        return "No payment inside the payer's window; flagged unpaid"
    return f"Paid below expected; shortfall ${float(item.amount_at_risk):,.2f}"


async def scheduler(
    sm: async_sessionmaker[AsyncSession],
    connector: ReadOnlyConnector,
    tenant_id: str,
    interval_minutes: int,
    today_fn: Any,
) -> None:
    """Background loop started by the app lifespan. Failures are logged loudly and retried next cycle."""
    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            async with sm() as session:
                await run_sync(session, connector, tenant_id, today_fn(), kind="scheduled")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("scheduled sync failed; will retry in %s minutes", interval_minutes, exc_info=True)
