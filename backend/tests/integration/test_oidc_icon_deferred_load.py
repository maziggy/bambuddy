"""Verify icon_data stays deferred on list queries (#1333).

Regression guard: if ``deferred=True`` ever gets dropped from
``OIDCProvider.icon_data``, every login-page hit pulls the full BLOB on
the listing query, adding ~MB of bandwidth per anonymous request. These
tests assert via SQLAlchemy's instance inspector that the column is
**not** loaded by default and **is** loaded after an explicit
``undefer()``.
"""

import hashlib

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63000100000005000100"
    "0d0a2db40000000049454e44ae426082"
)


async def _seed_provider(db_session: AsyncSession, name: str = "DeferredProv"):
    from backend.app.models.oidc_provider import OIDCProvider

    provider = OIDCProvider(
        name=name,
        issuer_url="https://idp.example.com",
        client_id="c",
        scopes="openid",
        is_enabled=True,
    )
    provider.client_secret = "secret"
    provider.icon_url = "https://example.com/icon.png"
    provider.icon_data = _PNG_BYTES
    provider.icon_content_type = "image/png"
    provider.icon_etag = hashlib.sha256(_PNG_BYTES).hexdigest()
    db_session.add(provider)
    await db_session.commit()
    db_session.expire_all()  # force the next read to come from DB, not identity-map


@pytest.mark.asyncio
@pytest.mark.integration
async def test_default_list_query_does_not_load_icon_data(db_session: AsyncSession):
    """`select(OIDCProvider)` without options keeps icon_data unloaded."""
    from backend.app.models.oidc_provider import OIDCProvider

    await _seed_provider(db_session)
    result = await db_session.execute(select(OIDCProvider))
    provider = result.scalar_one()

    state = inspect(provider)
    assert "icon_data" in state.unloaded, (
        "icon_data should be deferred on the default list query — "
        "without this guard every login page hit pulls the full BLOB."
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_undefer_loads_icon_data(db_session: AsyncSession):
    """`select(...).options(undefer(...))` loads icon_data eagerly."""
    from backend.app.models.oidc_provider import OIDCProvider

    await _seed_provider(db_session, name="UndeferProv")
    result = await db_session.execute(
        select(OIDCProvider).options(undefer(OIDCProvider.icon_data)).where(OIDCProvider.name == "UndeferProv")
    )
    provider = result.scalar_one()

    state = inspect(provider)
    assert "icon_data" not in state.unloaded, "undefer() must eagerly load icon_data"
    # And the bytes are accessible without raising MissingGreenlet.
    assert provider.icon_data == _PNG_BYTES


@pytest.mark.asyncio
@pytest.mark.integration
async def test_icon_content_type_is_eager_indicator(db_session: AsyncSession):
    """icon_content_type must NOT be deferred — it's the eager has-icon
    indicator that route handlers consult instead of icon_data, so it must
    be loaded on every default query."""
    from backend.app.models.oidc_provider import OIDCProvider

    await _seed_provider(db_session, name="IndicatorProv")
    result = await db_session.execute(select(OIDCProvider).where(OIDCProvider.name == "IndicatorProv"))
    provider = result.scalar_one()

    state = inspect(provider)
    assert "icon_content_type" not in state.unloaded
    # Direct access does not raise MissingGreenlet (it was already loaded).
    assert provider.icon_content_type == "image/png"
