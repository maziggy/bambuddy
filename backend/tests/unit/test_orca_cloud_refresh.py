"""What a rejected Orca Cloud refresh is allowed to do to stored credentials.

The refresh token is single-use and rotating, and Orca reports every rejection
with one composite reason (``unknown, expired, revoked, or already used``), so
Bambuddy cannot tell a genuine revocation from a lost rotation race. Routes may
still clear on that signal — a person is looking at the page and can pair again
— but a background job must not, or an unattended run can destroy a working
pairing (#2717).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.app.api.routes.orca_cloud import _SETTINGS_KEYS, _build_authenticated_service
from backend.app.models.settings import Settings
from backend.app.services.orca_cloud import OrcaCloudAuthError, OrcaCloudError


async def _store_global_credentials(db):
    """An auth-disabled install's Orca credentials, expired so the helper
    refreshes rather than returning straight away."""
    db.add_all(
        [
            Settings(key=_SETTINGS_KEYS["token"], value="oc_ext_old"),
            Settings(key=_SETTINGS_KEYS["refresh_token"], value="oc_ext_rt_old"),
            Settings(key=_SETTINGS_KEYS["expires_at"], value="2000-01-01T00:00:00+00:00"),
            Settings(key=_SETTINGS_KEYS["email"], value="a@b.c"),
        ]
    )
    await db.commit()


async def _stored_keys(db) -> set[str]:
    result = await db.execute(select(Settings).where(Settings.key.in_(list(_SETTINGS_KEYS.values()))))
    return {s.key for s in result.scalars().all()}


def _expired_service(refresh_side_effect=None):
    """A service that reports its access token as expired, so the helper takes
    the refresh branch."""
    svc = MagicMock()
    svc.is_authenticated = False
    svc.refresh_token = "oc_ext_rt_old"
    svc.set_tokens = MagicMock()
    svc.refresh = AsyncMock(side_effect=refresh_side_effect)
    svc.access_token = "oc_ext_new"
    svc.token_expiry = None
    return svc


class TestRejectedRefresh:
    @pytest.mark.asyncio
    async def test_routes_clear_the_dead_pairing_by_default(self, db_session):
        """Unchanged behaviour for interactive callers: the page flips to
        disconnected while the user is there to pair again."""
        await _store_global_credentials(db_session)
        svc = _expired_service(OrcaCloudAuthError("grant already used"))

        with (
            patch("backend.app.api.routes.orca_cloud.OrcaCloudService", return_value=svc),
            pytest.raises(HTTPException) as exc,
        ):
            await _build_authenticated_service(db_session, None)

        assert exc.value.status_code == 401
        assert await _stored_keys(db_session) == set()

    @pytest.mark.asyncio
    async def test_background_callers_leave_the_credentials_alone(self, db_session):
        """The whole point of the flag. A scheduled backup that guesses wrong
        here destroys a pairing nobody asked it to touch, and the user finds
        out when their profiles stop being backed up."""
        await _store_global_credentials(db_session)
        svc = _expired_service(OrcaCloudAuthError("grant already used"))

        with (
            patch("backend.app.api.routes.orca_cloud.OrcaCloudService", return_value=svc),
            pytest.raises(HTTPException) as exc,
        ):
            await _build_authenticated_service(db_session, None, clear_on_auth_failure=False)

        # Still reported as a hard auth failure — the caller has to skip the
        # account — but nothing was destroyed on the way out.
        assert exc.value.status_code == 401
        assert _SETTINGS_KEYS["token"] in await _stored_keys(db_session)
        assert _SETTINGS_KEYS["refresh_token"] in await _stored_keys(db_session)

    @pytest.mark.asyncio
    async def test_an_unreachable_orca_never_clears_either_way(self, db_session):
        """A transport failure says nothing about the credentials' validity."""
        await _store_global_credentials(db_session)
        svc = _expired_service(OrcaCloudError("connection reset"))

        with (
            patch("backend.app.api.routes.orca_cloud.OrcaCloudService", return_value=svc),
            pytest.raises(HTTPException) as exc,
        ):
            await _build_authenticated_service(db_session, None)

        assert exc.value.status_code == 502
        assert _SETTINGS_KEYS["token"] in await _stored_keys(db_session)


class TestSuccessfulRefresh:
    @pytest.mark.asyncio
    async def test_the_rotated_pair_is_persisted_even_for_background_callers(self, db_session):
        """Not optional: by the time the refresh succeeds the old token is
        consumed, so failing to store the new pair would break a live pairing
        for real. The flag suppresses destruction, never persistence.
        """
        await _store_global_credentials(db_session)
        svc = _expired_service()
        svc.refresh_token = "oc_ext_rt_new"

        with patch("backend.app.api.routes.orca_cloud.OrcaCloudService", return_value=svc):
            returned = await _build_authenticated_service(db_session, None, clear_on_auth_failure=False)

        assert returned is svc
        result = await db_session.execute(select(Settings).where(Settings.key == _SETTINGS_KEYS["token"]))
        assert result.scalar_one().value == "oc_ext_new"
        result = await db_session.execute(select(Settings).where(Settings.key == _SETTINGS_KEYS["refresh_token"]))
        assert result.scalar_one().value == "oc_ext_rt_new"
