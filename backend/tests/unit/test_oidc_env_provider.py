import pytest

from backend.app.models.oidc_provider import OIDCProvider


@pytest.mark.asyncio
async def test_is_env_managed_defaults_false(db_session):
    p = OIDCProvider(name="x", issuer_url="https://i", client_id="c", client_secret="s")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    assert p.is_env_managed is False
