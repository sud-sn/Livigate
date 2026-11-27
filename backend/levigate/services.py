"""Shared lookups used by several routers: reference data cache, masked patient display, prescriber names."""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from levigate.connectors import ReadOnlyConnector
from levigate.models import User
from levigate.policy import mask_name, mask_phone, phi_visible

_REF_TTL = 600.0
_ref_cache: dict[str, tuple[float, Any]] = {}


async def _cached(connector: ReadOnlyConnector, name: str) -> list[dict[str, Any]]:
    hit = _ref_cache.get(name)
    if hit and time.monotonic() - hit[0] < _REF_TTL:
        return hit[1]
    rows = await connector.fetch(name)
    _ref_cache[name] = (time.monotonic(), rows)
    return rows


def clear_reference_cache() -> None:
    _ref_cache.clear()


async def payer_names(connector: ReadOnlyConnector) -> dict[str, str]:
    return {str(r["PAYER_ID"]): str(r["PAYER_NAME"]) for r in await _cached(connector, "payers")}


async def locations(connector: ReadOnlyConnector) -> list[str]:
    return [str(r["LOCATION"]) for r in await _cached(connector, "locations")]


def display_case(name: Any) -> Any:
    """Pharmacy systems often store names in capitals ("O'BRIEN-SMITH"); show them as people write them. Names that
    already mix case are left exactly as entered."""
    if not isinstance(name, str) or not name or not (name.isupper() or name.islower()):
        return name
    out, cap = [], True
    for ch in name.lower():
        out.append(ch.upper() if cap else ch)
        cap = ch in " -'"
    word = "".join(out)
    return re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), word)


def phone_digits(phone: Any) -> str | None:
    """One phone format everywhere: ten digits (US), whatever the source stored ("(708) 555-0124", "+1 708…")."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def normalize_patient(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "FIRST_NAME": display_case(row.get("FIRST_NAME")),
        "LAST_NAME": display_case(row.get("LAST_NAME")),
        "PHONE": phone_digits(row.get("PHONE")),
    }


async def patient_rows(connector: ReadOnlyConnector, patient_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Patient rows for the given IDs, normalised (name case, phone format) so every screen and script shows the same
    value whatever layout the source uses."""
    ids = sorted({str(p) for p in patient_ids})
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), 500):
        for r in await connector.fetch("patients_by_ids", patient_ids=ids[i : i + 500]):
            out[str(r["PATIENT_ID"])] = normalize_patient(r)
    return out


def masked_patient(patient_id: str, row: dict[str, Any] | None) -> dict[str, Any]:
    """PHI masked by default in lists: initials + last 4 of phone. Full details only via the audited reveal."""
    row = row or {}
    return {
        "patient_id": patient_id,
        "display": mask_name(row.get("FIRST_NAME"), row.get("LAST_NAME")),
        "phone": mask_phone(row.get("PHONE")),
        "masked": True,
    }


async def masked_patients(connector: ReadOnlyConnector, patient_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    rows = await patient_rows(connector, patient_ids)
    return {pid: masked_patient(pid, rows.get(pid)) for pid in {str(p) for p in patient_ids}}


def revealed_patient(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "patient_id": str(row["PATIENT_ID"]),
        "first_name": row["FIRST_NAME"],
        "last_name": row["LAST_NAME"],
        "dob": row["DOB"],
        "phone": row["PHONE"],
        "preferred_language": row.get("PREFERRED_LANGUAGE"),
        "masked": False,
    }


def can_reveal(user: User) -> bool:
    return phi_visible(user)


async def user_names(session: AsyncSession, ids: Iterable[str | None]) -> dict[str, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = await session.execute(select(User.id, User.name).where(User.id.in_(wanted)))
    names = {r.id: r.name for r in rows}
    names.setdefault("svc-sync", "Levigate sync")
    return names


def prescriber_name(row: dict[str, Any]) -> str:
    cred = row.get("CREDENTIAL")
    prefix = "Dr. " if cred in ("MD", "DO") else ""
    suffix = f", {cred}" if cred and not prefix else ""
    return f"{prefix}{row.get('FIRST_NAME')} {row.get('LAST_NAME')}{suffix}"
