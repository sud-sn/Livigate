"""Performance with a large backlog: 50,000 open work items (far beyond a pilot pharmacy's few hundred).

Budgets are several times what a laptop measures, so a slow CI runner does not flake, while an accidental
full-table load (e.g. sorting the queue in Python again) still fails clearly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
BUDGET_SECONDS = {
    "queue_first_page": 1.5,
    "queue_page_400": 2.5,
    "queue_filtered": 1.5,
    "pharmacist_queue": 1.5,
    "work_item_detail": 1.0,
    "claims_tracker": 2.0,
    "dashboard": 2.0,
    "call_list": 2.0,
    "sync_incremental": 15.0,
}

pytestmark = pytest.mark.skipif(
    os.environ.get("LEVIGATE_TEST_PROFILE", "canonical") != "canonical", reason="runs once, in the default profile"
)


@pytest.fixture(scope="module")
def load(tmp_path_factory: pytest.TempPathFactory) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LEVIGATE_")}
    proc = subprocess.run(  # fixed arguments, our own script
        [sys.executable, str(BACKEND / "tests" / "perf_probe.py"), str(tmp_path_factory.mktemp("perf")), "50000"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout)


def test_backlog_is_really_there(load) -> None:  # type: ignore[no-untyped-def]
    assert load["open_items"] == 50_000
    assert load["queue_total"] > 45_000 and load["deep_page_size"] == 50


def test_queue_order_holds_at_scale(load) -> None:  # type: ignore[no-untyped-def]
    page = load["first_page"]
    assert all(days > 0 for days, _ in page), "overdue items come first"
    amounts = [amount for _, amount in page]
    assert amounts == sorted(amounts, reverse=True), "overdue items are ordered by dollars at risk"


@pytest.mark.parametrize("name", sorted(BUDGET_SECONDS))
def test_within_budget(load, name: str) -> None:  # type: ignore[no-untyped-def]
    took = load["timings"][name]
    assert took <= BUDGET_SECONDS[name], f"{name} took {took}s (budget {BUDGET_SECONDS[name]}s)"
