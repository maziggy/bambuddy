"""Editing a user can remove their email: an empty email clears it.

Left out or null, the email stays as it is. With advanced authentication on,
a user needs an email (password resets go there), so it can't be removed.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.models.settings import Settings
from backend.app.models.user import User


async def _user(db_session, email: str | None = "mia@example.invalid") -> User:
    from backend.app.core.auth import get_password_hash

    user = User(username="mia", password_hash=get_password_hash("Whatever1!"), email=email, role="user", is_active=True)
    db_session.add(user)
    await db_session.commit()
    return user


async def _email(db_session, user_id: int) -> str | None:
    db_session.expire_all()
    return (await db_session.execute(select(User.email).where(User.id == user_id))).scalar_one()


@pytest.mark.asyncio
@pytest.mark.parametrize("email", ["", "   "])
async def test_an_empty_email_clears_it(async_client: AsyncClient, db_session, email):
    user = await _user(db_session)

    response = await async_client.patch(f"/api/v1/users/{user.id}", json={"email": email})

    assert response.status_code == 200, response.text
    assert response.json()["email"] is None
    assert await _email(db_session, user.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{}, {"email": None}, {"role": "user"}])
async def test_left_out_or_null_keeps_it(async_client: AsyncClient, db_session, body):
    user = await _user(db_session)

    response = await async_client.patch(f"/api/v1/users/{user.id}", json=body)

    assert response.status_code == 200, response.text
    assert await _email(db_session, user.id) == "mia@example.invalid"


@pytest.mark.asyncio
async def test_two_users_without_email_are_no_duplicate(async_client: AsyncClient, db_session):
    from backend.app.core.auth import get_password_hash

    db_session.add(User(username="other", password_hash=get_password_hash("Whatever1!"), email=None, role="user"))
    user = await _user(db_session)

    response = await async_client.patch(f"/api/v1/users/{user.id}", json={"email": ""})

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_refused_while_advanced_authentication_is_on(async_client: AsyncClient, db_session):
    user = await _user(db_session)
    db_session.add(Settings(key="advanced_auth_enabled", value="true"))
    await db_session.commit()

    response = await async_client.patch(f"/api/v1/users/{user.id}", json={"email": ""})

    assert response.status_code == 400
    assert "advanced authentication" in response.json()["detail"]
    assert await _email(db_session, user.id) == "mia@example.invalid"


@pytest.mark.asyncio
async def test_a_user_without_email_can_still_be_edited_with_advanced_authentication(
    async_client: AsyncClient, db_session
):
    user = await _user(db_session, email=None)
    db_session.add(Settings(key="advanced_auth_enabled", value="true"))
    await db_session.commit()

    response = await async_client.patch(f"/api/v1/users/{user.id}", json={"email": "", "role": "user"})

    assert response.status_code == 200, response.text
    assert await _email(db_session, user.id) is None


@pytest.mark.asyncio
async def test_refused_while_the_user_signs_in_with_an_email_code(async_client: AsyncClient, db_session):
    """Email 2FA needs SMTP, not advanced authentication: the code goes to this address."""
    user = await _user(db_session)
    db_session.add(Settings(key=f"user_{user.id}_email_2fa_enabled", value="true"))
    await db_session.commit()

    response = await async_client.patch(f"/api/v1/users/{user.id}", json={"email": ""})

    assert response.status_code == 400
    assert "email" in response.json()["detail"].lower()
    assert await _email(db_session, user.id) == "mia@example.invalid"
