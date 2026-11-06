"""Creating a user sends the Welcome template with the login details added by
Bambuddy, never a password placed by the template."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from backend.app.models.notification_template import NotificationTemplate
from backend.tests.integration.test_advanced_auth_api import _setup_admin, _setup_smtp_and_advanced_auth

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_a_new_users_password_is_not_wrapped_in_the_templates_link(async_client: AsyncClient, db_session):
    token = await _setup_admin(async_client, "welcadmin", "AdminPass1!")
    headers = {"Authorization": f"Bearer {token}"}
    db_session.add(
        NotificationTemplate(
            event_type="user_created",
            name="Welcome Email",
            title_template="Welcome {password}",
            body_template="Activate: https://elsewhere.example/a?p={password}",
            is_default=False,
        )
    )
    await db_session.commit()

    with patch("backend.app.api.routes.users.send_email") as mock_send:
        await _setup_smtp_and_advanced_auth(async_client, token)
        created = await async_client.post(
            "/api/v1/users/",
            headers=headers,
            json={"username": "newbie", "email": "newbie@test.com", "role": "user"},
        )

    assert created.status_code == 201, created.text
    mock_send.assert_called_once()
    _smtp, to, subject, text_body, html_body = mock_send.call_args[0][:5]
    assert to == "newbie@test.com"
    assert subject == "Welcome "
    words, details = text_body.split("\n\n")
    assert words == "Activate: https://elsewhere.example/a?p="
    assert details.startswith("Username: newbie\nPassword: ")
    assert len(details.removeprefix("Username: newbie\nPassword: ")) >= 12
    assert "p=" + details.split("Password: ")[1][:4] not in html_body
