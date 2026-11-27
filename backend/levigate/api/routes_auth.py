"""Sign-in. Production uses the client's identity provider (OIDC with MFA, C-04); the demo sign-in lets a presenter
pick a fictional user and exists only when LEVIGATE_ENV is dev, demo or test."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from levigate import audit
from levigate.api.deps import SESSION_COOKIE, Principal, current_principal, db_session, open_session
from levigate.audit import Actor
from levigate.config import get_settings
from levigate.models import User
from levigate.policy import permissions_for, phi_visible

router = APIRouter(prefix="/api/auth", tags=["auth"])

ROLE_TITLES = {
    "billing": "Billing specialist",
    "outreach": "Patient outreach",
    "marketing": "Prescriber relations",
    "pharmacist": "Pharmacist in charge",
    "admin": "Platform admin",
    "auditor": "Compliance auditor",
}


class DemoLoginIn(BaseModel):
    user_id: str


def me_payload(principal: Principal) -> dict:
    u = principal.user
    s = get_settings()
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "role_title": ROLE_TITLES.get(u.role, u.role),
        "location": u.location,
        "tenant_id": u.tenant_id,
        "pharmacy_name": s.pharmacy_name,
        "permissions": permissions_for(u),
        "phi_visible": phi_visible(u),
        "csrf_token": principal.session.csrf_token,
        "idle_timeout_minutes": s.session_idle_minutes,
        "env": s.env,
    }


@router.get("/config")
async def auth_config() -> dict:
    s = get_settings()
    return {"demo_login": s.demo_login_enabled, "sso": s.env == "prod", "pharmacy_name": s.pharmacy_name}


@router.get("/demo-users")
async def demo_users(db: AsyncSession = Depends(db_session)) -> list[dict]:
    if not get_settings().demo_login_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    users = (
        await db.scalars(
            select(User).where(User.active.is_(True), User.is_service.is_(False)).order_by(User.role, User.name)
        )
    ).all()
    return [
        {"id": u.id, "name": u.name, "role": u.role, "title": ROLE_TITLES.get(u.role, u.role), "location": u.location}
        for u in users
    ]


@router.post("/demo-login")
async def demo_login(
    body: DemoLoginIn, request: Request, response: Response, db: AsyncSession = Depends(db_session)
) -> dict:
    if not get_settings().demo_login_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    user = await db.get(User, body.user_id)
    if user is None or not user.active or user.is_service:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user.")
    async with audit.audit_transaction(db):
        sess = await open_session(db, user, request, response)
        ip = request.client.host if request.client else None
        actor = Actor(user.id, user.role, user.tenant_id, user.location, ip)
        await audit.record(db, actor, "auth.login", "user", user.id, detail={"method": "demo"})
    actor_principal = Principal(user=user, session=sess, actor=actor)
    return me_payload(actor_principal)


@router.get("/session")
async def session(request: Request, db: AsyncSession = Depends(db_session)) -> dict:
    """App boot: who is signed in, if anyone. Always 200, so a signed-out visit is not an error."""
    if not request.cookies.get(SESSION_COOKIE):
        return {"me": None}
    try:
        principal = await current_principal(request, db)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return {"me": None, "notice": exc.detail}
        raise
    return {"me": me_payload(principal)}


@router.get("/me")
async def me(principal: Principal = Depends(current_principal)) -> dict:
    return me_payload(principal)


@router.post("/logout")
async def logout(
    response: Response, principal: Principal = Depends(current_principal), db: AsyncSession = Depends(db_session)
) -> dict:
    async with audit.audit_transaction(db):
        sess = await db.get(type(principal.session), principal.session.token_hash)
        if sess is not None:
            await db.delete(sess)
        await audit.record(db, principal.actor, "auth.logout", "user", principal.user.id)
    response.delete_cookie(SESSION_COOKIE, path="/api")
    return {"ok": True}
