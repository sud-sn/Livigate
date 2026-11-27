"""Build one complete demo environment on a given warehouse layout and print what every role would see, as JSON.

Run in a subprocess by ``test_layouts.py`` (the app keeps its engine and connector in process globals, so each
layout gets a fresh interpreter):

    python tests/layout_probe.py <profile> <work dir> full
    python tests/layout_probe.py <profile> <work dir> incremental

``full``: the warehouse holds everything up to today; one sync.
``incremental``: the warehouse first holds data up to yesterday (synced), then today's Talend load lands and the
regular 15-minute sync runs incrementally from its watermark. The result must equal ``full``.

Surrogate keys and wall-clock timestamps are dropped; everything else — business keys, amounts, dates, statuses,
counts, names — is compared exactly.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

TODAY = date(2026, 11, 27)
DROP = {
    "id",
    "item_id",
    "fill_id",
    "claim_id",
    "call_id",
    "work_item_id",
    "created_at",
    "updated_at",
    "ts",
    "started_at",
    "finished_at",
    "generated_at",
    "last_sync",
    "last_synced_at",
    "csrf_token",
    "next_item_id",
}


def normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in sorted(value.items()) if k not in DROP}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, float):
        return round(value, 2)
    return value


async def _build(profile: str, root: Path, mode: str) -> dict[str, Any]:
    from levigate import clock
    from levigate.config import get_settings
    from levigate.demo.seed import reset_demo

    get_settings.cache_clear()
    clock.set_today(TODAY)
    settings = get_settings()
    if mode == "full":
        return await reset_demo(settings, today=TODAY, n_patients=600, n_prescribers=40)

    # Yesterday's data, synced; then today's load lands and the scheduled sync runs.
    first = await reset_demo(settings, today=TODAY, as_of=TODAY - timedelta(days=1), n_patients=600, n_prescribers=40)
    from levigate.connectors import get_connector
    from levigate.db import sessionmaker
    from levigate.demo.warehouse import build_warehouse
    from levigate.sources import DEMO_LAYOUT
    from levigate.sync import run_sync

    build_warehouse(
        root / "warehouse.db",
        TODAY,
        layout=DEMO_LAYOUT[profile],
        as_of=TODAY,
        n_patients=600,
        n_prescribers=40,
    )
    # The demo rebuild replaces the warehouse file; drop pooled connections to the old copy (a real warehouse is
    # updated in place, so production has nothing like this).
    get_connector().engine.dispose()
    async with sessionmaker()() as db:
        run = await run_sync(db, get_connector(), settings.tenant_id, TODAY, kind="scheduled")
    before = _work_items(root / "app.db")
    # If the incremental pull missed anything, a full re-read of the source would now create or change items.
    async with sessionmaker()() as db:
        check = await run_sync(db, get_connector(), settings.tenant_id, TODAY, kind="manual", force_full=True)
    return {
        **first,
        "sync": run.stats,
        "first_sync": first["sync"],
        "full_rescan": check.stats,
        "work_items_before_rescan": before,
    }


def _work_items(app_db: Path) -> list[dict[str, Any]]:
    import sqlite3

    con = sqlite3.connect(app_db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT rx_number, fill_number, type, status, queue_role, reject_code, payer_id, patient_id, location, "
        "amount_at_risk, due_date, next_followup_date, source_status FROM work_items "
        "ORDER BY rx_number, fill_number, type, status"
    ).fetchall()
    con.close()
    return [normalize(dict(r)) for r in rows]


def _api_snapshot() -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from levigate.main import create_app

    out: dict[str, Any] = {}
    with TestClient(create_app(), base_url="http://testserver") as client:

        def as_user(user_id: str) -> None:
            client.cookies.clear()
            r = client.post("/api/auth/demo-login", json={"user_id": user_id})
            assert r.status_code == 200, r.text

        def get(key: str, url: str, **params: Any) -> Any:
            r = client.get(url, params=params)
            assert r.status_code == 200, f"{url}: {r.status_code} {r.text[:200]}"
            out[key] = normalize(r.json())
            return r.json()

        as_user("u-billing")
        queue = get("billing.queue", "/api/queue", limit=200)
        for c in queue["counts"]:
            if c["count"]:
                get(f"billing.queue.{c['type']}", "/api/queue", type=c["type"], limit=200)
        get("billing.claims", "/api/claims", start="2026-08-01", end="2026-11-27", limit=200)
        get("billing.claims.rejected", "/api/claims", start="2026-08-01", end="2026-11-27", status="rejected")
        get("billing.claims.filters", "/api/claims/filters")
        get("billing.dashboard", "/api/dashboard")
        as_user("u-billing-in")
        get("offshore.queue", "/api/queue", limit=200)
        as_user("u-pharmacist")
        get("pharmacist.queue", "/api/queue", limit=200)
        as_user("u-outreach")
        lists = get("outreach.lists", "/api/outreach/lists")
        for entry in lists["lists"]:
            get(f"outreach.lists.{entry['type']}", "/api/outreach/lists", list_type=entry["type"])
        as_user("u-marketing")
        prescribers = get("marketing.prescribers", "/api/prescribers")
        for p in prescribers["rows"][:10]:
            pid = p.get("prescriber_id") or p["id"]
            get(f"marketing.prescriber.{pid}", f"/api/prescribers/{pid}")
        get("marketing.dashboard", "/api/dashboard")
        as_user("u-auditor")
        get("auditor.consent", "/api/compliance/consent")
        get("auditor.approvals", "/api/compliance/approvals")
        as_user("u-admin")
        get("admin.dashboard", "/api/dashboard")
    return out


def main() -> None:
    profile, root, mode = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    root.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "LEVIGATE_ENV": "test",
            "LEVIGATE_APP_DB_URL": f"sqlite+aiosqlite:///{root / 'app.db'}",
            "LEVIGATE_WAREHOUSE_URL": f"sqlite:///{root / 'warehouse.db'}",
            "LEVIGATE_SYNC_ENABLED": "false",
            "LEVIGATE_RATE_LIMIT_PER_MINUTE": "100000",
            "LEVIGATE_SOURCE_PROFILE": profile,
        }
    )
    result = asyncio.run(_build(profile, root, mode))
    sync = result["sync"]
    report = {
        "profile": profile,
        "mode": mode,
        "sync": {k: sync.get(k) for k in ("full_scan", "rows", "created", "review", "resolved", "consent_imported")},
        "first_sync": result.get("first_sync"),
        "full_rescan": result.get("full_rescan"),
        "work_items_before_rescan": result.get("work_items_before_rescan"),
        "history": {k: result.get(k) for k in ("history_resolved_items", "history_calls")},
        "mess": result.get("mess"),
        "work_items": _work_items(root / "app.db"),
        "api": _api_snapshot(),
    }
    json.dump(report, sys.stdout, default=str)


if __name__ == "__main__":
    main()
