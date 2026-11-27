"""The whole product, end to end, on every warehouse layout: every role must see exactly the same thing.

``layout_probe.py`` builds a complete environment (warehouse → mapping → sync → work items → API) in a fresh
process per layout and records what each role's screens return. Here we compare them:

* the reference layout, the client's PMS tables with the ported BI formula, and the same tables with the BI view's
  own expected amounts must give identical work items and identical API answers for every role;
* the incremental 15-minute sync, after today's Talend load lands, must miss nothing: a full re-read right after
  it changes no work item.

Runs once (in the default ``canonical`` test run); the probe covers all layouts itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

PROFILES = ("canonical", "pms_snowflake", "pms_snowflake_bi")
BACKEND = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    os.environ.get("LEVIGATE_TEST_PROFILE", "canonical") != "canonical",
    reason="covers every layout itself; runs once, in the default profile",
)


def _probe(profile: str, mode: str, root: Path) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LEVIGATE_")}
    proc = subprocess.run(  # fixed arguments, our own script
        [sys.executable, str(BACKEND / "tests" / "layout_probe.py"), profile, str(root / f"{profile}-{mode}"), mode],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert proc.returncode == 0, f"{profile} {mode} probe failed:\n{proc.stderr[-3000:]}"
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def probes(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, str], dict[str, Any]]:
    root = tmp_path_factory.mktemp("probes")
    jobs = [(p, m) for p in PROFILES for m in ("full", "incremental")]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        results = list(pool.map(lambda j: _probe(j[0], j[1], root), jobs))
    return dict(zip(jobs, results, strict=True))


def _first_differences(a: Any, b: Any, path: str = "", out: list[str] | None = None) -> list[str]:
    out = [] if out is None else out
    if len(out) >= 10:
        return out
    if type(a) is not type(b):
        out.append(f"{path}: {a!r:.100} != {b!r:.100}")
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only on one side")
            else:
                _first_differences(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} items != {len(b)} items")
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            _first_differences(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(f"{path}: {a!r:.100} != {b!r:.100}")
    return out


def test_the_comparison_covers_every_role_and_real_volume(probes) -> None:  # type: ignore[no-untyped-def]
    ref = probes[("canonical", "full")]
    assert len(ref["work_items"]) > 100
    roles = {k.split(".")[0] for k in ref["api"]}
    assert roles == {"billing", "offshore", "pharmacist", "outreach", "marketing", "auditor", "admin"}
    assert len(ref["api"]) >= 20
    assert ref["api"]["billing.queue"]["items"], "the billing queue must not be empty"
    for list_type in ("refill_due", "adherence_gap", "ready_for_pickup", "balance_due"):
        assert ref["api"][f"outreach.lists.{list_type}"]["entries"], f"the {list_type} call list must not be empty"
    assert ref["api"]["marketing.prescribers"]["rows"], "the prescriber list must not be empty"
    mess = probes[("pms_snowflake", "full")]["mess"]
    assert all(v > 0 for v in mess.values()), f"the client layout must actually contain the mess: {mess}"


@pytest.mark.parametrize("profile", ["pms_snowflake", "pms_snowflake_bi"])
def test_every_role_sees_the_same_on_the_client_layout(probes, profile: str) -> None:  # type: ignore[no-untyped-def]
    ref, got = probes[("canonical", "full")], probes[(profile, "full")]
    for part in ("sync", "history", "work_items", "api"):
        diff = _first_differences(ref[part], got[part], part)
        assert not diff, f"{profile} differs from the reference layout:\n" + "\n".join(diff)


@pytest.mark.parametrize("profile", PROFILES)
def test_incremental_sync_misses_nothing(probes, profile: str) -> None:  # type: ignore[no-untyped-def]
    run = probes[(profile, "incremental")]
    assert run["sync"]["full_scan"] is False, "the second sync of the day must be incremental"
    assert 0 < run["sync"]["rows"] < run["full_rescan"]["rows"], "it reads only what changed"
    assert run["sync"]["created"] > 0, "today's load must bring new work"
    rescan = run["full_rescan"]
    assert rescan["full_scan"] is True
    changed = {k: rescan[k] for k in ("created", "updated", "resolved", "closed_upstream", "review") if rescan[k]}
    assert not changed, f"a full re-read found work the incremental sync missed: {changed}"
    assert run["work_items_before_rescan"] == run["work_items"]
