"""Both reset paths send the "Password Reset" template, the reset link added at the end.

The self-service reset (/auth/forgot-password) and the admin one
(/auth/reset-password) built the link email from fixed text, so an admin's
edit of the template never reached anyone.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from backend.app.models.notification_template import NotificationTemplate
from backend.tests.integration.test_advanced_auth_api import _setup_admin, _setup_smtp_and_advanced_auth

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _prepare(async_client: AsyncClient, db_session) -> tuple[dict, int]:
    token = await _setup_admin(async_client, "tpladmin", "AdminPass1!")
    headers = {"Authorization": f"Bearer {token}"}
    db_session.add(
        NotificationTemplate(
            event_type="password_reset",
            name="Password Reset",
            title_template="Custom reset for {username}",
            body_template="Our own words.",
            is_default=False,
        )
    )
    await db_session.commit()
    with patch("backend.app.api.routes.users.send_email"):
        await _setup_smtp_and_advanced_auth(async_client, token)
        created = await async_client.post(
            "/api/v1/users/",
            headers=headers,
            json={"username": "tpluser", "email": "tpluser@test.com", "role": "user"},
        )
    assert created.status_code == 201, created.text
    return headers, created.json()["id"]


def _assert_template_sent(mock_send) -> None:
    mock_send.assert_called_once()
    _smtp, to, subject, text_body, html_body = mock_send.call_args[0][:5]
    assert to == "tpluser@test.com"
    assert subject == "Custom reset for tpluser"
    words, link = text_body.split("\n\n")
    assert words == "Our own words."
    assert "#reset_token=" in link
    assert "#reset_token=" in html_body


async def test_the_self_service_reset_sends_the_template(async_client: AsyncClient, db_session):
    await _prepare(async_client, db_session)

    with patch("backend.app.api.routes.auth.send_email") as mock_send:
        response = await async_client.post("/api/v1/auth/forgot-password", json={"email": "tpluser@test.com"})

    assert response.status_code == 200
    _assert_template_sent(mock_send)


async def test_the_admin_reset_sends_the_template(async_client: AsyncClient, db_session):
    headers, user_id = await _prepare(async_client, db_session)

    with patch("backend.app.api.routes.auth.send_email") as mock_send:
        response = await async_client.post("/api/v1/auth/reset-password", headers=headers, json={"user_id": user_id})

    assert response.status_code == 200
    _assert_template_sent(mock_send)
