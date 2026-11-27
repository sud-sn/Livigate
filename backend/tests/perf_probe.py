"""Load test: a pharmacy group with a large backlog. Prints timings as JSON (run by ``test_performance.py``).

    python tests/perf_probe.py <work dir> <open work items>

Builds the demo, then adds synthetic open work items (copies of real ones with new Rx numbers, amounts and due
dates) until the queue holds the requested number, and times what users and the sync do against it. Each timing is
the median of several calls, after one warm-up call.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sqlite3
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

TODAY = date(2026, 11, 27)


def _grow_backlog(app_db: Path, target: int) -> int:
    con = sqlite3.connect(app_db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(work_items)")]
    templates = con.execute(
        f"SELECT {', '.join(cols)} FROM work_items WHERE status = 'open'"  # noqa: S608 - column names from PRAGMA
    ).fetchall()
    current = con.execute("SELECT COUNT(*) FROM work_items WHERE status = 'open'").fetchone()[0]
    rng = random.Random(7)
    idx = {c: i for i, c in enumerate(cols)}
    rows = []
    for n in range(target - current):
        row = list(rng.choice(templates))
        row[idx["id"]] = None
        row[idx["rx_number"]] = str(90_000 + n)
        row[idx["fill_id"]] = f"LOAD-{n}"
        row[idx["claim_id"]] = f"LOAD-{n}"
        row[idx["amount_at_risk"]] = round(rng.uniform(20, 900), 2)
        row[idx["due_date"]] = (TODAY + timedelta(days=rng.randint(-60, 30))).isoformat()
        rows.append(row)
    con.executemany(
        f"INSERT INTO work_items ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # noqa: S608
        rows,
    )
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM work_items WHERE status = 'open'").fetchone()[0]
    con.close()
    return int(total)


def _time(fn: Any, repeat: int = 5) -> float:
    fn()  # warm-up
    samples = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t)
    return round(statistics.median(samples), 3)


def main() -> None:
    root, target = Path(sys.argv[1]), int(sys.argv[2])
    root.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "LEVIGATE_ENV": "test",
            "LEVIGATE_APP_DB_URL": f"sqlite+aiosqlite:///{root / 'app.db'}",
            "LEVIGATE_WAREHOUSE_URL": f"sqlite:///{root / 'warehouse.db'}",
            "LEVIGATE_SYNC_ENABLED": "false",
            "LEVIGATE_RATE_LIMIT_PER_MINUTE": "1000000",
            "LEVIGATE_SOURCE_PROFILE": os.environ.get("LEVIGATE_TEST_PROFILE", "canonical"),
        }
    )
    from levigate import clock
    from levigate.config import get_settings
    from levigate.demo.seed import reset_demo

    get_settings.cache_clear()
    clock.set_today(TODAY)
    t = time.perf_counter()
    asyncio.run(reset_demo(get_settings(), today=TODAY))
    reset_s = round(time.perf_counter() - t, 2)
    total = _grow_backlog(root / "app.db", target)

    from fastapi.testclient import TestClient

    from levigate.main import create_app

    timings: dict[str, float] = {}
    pages: dict[str, Any] = {}
    with TestClient(create_app(), base_url="http://testserver") as client:

        def login(user: str) -> str:
            client.cookies.clear()
            r = client.post("/api/auth/demo-login", json={"user_id": user})
            assert r.status_code == 200, r.text
            return str(r.json()["csrf_token"])

        def get(url: str, **params: Any) -> Any:
            r = client.get(url, params=params)
            assert r.status_code == 200, f"{url}: {r.status_code} {r.text[:200]}"
            return r.json()

        login("u-billing")
        pages["first"] = get("/api/queue", limit=50)
        pages["deep"] = get("/api/queue", limit=50, page=400)
        timings["queue_first_page"] = _time(lambda: get("/api/queue", limit=50))
        timings["queue_page_400"] = _time(lambda: get("/api/queue", limit=50, page=400))
        timings["queue_filtered"] = _time(lambda: get("/api/queue", type="claim_unpaid", limit=50))
        timings["claims_tracker"] = _time(lambda: get("/api/claims", start="2026-08-01", end="2026-11-27"))
        timings["dashboard"] = _time(lambda: get("/api/dashboard"))
        item_id = pages["first"]["items"][0]["id"]
        timings["work_item_detail"] = _time(lambda: get(f"/api/work-items/{item_id}"))
        login("u-pharmacist")
        timings["pharmacist_queue"] = _time(lambda: get("/api/queue", limit=50))
        login("u-outreach")
        timings["call_list"] = _time(lambda: get("/api/outreach/lists"))
        login("u-admin")
        csrf = login("u-admin")

        def sync() -> None:
            r = client.post("/api/admin/sync", headers={"x-csrf-token": csrf})
            assert r.status_code == 200, r.text

        timings["sync_incremental"] = _time(sync, repeat=3)

    json.dump(
        {
            "open_items": total,
            "reset_seconds": reset_s,
            "timings": timings,
            "queue_total": pages["first"]["total"],
            "first_page": [(i["days_overdue"], i["amount_at_risk"]) for i in pages["first"]["items"]],
            "deep_page_size": len(pages["deep"]["items"]),
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
