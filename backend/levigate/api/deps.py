"""Request dependencies: session resolution (C-04), CSRF, rate limits (API4) and permission checks (API5).

Production sign-in is SSO through the client's identity provider (OIDC with MFA, see ``api/auth.py``); whichever
way a user signs in, the result is the same server-side session row, so everything below applies to both.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from levigate.audit import Actor
from levigate.config import get_settings
from levigate.db import sessionmaker
from levigate.models import User, UserSession, as_utc, utcnow
from levigate.policy import has_permission

SESSION_COOKIE = "lv_session"
CSRF_HEADER = "x-csrf-token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def db_session() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        yield session


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def open_session(db: AsyncSession, user: User, request: Request, response: Response) -> UserSession:
    token = secrets.token_urlsafe(32)
    row = UserSession(
        token_hash=_hash(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(24),
        ip=client_ip(request),
    )
    db.add(row)
    user.last_login_at = utcnow()
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/api",
        max_age=12 * 3600,  # hard cap; the 15-minute idle timeout is enforced server-side
    )
    return row


@dataclass
class Principal:
    user: User
    session: UserSession
    actor: Actor


class _RateLimiter:
    """Sliding one-minute window per user. Single-process pilot; swap for Redis when scaled out."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int) -> bool:
        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


rate_limiter = _RateLimiter()


async def current_principal(request: Request, db: AsyncSession = Depends(db_session)) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    sess = await db.get(UserSession, _hash(token))
    settings = get_settings()
    now = utcnow()
    if sess is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session has ended. Sign in again.")
    last_seen = as_utc(sess.last_seen_at)
    if now - last_seen > timedelta(minutes=settings.session_idle_minutes):
        await db.delete(sess)
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "You were signed out after 15 minutes idle.")
    # Role and active flag are read on every request, so a role change or removal takes effect immediately.
    user = await db.get(User, sess.user_id)
    if user is None or not user.active:
        await db.delete(sess)
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This account no longer has access.")
    if request.method not in _SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not secrets.compare_digest(sent, sess.csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Security check failed. Reload the page and try again.")
    if not rate_limiter.check(user.id, settings.rate_limit_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Wait a moment and retry.")
    if (now - last_seen).total_seconds() > 30:
        sess.last_seen_at = now
        await db.commit()
    actor = Actor(id=user.id, role=user.role, tenant_id=user.tenant_id, location=user.location, ip=client_ip(request))
    return Principal(user=user, session=sess, actor=actor)


def require(permission: str) -> Callable[..., Coroutine[Any, Any, Principal]]:
    async def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not has_permission(principal.user, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role does not have access to this.")
        return principal

    _dep.permission = permission  # type: ignore[attr-defined]  # read by the route x role test matrix
    return _dep


def page_size(limit: int = 50) -> int:
    return max(1, min(limit, get_settings().max_page_size))
