"""PostgreSQL-only guarantees, run in a fresh process by tests/test_zzz_postgres.py.

LEVIGATE_TEST_PG_URL=postgresql+asyncpg://user@host:5432/db  python tests/pg_check.py <warehouse.db>
"""

from __future__ import annotations

import asyncio
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date

from sqlalchemy import text

from levigate import audit
from levigate.audit import Actor
from levigate.config import Settings
from levigate.db import init_engine, sessionmaker

PG = os.environ["LEVIGATE_TEST_PG_URL"]
WRITERS, PER_WRITER = 4, 15


def _writer(n: int) -> int:
    async def run() -> int:
        engine = init_engine(PG)
        sm = sessionmaker()
        for i in range(PER_WRITER):
            async with sm() as db, audit.audit_transaction(db):
                await audit.record(db, Actor("u-billing", "billing", "demo"), "test.concurrent", "proc", f"{n}-{i}")
        await engine.dispose()
        return PER_WRITER

    return asyncio.run(run())


async def main(warehouse: str) -> None:
    from levigate.demo.seed import reset_demo

    settings = Settings(env="test", app_db_url=PG, warehouse_url=f"sqlite:///{warehouse}")
    result = await reset_demo(settings, today=date(2026, 11, 27), n_patients=300, n_prescribers=30)
    assert result["sync"]["created"] > 0, result
    print("reset + sync on PostgreSQL:", result["sync"])

    engine = init_engine(PG)
    for stmt in ("UPDATE audit_events SET action = 'tampered' WHERE seq = 1", "DELETE FROM audit_events WHERE seq = 1"):
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception as exc:  # the trigger must refuse it
            assert "append-only" in str(exc), exc
            print("refused by the database:", stmt.split()[0])
        else:
            raise AssertionError(f"database allowed: {stmt}")
    async with engine.connect() as conn:
        before = (await conn.execute(text("SELECT COUNT(*) FROM audit_events"))).scalar_one()
    await engine.dispose()

    # Separate processes, each with its own connection pool: only the advisory lock can serialise them.
    with ProcessPoolExecutor(max_workers=WRITERS) as pool:
        written = sum(pool.map(_writer, range(WRITERS)))

    engine = init_engine(PG)
    async with sessionmaker()() as db:
        check = await audit.verify_chain(db)
    await engine.dispose()
    assert check.ok, f"chain forked at seq {check.first_bad_seq}"
    assert check.checked == before + written, (check.checked, before, written)
    print(f"{written} concurrent events from {WRITERS} processes; chain intact across {check.checked} events")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
    print("PG CHECK OK")
