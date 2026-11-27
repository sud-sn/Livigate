"""Admin (AD-01 … AD-03): users and roles, data connections, the business catalog, sync runs and health."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from levigate import audit, catalog, clock
from levigate.api.deps import Principal, db_session, require
from levigate.config import get_settings
from levigate.connectors import get_connector
from levigate.models import (
    CatalogEntry,
    DataConnection,
    Role,
    SyncRun,
    User,
    UserSession,
    Vendor,
    as_utc,
    new_id,
    utcnow,
)
from levigate.services import clear_reference_cache, user_names
from levigate.sync import run_sync

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------------------------------------------
# Users and roles
# ---------------------------------------------------------------------------------------------------------------


def _user(u: User) -> dict[str, Any]:
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "location": u.location,
        "offshore_phi_approved": u.offshore_phi_approved,
        "active": u.active,
        "last_login_at": as_utc(u.last_login_at).isoformat() if u.last_login_at else None,
    }


@router.get("/users")
async def list_users(
    principal: Principal = Depends(require("admin.users")), db: AsyncSession = Depends(db_session)
) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(
            select(User)
            .where(User.tenant_id == principal.user.tenant_id, User.is_service.is_(False))
            .order_by(User.name)
        )
    ).all()
    return [_user(u) for u in rows]


class UserIn(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    role: Role
    location: str = Field(default="US", pattern=r"^[A-Z]{2}$")


@router.post("/users")
async def create_user(
    body: UserIn, principal: Principal = Depends(require("admin.users")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    if await db.scalar(select(User.id).where(User.email == body.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists.")
    async with audit.audit_transaction(db):
        u = User(
            id=new_id(),
            tenant_id=principal.user.tenant_id,
            name=body.name,
            email=body.email,
            role=body.role.value,
            location=body.location,
        )
        db.add(u)
        await db.flush()
        await audit.record(
            db, principal.actor, "admin.user_create", "user", u.id, detail={"role": u.role, "location": u.location}
        )
    return _user(u)


class UserPatch(BaseModel):
    role: Role | None = None
    active: bool | None = None
    offshore_phi_approved: bool | None = None


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserPatch,
    principal: Principal = Depends(require("admin.users")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    u = await db.get(User, user_id)
    if u is None or u.tenant_id != principal.user.tenant_id or u.is_service:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if u.id == principal.user.id and (body.active is False or (body.role and body.role != Role.ADMIN)):
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot remove your own admin access.")
    changes: dict[str, Any] = {}
    async with audit.audit_transaction(db):
        if body.role is not None and body.role.value != u.role:
            changes["role"] = {"from": u.role, "to": body.role.value}
            u.role = body.role.value
        if body.active is not None and body.active != u.active:
            changes["active"] = {"from": u.active, "to": body.active}
            u.active = body.active
            u.deactivated_at = None if body.active else utcnow()
            if not body.active:
                # CC-04: removed users lose access the same moment — every open session is ended.
                await db.execute(delete(UserSession).where(UserSession.user_id == u.id))
        if body.offshore_phi_approved is not None and body.offshore_phi_approved != u.offshore_phi_approved:
            changes["offshore_phi_approved"] = {"from": u.offshore_phi_approved, "to": body.offshore_phi_approved}
            u.offshore_phi_approved = body.offshore_phi_approved
        if changes:
            await audit.record(db, principal.actor, "admin.user_update", "user", u.id, detail=changes)
    return _user(u)


# ---------------------------------------------------------------------------------------------------------------
# Data connections (AD-02)
# ---------------------------------------------------------------------------------------------------------------


async def _connections(db: AsyncSession, tenant: str) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(select(DataConnection).where(DataConnection.tenant_id == tenant).order_by(DataConnection.name))
    ).all()
    vendors = {v.id: v for v in (await db.scalars(select(Vendor).where(Vendor.tenant_id == tenant))).all()}
    out = []
    for c in rows:
        v = vendors.get(c.vendor_id or "")
        blocked = bool(v and v.touches_phi and v.baa_status != "signed")
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "kind": c.kind,
                "host": c.host,
                "database": c.database,
                "schema": c.schema_name,
                "auth_method": c.auth_method,
                "secret_ref": c.secret_ref,
                "mode": c.mode,
                "enabled": c.enabled and not blocked,
                "blocked_reason": "Vendor has no signed BAA (C-01)" if blocked else None,
                "vendor": v.name if v else None,
                "last_test_at": as_utc(c.last_test_at).isoformat() if c.last_test_at else None,
                "last_test_ok": c.last_test_ok,
                "last_test_message": c.last_test_message,
            }
        )
    return out


@router.get("/connections")
async def list_connections(
    principal: Principal = Depends(require("admin.connections")), db: AsyncSession = Depends(db_session)
) -> list[dict[str, Any]]:
    return await _connections(db, principal.user.tenant_id)


@router.post("/connections/{connection_id}/test")
async def test_connection(
    connection_id: str,
    principal: Principal = Depends(require("admin.connections")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    conn = await db.get(DataConnection, connection_id)
    if conn is None or conn.tenant_id != principal.user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found.")
    if not conn.enabled:
        ok, message = False, "Connection is disabled"
    elif conn.kind == "sqlite" or conn.name.lower().startswith("demo"):
        ok, message = await get_connector().test()
    else:
        ok, message = False, "Secret not available in this environment (vault reference only)"
    async with audit.audit_transaction(db):
        conn.last_test_at, conn.last_test_ok, conn.last_test_message = utcnow(), ok, message
        await audit.record(db, principal.actor, "admin.connection_test", "data_connection", conn.id, detail={"ok": ok})
    return {"ok": ok, "message": message}


class ConnectionPatch(BaseModel):
    enabled: bool


@router.patch("/connections/{connection_id}")
async def toggle_connection(
    connection_id: str,
    body: ConnectionPatch,
    principal: Principal = Depends(require("admin.connections")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    conn = await db.get(DataConnection, connection_id)
    if conn is None or conn.tenant_id != principal.user.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found.")
    async with audit.audit_transaction(db):
        conn.enabled = body.enabled
        await audit.record(
            db, principal.actor, "admin.connection_toggle", "data_connection", conn.id, detail={"enabled": body.enabled}
        )
    return {"ok": True}


# ---------------------------------------------------------------------------------------------------------------
# Business catalog (AD-03)
# ---------------------------------------------------------------------------------------------------------------


def _entry(e: CatalogEntry, names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": e.id,
        "kind": e.kind,
        "key": e.key,
        "version": e.version,
        "title": e.title,
        "body": e.body,
        "status": e.status,
        "is_current": e.is_current,
        "change_note": e.change_note,
        "created_by": names.get(e.created_by, e.created_by),
        "created_at": as_utc(e.created_at).isoformat(),
        "approved_by": names.get(e.approved_by or "", e.approved_by),
        "approved_at": as_utc(e.approved_at).isoformat() if e.approved_at else None,
    }


@router.get("/catalog")
async def list_catalog(
    kind: str | None = None,
    principal: Principal = Depends(require("catalog.view")),
    db: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    q = select(CatalogEntry).where(
        CatalogEntry.tenant_id == principal.user.tenant_id,
        (CatalogEntry.is_current.is_(True)) | (CatalogEntry.status == "proposed"),
    )
    if kind:
        q = q.where(CatalogEntry.kind == kind)
    rows = (await db.scalars(q.order_by(CatalogEntry.kind, CatalogEntry.key))).all()
    names = await user_names(db, [r.created_by for r in rows] + [r.approved_by for r in rows])
    return {"kinds": list(catalog.KINDS), "entries": [_entry(r, names) for r in rows]}


@router.get("/catalog/{kind}/{key}/history")
async def catalog_history(
    kind: str, key: str, principal: Principal = Depends(require("catalog.view")), db: AsyncSession = Depends(db_session)
) -> list[dict[str, Any]]:
    rows = await catalog.history(db, principal.user.tenant_id, kind, key)
    names = await user_names(db, [r.created_by for r in rows] + [r.approved_by for r in rows])
    return [_entry(r, names) for r in rows]


class ProposeIn(BaseModel):
    kind: str
    key: str = Field(min_length=1, max_length=96)
    title: str = Field(min_length=2, max_length=255)
    body: dict[str, Any]
    change_note: str = Field(min_length=3, max_length=2000)


@router.post("/catalog/proposals")
async def propose(
    body: ProposeIn, principal: Principal = Depends(require("admin.catalog")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    if body.kind == "reject_playbook" and body.body.get("handler") not in {
        "refill_too_soon",
        "prior_auth",
        "plan_limit",
        "not_covered",
        "coverage_lookup",
        "cob_primary",
        "verify_prescriber",
        "dur_review",
    }:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Choose one of the supported playbook handlers.")
    async with audit.audit_transaction(db):
        try:
            e = await catalog.propose(db, principal.actor, body.kind, body.key, body.title, body.body, body.change_note)
        except catalog.CatalogError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _entry(e, await user_names(db, [e.created_by]))


@router.post("/catalog/proposals/{entry_id}/approve")
async def approve_proposal(
    entry_id: int, principal: Principal = Depends(require("admin.catalog")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    async with audit.audit_transaction(db):
        try:
            e = await catalog.approve(db, principal.actor, entry_id)
        except catalog.CatalogError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _entry(e, await user_names(db, [e.created_by, e.approved_by]))


# ---------------------------------------------------------------------------------------------------------------
# Sync and health
# ---------------------------------------------------------------------------------------------------------------


def _run(r: SyncRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "kind": r.kind,
        "status": r.status,
        "steps": r.steps,
        "stats": r.stats,
        "error": r.error,
        "started_at": as_utc(r.started_at).isoformat(),
        "finished_at": as_utc(r.finished_at).isoformat() if r.finished_at else None,
    }


@router.post("/sync")
async def sync_now(
    principal: Principal = Depends(require("admin.sync")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    async with audit.audit_transaction(db):
        await audit.record(db, principal.actor, "admin.sync_requested", "sync", "manual")
    clear_reference_cache()
    try:
        run = await run_sync(db, get_connector(), principal.user.tenant_id, clock.today(), kind="manual")
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The sync could not finish. The details are in the sync log; nothing was changed.",
        ) from exc
    return _run(run)


@router.get("/sync/runs")
async def sync_runs(
    principal: Principal = Depends(require("admin.sync")), db: AsyncSession = Depends(db_session)
) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(
            select(SyncRun).where(SyncRun.tenant_id == principal.user.tenant_id).order_by(SyncRun.id.desc()).limit(20)
        )
    ).all()
    return [_run(r) for r in rows]


@router.get("/health")
async def health(
    principal: Principal = Depends(require("admin.sync")), db: AsyncSession = Depends(db_session)
) -> dict[str, Any]:
    ok, message = await get_connector().test()
    last = await db.scalar(
        select(SyncRun).where(SyncRun.tenant_id == principal.user.tenant_id).order_by(SyncRun.id.desc()).limit(1)
    )
    chain = await audit.verify_chain(db)
    s = get_settings()
    return {
        "warehouse": {"ok": ok, "message": message},
        "last_sync": _run(last) if last else None,
        "audit_chain": {"ok": chain.ok, "events": chain.checked},
        "ai_provider": s.ai_provider,
        "env": s.env,
        "sync_interval_minutes": s.sync_interval_minutes,
    }
