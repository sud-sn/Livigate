"""PostgreSQL guarantees: append-only audit table and an unforked hash chain under multi-process writes.

Skipped unless LEVIGATE_TEST_PG_URL points at an empty PostgreSQL database (CI provides one). Runs in a separate
process so it never touches the SQLite environment the rest of the suite uses.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PG = os.environ.get("LEVIGATE_TEST_PG_URL")


@pytest.mark.skipif(not PG, reason="set LEVIGATE_TEST_PG_URL to run the PostgreSQL checks")
def test_postgres_audit_guarantees(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LEVIGATE_") or k == "LEVIGATE_TEST_PG_URL"}
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("pg_check.py")), str(tmp_path / "warehouse.db")],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        check=False,
    )
    print(proc.stdout)
    assert proc.returncode == 0, proc.stderr[-4000:]
    assert "PG CHECK OK" in proc.stdout
