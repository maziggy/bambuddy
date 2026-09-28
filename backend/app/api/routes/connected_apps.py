"""Connected apps: sign users in to external applications with Bambuddy.

A minimal OAuth 2.0 authorization-code flow with PKCE (S256 only):

1. The app sends the user's browser to the SPA page ``/connect/authorize``.
   The page, running with the user's normal Bambuddy session, calls
   ``GET /connect/authorize/info`` to show who is asking and, after consent,
   ``POST /connect/authorize`` for a code. It then sends the browser back to
   the app's registered callback URL with that code.
2. The app's server calls ``POST /connect/token`` with the code, its client
   secret and the PKCE verifier, and receives the user's identity and
   permissions.

What the app never gets is the user's Bambuddy login token. What a code is
worth is deliberately small: single use, 60 seconds, bound to one app, its
exact callback URL and the PKCE challenge, and only redeemable with the app's
secret.

Sign-in through an app only exists while Bambuddy authentication is enabled.
With it disabled there are no users to sign in, and both authorize and token
answer ``auth_disabled`` so the app can fall back to its own login rather
than let everyone in.
"""

import base64
import functools
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import (
    RequirePermissionIfAuthEnabled,
    get_current_user,
    get_password_hash,
    get_user_by_username,
    is_auth_enabled,
    security,
    verify_password,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.auth_ephemeral import AuthEphemeralToken, EventType, TokenType
from backend.app.models.connected_app import ConnectedApp, ConnectedAppGrant
from backend.app.models.user import User
from backend.app.schemas.connected_app import (
    ConnectAuthorizeInfo,
    ConnectAuthorizeRequest,
    ConnectAuthorizeResponse,
    ConnectedAppCreate,
    ConnectedAppResponse,
    ConnectedAppSecretResponse,
    ConnectedAppUpdate,
    ConnectedUser,
    ConnectTokenRequest,
    ConnectTokenResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connect", tags=["connected-apps"])

CODE_TTL = timedelta(seconds=60)
# Failed token exchanges tolerated per client and per IP in the rate-limit
# window (mfa.LOCKOUT_WINDOW). A working app fails almost never; this only has
# to stop someone guessing secrets or codes.
MAX_FAILED_TOKEN_EXCHANGES = 20

AUTH_DISABLED = "auth_disabled"


@functools.cache
def _dummy_secret_hash() -> str:
    """Verified against when the client_id is unknown, so an unknown client
    costs the same bcrypt round as a wrong secret and the two can't be told
    apart by timing. Built on first use to keep bcrypt out of import time."""
    return get_password_hash(secrets.token_urlsafe(32))


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _new_client_credentials() -> tuple[str, str]:
    return f"bba_{secrets.token_hex(12)}", f"bbs_{secrets.token_urlsafe(32)}"


def _with_secret(app: ConnectedApp, client_secret: str) -> ConnectedAppSecretResponse:
    return ConnectedAppSecretResponse(
        **ConnectedAppResponse.model_validate(app).model_dump(), client_secret=client_secret
    )


async def _get_app_or_404(db: AsyncSession, app_id: int) -> ConnectedApp:
    app = await db.get(ConnectedApp, app_id)
    if app is None:
        raise HTTPException(404, "Connected app not found")
    return app


# --------------------------------------------------------------------------
# Admin: register and manage apps
# --------------------------------------------------------------------------


@router.get("/apps", response_model=list[ConnectedAppResponse])
async def list_connected_apps(
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    result = await db.execute(select(ConnectedApp).order_by(ConnectedApp.created_at.desc()))
    return list(result.scalars().all())


@router.post("/apps", response_model=ConnectedAppSecretResponse)
async def create_connected_app(
    data: ConnectedAppCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Register an app. The client secret is in this response and nowhere else."""
    if not await is_auth_enabled(db):
        # Nobody could sign in through it, and an admin who set one up would
        # reasonably assume the app is now protected by Bambuddy's login.
        raise HTTPException(400, "Connected apps require authentication to be enabled")
    client_id, client_secret = _new_client_credentials()
    app = ConnectedApp(
        name=data.name.strip(),
        client_id=client_id,
        client_secret_hash=get_password_hash(client_secret),
        redirect_uri=data.redirect_uri,
        enabled=True,
        created_by_id=current_user.id if current_user else None,
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    logger.info("Connected app %s '%s' registered", app.id, app.name)
    return _with_secret(app, client_secret)


@router.patch("/apps/{app_id}", response_model=ConnectedAppResponse)
async def update_connected_app(
    app_id: int,
    data: ConnectedAppUpdate,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    app = await _get_app_or_404(db, app_id)
    if data.name is not None:
        app.name = data.name.strip()
    if data.redirect_uri is not None:
        app.redirect_uri = data.redirect_uri
    if data.enabled is not None:
        app.enabled = data.enabled
    await db.commit()
    await db.refresh(app)
    return app


@router.post("/apps/{app_id}/rotate-secret", response_model=ConnectedAppSecretResponse)
async def rotate_connected_app_secret(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    """Replace the client secret. The old one stops working immediately."""
    app = await _get_app_or_404(db, app_id)
    _, client_secret = _new_client_credentials()
    app.client_secret_hash = get_password_hash(client_secret)
    await db.commit()
    await db.refresh(app)
    logger.info("Connected app %s secret rotated", app.id)
    return _with_secret(app, client_secret)


@router.delete("/apps/{app_id}")
async def delete_connected_app(
    app_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = RequirePermissionIfAuthEnabled(Permission.SETTINGS_UPDATE),
):
    app = await _get_app_or_404(db, app_id)
    await db.execute(delete(ConnectedAppGrant).where(ConnectedAppGrant.app_id == app.id))
    # Outstanding codes would fail anyway (their app is gone); drop them now.
    await db.execute(
        delete(AuthEphemeralToken).where(
            AuthEphemeralToken.token_type == TokenType.CONNECT_CODE,
            AuthEphemeralToken.provider_id == app.id,
        )
    )
    await db.delete(app)
    await db.commit()
    logger.info("Connected app %s deleted", app_id)
    return {"message": "Connected app deleted"}


# --------------------------------------------------------------------------
# Authorize: called by the SPA with the signed-in user's session
# --------------------------------------------------------------------------


async def require_signed_in_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    """The user behind a Bambuddy login token. API keys are refused.

    An API key would let a script sign its owner in to an app without the
    owner ever seeing a consent screen; only a person's own session may do
    that. ``get_current_user`` accepts JWTs only, so a ``bb_`` key fails there.
    """
    if not await is_auth_enabled(db):
        raise HTTPException(409, AUTH_DISABLED)
    return await get_current_user(credentials)


async def _app_for_authorize(db: AsyncSession, client_id: str, redirect_uri: str) -> ConnectedApp:
    result = await db.execute(select(ConnectedApp).where(ConnectedApp.client_id == client_id))
    app = result.scalar_one_or_none()
    # One message for every case: the page must not redirect anywhere unless
    # all of these hold, and there is nothing useful to tell the user apart.
    if app is None or not app.enabled or not hmac.compare_digest(app.redirect_uri, redirect_uri):
        raise HTTPException(400, "Unknown app, app disabled, or callback URL does not match its registration")
    return app


@router.get("/authorize/info", response_model=ConnectAuthorizeInfo)
async def authorize_info(
    client_id: str = Query(..., max_length=64),
    redirect_uri: str = Query(..., max_length=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_signed_in_user),
):
    """What the consent screen shows. Validates the request before the page acts on it."""
    app = await _app_for_authorize(db, client_id, redirect_uri)
    granted = await db.execute(
        select(ConnectedAppGrant.id).where(ConnectedAppGrant.app_id == app.id, ConnectedAppGrant.user_id == user.id)
    )
    return ConnectAuthorizeInfo(
        app_name=app.name, username=user.username, already_granted=granted.scalar_one_or_none() is not None
    )


@router.post("/authorize", response_model=ConnectAuthorizeResponse)
async def authorize(
    data: ConnectAuthorizeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_signed_in_user),
):
    """Issue a code for the signed-in user and record their consent."""
    app = await _app_for_authorize(db, data.client_id, data.redirect_uri)
    now = datetime.now(timezone.utc)

    # Keep the table small: codes live for a minute, so anything expired is junk.
    await db.execute(
        delete(AuthEphemeralToken).where(
            AuthEphemeralToken.token_type == TokenType.CONNECT_CODE,
            AuthEphemeralToken.expires_at < now,
        )
    )

    granted = await db.execute(
        select(ConnectedAppGrant.id).where(ConnectedAppGrant.app_id == app.id, ConnectedAppGrant.user_id == user.id)
    )
    if granted.scalar_one_or_none() is None:
        db.add(ConnectedAppGrant(app_id=app.id, user_id=user.id))

    code = secrets.token_urlsafe(32)
    db.add(
        AuthEphemeralToken.new_connect_code(
            code_hash=_hash_code(code),
            username=user.username,
            app_id=app.id,
            code_challenge=data.code_challenge,
            expires_at=now + CODE_TTL,
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # Two tabs consenting at once both inserted the grant; the other one won.
        await db.rollback()
        db.add(
            AuthEphemeralToken.new_connect_code(
                code_hash=_hash_code(code),
                username=user.username,
                app_id=app.id,
                code_challenge=data.code_challenge,
                expires_at=now + CODE_TTL,
            )
        )
        await db.commit()

    logger.info("Connected app %s: code issued for user %s", app.id, user.id)
    return ConnectAuthorizeResponse(code=code, redirect_uri=app.redirect_uri)


# --------------------------------------------------------------------------
# Token: called by the app's server, authenticated by its client secret
# --------------------------------------------------------------------------


def _token_error(status_code: int, error: str) -> HTTPException:
    return HTTPException(status_code, {"error": error})


@router.post("/token", response_model=ConnectTokenResponse)
async def exchange_code(
    request: Request,
    data: ConnectTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Swap a code for the user's identity and permissions.

    Every failure after the rate-limit check counts against both the client and
    the caller's IP. The response says only ``invalid_client`` (who is asking
    is wrong) or ``invalid_grant`` (the code is), never which check failed.
    """
    from backend.app.api.routes.auth import _get_client_ip
    from backend.app.api.routes.mfa import check_rate_limit, clear_failed_attempts, record_failed_attempt

    client_ip = _get_client_ip(request)
    await check_rate_limit(
        db, data.client_id, event_type=EventType.CONNECT_TOKEN_CLIENT, max_attempts=MAX_FAILED_TOKEN_EXCHANGES
    )
    await check_rate_limit(
        db, client_ip, event_type=EventType.CONNECT_TOKEN_IP, max_attempts=MAX_FAILED_TOKEN_EXCHANGES
    )

    async def fail(status_code: int, error: str, reason: str) -> HTTPException:
        await record_failed_attempt(db, data.client_id, event_type=EventType.CONNECT_TOKEN_CLIENT)
        await record_failed_attempt(db, client_ip, event_type=EventType.CONNECT_TOKEN_IP)
        logger.warning("Connected app token exchange refused for client %s: %s", data.client_id, reason)
        return _token_error(status_code, error)

    if not await is_auth_enabled(db):
        raise _token_error(400, AUTH_DISABLED)

    result = await db.execute(select(ConnectedApp).where(ConnectedApp.client_id == data.client_id))
    app = result.scalar_one_or_none()
    secret_ok = verify_password(data.client_secret, app.client_secret_hash if app else _dummy_secret_hash())
    if app is None or not secret_ok:
        raise await fail(401, "invalid_client", "unknown client or wrong secret")
    if not app.enabled:
        raise await fail(401, "invalid_client", "app disabled")

    # Consume first, then check: DELETE ... RETURNING makes the code single-use
    # even under concurrent exchanges, and a code that fails a later check is
    # spent all the same.
    now = datetime.now(timezone.utc)
    consumed = await db.execute(
        delete(AuthEphemeralToken)
        .where(
            AuthEphemeralToken.token == _hash_code(data.code),
            AuthEphemeralToken.token_type == TokenType.CONNECT_CODE,
            AuthEphemeralToken.provider_id == app.id,
            AuthEphemeralToken.expires_at > now,
        )
        .returning(AuthEphemeralToken.username, AuthEphemeralToken.nonce)
    )
    row = consumed.one_or_none()
    await db.commit()
    if row is None:
        raise await fail(400, "invalid_grant", "unknown, expired, reused or foreign code")
    username, code_challenge = row

    if not hmac.compare_digest(app.redirect_uri, data.redirect_uri):
        raise await fail(400, "invalid_grant", "callback URL mismatch")
    if not code_challenge or not hmac.compare_digest(_s256(data.code_verifier), code_challenge):
        raise await fail(400, "invalid_grant", "PKCE verifier mismatch")

    user = await get_user_by_username(db, username)
    if user is None or not user.is_active:
        raise await fail(400, "invalid_grant", "user gone or disabled")

    app.last_used_at = now.replace(tzinfo=None)
    await clear_failed_attempts(db, data.client_id, event_type=EventType.CONNECT_TOKEN_CLIENT)
    await db.commit()

    logger.info("Connected app %s: user %s signed in", app.id, user.id)
    return ConnectTokenResponse(
        user=ConnectedUser(
            id=user.id,
            username=user.username,
            email=user.email,
            is_admin=user.is_admin,
            groups=sorted(g.name for g in user.groups),
            permissions=sorted(user.get_permissions()),
        ),
        issued_at=now,
    )
